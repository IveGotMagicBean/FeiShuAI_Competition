"""跨进程的 ASK_USER 审批队列。

设计：
  Proxy 进程（写入 + 轮询）  ←→  共享 SQLite 表 `pending_decisions`  ←→  Dashboard 进程（读取 + 改状态）

为什么用 SQLite 不用 socket / pipe：
  - 一个 PWA dashboard 服务器可能要管多个 Proxy 实例（多设备 / 多 MCP server）
  - SQLite WAL 模式下多进程读写都没问题，且能持久化历史审批
  - 实现复杂度最低，不需要管连接管理 / 重连 / 心跳

表结构在 INIT_SCHEMA。

使用：
  approvals = PendingDecisions(db_path)
  callback = approvals.make_callback(timeout_seconds=60)
  guard = Guard.from_yaml(cfg, audit_db_path=db_path, ask_user_callback=callback)

Dashboard 侧：
  approvals.list_pending()                  # 列待审批
  approvals.decide(id, approved=True, by="phone")
  approvals.subscribe()                     # 给 SSE 用：返回新 pending 事件迭代器
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_decisions (
    id           TEXT PRIMARY KEY,
    created_at   REAL NOT NULL,
    decided_at   REAL,
    tool_name    TEXT NOT NULL,
    args_json    TEXT NOT NULL,
    reason       TEXT,
    risk_score   REAL,
    triggered    TEXT,                       -- JSON array
    status       TEXT NOT NULL,              -- pending | approved | denied | expired
    decided_by   TEXT,                       -- 'dashboard' | 'phone' | 'cli' | 'auto-expire'
    session_id   TEXT
);
CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_decisions(status);
CREATE INDEX IF NOT EXISTS idx_pending_created ON pending_decisions(created_at);
"""


class PendingDecisions:
    """共享 SQLite 的审批队列。线程安全；多进程并发也安全（SQLite 自带文件锁）。"""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        # WAL 模式：让多进程读写不会互相阻塞太久
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._lock = threading.Lock()

    # ---- 写入侧（Proxy 调用）-----------------------------------------

    def create(
        self,
        tool_name: str,
        args: dict,
        reason: str = "",
        risk_score: float = 0.0,
        triggered: list | None = None,
        session_id: str = "default",
    ) -> str:
        import json

        pid = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                "INSERT INTO pending_decisions "
                "(id, created_at, tool_name, args_json, reason, risk_score, triggered, status, session_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                (
                    pid,
                    time.time(),
                    tool_name,
                    json.dumps(args, ensure_ascii=False, default=str)[:8000],
                    reason,
                    risk_score,
                    json.dumps(triggered or [], ensure_ascii=False),
                    session_id,
                ),
            )
        return pid

    def wait(self, pid: str, timeout: float = 60.0, poll_interval: float = 0.25) -> bool | None:
        """阻塞等审批结果。超时则把状态置为 expired 并返回 None。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            row = self._fetch(pid)
            if row is None:
                return None
            status = row["status"]
            if status == "approved":
                return True
            if status == "denied":
                return False
            if status == "expired":
                return None
            time.sleep(poll_interval)
        # 超时，自己置状态防止 dashboard 还能误点击
        with self._lock:
            self._conn.execute(
                "UPDATE pending_decisions SET status='expired', decided_at=?, decided_by='auto-expire' "
                "WHERE id=? AND status='pending'",
                (time.time(), pid),
            )
        return None

    def make_callback(
        self,
        timeout_seconds: float = 60.0,
        on_request: Callable[[str, dict], None] | None = None,
    ) -> Callable:
        """给 Guard 用的 ask_user_callback 闭包。

        v0.3 起：在创建 pending 之前先查 auto_decisions —— 用户对该 tool
        点过「总是批准 / 拒绝」就立刻决断，不再弹卡片、不发飞书、不响铃。
        """

        def _callback(call, pending) -> bool:
            # 1) 自动决策：跳过 pending 创建 + 等待
            #    import 放在闭包内：让测试 monkey-patch + 用户运行时改 DEFAULT_PATH 都能立刻生效
            from sentinel_mcp.auto_decisions import lookup_decision
            auto = lookup_decision(call.tool_name)
            if auto is not None:
                # 仍记一条 audit 让用户可追溯（通过 create + 立刻 decide 复用现有路径）
                pid = self.create(
                    tool_name=call.tool_name,
                    args=call.args,
                    reason=f"自动决策: {pending.reason}",
                    risk_score=pending.risk_score,
                    triggered=list(pending.triggered_rules or []) + [f"auto:{auto}"],
                    session_id=getattr(call, "session_id", "default"),
                )
                self.decide(pid, approved=(auto == "allow"), by="auto-decision")
                return auto == "allow"

            # 2) 否则走原来的 pending → 等审批 流程
            pid = self.create(
                tool_name=call.tool_name,
                args=call.args,
                reason=pending.reason,
                risk_score=pending.risk_score,
                triggered=list(pending.triggered_rules or []),
                session_id=getattr(call, "session_id", "default"),
            )
            if on_request is not None:
                try:
                    on_request(pid, {
                        "tool_name": call.tool_name,
                        "args": call.args,
                        "reason": pending.reason,
                        "risk_score": pending.risk_score,
                    })
                except Exception:
                    pass
            result = self.wait(pid, timeout=timeout_seconds)
            return result is True  # None（超时）/ False（拒绝）都当拒绝

        return _callback

    # ---- 读取侧 / 决策侧（Dashboard 调用）----------------------------

    def decide(self, pid: str, approved: bool, by: str = "dashboard") -> bool:
        """改 pending 行的状态。返回是否真的改动了一行（False 表示已被处理过）。"""
        new_status = "approved" if approved else "denied"
        with self._lock:
            cur = self._conn.execute(
                "UPDATE pending_decisions SET status=?, decided_at=?, decided_by=? "
                "WHERE id=? AND status='pending'",
                (new_status, time.time(), by, pid),
            )
        return cur.rowcount > 0

    def list_pending(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM pending_decisions WHERE status='pending' "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_recent(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM pending_decisions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_since(self, since_ts: float, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM pending_decisions WHERE created_at > ? OR (decided_at IS NOT NULL AND decided_at > ?) "
                "ORDER BY created_at DESC LIMIT ?",
                (since_ts, since_ts, limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def _fetch(self, pid: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM pending_decisions WHERE id=?", (pid,)
            ).fetchone()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        import json

        d = dict(row)
        try:
            d["triggered"] = json.loads(d.get("triggered") or "[]")
        except Exception:
            d["triggered"] = []
        try:
            d["args"] = json.loads(d.get("args_json") or "{}")
        except Exception:
            d["args"] = {}
        return d
