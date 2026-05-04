"""一键集成：检测本机的 Claude Desktop / Cursor，把它们的 mcpServers 配置改成走 Sentinel-MCP。

为什么放在 dashboard 这一层而不是 Tauri 端：
  dashboard 是 Tauri 主窗口加载的同一个 URL，也是 PyPI 装的 dev 用户的入口。
  把这个能力做在 dashboard，桌面包用户和 pip 用户**同样的体验**，零分叉。

设计原则：
  - 写 config 前先备份原文件到 <path>.bak.<timestamp>，永不静默清空用户配置
  - 只新增 / 覆盖 mcpServers 下指定 key，**不动其它字段**
  - 写入用 temp + rename 原子操作，避免半个文件
  - 检测时不要求 client 在跑——只看 config 文件是否存在
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


# ---------- 客户端定义 -----------------------------------------------

@dataclass
class ClientSpec:
    """一个 MCP-aware 客户端的元数据。"""
    key: str               # 内部标识（"claude_desktop" / "cursor"）
    display_name: str      # UI 显示名
    description: str       # UI 副标题
    config_paths: dict[str, str]  # platform -> path（支持 ~ 和 $VARS）

    def resolve(self) -> Path | None:
        """返回当前平台对应的 config 绝对路径；不支持的平台返回 None。"""
        plat = sys.platform
        # macOS / linux / win32
        raw = self.config_paths.get(plat)
        if raw is None:
            return None
        return Path(os.path.expandvars(os.path.expanduser(raw)))


# 注意路径：
#   Claude Desktop 没有 Linux 官方版本，所以 linux 故意不列。
#   Cursor 0.45+ 用户级 config 在 ~/.cursor/mcp.json，跨平台一致。
CLIENTS: dict[str, ClientSpec] = {
    "claude_desktop": ClientSpec(
        key="claude_desktop",
        display_name="Claude Desktop",
        description="Anthropic 官方桌面 app — macOS / Windows",
        config_paths={
            "darwin": "~/Library/Application Support/Claude/claude_desktop_config.json",
            "win32":  r"$APPDATA\Claude\claude_desktop_config.json",
        },
    ),
    "cursor": ClientSpec(
        key="cursor",
        display_name="Cursor",
        description="AI 代码编辑器 — 全平台",
        config_paths={
            "darwin": "~/.cursor/mcp.json",
            "linux":  "~/.cursor/mcp.json",
            "win32":  r"$USERPROFILE\.cursor\mcp.json",
        },
    ),
}


# ---------- 预设 MCP server ------------------------------------------

# 用户可一键加入的常见上游 MCP server。
# args 里的占位符 {{HOME}} 会在写入时替换成 ~ 的真实展开路径。
PRESETS: dict[str, dict[str, Any]] = {
    "filesystem": {
        "label": "Filesystem · 文件系统",
        "description": "读写本机文件，需指定一个工作目录",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "{{HOME}}/work"],
        "env": {},
        "needs_input": [
            {"key": "work_dir", "label": "工作目录", "default": "{{HOME}}/work", "patches_arg_index": 2}
        ],
    },
    "github": {
        "label": "GitHub · 仓库 / Issue / PR",
        "description": "需要 Personal Access Token",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": ""},
        "needs_input": [
            {"key": "github_token", "label": "GitHub Token", "default": "", "patches_env": "GITHUB_PERSONAL_ACCESS_TOKEN"}
        ],
    },
    "brave_search": {
        "label": "Brave Search · 网页搜索",
        "description": "需要 Brave Search API Key（免费）",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-brave-search"],
        "env": {"BRAVE_API_KEY": ""},
        "needs_input": [
            {"key": "brave_key", "label": "Brave API Key", "default": "", "patches_env": "BRAVE_API_KEY"}
        ],
    },
    "puppeteer": {
        "label": "Puppeteer · 浏览器自动化",
        "description": "无需配置",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-puppeteer"],
        "env": {},
        "needs_input": [],
    },
    "lark_mcp": {
        "label": "飞书 OpenAPI · @larksuiteoapi/lark-mcp",
        "description": "飞书官方 MCP server — 让 Cursor / Claude Desktop 调飞书全量 API（消息 / Docs / Bitable / Calendar），需要自建应用 App ID + Secret",
        "command": "npx",
        # 索引 0..6：-y / 包名 / mcp / -a / <APP_ID> / -s / <APP_SECRET>
        "args": ["-y", "@larksuiteoapi/lark-mcp", "mcp", "-a", "", "-s", ""],
        "env": {},
        "needs_input": [
            {"key": "lark_app_id", "label": "App ID（cli_xxxxxxxxxxxx）", "default": "cli_", "patches_arg_index": 4},
            {"key": "lark_app_secret", "label": "App Secret", "default": "", "patches_arg_index": 6},
        ],
    },
}


# ---------- 检测 ------------------------------------------------------

def _read_config(path: Path) -> dict[str, Any] | None:
    """读取 JSON config；不存在返回 None；解析失败返回 {} 并记录损坏。"""
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        # 损坏的 config 不抛异常——返回特殊标记让前端展示
        return {"__sentinel_parse_error__": True}


def _is_sentinel_wrapped(server_entry: dict[str, Any]) -> bool:
    """判断一条 mcpServers 条目是不是已经被 Sentinel-MCP 包过。"""
    cmd = (server_entry or {}).get("command") or ""
    args = (server_entry or {}).get("args") or []
    if cmd in ("sentinel-mcp", "sentinel_mcp") and "wrap" in args:
        return True
    # 兼容 `python -m sentinel_mcp.cli wrap ...` 形态
    if cmd in ("python", "python3", "py") and "sentinel_mcp.cli" in args and "wrap" in args:
        return True
    return False


def detect_all() -> dict[str, Any]:
    """扫所有支持的 client，返回前端能直接渲染的状态字典。"""
    result: dict[str, Any] = {"platform": sys.platform, "clients": []}
    for spec in CLIENTS.values():
        path = spec.resolve()
        item = {
            **asdict(spec),
            "supported_on_platform": path is not None,
            "config_path": str(path) if path else None,
            "installed": False,
            "config_exists": False,
            "parse_error": False,
            "mcp_servers": [],         # 已配置的 server 名称列表
            "wrapped_servers": [],     # 已被 Sentinel 包过的 server 名称
        }
        if path is None:
            result["clients"].append(item)
            continue
        cfg = _read_config(path)
        if cfg is None:
            # 平台支持但 config 文件不存在 — client 可能没装或没生成 config
            result["clients"].append(item)
            continue
        if cfg.get("__sentinel_parse_error__"):
            item["parse_error"] = True
            item["config_exists"] = True
            item["installed"] = True
            result["clients"].append(item)
            continue
        item["installed"] = True
        item["config_exists"] = True
        servers = (cfg.get("mcpServers") or {})
        item["mcp_servers"] = list(servers.keys())
        item["wrapped_servers"] = [
            name for name, entry in servers.items() if _is_sentinel_wrapped(entry)
        ]
        result["clients"].append(item)
    return result


# ---------- 写入 ------------------------------------------------------

def _resolve_sentinel_command() -> tuple[str, list[str]]:
    """返回应该写到 client config 里的 command + 前置 args。

    优先用 PATH 上的 `sentinel-mcp` —— 简洁、跨平台一致。
    PATH 找不到时回退到 `python -m sentinel_mcp.cli`，保证哪怕用户
    pip --user 装到没加 PATH 的目录也能用。
    """
    found = shutil.which("sentinel-mcp")
    if found:
        return "sentinel-mcp", []
    py = shutil.which("python3") or shutil.which("python") or "python"
    return py, ["-m", "sentinel_mcp.cli"]


def _expand_placeholders(text: str) -> str:
    return text.replace("{{HOME}}", str(Path.home()))


def _build_wrapped_entry(
    upstream_command: str,
    upstream_args: list[str],
    upstream_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """把一个上游 MCP server spec 包成 sentinel-mcp wrap 形态的 entry。"""
    sent_cmd, sent_prefix = _resolve_sentinel_command()
    args = list(sent_prefix) + ["wrap", "--", upstream_command, *upstream_args]
    entry: dict[str, Any] = {"command": sent_cmd, "args": args}
    if upstream_env:
        entry["env"] = dict(upstream_env)
    return entry


def install(
    client_key: str,
    server_name: str,
    upstream_command: str,
    upstream_args: list[str],
    upstream_env: dict[str, str] | None = None,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """把一条 wrapped MCP server entry 写进指定 client 的 config。

    返回:
      {ok, written_path, backup_path, server_name, action: 'created'|'replaced'}
    """
    spec = CLIENTS.get(client_key)
    if spec is None:
        raise ValueError(f"unknown client: {client_key}")
    path = spec.resolve()
    if path is None:
        raise ValueError(f"{spec.display_name} 不支持当前平台 ({sys.platform})")
    if not server_name or not server_name.replace("_", "").replace("-", "").isalnum():
        raise ValueError("server_name 只能含字母 / 数字 / _ / -")

    # 读现有 config（不存在 → 空骨架）
    existing = _read_config(path) or {}
    if existing.get("__sentinel_parse_error__"):
        raise ValueError(f"现有 config 解析失败，无法安全合并：{path}")

    mcp_servers = existing.setdefault("mcpServers", {})
    action = "replaced" if server_name in mcp_servers else "created"
    if action == "replaced" and not overwrite:
        raise ValueError(f"server '{server_name}' 已存在；要覆盖请设置 overwrite=true")

    # 占位符展开（{{HOME}}）+ 构造新 entry
    upstream_args = [_expand_placeholders(a) for a in upstream_args]
    if upstream_env:
        upstream_env = {k: _expand_placeholders(str(v)) for k, v in upstream_env.items()}
    mcp_servers[server_name] = _build_wrapped_entry(
        upstream_command, upstream_args, upstream_env
    )

    # 备份 + 原子写
    backup_path: Path | None = None
    if path.exists():
        backup_path = path.with_suffix(path.suffix + f".bak.{int(time.time())}")
        shutil.copy2(path, backup_path)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(path)

    return {
        "ok": True,
        "written_path": str(path),
        "backup_path": str(backup_path) if backup_path else None,
        "server_name": server_name,
        "action": action,
        "entry": mcp_servers[server_name],
    }


def preview(
    client_key: str,
    server_name: str,
    upstream_command: str,
    upstream_args: list[str],
    upstream_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """干跑：返回最终会写入的 entry，不动磁盘。前端「先看效果再确认」按钮用。"""
    spec = CLIENTS.get(client_key)
    if spec is None:
        raise ValueError(f"unknown client: {client_key}")
    path = spec.resolve()
    upstream_args = [_expand_placeholders(a) for a in upstream_args]
    if upstream_env:
        upstream_env = {k: _expand_placeholders(str(v)) for k, v in upstream_env.items()}
    return {
        "client": client_key,
        "config_path": str(path) if path else None,
        "server_name": server_name,
        "entry": _build_wrapped_entry(upstream_command, upstream_args, upstream_env),
    }
