"""核心：决策引擎、类型定义、Guard 主类"""

from __future__ import annotations

import functools
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class Decision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK_USER = "ask_user"
    REDACT = "redact"


class GuardBlockedError(Exception):
    """工具调用被 Guard 拦截"""

    def __init__(self, reason: str, rules: list[str] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.rules = rules or []


@dataclass
class ToolCall:
    tool_name: str
    args: dict
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = field(default_factory=time.time)
    source: str = "sdk"
    parent_id: str | None = None
    session_id: str = "default"


@dataclass
class GuardResult:
    decision: Decision
    reason: str = ""
    risk_score: float = 0.0
    triggered_rules: list[str] = field(default_factory=list)
    redacted_args: dict | None = None
    redacted_output: Any = None

    def to_dict(self) -> dict:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "risk_score": self.risk_score,
            "triggered_rules": self.triggered_rules,
        }


class Guard:
    """安全卫士主入口

    使用方式：
        guard = Guard.from_yaml("config/policies.yaml")

        @guard.protected("filesystem")
        def read_file(path: str) -> str: ...
    """

    def __init__(
        self,
        config: dict | None = None,
        audit_db_path: str | Path = "data/audit.db",
        ask_user_callback: Callable[[ToolCall, GuardResult], bool] | None = None,
    ):
        from guard.audit import AuditLog
        from guard.detectors.dlp import DLPDetector
        from guard.detectors.prompt_injection import PromptInjectionDetector
        from guard.policies import load_policies

        self.config = config or {}
        self.policies = load_policies(self.config)
        self.injection_detector = PromptInjectionDetector()
        self.dlp_detector = DLPDetector(
            extra_patterns=self.config.get("detectors", {})
            .get("dlp", {})
            .get("extra_patterns", {}),
        )
        Path(audit_db_path).parent.mkdir(parents=True, exist_ok=True)
        self.audit = AuditLog(str(audit_db_path))
        self.ask_user_callback = ask_user_callback or self._default_ask_user
        self._authorized_once: set[str] = set()  # 记住"本会话一次性"授权
        self._call_history: list[ToolCall] = []

    # ---------- 工厂 ----------
    @classmethod
    def from_yaml(cls, path: str | Path, **kwargs) -> Guard:
        import yaml

        with open(path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        return cls(config=config, **kwargs)

    # ---------- L1：输入层 ----------
    def check_input(self, prompt: str, source: str = "user") -> GuardResult:
        """检查用户输入或 RAG 上下文中的注入攻击"""
        threshold = (
            self.config.get("detectors", {})
            .get("prompt_injection", {})
            .get("threshold", 0.5)
        )
        risk, rules = self.injection_detector.detect(prompt)
        if risk >= threshold:
            result = GuardResult(
                decision=Decision.DENY,
                reason=f"检测到提示注入风险（{source}）：{', '.join(rules[:3])}",
                risk_score=risk,
                triggered_rules=rules,
            )
        else:
            result = GuardResult(
                decision=Decision.ALLOW,
                reason="输入安全",
                risk_score=risk,
                triggered_rules=rules,
            )
        self.audit.log_event(
            event_type="input_check",
            tool_name=source,
            args={"prompt": prompt[:500]},
            result=result,
        )
        return result

    # ---------- L2 + L3：调用层 + 沙箱层 ----------
    def check_tool_call(self, call: ToolCall) -> GuardResult:
        """检查一次工具调用"""
        # 1) 参数中的注入扫描
        flat_args = json.dumps(call.args, ensure_ascii=False)
        injection_risk, injection_rules = self.injection_detector.detect(flat_args)

        # 2) 策略沙箱
        sandbox_decision, sandbox_reason, sandbox_rules = self._check_sandbox(call)

        # 3) 速率限制
        rate_ok, rate_reason = self._check_rate_limit(call)
        if not rate_ok:
            result = GuardResult(
                decision=Decision.DENY,
                reason=f"速率超限：{rate_reason}",
                risk_score=0.7,
                triggered_rules=["rate_limit"],
            )
            self.audit.log_event("tool_call", call.tool_name, call.args, result, call_id=call.id)
            return result

        # 4) 综合裁定
        rules = sandbox_rules + (injection_rules if injection_risk >= 0.5 else [])
        risk = max(injection_risk, 1.0 if sandbox_decision == Decision.DENY else 0.0)

        if sandbox_decision == Decision.DENY:
            result = GuardResult(
                decision=Decision.DENY,
                reason=sandbox_reason,
                risk_score=risk,
                triggered_rules=rules,
            )
        elif self._needs_user_authz(call):
            result = GuardResult(
                decision=Decision.ASK_USER,
                reason="此为敏感工具调用，需要用户确认",
                risk_score=risk,
                triggered_rules=rules,
            )
        else:
            result = GuardResult(
                decision=Decision.ALLOW,
                reason=sandbox_reason or "通过沙箱检查",
                risk_score=risk,
                triggered_rules=rules,
            )

        # 5) 用户授权交互
        if result.decision == Decision.ASK_USER:
            authz_key = f"{call.tool_name}:{call.session_id}"
            if authz_key in self._authorized_once:
                result = GuardResult(
                    decision=Decision.ALLOW,
                    reason="本会话已授权",
                    risk_score=risk,
                    triggered_rules=rules,
                )
            else:
                approved = self.ask_user_callback(call, result)
                if approved:
                    self._authorized_once.add(authz_key)
                    result = GuardResult(
                        decision=Decision.ALLOW,
                        reason="用户授权通过",
                        risk_score=risk,
                        triggered_rules=rules,
                    )
                else:
                    result = GuardResult(
                        decision=Decision.DENY,
                        reason="用户拒绝授权",
                        risk_score=risk,
                        triggered_rules=rules + ["user_denied"],
                    )

        self.audit.log_event(
            "tool_call", call.tool_name, call.args, result, call_id=call.id
        )
        if result.decision == Decision.ALLOW:
            self._call_history.append(call)
        return result

    # ---------- L4：输出层 ----------
    def check_output(self, output: Any, call: ToolCall) -> GuardResult:
        """对工具返回内容做 DLP 扫描和脱敏"""
        if not self.config.get("detectors", {}).get("dlp", {}).get("enabled", True):
            return GuardResult(decision=Decision.ALLOW, redacted_output=output)

        text = output if isinstance(output, str) else json.dumps(
            output, ensure_ascii=False, default=str
        )
        findings, redacted = self.dlp_detector.scan(text)
        if findings:
            rules = [f"dlp:{f['type']}" for f in findings]
            result = GuardResult(
                decision=Decision.REDACT,
                reason=f"输出含 {len(findings)} 处敏感信息，已脱敏",
                risk_score=min(0.3 + 0.1 * len(findings), 1.0),
                triggered_rules=rules,
                redacted_output=redacted if isinstance(output, str) else redacted,
            )
        else:
            result = GuardResult(
                decision=Decision.ALLOW,
                reason="输出无敏感信息",
                redacted_output=output,
            )
        self.audit.log_event(
            "output_check",
            call.tool_name,
            {"output_preview": text[:200]},
            result,
            call_id=call.id,
        )
        return result

    # ---------- 装饰器接口（即插即用核心） ----------
    def protected(
        self,
        policy: str = "default",
        tool_name: str | None = None,
        require_user_authz: bool | None = None,
    ) -> Callable:
        def decorator(func: Callable) -> Callable:
            name = tool_name or func.__name__

            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                merged = self._args_to_dict(func, args, kwargs)
                call = ToolCall(
                    tool_name=name,
                    args=merged,
                    source="sdk",
                )
                if require_user_authz is not None:
                    call.args["__require_user_authz__"] = require_user_authz

                pre = self.check_tool_call(call)
                if pre.decision == Decision.DENY:
                    raise GuardBlockedError(pre.reason, pre.triggered_rules)

                output = func(*args, **kwargs)
                post = self.check_output(output, call)
                if post.decision == Decision.REDACT:
                    return post.redacted_output
                return output

            wrapper.__guard_policy__ = policy  # type: ignore
            wrapper.__guard_tool_name__ = name  # type: ignore
            return wrapper

        return decorator

    # ---------- 内部辅助 ----------
    def _check_sandbox(self, call: ToolCall) -> tuple[Decision, str, list[str]]:
        tool_cfg = self.config.get("tools", {}).get(call.tool_name, {})
        policy_name = tool_cfg.get("policy")
        if not policy_name:
            return Decision.ALLOW, "无关联沙箱策略", []

        if policy_name == "filesystem":
            path = call.args.get("path") or call.args.get("file") or call.args.get("filename")
            if path is None:
                return Decision.ALLOW, "未提取到路径参数", []
            ok, reason = self.policies["filesystem"].check(str(path))
            return (Decision.ALLOW if ok else Decision.DENY, reason, ["filesystem"])

        if policy_name == "network":
            url = call.args.get("url") or call.args.get("uri") or call.args.get("endpoint")
            if url is None:
                return Decision.ALLOW, "未提取到 URL 参数", []
            ok, reason = self.policies["network"].check(str(url))
            return (Decision.ALLOW if ok else Decision.DENY, reason, ["network"])

        if policy_name == "shell":
            cmd = call.args.get("cmd") or call.args.get("command") or call.args.get("script")
            if cmd is None:
                return Decision.ALLOW, "未提取到命令参数", []
            ok, reason = self.policies["shell"].check(str(cmd))
            return (Decision.ALLOW if ok else Decision.DENY, reason, ["shell"])

        return Decision.ALLOW, f"未知策略 {policy_name}", []

    def _check_rate_limit(self, call: ToolCall) -> tuple[bool, str]:
        limits = self.config.get("rate_limits", {}).get(call.tool_name)
        if not limits:
            return True, ""
        window = limits.get("window", 60)
        max_calls = limits.get("max", 100)
        now = call.timestamp
        recent = [
            c
            for c in self._call_history
            if c.tool_name == call.tool_name and now - c.timestamp <= window
        ]
        if len(recent) >= max_calls:
            return False, f"{call.tool_name} 在 {window}s 内已调用 {len(recent)} 次（上限 {max_calls}）"
        return True, ""

    def _needs_user_authz(self, call: ToolCall) -> bool:
        if call.args.pop("__require_user_authz__", None) is True:
            return True
        return self.config.get("tools", {}).get(call.tool_name, {}).get(
            "require_user_authz", False
        )

    @staticmethod
    def _args_to_dict(func: Callable, args: tuple, kwargs: dict) -> dict:
        import inspect

        try:
            sig = inspect.signature(func)
            bound = sig.bind_partial(*args, **kwargs)
            return dict(bound.arguments)
        except Exception:
            return {"args": list(args), "kwargs": kwargs}

    @staticmethod
    def _default_ask_user(call: ToolCall, pending: GuardResult) -> bool:
        """终端模式下的简易授权弹窗"""
        try:
            from rich.console import Console
            from rich.panel import Panel

            console = Console()
            console.print(
                Panel.fit(
                    f"[bold yellow]工具调用授权请求[/bold yellow]\n"
                    f"工具：[cyan]{call.tool_name}[/cyan]\n"
                    f"参数：{json.dumps(call.args, ensure_ascii=False)[:200]}\n"
                    f"理由：{pending.reason}\n"
                    f"风险分：{pending.risk_score:.2f}",
                    title="🛡  Agent Guard",
                    border_style="yellow",
                )
            )
            ans = input("是否授权？[y/N] ").strip().lower()
            return ans in ("y", "yes")
        except Exception:
            ans = input(
                f"[Agent Guard] 工具 {call.tool_name} 请求授权（参数 {call.args}）。允许？[y/N] "
            ).strip().lower()
            return ans in ("y", "yes")
