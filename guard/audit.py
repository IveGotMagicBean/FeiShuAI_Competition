"""审计日志：SQLite 存储 + JSONL 导出"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from guard.core import GuardResult


class AuditLog:
    """线程安全的本地审计日志"""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS audit_log (
        id TEXT PRIMARY KEY,
        call_id TEXT,
        timestamp REAL NOT NULL,
        event_type TEXT NOT NULL,
        tool_name TEXT,
        args_json TEXT,
        decision TEXT,
        reason TEXT,
        risk_score REAL,
        triggered_rules TEXT,
        session_id TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(timestamp);
    CREATE INDEX IF NOT EXISTS idx_audit_decision ON audit_log(decision);
    CREATE INDEX IF NOT EXISTS idx_audit_call ON audit_log(call_id);
    """

    def __init__(self, db_path: str = "data/audit.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(self.SCHEMA)
        self._conn.commit()

    def log_event(
        self,
        event_type: str,
        tool_name: str,
        args: dict,
        result: GuardResult,
        call_id: str | None = None,
        session_id: str = "default",
    ) -> str:
        eid = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit_log (id, call_id, timestamp, event_type, tool_name, args_json, decision, reason, risk_score, triggered_rules, session_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    eid,
                    call_id,
                    time.time(),
                    event_type,
                    tool_name,
                    json.dumps(args, ensure_ascii=False, default=str)[:8000],
                    result.decision.value,
                    result.reason,
                    result.risk_score,
                    json.dumps(result.triggered_rules, ensure_ascii=False),
                    session_id,
                ),
            )
            self._conn.commit()
        return eid

    def query(
        self,
        limit: int = 100,
        decision: str | None = None,
        min_risk: float = 0.0,
        tool_name: str | None = None,
        since: float | None = None,
    ) -> list[dict]:
        sql = "SELECT * FROM audit_log WHERE risk_score >= ?"
        params: list = [min_risk]
        if decision:
            sql += " AND decision = ?"
            params.append(decision)
        if tool_name:
            sql += " AND tool_name = ?"
            params.append(tool_name)
        if since:
            sql += " AND timestamp >= ?"
            params.append(since)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def stats(self, since: float = 0.0) -> dict:
        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE timestamp >= ?", (since,)
            ).fetchone()[0]
            by_dec = self._conn.execute(
                "SELECT decision, COUNT(*) FROM audit_log WHERE timestamp >= ? GROUP BY decision",
                (since,),
            ).fetchall()
            by_tool = self._conn.execute(
                "SELECT tool_name, COUNT(*) AS c FROM audit_log WHERE timestamp >= ? GROUP BY tool_name ORDER BY c DESC LIMIT 10",
                (since,),
            ).fetchall()
            high_risk = self._conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE timestamp >= ? AND risk_score >= 0.7",
                (since,),
            ).fetchone()[0]
        return {
            "total": total,
            "high_risk_alerts": high_risk,
            "by_decision": {row[0]: row[1] for row in by_dec},
            "by_tool": [{"tool_name": r[0], "count": r[1]} for r in by_tool],
        }

    def export_jsonl(self, out_path: str) -> int:
        rows = self.query(limit=100000)
        with open(out_path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return len(rows)

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM audit_log")
            self._conn.commit()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        try:
            d["triggered_rules"] = json.loads(d.get("triggered_rules") or "[]")
        except Exception:
            d["triggered_rules"] = []
        try:
            d["args"] = json.loads(d.get("args_json") or "{}")
        except Exception:
            d["args"] = {}
        return d
