"""飞书每日 ASK_USER 摘要 bot：每天定时推一张卡片到飞书群，告诉用户：
  - 今天 AI Agent 触发了多少次审批
  - 多少批准 / 拒绝 / 超时
  - 高风险 N 次（含具体 tool 名）
  - 当前自动决策规则数 / 当前模式

为什么要这个：
  飞书 AI 校园挑战赛 → 飞书集成深度加分。每日摘要把 dashboard 的「实时事件流」抽
  象成「日报」，让用户不用打开 dashboard 也能掌握 AI 行为大盘。

设计：
  - 后台 daemon thread；每分钟检查一次时间是否到「推送时间」（默认 21:00）
  - 用 audit DB 直查今天数据
  - 借 LarkNotifier 推一张交互卡片
  - 用文件 ~/.sentinel-mcp/digest_state.json 记上次推送日期，避免同一天重复推
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

STATE_PATH = Path(os.environ.get(
    "SENTINEL_DIGEST_STATE",
    str(Path.home() / ".sentinel-mcp" / "digest_state.json"),
))
DEFAULT_HOUR = 21  # 推送时间（24h 制）
DEFAULT_MINUTE = 0


def _read_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def build_digest_card(stats: dict[str, Any]) -> dict:
    """构造交互卡片（飞书 schema）。"""
    today = stats.get("date", datetime.now().strftime("%Y-%m-%d"))
    total = stats.get("total", 0)
    approved = stats.get("approved", 0)
    denied = stats.get("denied", 0)
    expired = stats.get("expired", 0)
    high_risk = stats.get("high_risk_count", 0)
    high_risk_tools = stats.get("high_risk_tools", [])
    mode = stats.get("mode", "active")
    auto_rules_count = stats.get("auto_rules_count", 0)

    elements = [
        {
            "tag": "div",
            "fields": [
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**📊 总审批**\n{total} 次"}},
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**✓ 批准**\n{approved} 次"}},
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**✗ 拒绝**\n{denied} 次"}},
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**⏱ 超时**\n{expired} 次"}},
            ],
        },
        {"tag": "hr"},
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**🚨 高风险事件**：{high_risk} 次"
                + (f"\n_常见工具：{', '.join(high_risk_tools[:5])}_" if high_risk_tools else ""),
            },
        },
        {"tag": "hr"},
        {
            "tag": "div",
            "fields": [
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**🛡 当前模式**\n`{mode}`"}},
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**⚡ 自动规则**\n{auto_rules_count} 条"}},
            ],
        },
        {
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": f"Sentinel-MCP · 日报 · {today}"}],
        },
    ]
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"📅 今日 AI 工具调用日报"},
            "template": "blue" if denied == 0 and high_risk == 0 else ("orange" if denied > 0 else "red"),
        },
        "elements": elements,
    }


def compute_today_stats(audit_db_path: str) -> dict[str, Any]:
    """直接从 audit DB 拿今天的统计。"""
    import sqlite3

    today_str = datetime.now().strftime("%Y-%m-%d")
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    tomorrow_start = today_start + 86400

    out: dict[str, Any] = {
        "date": today_str,
        "total": 0, "approved": 0, "denied": 0, "expired": 0,
        "high_risk_count": 0, "high_risk_tools": [],
    }

    if not Path(audit_db_path).exists():
        return out

    try:
        conn = sqlite3.connect(audit_db_path, timeout=5)
        # pending_decisions 是 PendingDecisions 用的表
        cur = conn.execute(
            "SELECT status, tool_name, risk_score FROM pending_decisions "
            "WHERE created_at >= ? AND created_at < ?",
            (today_start, tomorrow_start),
        )
        rows = cur.fetchall()
        conn.close()
    except sqlite3.Error:
        return out

    out["total"] = len(rows)
    high_risk_tools_count: dict[str, int] = {}
    for status, tool_name, risk_score in rows:
        if status == "approved":
            out["approved"] += 1
        elif status == "denied":
            out["denied"] += 1
        elif status == "expired":
            out["expired"] += 1
        if (risk_score or 0) >= 0.7:
            out["high_risk_count"] += 1
            high_risk_tools_count[tool_name] = high_risk_tools_count.get(tool_name, 0) + 1

    out["high_risk_tools"] = sorted(
        high_risk_tools_count, key=lambda k: -high_risk_tools_count[k]
    )
    return out


class DigestScheduler:
    """daemon thread：每分钟检查时间，到点推一次。"""

    def __init__(
        self,
        audit_db_path: str,
        notifier_factory,  # 0-arg callable → LarkNotifier | None
        target_hour: int = DEFAULT_HOUR,
        target_minute: int = DEFAULT_MINUTE,
    ) -> None:
        self.audit_db_path = audit_db_path
        self.notifier_factory = notifier_factory
        self.target_hour = target_hour
        self.target_minute = target_minute
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_pushed_date: str | None = None
        self._last_result: dict[str, Any] = {}
        # 启动时从 state 文件恢复 last_pushed_date
        state = _read_state()
        self._last_pushed_date = state.get("last_pushed_date")

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="lark-digest")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict[str, Any]:
        return {
            "running": self._thread is not None and self._thread.is_alive(),
            "target_hour": self.target_hour,
            "target_minute": self.target_minute,
            "last_pushed_date": self._last_pushed_date,
            "last_result": self._last_result,
        }

    def push_now(self) -> dict[str, Any]:
        """手动触发：管理面板「立刻发」按钮用。"""
        return self._do_push(force=True)

    def _do_push(self, force: bool = False) -> dict[str, Any]:
        today_str = datetime.now().strftime("%Y-%m-%d")
        if not force and self._last_pushed_date == today_str:
            return {"ok": False, "reason": "already_pushed_today"}
        notifier = self.notifier_factory()
        if notifier is None:
            return {"ok": False, "reason": "lark_not_configured"}
        if not notifier.cfg.target_chat_id:
            return {"ok": False, "reason": "no_target_chat_id"}

        stats = compute_today_stats(self.audit_db_path)
        # 加 mode + auto_rules
        try:
            from sentinel_mcp.runtime_mode import read_mode
            from sentinel_mcp.auto_decisions import list_rules
            stats["mode"] = read_mode()
            stats["auto_rules_count"] = len(list_rules())
        except Exception:
            stats["mode"] = "?"
            stats["auto_rules_count"] = 0

        card = build_digest_card(stats)
        # 直接用 lark sdk 发卡片（绕开 send_pending 的 pending dict 假设）
        try:
            from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
            req = CreateMessageRequest.builder() \
                .receive_id_type(notifier._infer_id_type(notifier.cfg.target_chat_id)) \
                .request_body(
                    CreateMessageRequestBody.builder()
                        .receive_id(notifier.cfg.target_chat_id)
                        .msg_type("interactive")
                        .content(json.dumps(card, ensure_ascii=False))
                        .build()
                ).build()
            resp = notifier.client.im.v1.message.create(req)
            if not resp.success():
                return {"ok": False, "reason": "send_failed", "code": resp.code, "msg": resp.msg}
        except Exception as e:
            return {"ok": False, "reason": "exception", "error": str(e)}

        self._last_pushed_date = today_str
        self._last_result = {"date": today_str, "stats": stats}
        _write_state({"last_pushed_date": today_str, "last_stats": stats})
        return {"ok": True, "date": today_str, "stats": stats}

    def _loop(self) -> None:
        # 每 60 秒检查一次：到点 + 还没推过 → 推
        while not self._stop.is_set():
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            if (now.hour == self.target_hour and now.minute >= self.target_minute
                    and self._last_pushed_date != today_str):
                try:
                    self._do_push()
                except Exception as e:
                    print(f"[digest] push failed: {e}")
            # sleep 60s（被 stop() 唤醒立刻退）
            self._stop.wait(60)
