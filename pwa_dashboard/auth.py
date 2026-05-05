"""dashboard 鉴权：localhost-only 默认 + 公网暴露时自动启 token。

设计理念：
  - **本机自用**：dashboard 默认 bind 127.0.0.1，外人本来就连不上 → 不要密码
  - **被 cloudflared 暴露**：请求 Host header 带 `*.trycloudflare.com` →
    自动开启 token 验证；token 在 ~/.sentinel-mcp/access_token，启动时打印一次
  - **token 验证**：cookie sentinel_token 或 query ?token=xxx 或 header
    X-Sentinel-Token，命中即放行
  - **静态资源 / login 页**：白名单不要鉴权（否则 login 页死锁）

为什么不强制 token：
  90% 用户是本机自用，密码增加摩擦；只有极少数把 dashboard 暴露公网时才需要。
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

TOKEN_PATH = Path(os.environ.get(
    "SENTINEL_TOKEN_FILE",
    str(Path.home() / ".sentinel-mcp" / "access_token"),
))

# 鉴权白名单：static / login / health / 飞书 callback 不要鉴权
_ALLOW_PREFIXES = (
    "/static/",
    "/login",
    "/api/auth/",
    "/manifest.webmanifest",
    "/sw.js",
    "/icon-",
    "/icon.svg",
    "/favicon",
    "/api/health",                # 给监控用
    "/api/lark/callback",         # 飞书 callback POST — 飞书侧不会带我们的 token
    "/api/push/subscribe",        # Web Push 订阅（PWA 跨设备时必需）
    "/api/push/unsubscribe",
    "/api/push/vapid-public-key",
)

# 视为「本机直连」的 host：不强制 token
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")


def ensure_token() -> str:
    """读 token，没有就生成 + chmod 600 + 打印一次到 stdout。"""
    if TOKEN_PATH.exists():
        try:
            return TOKEN_PATH.read_text().strip()
        except OSError:
            pass
    token = secrets.token_urlsafe(24)
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(token)
    try:
        TOKEN_PATH.chmod(0o600)
    except OSError:
        pass
    print(f"\n{'=' * 60}\n"
          f"[sentinel-mcp] 🔑 dashboard access token (公网访问时需要):\n"
          f"  {token}\n"
          f"  (saved to {TOKEN_PATH})\n"
          f"  本机访问 (localhost / 127.0.0.1) 不需要 token\n"
          f"{'=' * 60}\n", flush=True)
    return token


def _is_local_request(request: Request) -> bool:
    """判断请求是否来自本机：Host header 是 localhost/127.0.0.1，且 client IP 是回环地址。"""
    host = (request.headers.get("host") or "").split(":")[0].lower()
    if host not in _LOCAL_HOSTS:
        return False
    client = request.client
    if client is None:
        return True  # 无法判断 → 信任 host header
    ip = client.host or ""
    return ip in ("127.0.0.1", "::1", "localhost")


def _extract_token(request: Request) -> str:
    """从 cookie / query / header 中拿 token。"""
    for src in (
        request.cookies.get("sentinel_token"),
        request.query_params.get("token"),
        request.headers.get("x-sentinel-token"),
    ):
        if src:
            return src.strip()
    return ""


async def auth_middleware(request: Request, call_next: Callable):
    """鉴权 middleware：
      1. 本机请求 → 永远放行
      2. 白名单路径 → 放行
      3. 公网请求 → 必须 token 命中
    """
    path = request.url.path
    if any(path.startswith(p) for p in _ALLOW_PREFIXES):
        return await call_next(request)

    if _is_local_request(request):
        return await call_next(request)

    # 远程请求 — 验 token
    expected = ensure_token()
    given = _extract_token(request)
    if given and secrets.compare_digest(given, expected):
        # 命中 → 让浏览器记 cookie 防下次再传
        response = await call_next(request)
        response.set_cookie(
            "sentinel_token", expected, max_age=3600 * 24 * 30,
            httponly=True, samesite="lax",
        )
        return response

    # 不命中 — API 请求返 401，HTML 请求重定向到 /login
    if path.startswith("/api/"):
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "auth required", "hint": "POST /api/auth/login {token}"}, status_code=401)
    return RedirectResponse(url=f"/login?next={path}", status_code=302)


LOGIN_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>Sentinel-MCP · 登录</title>
<style>
body{font-family:-apple-system,'PingFang SC',sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#f4f4f5;color:#18181b;}
.box{background:#fff;padding:32px;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.06),0 4px 16px rgba(0,0,0,.06);max-width:400px;width:90%;}
h1{font-size:20px;font-weight:600;margin-bottom:6px;display:flex;align-items:center;gap:10px;}
.logo-mark{width:28px;height:28px;border-radius:7px;background:linear-gradient(135deg,#4f46e5,#7c3aed);display:inline-flex;align-items:center;justify-content:center;color:white;}
.sub{color:#71717a;font-size:13px;margin-bottom:20px;}
input{width:100%;padding:10px 12px;font-size:14px;font-family:'DM Mono',monospace;border:1px solid #e4e4e7;border-radius:6px;color:#18181b;}
input:focus{outline:none;border-color:#4f46e5;box-shadow:0 0 0 3px rgba(79,70,229,.1);}
button{margin-top:14px;width:100%;height:38px;background:#4f46e5;color:white;font-size:14px;font-weight:500;border:none;border-radius:6px;cursor:pointer;}
button:hover{background:#4338ca;}
.err{color:#ef4444;font-size:12px;margin-top:10px;}
.hint{color:#71717a;font-size:12px;margin-top:14px;line-height:1.6;}
.hint code{background:#f4f4f5;padding:2px 6px;border-radius:4px;font-family:'DM Mono',monospace;}
</style></head><body>
<form class="box" method="post" action="/api/auth/login">
  <h1><div class="logo-mark">🛡</div>Sentinel-MCP</h1>
  <div class="sub">公网访问需要 access token</div>
  <input name="token" type="password" placeholder="粘贴 access token..." required autofocus>
  <input name="next" type="hidden" value="__NEXT__">
  <button type="submit">登录</button>
  <div class="hint">
    Token 在 dashboard 启动时打印到终端 + 存于<br>
    <code>~/.sentinel-mcp/access_token</code>
  </div>
</form>
</body></html>"""


def render_login(next_url: str = "/") -> HTMLResponse:
    return HTMLResponse(LOGIN_HTML.replace("__NEXT__", next_url or "/"))
