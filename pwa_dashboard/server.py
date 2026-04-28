"""Sentinel-MCP PWA Dashboard

继承 v0.1 dashboard 的所有 API，加上 PWA 三件套（manifest/sw/icons）。

为什么要新写一个 server 而不直接改 v0.1：
  v0.1 在 0425_test01/，用户明确说不要动；PWA 资源（sw.js / manifest）
  必须放在根路径下才能让 Service Worker 拿到 / 范围内的 fetch，
  所以这里另起一个 FastAPI app，把 v0.1 的 AuditLog 直接 import 来用。

跑法：
  source ~/miniconda3/etc/profile.d/conda.sh && conda activate Z-Deep
  cd 0427_test01
  python -m pwa_dashboard.server

  默认端口 8766（避开 v0.1 的 8765）。
  审计 DB 默认指向 v0.1 的 data/audit.db，可用 SENTINEL_DB env 覆盖。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# 把本仓库根加进 sys.path（cwd 可能不在 0427_test01）
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from guard.audit import AuditLog  # noqa: E402
from pwa_dashboard.push import WebPushManager  # noqa: E402
from sentinel_mcp.approvals import PendingDecisions  # noqa: E402

# ---- 路径 -------------------------------------------------------
HERE = Path(__file__).resolve().parent
TEMPLATES_DIR = HERE / "templates"
STATIC_DIR = HERE / "static"

app = FastAPI(title="Sentinel-MCP Dashboard", version="0.2.0-dev")

# 审计 DB：默认 0427_test01/data/sentinel.db（与 sentinel-mcp wrap 共用），
# 环境变量 SENTINEL_DB 覆盖。沿用旧的 0425_test01/data/audit.db 也行，但
# 那是 v0.1 demo 的库，演示 v0.2 实时事件流时会混入历史脏数据。
_DEFAULT_DB = _REPO_ROOT / "data" / "sentinel.db"
DB_PATH = os.environ.get("SENTINEL_DB", str(_DEFAULT_DB))
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
audit = AuditLog(DB_PATH)
approvals = PendingDecisions(DB_PATH)
push = WebPushManager(
    db_path=DB_PATH,
    vapid_path=Path(DB_PATH).parent / "vapid_keys.json",
    vapid_subject=os.environ.get("SENTINEL_VAPID_SUBJECT", "mailto:542058929@qq.com"),
)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# 内部静态资源（如果以后要加）
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ---- PWA 资源必须挂在根路径 ------------------------------------

@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    # Service Worker 必须 application/javascript 才被浏览器接受；
    # 同时 Service-Worker-Allowed: / 让它的 scope 覆盖整站
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@app.get("/icon-192.png", include_in_schema=False)
def icon_192():
    return FileResponse(STATIC_DIR / "icon-192.png", media_type="image/png")


@app.get("/icon-512.png", include_in_schema=False)
def icon_512():
    return FileResponse(STATIC_DIR / "icon-512.png", media_type="image/png")


@app.get("/icon-512-maskable.png", include_in_schema=False)
def icon_512_maskable():
    return FileResponse(STATIC_DIR / "icon-512-maskable.png", media_type="image/png")


@app.get("/icon.svg", include_in_schema=False)
def icon_svg():
    return FileResponse(STATIC_DIR / "icon.svg", media_type="image/svg+xml")


# ---- 主页 -------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    # starlette 1.0+ 把 request 提前到第一个位置参数；旧的 (name, {"request": ...}) 会
    # 把 dict 当 cache key 抛 TypeError。这里用关键字参数兼容老 starlette。
    return templates.TemplateResponse(request=request, name="index.html")


# ---- API（与 v0.1 保持兼容） -----------------------------------

@app.get("/api/health")
def api_health():
    return {"status": "ok", "db": DB_PATH, "version": "0.2.0-dev", "pwa": True}


@app.get("/api/events")
def api_events(
    limit: int = 200,
    decision: str | None = None,
    min_risk: float = 0.0,
    tool_name: str | None = None,
    since_seconds: int = 0,
):
    since = time.time() - since_seconds if since_seconds > 0 else None
    events = audit.query(
        limit=limit,
        decision=decision,
        min_risk=min_risk,
        tool_name=tool_name,
        since=since,
    )
    return {"events": events, "count": len(events)}


@app.get("/api/stats")
def api_stats(since_seconds: int = 0):
    since = time.time() - since_seconds if since_seconds > 0 else 0.0
    return audit.stats(since=since)


@app.post("/api/clear")
def api_clear():
    audit.clear()
    return {"cleared": True}


# ---- 审批 API（ASK_USER 闭环）-------------------------------------

@app.get("/api/approvals")
def api_approvals(only_pending: bool = True, limit: int = 50):
    if only_pending:
        return {"approvals": approvals.list_pending(limit=limit)}
    return {"approvals": approvals.list_recent(limit=limit)}


@app.post("/api/approvals/{pid}/decide")
def api_approvals_decide(pid: str, payload: dict):
    """body: {"approved": true/false, "by": "dashboard|phone"}"""
    if "approved" not in payload:
        raise HTTPException(status_code=400, detail="missing 'approved'")
    by = str(payload.get("by") or "dashboard")
    ok = approvals.decide(pid, approved=bool(payload["approved"]), by=by)
    if not ok:
        # 已处理 / 不存在 / 已超时
        raise HTTPException(status_code=409, detail="approval already settled or not found")
    return {"ok": True, "id": pid, "approved": bool(payload["approved"])}


# ---- Web Push API ---------------------------------------------------

@app.get("/api/push/vapid-public-key")
def api_push_vapid_pubkey():
    return {"key": push.vapid_public_key_b64url}


@app.post("/api/push/subscribe")
def api_push_subscribe(payload: dict, request: Request):
    """body: {endpoint, keys: {p256dh, auth}}"""
    ua = request.headers.get("user-agent", "")
    ok = push.add_subscription(payload, ua=ua)
    if not ok:
        raise HTTPException(status_code=400, detail="invalid subscription payload")
    return {"ok": True, "endpoint": payload.get("endpoint", "")}


@app.post("/api/push/unsubscribe")
def api_push_unsubscribe(payload: dict):
    endpoint = payload.get("endpoint")
    if not endpoint:
        raise HTTPException(status_code=400, detail="missing endpoint")
    return {"removed": push.remove_subscription(endpoint)}


@app.get("/api/push/subscriptions")
def api_push_list():
    return {"subscriptions": push.list_subscriptions()}


@app.post("/api/push/test")
def api_push_test(payload: dict | None = None):
    """主动发一条测试推送，验证全链路。"""
    body = payload or {"title": "Sentinel-MCP", "body": "test push from dashboard"}
    return push.send_to_all(body)


# ---- SSE 实时事件流 -------------------------------------------------
# 为什么不用 WebSocket：dashboard 是单向（server → client）推送，SSE
# 原生在浏览器里有自动重连 + 简单 text/event-stream，比 WS 更省心。
# 多端 Proxy 可以同时往 SQLite 写，dashboard 用 timestamp 游标轮询新行。
# 每个客户端各自维护游标，互不干扰；轮询频率 500ms 足够「实时」体感。

_SSE_POLL_INTERVAL = float(os.environ.get("SENTINEL_SSE_INTERVAL", "0.5"))


async def _sse_event_generator(start_ts: float):
    cursor = start_ts
    pending_seen: set[str] = set()
    decided_seen: set[str] = set()

    # 起手发一次 hello + 最近 50 条历史 + 当前所有 pending，让前端能立刻填满
    backlog = audit.query(limit=50)
    yield _sse_pack("hello", {"db": DB_PATH, "version": "0.2.0-dev"})
    for evt in reversed(backlog):
        yield _sse_pack("event", evt)
        cursor = max(cursor, float(evt.get("timestamp", 0)))
    for p in approvals.list_pending(limit=50):
        yield _sse_pack("pending", p)
        pending_seen.add(p["id"])

    while True:
        await asyncio.sleep(_SSE_POLL_INTERVAL)
        sent_anything = False

        # 1) 新 audit 事件
        new_events = audit.query(limit=200, since=cursor + 1e-6)
        for evt in reversed(new_events):
            yield _sse_pack("event", evt)
            cursor = max(cursor, float(evt.get("timestamp", 0)))
            sent_anything = True

        # 2) 审批队列：差量推送 pending（新出现）+ decided（已被处理）
        current = approvals.list_recent(limit=100)
        for row in current:
            rid = row["id"]
            if row["status"] == "pending" and rid not in pending_seen:
                yield _sse_pack("pending", row)
                pending_seen.add(rid)
                sent_anything = True
                # 触发 Web Push（异步，不阻塞 SSE 流）
                asyncio.get_event_loop().run_in_executor(
                    None, _fire_push_for_pending, row
                )
            elif row["status"] != "pending" and rid not in decided_seen:
                yield _sse_pack("decided", row)
                decided_seen.add(rid)
                pending_seen.discard(rid)
                sent_anything = True

        if not sent_anything:
            yield ": ping\n\n"  # SSE 注释行 = 心跳，防中间代理断


def _sse_pack(event: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def _fire_push_for_pending(pending: dict) -> None:
    """新 pending 出现时给所有订阅推一条 — 锁屏也能弹通知。
    在线程池里跑，因为 pywebpush 是同步阻塞的。"""
    try:
        push.send_to_all({
            "title": "🛡 Sentinel-MCP 审批请求",
            "body": f"工具 {pending.get('tool_name', '?')} · 风险 {pending.get('risk_score', 0):.2f}\n{pending.get('reason') or ''}",
            "tag": f"pending-{pending.get('id', '')}",
            "url": "/",
            "pending_id": pending.get("id"),
        }, ttl=60)
    except Exception:
        pass


@app.get("/api/events/stream")
async def events_stream(request: Request, since_seconds: int = 0):
    start_ts = time.time() - since_seconds if since_seconds > 0 else time.time()

    async def streamer():
        try:
            async for chunk in _sse_event_generator(start_ts):
                if await request.is_disconnected():
                    break
                yield chunk
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        streamer(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 禁 nginx 缓冲
        },
    )


def main():
    import uvicorn

    port = int(os.environ.get("SENTINEL_PORT", "8766"))
    print(f"[sentinel-mcp pwa] db={DB_PATH}  →  http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
