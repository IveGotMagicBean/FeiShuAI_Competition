"""自动决策规则：用户在审批卡片上点「总是批准 / 总是拒绝」后，
后续相同 tool_name 的 ASK_USER 自动决断，不再弹卡片。

用户痛点：每次 npm install 都被问一遍 → 两小时关掉 dashboard。
解决：用户对一个工具点过「总是批准」之后，未来所有 (tool_name) 命中的
ASK_USER 直接放行，不再创建 pending_decisions、不发飞书、不响铃。

存储：~/.sentinel-mcp/auto_decisions.json
读取频率：每个 ASK_USER 决策点 1 次（μs 级，不缓存以保证热更新）。

匹配维度（v0.3 起步）：
  - tool_name 精确匹配
  - 后续可加：args.path 子串 / glob，但要小心 false-allow 风险
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Literal

DecisionLiteral = Literal["allow", "deny"]

DEFAULT_PATH = Path(os.environ.get(
    "SENTINEL_AUTO_DECISIONS_FILE",
    str(Path.home() / ".sentinel-mcp" / "auto_decisions.json"),
))


def _read_file(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    rules = data.get("rules") or []
    return rules if isinstance(rules, list) else []


def _write_file(rules: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump({"rules": rules}, f, indent=2)
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def _resolve_path(path: Path | None) -> Path:
    """用 None 兜底 → 走全局 DEFAULT_PATH（每次动态读，monkey-patch 立刻生效）。"""
    return path if path is not None else DEFAULT_PATH


def list_rules(path: Path | None = None) -> list[dict]:
    """列出当前所有自动决策规则。"""
    return _read_file(_resolve_path(path))


def add_rule(
    tool_name: str,
    decision: DecisionLiteral,
    by: str = "dashboard",
    path: Path | None = None,
) -> dict:
    """新增 / 覆盖一条规则。返回该规则。"""
    if not tool_name:
        raise ValueError("tool_name 不能空")
    if decision not in ("allow", "deny"):
        raise ValueError(f"decision 必须是 allow / deny，收到: {decision}")
    p = _resolve_path(path)
    rules = _read_file(p)
    # 同 tool_name 已有 → 覆盖（更新 decision + 时间戳）
    rules = [r for r in rules if r.get("tool_name") != tool_name]
    new_rule = {
        "tool_name": tool_name,
        "decision": decision,
        "created_at": time.time(),
        "by": by,
    }
    rules.append(new_rule)
    _write_file(rules, p)
    return new_rule


def delete_rule(tool_name: str, path: Path | None = None) -> bool:
    """删除一条规则。返回是否真的删了。"""
    p = _resolve_path(path)
    rules = _read_file(p)
    new = [r for r in rules if r.get("tool_name") != tool_name]
    if len(new) == len(rules):
        return False
    _write_file(new, p)
    return True


def lookup_decision(
    tool_name: str, path: Path | None = None
) -> DecisionLiteral | None:
    """proxy/审批回调里调：返回该 tool_name 的自动决策；无 → None。"""
    for r in _read_file(_resolve_path(path)):
        if r.get("tool_name") == tool_name:
            d = r.get("decision")
            if d in ("allow", "deny"):
                return d  # type: ignore[return-value]
    return None
