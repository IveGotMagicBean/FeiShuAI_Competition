"""
Sentinel-MCP · Self-hosted backend (FastAPI + SQLite)
=====================================================

替代 Cloudflare Worker，部署到任何 Linux VPS（阿里云/腾讯云/...）。

启动:
    pip install fastapi uvicorn
    python server.py

或者用 uvicorn:
    uvicorn server:app --host 0.0.0.0 --port 8080

API 与 Cloudflare Worker 完全等价：
    POST /api/pair/register     - 桌面端拿配对码
    POST /api/pair/redeem       - 手机端兑换 mobile_token
    POST /api/events/push       - 桌面端 push 事件
    GET  /api/events/list       - 手机端拉事件
    POST /api/approvals/push    - 桌面端 push 审批
    GET  /api/approvals/list    - 手机端拉审批
    POST /api/approvals/decide  - 手机端决策
    GET  /api/decisions/poll    - 桌面端拉决策

静态文件: GET /  →  ../website/index.html  等等

存储:
    SQLite 单文件 ./sentinel.db
    自动建表 + TTL 用 expires_at 列 + 后台清理

Bindings 等价于 Cloudflare KV 的 PAIR_KV，但用 SQLite 表实现。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

# ============================================================
# 常量 / TTL
# ============================================================

TTL_PAIR_CODE = 5 * 60                # 5 min
TTL_EVENT = 60 * 60                   # 1 hour
TTL_APPROVAL = 6 * 60 * 60            # 6 hours
TTL_DECISION = 24 * 60 * 60           # 1 day
TTL_INSTANCE = 30 * 24 * 60 * 60      # 30 days

DB_PATH = Path(os.environ.get("SENTINEL_DB_PATH", "./sentinel.db"))
WEBSITE_DIR = Path(os.environ.get("SENTINEL_WEBSITE_DIR", "../website")).resolve()

# ============================================================
# DB 初始化
# ============================================================

SCHEMA = """
-- 通用 KV 表，模仿 Cloudflare KV，所有 namespace 复用一张表
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    expires_at INTEGER NOT NULL  -- unix epoch seconds (0 = 永不过期)
);
CREATE INDEX IF NOT EXISTS idx_kv_prefix ON kv(key);
CREATE INDEX IF NOT EXISTS idx_kv_expires ON kv(expires_at);
"""

def db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn

def init_db() -> None:
    with db_conn() as c:
        c.executescript(SCHEMA)

# ============================================================
# KV helpers (模仿 Cloudflare KV API)
# ============================================================

def kv_get(key: str) -> str | None:
    now = int(time.time())
    with db_conn() as c:
        row = c.execute(
            "SELECT value FROM kv WHERE key=? AND (expires_at=0 OR expires_at>?)",
            (key, now),
        ).fetchone()
    return row["value"] if row else None

def kv_put(key: str, value: str, ttl: int) -> None:
    expires_at = int(time.time()) + ttl if ttl > 0 else 0
    with db_conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO kv(key, value, expires_at) VALUES(?, ?, ?)",
            (key, value, expires_at),
        )

def kv_delete(key: str) -> None:
    with db_conn() as c:
        c.execute("DELETE FROM kv WHERE key=?", (key,))

def kv_list(prefix: str, start: str | None = None, limit: int = 100) -> list[str]:
    """Return keys (sorted) matching prefix, optionally starting after `start`."""
    now = int(time.time())
    with db_conn() as c:
        if start:
            rows = c.execute(
                "SELECT key FROM kv WHERE key LIKE ? AND key >= ? AND (expires_at=0 OR expires_at>?) ORDER BY key LIMIT ?",
                (prefix + "%", start, now, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT key FROM kv WHERE key LIKE ? AND (expires_at=0 OR expires_at>?) ORDER BY key LIMIT ?",
                (prefix + "%", now, limit),
            ).fetchall()
    return [r["key"] for r in rows]

async def gc_loop():
    """后台每 60s 清掉过期 key"""
    while True:
        await asyncio.sleep(60)
        try:
            now = int(time.time())
            with db_conn() as c:
                c.execute("DELETE FROM kv WHERE expires_at>0 AND expires_at<?", (now,))
        except Exception as e:
            print(f"[gc] error: {e}")

# ============================================================
# Auth helpers
# ============================================================

def auth_admin(instance_id: str | None, admin_token: str | None) -> dict | None:
    if not instance_id or not admin_token:
        return None
    raw = kv_get(f"inst:{instance_id}")
    if not raw:
        return None
    try:
        inst = json.loads(raw)
    except Exception:
        return None
    if inst.get("admin_token") != admin_token:
        return None
    return inst

def auth_mobile(mobile_token: str | None) -> tuple[str, dict] | None:
    if not mobile_token:
        return None
    instance_id = kv_get(f"mtok:{mobile_token}")
    if not instance_id:
        return None
    raw = kv_get(f"inst:{instance_id}")
    if not raw:
        return None
    try:
        inst = json.loads(raw)
    except Exception:
        return None
    if inst.get("mobile_token") != mobile_token:
        return None
    return instance_id, inst

def touch_instance(instance_id: str, inst: dict) -> None:
    inst["last_seen"] = int(time.time() * 1000)
    kv_put(f"inst:{instance_id}", json.dumps(inst), TTL_INSTANCE)

# ============================================================
# Random gen
# ============================================================

PAIR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 去掉易混 0/O/1/I

def new_pair_code() -> str:
    return "".join(secrets.choice(PAIR_ALPHABET) for _ in range(6))

def new_token() -> str:
    return secrets.token_hex(32)

def new_instance_id() -> str:
    return str(uuid.uuid4())

# ============================================================
# FastAPI app + lifespan
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(gc_loop())
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan, title="Sentinel-MCP Self-hosted Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["content-type", "x-admin-token", "x-mobile-token"],
    max_age=86400,
)

def err(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})

# ============================================================
# /api/pair/register
# ============================================================

@app.post("/api/pair/register")
async def pair_register(request: Request):
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    instance_id = body.get("instance_id")
    admin_token = body.get("admin_token")

    if instance_id and admin_token:
        inst = auth_admin(instance_id, admin_token)
        if not inst:
            return err(401, "invalid instance_id or admin_token")
    else:
        instance_id = new_instance_id()
        admin_token = new_token()
        inst = {
            "admin_token": admin_token,
            "mobile_token": None,
            "created_at": int(time.time() * 1000),
            "last_seen": int(time.time() * 1000),
        }
        kv_put(f"inst:{instance_id}", json.dumps(inst), TTL_INSTANCE)

    pair_code = new_pair_code()
    kv_put(f"pair:{pair_code}", instance_id, TTL_PAIR_CODE)
    touch_instance(instance_id, inst)

    return {
        "instance_id": instance_id,
        "admin_token": admin_token,
        "pair_code": pair_code,
        "pair_code_expires_at": int(time.time() * 1000) + TTL_PAIR_CODE * 1000,
    }

# ============================================================
# /api/pair/redeem
# ============================================================

@app.post("/api/pair/redeem")
async def pair_redeem(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    code = str(body.get("code", "")).strip().upper()
    if not re.fullmatch(r"[A-Z2-9]{6}", code):
        return err(400, "invalid code format (expected 6 chars A-Z 2-9)")

    instance_id = kv_get(f"pair:{code}")
    if not instance_id:
        return err(404, "code expired or not found")

    kv_delete(f"pair:{code}")

    raw = kv_get(f"inst:{instance_id}")
    if not raw:
        return err(410, "instance expired")
    inst = json.loads(raw)

    old_mtok = inst.get("mobile_token")
    if old_mtok:
        kv_delete(f"mtok:{old_mtok}")

    mobile_token = new_token()
    inst["mobile_token"] = mobile_token
    inst["last_seen"] = int(time.time() * 1000)

    kv_put(f"inst:{instance_id}", json.dumps(inst), TTL_INSTANCE)
    kv_put(f"mtok:{mobile_token}", instance_id, TTL_INSTANCE)

    return {"instance_id": instance_id, "mobile_token": mobile_token}

# ============================================================
# /api/events/push
# ============================================================

@app.post("/api/events/push")
async def events_push(request: Request, x_admin_token: str = Header(default="")):
    try:
        body = await request.json()
    except Exception:
        return err(400, "invalid JSON body")

    instance_id = body.get("instance_id")
    event = body.get("event")
    if not instance_id or not event:
        return err(400, "missing instance_id or event")

    inst = auth_admin(instance_id, x_admin_token)
    if not inst:
        return err(401, "auth failed")

    ts = int(time.time() * 1000)
    rand4 = secrets.token_hex(2)
    key = f"evt:{instance_id}:{ts:013d}:{rand4}"
    event = {**event, "_ts": ts}
    kv_put(key, json.dumps(event), TTL_EVENT)
    touch_instance(instance_id, inst)

    return {"ok": True, "key": key}

# ============================================================
# /api/events/list
# ============================================================

@app.get("/api/events/list")
async def events_list(request: Request, x_mobile_token: str = Header(default="")):
    auth = auth_mobile(x_mobile_token)
    if not auth:
        return err(401, "auth failed")
    instance_id, _ = auth

    since = int(request.query_params.get("since", "0"))
    limit = min(int(request.query_params.get("limit", "50")), 200)

    prefix = f"evt:{instance_id}:"
    start = f"{prefix}{(since + 1):013d}" if since > 0 else None
    keys = kv_list(prefix, start=start, limit=limit)

    events = []
    next_since = since
    for k in keys:
        val = kv_get(k)
        if not val:
            continue
        try:
            ev = json.loads(val)
            events.append(ev)
            if ev.get("_ts", 0) > next_since:
                next_since = ev["_ts"]
        except Exception:
            pass

    return {"events": events, "count": len(events), "next_since": next_since}

# ============================================================
# /api/approvals/push
# ============================================================

@app.post("/api/approvals/push")
async def approvals_push(request: Request, x_admin_token: str = Header(default="")):
    try:
        body = await request.json()
    except Exception:
        return err(400, "invalid JSON")

    instance_id = body.get("instance_id")
    approval = body.get("approval")
    if not instance_id or not approval or not approval.get("id"):
        return err(400, "missing instance_id or approval (with .id)")

    inst = auth_admin(instance_id, x_admin_token)
    if not inst:
        return err(401, "auth failed")

    stored = {**approval, "_ts": int(time.time() * 1000), "_state": "pending"}
    kv_put(f"apr:{instance_id}:{approval['id']}", json.dumps(stored), TTL_APPROVAL)
    touch_instance(instance_id, inst)

    return {"ok": True, "id": approval["id"]}

# ============================================================
# /api/approvals/list
# ============================================================

@app.get("/api/approvals/list")
async def approvals_list(request: Request, x_mobile_token: str = Header(default="")):
    auth = auth_mobile(x_mobile_token)
    if not auth:
        return err(401, "auth failed")
    instance_id, _ = auth

    only_pending = request.query_params.get("only_pending", "1") != "0"

    prefix = f"apr:{instance_id}:"
    keys = kv_list(prefix, limit=100)

    approvals = []
    for k in keys:
        val = kv_get(k)
        if not val:
            continue
        try:
            a = json.loads(val)
            if only_pending and a.get("_state") != "pending":
                continue
            approvals.append(a)
        except Exception:
            pass

    approvals.sort(key=lambda x: x.get("_ts", 0), reverse=True)
    return {"approvals": approvals, "count": len(approvals)}

# ============================================================
# /api/approvals/decide
# ============================================================

@app.post("/api/approvals/decide")
async def approvals_decide(request: Request, x_mobile_token: str = Header(default="")):
    auth = auth_mobile(x_mobile_token)
    if not auth:
        return err(401, "auth failed")
    instance_id, _ = auth

    try:
        body = await request.json()
    except Exception:
        return err(400, "invalid JSON")

    apr_id = body.get("id")
    approved = body.get("approved")
    if not apr_id:
        return err(400, "missing id")
    if not isinstance(approved, bool):
        return err(400, "missing 'approved' boolean")

    apr_key = f"apr:{instance_id}:{apr_id}"
    raw = kv_get(apr_key)
    if not raw:
        return err(404, "approval not found or expired")

    apr = json.loads(raw)
    if apr.get("_state") and apr["_state"] != "pending":
        return err(409, f"already {apr['_state']}")

    apr["_state"] = "approved" if approved else "denied"
    apr["_decided_at"] = int(time.time() * 1000)
    apr["_decided_by"] = str(body.get("by", "phone"))
    kv_put(apr_key, json.dumps(apr), TTL_APPROVAL)

    decision = {"id": apr_id, "approved": approved, "by": apr["_decided_by"], "ts": apr["_decided_at"]}
    kv_put(f"dec:{instance_id}:{apr_id}", json.dumps(decision), TTL_DECISION)

    return {"ok": True, "id": apr_id, "approved": approved}

# ============================================================
# /api/decisions/poll
# ============================================================

@app.get("/api/decisions/poll")
async def decisions_poll(request: Request, x_admin_token: str = Header(default="")):
    instance_id = request.query_params.get("instance_id", "")
    consume = request.query_params.get("consume", "1") != "0"

    inst = auth_admin(instance_id, x_admin_token)
    if not inst:
        return err(401, "auth failed")

    prefix = f"dec:{instance_id}:"
    keys = kv_list(prefix, limit=100)
    decisions = []
    for k in keys:
        val = kv_get(k)
        if not val:
            continue
        try:
            decisions.append(json.loads(val))
            if consume:
                kv_delete(k)
        except Exception:
            pass
    touch_instance(instance_id, inst)

    return {"decisions": decisions, "count": len(decisions)}

# ============================================================
# Health check
# ============================================================

@app.get("/api/health")
async def health():
    return {"ok": True, "ts": int(time.time())}

# ============================================================
# 静态文件 (website/)
# ============================================================
# 必须放在所有 @app.get/post 之后，否则会拦截 API 路由

if WEBSITE_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEBSITE_DIR), html=True), name="static")
else:
    print(f"[warn] WEBSITE_DIR not found: {WEBSITE_DIR}")

# ============================================================
# 直接 python server.py 启动
# ============================================================

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("SENTINEL_PORT", "8080"))
    host = os.environ.get("SENTINEL_HOST", "0.0.0.0")
    print(f"[boot] Sentinel-MCP backend on {host}:{port}")
    print(f"[boot] DB: {DB_PATH.resolve()}")
    print(f"[boot] WEBSITE: {WEBSITE_DIR}")
    uvicorn.run(app, host=host, port=port, log_level="info")
