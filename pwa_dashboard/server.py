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


# ---- 一键集成 Cursor / Claude Desktop -------------------------------
# 让 dashboard 自动改这俩客户端的 mcp config，**用户不用手改 JSON**。
# 核心逻辑全在 pwa_dashboard.integrations，这里只暴露 HTTP 接口。

from pwa_dashboard import integrations  # noqa: E402  (放尾部避免循环)


@app.get("/api/integrations/detect")
def api_integrations_detect():
    """扫本机 Claude Desktop / Cursor 的 config 文件，告诉前端哪些能装。"""
    return integrations.detect_all()


@app.get("/api/integrations/presets")
def api_integrations_presets():
    """常见上游 MCP server 预设清单（filesystem / github / brave_search / puppeteer）。"""
    return {"presets": integrations.PRESETS}


@app.post("/api/integrations/preview")
def api_integrations_preview(payload: dict):
    """干跑：返回最终会写入的 entry，前端用来给用户预览。"""
    try:
        return integrations.preview(
            client_key=payload["client"],
            server_name=payload["server_name"],
            upstream_command=payload["upstream_command"],
            upstream_args=payload.get("upstream_args") or [],
            upstream_env=payload.get("upstream_env") or None,
        )
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/integrations/install")
def api_integrations_install(payload: dict):
    """实际写入 client config（备份原文件 + 原子 replace）。"""
    try:
        return integrations.install(
            client_key=payload["client"],
            server_name=payload["server_name"],
            upstream_command=payload["upstream_command"],
            upstream_args=payload.get("upstream_args") or [],
            upstream_env=payload.get("upstream_env") or None,
            overwrite=bool(payload.get("overwrite", False)),
        )
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---- Discovery：全机扫描 + 一键截关 -------------------------------------
# 「商业化成熟产品」的核心入口：dashboard 第一屏 = 你机器上 N 个 MCP 客户端
# 共有 M 个 mcpServers，K 个已被 Sentinel 保护。点「全部包装」一秒接管。
# 实现都在 pwa_dashboard.discovery（adapter 模式，扩 client 加 adapter 即可）。

from pwa_dashboard import discovery  # noqa: E402


@app.get("/api/discovery/scan")
def api_discovery_scan():
    """扫所有已注册 client，返回每个 client 的安装 / server 数 + 全机扁平 server 列表。"""
    return discovery.scan_all()


@app.post("/api/discovery/wrap")
def api_discovery_wrap(payload: dict):
    """批量 wrap。body: {selections: [{client_key, config_path, scope, server_name}, ...]}"""
    sels = payload.get("selections") or []
    if not isinstance(sels, list):
        raise HTTPException(status_code=400, detail="selections 必须是 list")
    results = discovery.wrap_servers(sels)
    return {
        "results": [
            {
                "client_key": r.client_key, "config_path": r.config_path,
                "scope": r.scope, "server_name": r.server_name,
                "ok": r.ok, "action": r.action,
                "backup_path": r.backup_path, "error": r.error,
            }
            for r in results
        ]
    }


@app.post("/api/discovery/unwrap")
def api_discovery_unwrap(payload: dict):
    """批量 unwrap：把 wrap 过的还原成裸 upstream entry。"""
    sels = payload.get("selections") or []
    if not isinstance(sels, list):
        raise HTTPException(status_code=400, detail="selections 必须是 list")
    results = discovery.unwrap_servers(sels)
    return {
        "results": [
            {
                "client_key": r.client_key, "config_path": r.config_path,
                "scope": r.scope, "server_name": r.server_name,
                "ok": r.ok, "action": r.action,
                "backup_path": r.backup_path, "error": r.error,
            }
            for r in results
        ]
    }


@app.post("/api/discovery/restore")
def api_discovery_restore(payload: dict):
    """从 .sentinel-backup.<ts> 文件还原原 config。"""
    backup_path = (payload or {}).get("backup_path") or ""
    if not backup_path:
        raise HTTPException(status_code=400, detail="backup_path 必填")
    try:
        return discovery.restore_backup(backup_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---- 运行时模式（active / passive / off） -----------------------------
# 让用户可在 dashboard 一键切换 Sentinel 行为，无需重启。
# 实际由 ~/.sentinel-mcp/mode.json 持久化，proxy 每次工具调用读一次（μs 级）。

from sentinel_mcp import runtime_mode  # noqa: E402


@app.get("/api/mode")
def api_mode_get():
    """当前运行时模式。"""
    return {"mode": runtime_mode.read_mode(), "config_path": str(runtime_mode.DEFAULT_PATH)}


@app.post("/api/mode")
def api_mode_set(payload: dict):
    """切换运行时模式。body: {"mode": "active" | "passive" | "off"}"""
    mode = (payload or {}).get("mode", "")
    try:
        path = runtime_mode.write_mode(mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "mode": mode, "saved_to": str(path)}


# ---- 自动决策规则（「总是批准 / 总是拒绝 这个工具」）-------------------

from sentinel_mcp import auto_decisions  # noqa: E402


@app.get("/api/auto_decisions")
def api_auto_decisions_list():
    return {"rules": auto_decisions.list_rules()}


@app.post("/api/auto_decisions")
def api_auto_decisions_add(payload: dict):
    """body: {"tool_name": "write_file", "decision": "allow"|"deny"}"""
    try:
        rule = auto_decisions.add_rule(
            tool_name=(payload or {}).get("tool_name", ""),
            decision=(payload or {}).get("decision", ""),
            by=(payload or {}).get("by", "dashboard"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "rule": rule}


@app.delete("/api/auto_decisions/{tool_name}")
def api_auto_decisions_delete(tool_name: str):
    removed = auto_decisions.delete_rule(tool_name)
    return {"ok": True, "removed": removed}


# ---- bootstrap：装 ~/.local/bin/sentinel-mcp shim --------------------
# 让 wrap 配置里的 `sentinel-mcp` 命令在 AppImage / 任意安装方式下都能被找到。
# 模块本身幂等，每次 dashboard 启动都自动跑一次。

from pwa_dashboard import bootstrap  # noqa: E402

try:
    _bootstrap_result = bootstrap.ensure_shim(log=lambda m: print(m))
except Exception as _e:
    print(f"[bootstrap] failed: {_e}")
    _bootstrap_result = {"action": "error", "error": str(_e)}


@app.get("/api/bootstrap/status")
def api_bootstrap_status():
    """返回 shim 装在哪 / PATH 是否包含 / 是否需要用户手动加 PATH。"""
    return _bootstrap_result


@app.post("/api/bootstrap/install")
def api_bootstrap_install():
    """手动重装 shim（force=True）。供 dashboard '修复' 按钮用。"""
    global _bootstrap_result
    _bootstrap_result = bootstrap.ensure_shim(force=True, log=lambda m: print(m))
    return _bootstrap_result


# ---- FS-watch：新 mcpServer 出现立刻告警 -----------------------------
# 用户在 Cursor / Cline 里手动加了一个新 server → Sentinel 1 秒内发现 →
# 一条 SSE event "watch" 推过去 + Web Push「检测到新增未保护 server」。

from pwa_dashboard.watcher import DiscoveryWatcher  # noqa: E402

# 新 server 事件队列（SSE 流轮询时消费）
_watch_events: list[dict] = []
_watch_events_lock = __import__("threading").Lock()


def _on_watcher_change(evt: dict) -> None:
    """watcher 触发的回调。在 watcher 自己线程跑。"""
    diff = evt.get("diff") or {}
    unprotected = diff.get("unprotected_added") or []
    added = diff.get("added") or []
    if not (added or diff.get("removed")):
        return
    # 1. 入队让 SSE 推
    with _watch_events_lock:
        _watch_events.append({"ts": time.time(), **evt})
        # 只保留最近 50 条
        if len(_watch_events) > 50:
            del _watch_events[: len(_watch_events) - 50]
    # 2. 新增未保护 server → 立刻 Web Push（即使 dashboard 没开）
    if unprotected:
        try:
            names = ", ".join(s["server_name"] for s in unprotected[:3])
            extra = "" if len(unprotected) <= 3 else f" 等 {len(unprotected)} 个"
            push.send_to_all({
                "title": "🛡 Sentinel-MCP 检测到新 MCP server",
                "body": f"未保护的 server: {names}{extra}\n点开 dashboard 一键包装。",
                "tag": "watcher-unprotected",
                "url": "/",
            }, ttl=300)
        except Exception as e:
            print(f"[watcher] push failed: {e}")


_watcher = DiscoveryWatcher(_on_watcher_change, rescan_interval=30.0)
try:
    _watcher.start()
except Exception as _e:
    print(f"[watcher] start failed: {_e}")


@app.get("/api/watcher/events")
def api_watcher_events(since: float = 0.0, limit: int = 20):
    """轮询接口（SSE 自动消费就不用前端单独调）。"""
    with _watch_events_lock:
        evts = [e for e in _watch_events if e.get("ts", 0) > since]
    return {"events": evts[-limit:]}


# ---- Slack / Discord / 自定义 webhook 推送通道 -----------------------
# 跟飞书是不同维度：飞书有 callback 能审批；webhook 是单向通知，给海外团队用。

from sentinel_mcp import webhooks  # noqa: E402


@app.get("/api/webhooks")
def api_webhooks_list():
    return {"endpoints": webhooks.list_endpoints()}


@app.post("/api/webhooks")
def api_webhooks_add(payload: dict):
    """body: {name, kind: 'slack'|'discord'|'custom', url, enabled?: true}"""
    try:
        ep = webhooks.add_endpoint(
            name=(payload or {}).get("name", ""),
            kind=(payload or {}).get("kind", ""),
            url=(payload or {}).get("url", ""),
            enabled=bool((payload or {}).get("enabled", True)),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "endpoint": ep}


@app.delete("/api/webhooks/{name}")
def api_webhooks_delete(name: str):
    return {"ok": True, "removed": webhooks.delete_endpoint(name)}


@app.post("/api/webhooks/test")
def api_webhooks_test(payload: dict | None = None):
    """对所有 enabled endpoint 发一条测试。"""
    p = payload or {}
    results = webhooks.send_all(
        title=p.get("title") or "🛡 Sentinel-MCP 测试通知",
        body=p.get("body") or "这是来自 Sentinel-MCP dashboard 的测试推送。",
    )
    return {"results": results}


# ---- Claude Code PreToolUse Hook 安装 / 卸载 -------------------------
# 装上之后 Claude Code 调内置 Bash/Write/Edit 等工具也会被 Sentinel 拦
# （MCP wrap 只覆盖第三方 mcpServers，拦不到 Claude Code 自带工具）。

from pwa_dashboard import hooks_installer  # noqa: E402


@app.get("/api/hooks/status")
def api_hooks_status():
    """返回 Claude Code hook 当前状态 + 所有 client 的 hook/wrap 接入策略表。"""
    return {
        "claude_code": hooks_installer.status(),
        "clients": hooks_installer.list_supported_clients(),
    }


@app.post("/api/hooks/install")
def api_hooks_install(payload: dict | None = None):
    matcher = (payload or {}).get("matcher") or hooks_installer.DEFAULT_MATCHER
    try:
        return hooks_installer.install(matcher=matcher)
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/hooks/uninstall")
def api_hooks_uninstall():
    try:
        return hooks_installer.uninstall()
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---- 拦截强度（5 档预设 + 自定义） ----------------------------------

from sentinel_mcp import strength as _strength  # noqa: E402


@app.get("/api/strength")
def api_strength_get():
    return _strength.get_state()


@app.post("/api/strength/level")
def api_strength_set_level(payload: dict):
    lvl = (payload or {}).get("level", "")
    try:
        path = _strength.write_level(lvl)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "level": lvl, "saved_to": str(path)}


@app.post("/api/strength/custom_override")
def api_strength_custom_override(payload: dict):
    """body: {"key": "detectors.dlp.enabled", "value": false}"""
    try:
        _strength.set_custom_override(
            key=(payload or {}).get("key", ""),
            value=(payload or {}).get("value"),
        )
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@app.post("/api/strength/tool_allowlist")
def api_strength_tool_allowlist(payload: dict):
    tools = (payload or {}).get("tools") or []
    if not isinstance(tools, list):
        raise HTTPException(status_code=400, detail="tools 必须是 list[str]")
    _strength.set_tool_allowlist(tools)
    return {"ok": True, "tools": tools}


@app.post("/api/strength/tool_denylist")
def api_strength_tool_denylist(payload: dict):
    tools = (payload or {}).get("tools") or []
    if not isinstance(tools, list):
        raise HTTPException(status_code=400, detail="tools 必须是 list[str]")
    _strength.set_tool_denylist(tools)
    return {"ok": True, "tools": tools}


def _fire_webhooks_for_pending(pending: dict) -> None:
    """新 pending 出现 → 给所有 webhook endpoints 推一条通知（不阻塞 SSE）。"""
    try:
        title = f"🛡 Sentinel-MCP · {pending.get('tool_name', '?')} 等审批"
        body = (
            f"风险 {pending.get('risk_score', 0):.2f}\n"
            f"原因：{pending.get('reason') or '(none)'}\n"
            f"打开 dashboard 决断 → http://localhost:8766/"
        )
        webhooks.send_all(title, body)
    except Exception as e:
        print(f"[webhooks] send failed: {e}")


# ---- 飞书审批通道 --------------------------------------------------
# 把 ASK_USER 待审批推到飞书消息卡片，用户点[批准]/[拒绝]按钮回调到这里。
# 跟 Web Push / SSE 并列做第三个推送 sink，互不替代。

from sentinel_mcp import lark_notifier as larkn  # noqa: E402

# 模块级状态：notifier 实例 + pending_id -> 飞书 message_id 映射
_lark_state: dict[str, Any] = {
    "notifier": None,           # type: larkn.LarkNotifier | None
    "pending_msg_ids": {},      # pid -> 飞书 message_id（用于决策后 patch 卡片）
}


def _refresh_lark_notifier() -> larkn.LarkNotifier | None:
    """从磁盘 config 重建 notifier；config 缺失或 SDK 没装时返回 None。"""
    cfg = larkn.load_config()
    if cfg is None:
        _lark_state["notifier"] = None
        return None
    try:
        _lark_state["notifier"] = larkn.LarkNotifier(cfg)
    except larkn.LarkNotifierUnavailable:
        _lark_state["notifier"] = None
    return _lark_state["notifier"]


# 启动时尝试加载一次
_refresh_lark_notifier()


@app.get("/api/lark/status")
def api_lark_status():
    """返回飞书集成状态：SDK 是否装、config 是否存在、target 是否配。"""
    cfg = larkn.load_config()
    return {
        "sdk_available": larkn._LARK_AVAILABLE,
        "config_exists": cfg is not None,
        "app_id": cfg.app_id if cfg else "",
        "target_chat_id": cfg.target_chat_id if cfg else "",
        "has_secret": bool(cfg.app_secret) if cfg else False,
        "has_encrypt_key": bool(cfg.encrypt_key) if cfg else False,
        "notifier_active": _lark_state["notifier"] is not None,
        "config_path": str(larkn.DEFAULT_CONFIG_PATH),
    }


@app.post("/api/lark/config")
def api_lark_config_set(payload: dict):
    """保存 App ID / Secret / target 到 ~/.sentinel-mcp/lark_config.json。"""
    if not payload.get("app_id") or not payload.get("app_secret"):
        raise HTTPException(status_code=400, detail="app_id / app_secret 必填")
    cfg = larkn.LarkConfig(
        app_id=payload["app_id"].strip(),
        app_secret=payload["app_secret"].strip(),
        target_chat_id=(payload.get("target_chat_id") or "").strip(),
        encrypt_key=(payload.get("encrypt_key") or "").strip(),
        verification_token=(payload.get("verification_token") or "").strip(),
    )
    path = larkn.save_config(cfg)
    _refresh_lark_notifier()
    return {"ok": True, "saved_to": str(path)}


@app.post("/api/lark/test")
def api_lark_test(payload: dict | None = None):
    """主动发一条测试消息验证 App ID/Secret + target_chat_id 都对。"""
    notifier = _lark_state["notifier"] or _refresh_lark_notifier()
    if notifier is None:
        raise HTTPException(status_code=400, detail="飞书集成未启用：先保存 config")
    text = (payload or {}).get("text") or "Sentinel-MCP 飞书集成测试 ✓"
    try:
        msg_id = notifier.send_test(text)
        return {"ok": True, "message_id": msg_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/lark/callback")
async def api_lark_callback(request: Request):
    """飞书事件订阅 webhook。处理：
       1) URL verification 握手
       2) card.action.trigger（按钮点击）→ 调 approvals.decide 完成审批
    """
    body = await request.body()
    cfg = larkn.load_config() or larkn.LarkConfig("", "")

    # 0) 解析 JSON
    try:
        outer = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid json")

    # 1) 如果 Encrypt Key 配了，飞书会把所有 payload（含 url_verification 握手）AES 加密
    try:
        payload = larkn.maybe_decrypt(outer, cfg)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 2) URL 验证握手（解密后才能拿到 challenge / token）
    try:
        challenge = larkn.verify_url_challenge(payload, cfg)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    if challenge is not None:
        return challenge

    # 3) 签名验证（仅对非握手事件）
    if cfg.encrypt_key:
        sig = request.headers.get("x-lark-signature", "")
        ts = request.headers.get("x-lark-request-timestamp", "")
        nonce = request.headers.get("x-lark-request-nonce", "")
        if sig and not larkn.verify_signature(ts, nonce, body, sig, cfg):
            raise HTTPException(status_code=401, detail="invalid signature")

    # 4) 卡片按钮点击 → 决断
    parsed = larkn.parse_card_action(payload)
    if parsed is None:
        # 其它事件类型暂不处理，但要 200 回 — 否则飞书会重试
        return {"ignored": True, "reason": "unsupported event"}

    pid, action, by, _raw = parsed
    approved = (action == "approve")

    ok = approvals.decide(pid, approved=approved, by=f"lark:{by}")
    if not ok:
        # 已被其它通道（dashboard / phone）决断或超时 — 仍 200，避免飞书重试
        return {"ok": False, "reason": "already settled or not found", "pid": pid}

    # 4) 更新原卡片为 "已批准 / 已拒绝" 终态
    notifier = _lark_state["notifier"]
    msg_id = _lark_state["pending_msg_ids"].pop(pid, None)
    if notifier and msg_id:
        # 拼一个最小 pending dict 供 build_decided_card 用
        # （可以从 approvals 表查更全的，这里偷懒只拿 tool_name）
        recents = approvals.list_recent(limit=10)
        ctx = next((r for r in recents if r["id"] == pid), {"id": pid, "tool_name": "?"})
        notifier.patch_to_decided(msg_id, ctx, approved, by)

    return {"ok": True, "pid": pid, "decision": "approve" if approved else "deny"}


def _fire_lark_for_pending(pending: dict[str, Any]) -> None:
    """新 pending 出现时给飞书推一张卡片；记 message_id 留着决策后 patch。"""
    notifier = _lark_state["notifier"]
    if notifier is None or not notifier.cfg.target_chat_id:
        return
    try:
        msg_id = notifier.send_pending(pending)
        _lark_state["pending_msg_ids"][pending["id"]] = msg_id
    except Exception as e:
        # 飞书发送失败不影响其它 sink（dashboard / Web Push 仍正常）
        print(f"[lark] send_pending failed: {e}")


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
    watch_cursor = start_ts  # FS-watch 事件游标

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
                # 触发 Web Push + 飞书 + Slack/Discord webhook（异步，不阻塞 SSE 流）
                loop = asyncio.get_event_loop()
                loop.run_in_executor(None, _fire_push_for_pending, row)
                loop.run_in_executor(None, _fire_lark_for_pending, row)
                loop.run_in_executor(None, _fire_webhooks_for_pending, row)
            elif row["status"] != "pending" and rid not in decided_seen:
                yield _sse_pack("decided", row)
                decided_seen.add(rid)
                pending_seen.discard(rid)
                sent_anything = True

        # 3) FS-watch 新 server 事件
        with _watch_events_lock:
            new_watch = [e for e in _watch_events if e.get("ts", 0) > watch_cursor]
        for evt in new_watch:
            yield _sse_pack("watch", evt)
            watch_cursor = max(watch_cursor, float(evt.get("ts", 0)))
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


# ---- Phase 2 / 3 新端点：cloudflared / lark chat 列表 / digest / auth ----

from pwa_dashboard import cloudflared as _cf  # noqa: E402
from pwa_dashboard import auth as _auth  # noqa: E402
from pwa_dashboard import lark_digest as _digest  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402
from fastapi.responses import Response, HTMLResponse  # noqa: E402


# 鉴权 middleware（本机不挡，公网走 token）
class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        return await _auth.auth_middleware(request, call_next)


app.add_middleware(AuthMiddleware)


@app.get("/login", include_in_schema=False)
def login_page(next: str = "/"):
    return _auth.render_login(next)


@app.post("/api/auth/login", include_in_schema=False)
async def auth_login(request: Request):
    """form-urlencoded 登录端点。命中 token → 写 cookie + 重定向。"""
    from fastapi.responses import RedirectResponse
    form = await request.form()
    given = (form.get("token") or "").strip()
    next_url = form.get("next") or "/"
    expected = _auth.ensure_token()
    import secrets as _s
    if given and _s.compare_digest(given, expected):
        resp = RedirectResponse(url=next_url, status_code=302)
        resp.set_cookie("sentinel_token", expected, max_age=3600 * 24 * 30, httponly=True, samesite="lax")
        return resp
    return _auth.render_login(next_url)


@app.post("/api/auth/logout", include_in_schema=False)
def auth_logout():
    resp = Response(status_code=204)
    resp.delete_cookie("sentinel_token")
    return resp


# ---- cloudflared tunnel ----

@app.get("/api/cloudflared/status")
def api_cf_status():
    return _cf.manager.status()


@app.post("/api/cloudflared/start")
def api_cf_start(payload: dict | None = None):
    port = int((payload or {}).get("port") or os.environ.get("SENTINEL_PORT", "8766"))
    return _cf.manager.start(port=port)


@app.post("/api/cloudflared/stop")
def api_cf_stop():
    return _cf.manager.stop()


# ---- 飞书 chat 自动列出 ----

@app.get("/api/lark/chats")
def api_lark_chats():
    """返回机器人当前所在的所有 chats，给 dashboard 下拉用。
    需要先配 App ID/Secret 且应用发布过。"""
    notifier = _lark_state["notifier"] or _refresh_lark_notifier()
    if notifier is None:
        return {"chats": [], "error": "lark_not_configured"}
    try:
        return {"chats": notifier.list_chats()}
    except Exception as e:
        return {"chats": [], "error": str(e)}


# ---- 飞书每日摘要 ----

_digest_scheduler = _digest.DigestScheduler(
    audit_db_path=DB_PATH,
    notifier_factory=lambda: _lark_state["notifier"] or _refresh_lark_notifier(),
)
try:
    _digest_scheduler.start()
except Exception as _e:
    print(f"[digest] start failed: {_e}")


@app.get("/api/lark/digest/status")
def api_digest_status():
    return _digest_scheduler.status()


@app.post("/api/lark/digest/push_now")
def api_digest_push_now():
    return _digest_scheduler.push_now()


@app.post("/api/lark/digest/schedule")
def api_digest_schedule(payload: dict):
    """改推送时间。body: {hour: 21, minute: 0}"""
    h = int((payload or {}).get("hour", 21))
    m = int((payload or {}).get("minute", 0))
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise HTTPException(status_code=400, detail="hour 0-23 / minute 0-59")
    _digest_scheduler.target_hour = h
    _digest_scheduler.target_minute = m
    return {"ok": True, "target_hour": h, "target_minute": m}


def main():
    import uvicorn

    port = int(os.environ.get("SENTINEL_PORT", "8766"))
    # 默认 bind 127.0.0.1（本机自用 = 安全），SENTINEL_BIND_HOST 可覆盖
    host = os.environ.get("SENTINEL_BIND_HOST", "127.0.0.1")
    print(f"[sentinel-mcp pwa] db={DB_PATH}  →  http://{host}:{port}")
    if host not in ("127.0.0.1", "localhost", "::1"):
        # 公网暴露 → 提前把 token 准备好让用户看到
        _auth.ensure_token()
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
