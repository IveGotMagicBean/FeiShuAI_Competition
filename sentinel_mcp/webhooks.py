"""通用 webhook 推送：Slack / Discord / 自定义 URL。

跟飞书审批通道是不同维度的事：
  - 飞书：消息 + 卡片按钮 = 远程审批通道（用户在飞书里点批准/拒绝）
  - webhook（Slack / Discord / 自定义）：单向通知 = 只通知，不接收回调
    适合海外团队或不想搭飞书的用户。

存储：~/.sentinel-mcp/webhooks.json
格式（简单）：
  {
    "endpoints": [
      {"name": "slack-team", "kind": "slack", "url": "https://hooks.slack.com/...", "enabled": true},
      {"name": "discord-ops", "kind": "discord", "url": "https://discord.com/api/webhooks/...", "enabled": true}
    ]
  }

发消息：每条 ASK_USER 出现时遍历 endpoints，按各自 kind 的 schema POST。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Literal

WebhookKind = Literal["slack", "discord", "custom"]

DEFAULT_PATH = Path(os.environ.get(
    "SENTINEL_WEBHOOKS_FILE",
    str(Path.home() / ".sentinel-mcp" / "webhooks.json"),
))


def _resolve_path(path: Path | None) -> Path:
    return path if path is not None else DEFAULT_PATH


def _read_file(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    eps = data.get("endpoints") or []
    return eps if isinstance(eps, list) else []


def _write_file(endpoints: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump({"endpoints": endpoints}, f, indent=2)
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def list_endpoints(path: Path | None = None) -> list[dict]:
    return _read_file(_resolve_path(path))


def add_endpoint(
    name: str, kind: WebhookKind, url: str,
    enabled: bool = True, path: Path | None = None,
) -> dict:
    if not name:
        raise ValueError("name 不能空")
    if kind not in ("slack", "discord", "custom"):
        raise ValueError(f"kind 必须是 slack/discord/custom，收到: {kind}")
    if not url.startswith(("http://", "https://")):
        raise ValueError("url 必须是 http(s) URL")
    p = _resolve_path(path)
    eps = [e for e in _read_file(p) if e.get("name") != name]  # 同名覆盖
    new = {"name": name, "kind": kind, "url": url, "enabled": enabled,
           "created_at": time.time()}
    eps.append(new)
    _write_file(eps, p)
    return new


def delete_endpoint(name: str, path: Path | None = None) -> bool:
    p = _resolve_path(path)
    eps = _read_file(p)
    new = [e for e in eps if e.get("name") != name]
    if len(new) == len(eps):
        return False
    _write_file(new, p)
    return True


def _build_payload(kind: WebhookKind, title: str, body: str) -> dict:
    if kind == "slack":
        # Slack incoming-webhook：blocks 排版，title 作 header，body 作 section
        return {
            "text": title,
            "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": title}},
                {"type": "section", "text": {"type": "mrkdwn", "text": body}},
            ],
        }
    if kind == "discord":
        # Discord webhook：embeds 风格
        return {
            "username": "Sentinel-MCP",
            "embeds": [
                {"title": title, "description": body, "color": 0x6366F1},
            ],
        }
    # custom：原样发原始字段，让用户的接收端自己决定怎么解析
    return {"title": title, "body": body, "ts": time.time(), "source": "sentinel-mcp"}


def send(endpoint: dict, title: str, body: str, *, timeout: float = 5.0) -> dict:
    """单 endpoint 发一条。返回 {ok, status, error?}"""
    if not endpoint.get("enabled", True):
        return {"ok": False, "skipped": True, "reason": "disabled"}
    kind = endpoint.get("kind") or "custom"
    url = endpoint.get("url") or ""
    payload = _build_payload(kind, title, body)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"ok": True, "status": resp.status, "name": endpoint.get("name", "")}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "error": e.reason, "name": endpoint.get("name", "")}
    except urllib.error.URLError as e:
        return {"ok": False, "error": str(e.reason), "name": endpoint.get("name", "")}
    except Exception as e:
        return {"ok": False, "error": str(e), "name": endpoint.get("name", "")}


def send_all(title: str, body: str, path: Path | None = None) -> list[dict]:
    """对所有启用的 endpoint 各推一条。"""
    return [send(ep, title, body) for ep in list_endpoints(path) if ep.get("enabled", True)]
