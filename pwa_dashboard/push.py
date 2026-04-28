"""Web Push 管理：VAPID 密钥 + 订阅表 + 直推。

为什么直推（而不是 Cloudflare Workers relay）：
  - Dashboard 是本机服务，浏览器订阅时拿到的 endpoint 直接是 FCM / Mozilla autopush 的 URL
  - 直接从本机 → FCM 完全可行，不需要中继；只是**手机休眠时本机要在线**才能推到
  - 真正长在线的部署再上 Cloudflare relay；当前赛事场景本机即可

订阅生命周期：
  1. 浏览器先调 GET /api/push/vapid-public-key 拿公钥
  2. 用公钥调 ServiceWorkerRegistration.pushManager.subscribe(...)，得到 subscription（含 endpoint/p256dh/auth）
  3. POST /api/push/subscribe 把 subscription 上传，server 存到 SQLite
  4. 当 dashboard SSE 检测到新 pending 时，对所有有效订阅发推
  5. 410/404 表示订阅失效，server 自动删
"""

from __future__ import annotations

import base64
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException, webpush


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


class WebPushManager:
    """VAPID 密钥 + Web Push 订阅。线程安全；多进程时各自有自己的 Connection。"""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS push_subscriptions (
        endpoint    TEXT PRIMARY KEY,
        p256dh      TEXT NOT NULL,
        auth        TEXT NOT NULL,
        ua          TEXT,
        created_at  REAL NOT NULL,
        last_ok_at  REAL,
        last_err_at REAL,
        last_err    TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_push_created ON push_subscriptions(created_at);
    """

    def __init__(self, db_path: str | Path, vapid_path: str | Path, vapid_subject: str = "mailto:542058929@qq.com"):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(self.SCHEMA)
        self._lock = threading.Lock()

        self.vapid_path = Path(vapid_path)
        self.vapid_subject = vapid_subject
        self._vapid_priv_pem, self._vapid_pub_b64 = self._load_or_create_vapid(self.vapid_path)

    # ---- VAPID 密钥 ----------------------------------------------

    def _load_or_create_vapid(self, path: Path) -> tuple[bytes, str]:
        """读已有 VAPID；不存在则现场生成 secp256r1 私钥并 PEM 落盘。"""
        if path.exists():
            data = json.loads(path.read_text())
            return data["private_pem"].encode(), data["public_b64url"]

        path.parent.mkdir(parents=True, exist_ok=True)
        priv = ec.generate_private_key(ec.SECP256R1(), default_backend())
        priv_pem = priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        pub = priv.public_key().public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        )
        pub_b64 = _b64url(pub)
        path.write_text(json.dumps({
            "private_pem": priv_pem.decode("ascii"),
            "public_b64url": pub_b64,
            "created_at": time.time(),
        }))
        try:
            path.chmod(0o600)  # 只有自己能读私钥
        except Exception:
            pass
        return priv_pem, pub_b64

    @property
    def vapid_public_key_b64url(self) -> str:
        return self._vapid_pub_b64

    # ---- 订阅 -----------------------------------------------------

    def add_subscription(self, sub: dict, ua: str = "") -> bool:
        endpoint = sub.get("endpoint")
        keys = sub.get("keys") or {}
        p256dh = keys.get("p256dh")
        auth = keys.get("auth")
        if not (endpoint and p256dh and auth):
            return False
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO push_subscriptions "
                "(endpoint, p256dh, auth, ua, created_at) VALUES (?, ?, ?, ?, ?)",
                (endpoint, p256dh, auth, ua[:200], time.time()),
            )
        return True

    def remove_subscription(self, endpoint: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,)
            )
        return cur.rowcount > 0

    def list_subscriptions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT endpoint, p256dh, auth, ua, created_at, last_ok_at, last_err_at, last_err "
                "FROM push_subscriptions ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- 发推 -----------------------------------------------------

    def send_to_all(self, payload: dict, ttl: int = 60) -> dict:
        """对每个订阅尝试发推。410/404 自动清。"""
        subs = self.list_subscriptions()
        sent = 0
        gone = 0
        errors: list[str] = []
        body = json.dumps(payload, ensure_ascii=False)
        for sub in subs:
            sub_obj = {
                "endpoint": sub["endpoint"],
                "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
            }
            try:
                webpush(
                    subscription_info=sub_obj,
                    data=body,
                    vapid_private_key=self._vapid_priv_pem.decode("ascii"),
                    vapid_claims={"sub": self.vapid_subject},
                    ttl=ttl,
                )
                sent += 1
                with self._lock:
                    self._conn.execute(
                        "UPDATE push_subscriptions SET last_ok_at=? WHERE endpoint=?",
                        (time.time(), sub["endpoint"]),
                    )
            except WebPushException as e:
                code = getattr(e.response, "status_code", 0) if getattr(e, "response", None) else 0
                if code in (404, 410):
                    self.remove_subscription(sub["endpoint"])
                    gone += 1
                else:
                    errors.append(f"{code}: {e!s}"[:200])
                    with self._lock:
                        self._conn.execute(
                            "UPDATE push_subscriptions SET last_err_at=?, last_err=? WHERE endpoint=?",
                            (time.time(), str(e)[:500], sub["endpoint"]),
                        )
            except Exception as e:  # noqa: BLE001
                errors.append(f"unknown: {e!s}"[:200])
        return {"sent": sent, "gone": gone, "total": len(subs), "errors": errors}
