"""Claude Code PreToolUse Hook 适配器：把 hook 传过来的 JSON 翻译成 Guard 决策。

Claude Code 的 hook 协议（v0.2+）：
  - hook 被调用时通过 stdin 传一个 JSON：
      {
        "session_id": "...",
        "transcript_path": "...",
        "cwd": "...",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash" | "Read" | "Write" | "Edit" | ...,
        "tool_input": { "command": "rm -rf /", ... }
      }
  - 退出码约定：
      · 0 → 允许（stdout 注入回 system 提示，可空）
      · 2 → 阻止（stderr 给 Claude 看；Claude 会重新规划）
      · 其他 → Claude 继续，stderr 给用户看

也支持结构化 JSON 输出，但退出码方案最简单可靠。

为什么独立模块：
  hook 是「单次进程」(每次工具调用 fork 一次 sentinel-mcp hook-check)，跟
  `wrap` 子命令的「常驻 stdio 代理」生命周期完全不同：不能复用 MCPProxy。
  这里只跑 Guard 的同步检查 + 写 audit，不涉及 stdio JSON-RPC 转发。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from guard import Decision, Guard, ToolCall
from sentinel_mcp.runtime_mode import is_off, is_passive


def _read_hook_input() -> dict:
    """从 stdin 读 PreToolUse JSON。"""
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[sentinel-hook] invalid JSON on stdin: {e}", file=sys.stderr)
        return {}


# Claude Code 内置工具名 → policy.yaml 里 tools 节定义的工具名
# 不在表里的（Glob, Grep, TodoWrite, NotebookEdit）默认走 shell_exec / write_file 兜底，
# 或保持原名（policy 不认识就用空 policy 走默认 ALLOW）
_TOOL_NAME_MAP = {
    "Bash": "shell_exec",
    "Write": "write_file",
    "Edit": "write_file",
    "MultiEdit": "write_file",
    "Read": "read_file",
    "Glob": "list_dir",
    "Grep": "read_file",
    "WebFetch": "http_request",
    "WebSearch": "http_request",
    "NotebookEdit": "write_file",
}


def _translate_tool(tool_name: str, tool_input: dict) -> tuple[str, dict]:
    """把 Claude Code hook 传的 tool_name + input 翻译成 policy 能识别的形态。

    映射后 args 字段名也尽量贴 policy 习惯：
      Bash:   {command, description}        → shell_exec: {command, description}
      Write:  {file_path, content}          → write_file: {path, content}
      Edit:   {file_path, old_string, ...}  → write_file: {path, old, new}
      Read:   {file_path, offset, limit}    → read_file:  {path}
      WebFetch: {url, prompt}               → http_request: {url, method:'GET'}
    """
    mapped = _TOOL_NAME_MAP.get(tool_name, tool_name)
    args = dict(tool_input or {})

    # 字段名标准化（policy 沙箱按 path / command / url 找）
    if "file_path" in args and "path" not in args:
        args["path"] = args["file_path"]
    if mapped == "http_request" and "url" in args:
        args.setdefault("method", "GET")

    return mapped, args


def _emit_block(reason: str, *, structured: bool = False) -> int:
    """发出阻止信号。Claude Code 期望 exit code 2 + stderr。"""
    if structured:
        # JSON 输出（v0.3+ Claude Code 支持）
        print(json.dumps({
            "continue": False,
            "stopReason": reason,
            "decision": "block",
            "reason": reason,
        }))
    print(f"❌ Sentinel-MCP 阻止：{reason}", file=sys.stderr)
    return 2


def _emit_allow(reason: str = "", *, structured: bool = False) -> int:
    if structured and reason:
        print(json.dumps({"continue": True, "decision": "approve", "reason": reason}))
    return 0


def run_hook_check(
    *,
    config_path: str | None = None,
    audit_db: str | None = None,
    structured_output: bool = False,
) -> int:
    """主入口：CLI 的 hook-check 子命令调它。"""
    payload = _read_hook_input()
    if not payload:
        # 输入为空 → 默认允许（防止 hook 配错把 Claude 卡死）
        return _emit_allow("(empty hook input)")

    raw_tool_name = payload.get("tool_name") or "<unknown>"
    raw_tool_input = payload.get("tool_input") or {}
    session_id = payload.get("session_id") or "default"

    # 把 Claude Code 内置工具名（Bash/Write/...）翻译成 policy 识别的工具名
    tool_name, tool_input = _translate_tool(raw_tool_name, raw_tool_input)

    # off 模式：完全透明，连 audit 都不写
    if is_off():
        return _emit_allow("off mode")

    # 决定 audit DB
    db = audit_db or os.environ.get("SENTINEL_DB") or str(Path.cwd() / "data" / "sentinel.db")
    Path(db).parent.mkdir(parents=True, exist_ok=True)

    # 决定 policy
    if config_path is None:
        # 默认 policy：包内自带
        config_path = str(Path(__file__).resolve().parent / "config" / "policies.yaml")

    # passive 模式：跳过 guard，仅记录到 audit
    if is_passive():
        try:
            from guard.audit import AuditLog
            from guard.core import GuardResult
            audit = AuditLog(db)
            audit.log_event(
                "tool_call_hook", tool_name, tool_input,
                GuardResult(
                    decision=Decision.ALLOW,
                    reason="passive 模式：跳过 Sentinel 检查",
                    risk_score=0.0,
                    triggered_rules=["mode:passive", "source:claude-code-hook"],
                ),
                call_id=f"hook-{int(time.time() * 1000)}",
            )
        except Exception as e:
            print(f"[sentinel-hook] passive audit failed: {e}", file=sys.stderr)
        return _emit_allow("passive mode")

    # active 模式：跑 guard
    # 注意 hook 是单进程（fork 一次跑一次），不应起 ASK_USER 阻塞 — 把回调改成「立刻拒绝」
    # 实际产品里 ASK_USER 走 dashboard / 飞书等异步通道，但 Claude Code hook 等不了
    # （hook 超时机制会卡死 Claude）。所以 hook 模式下的 ASK_USER 默认拒绝并提示用户去 dashboard 决断
    from sentinel_mcp.approvals import PendingDecisions
    approvals = PendingDecisions(db)

    def _hook_ask(call, pending) -> bool:
        # 创建 pending 让用户在 dashboard 看到
        try:
            approvals.create(
                tool_name=call.tool_name,
                args=call.args,
                reason=pending.reason,
                risk_score=pending.risk_score,
                triggered=list(pending.triggered_rules or []) + ["source:claude-code-hook"],
                session_id=session_id,
            )
        except Exception:
            pass
        # hook 不能等：直接当拒绝。提示用户去 dashboard 重试
        return False

    try:
        guard = Guard.from_yaml(config_path, audit_db_path=db, ask_user_callback=_hook_ask)
    except Exception as e:
        print(f"[sentinel-hook] guard init failed: {e}", file=sys.stderr)
        return _emit_allow("(guard init failed - permissive fallback)")

    call = ToolCall(tool_name=tool_name, args=tool_input, source="claude-code-hook")
    try:
        result = guard.check_tool_call(call)
    except Exception as e:
        print(f"[sentinel-hook] guard check failed: {e}", file=sys.stderr)
        return _emit_allow("(guard check failed - permissive fallback)")

    if result.decision == Decision.DENY:
        rules = ", ".join(result.triggered_rules or []) or "策略匹配"
        return _emit_block(
            f"{result.reason or '风险拒绝'} (risk={result.risk_score:.2f} · {rules})",
            structured=structured_output,
        )

    if result.decision == Decision.REDACT:
        # hook 没法重写 tool_input（PreToolUse 阶段不接受参数修改），降级为阻止
        return _emit_block(
            f"输入含敏感数据需脱敏，但 hook 无法重写参数：{result.reason}",
            structured=structured_output,
        )

    # ALLOW
    return _emit_allow(result.reason or "passed", structured=structured_output)
