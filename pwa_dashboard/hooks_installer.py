"""一键往各 client 装 PreToolUse hook，让它们调内置工具时也走 Sentinel。

支持的 client 矩阵（v0.3 起）：

  | client          | 原生 hook 机制                | 我们的接入方式                            |
  |-----------------|-------------------------------|-------------------------------------------|
  | Claude Code CLI | PreToolUse (~/.claude/...)   | 装 hook → sentinel-mcp hook-check         |
  | Cursor          | 无                           | 已通过 mcpServers wrap 覆盖（Chunk B）     |
  | Cline / Roo     | 无（仅 prompt 注入）         | 同上 — wrap                               |
  | Continue.dev    | 无（slash command）          | 同上 — wrap                               |
  | Windsurf        | 无（闭源）                   | 同上 — wrap                               |
  | Zed             | 无                           | wrap context_servers                      |
  | Goose           | 无                           | wrap stdio extensions                     |

只有 Claude Code 有原生 PreToolUse hook，所以这里 only 实现 Claude Code 安装。
其它 client 依赖 wrap 机制（已在 discovery 模块完成），dashboard 上只展示状态。

设计：
  - 一键安装：在 ~/.claude/settings.json 的 hooks.PreToolUse 里追加我们的 hook
  - 用 marker 字段 sentinelMcpManaged=true 识别我们装的，方便卸载
  - 修改前自动备份 settings.json.sentinel-backup.<ts>
  - matcher 默认匹配所有内置工具：Bash|Write|Edit|MultiEdit|Read|Glob|Grep
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

CLAUDE_SETTINGS_PATHS = {
    "darwin": Path.home() / ".claude" / "settings.json",
    "linux":  Path.home() / ".claude" / "settings.json",
    "win32":  Path.home() / ".claude" / "settings.json",  # 同样路径，Claude Code 跨平台一致
}

# matcher：哪些 Claude Code 内置工具会触发 hook。`Read` 是高频低风险，默认不拦
DEFAULT_MATCHER = "Bash|Write|Edit|MultiEdit|WebFetch|TodoWrite|NotebookEdit"

# 自定义字段：让我们能识别哪个 hook entry 是 Sentinel 装的
SENTINEL_MARKER = "sentinelMcpManaged"


def _settings_path() -> Path:
    import sys
    return CLAUDE_SETTINGS_PATHS.get(sys.platform, CLAUDE_SETTINGS_PATHS["linux"])


def _read_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    bp = path.with_suffix(path.suffix + f".sentinel-backup.{int(time.time())}")
    shutil.copy2(path, bp)
    return bp


def _write_settings(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(path)


def _resolve_sentinel_command() -> str:
    """决定 hook 配置里写哪个命令。

    优先 PATH 上的 `sentinel-mcp`（一般来自我们 bootstrap 装的 ~/.local/bin shim
    或 pip 装的 entry point）。找不到回退到 `python3 -m sentinel_mcp.cli`。
    """
    found = shutil.which("sentinel-mcp")
    if found:
        return f"{found} hook-check"
    py = shutil.which("python3") or shutil.which("python") or "python3"
    return f"{py} -m sentinel_mcp.cli hook-check"


def status(path: Path | None = None) -> dict[str, Any]:
    """检查 Claude Code hook 接入状态。"""
    p = path or _settings_path()
    data = _read_settings(p)
    hooks = data.get("hooks") or {}
    pre = hooks.get("PreToolUse") or []
    sentinel_entries = []
    other_entries = []
    for entry in pre if isinstance(pre, list) else []:
        if not isinstance(entry, dict):
            continue
        if entry.get(SENTINEL_MARKER) is True:
            sentinel_entries.append(entry)
        else:
            other_entries.append(entry)
    return {
        "settings_path": str(p),
        "settings_exists": p.exists(),
        "installed": len(sentinel_entries) > 0,
        "sentinel_hooks": sentinel_entries,
        "other_hooks_count": len(other_entries),
        "expected_command": _resolve_sentinel_command(),
    }


def install(path: Path | None = None, *, matcher: str = DEFAULT_MATCHER) -> dict[str, Any]:
    """往 ~/.claude/settings.json 写一条 PreToolUse hook 指向 sentinel-mcp。

    幂等：已装的会被替换（更新 command），保留备份。
    不动其它 hook（用户可能装了别的），只追加 / 替换我们带 marker 的那条。
    """
    p = path or _settings_path()
    data = _read_settings(p)
    backup = _backup(p)

    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"{p}: hooks 字段不是 dict，拒绝改写（请手动修复或备份后删除）")

    pre_list = hooks.setdefault("PreToolUse", [])
    if not isinstance(pre_list, list):
        raise ValueError(f"{p}: hooks.PreToolUse 字段不是 list")

    # 移除旧的 sentinel entry（识别 marker），别的 hook 一概保留
    pre_list[:] = [e for e in pre_list if not (isinstance(e, dict) and e.get(SENTINEL_MARKER) is True)]

    cmd = _resolve_sentinel_command()
    new_entry = {
        "matcher": matcher,
        "hooks": [{"type": "command", "command": cmd, "timeout": 8}],
        SENTINEL_MARKER: True,
    }
    pre_list.append(new_entry)

    _write_settings(p, data)
    return {
        "ok": True,
        "action": "installed",
        "settings_path": str(p),
        "backup_path": str(backup) if backup else None,
        "matcher": matcher,
        "command": cmd,
    }


def uninstall(path: Path | None = None) -> dict[str, Any]:
    """移除 Sentinel 装的 hook，保留用户自己的其它 hook。"""
    p = path or _settings_path()
    data = _read_settings(p)
    backup = _backup(p)

    hooks = data.get("hooks") or {}
    pre_list = (hooks.get("PreToolUse") or []) if isinstance(hooks, dict) else []
    if not isinstance(pre_list, list):
        return {"ok": False, "action": "noop", "reason": "PreToolUse 不是 list"}

    before = len(pre_list)
    pre_list = [e for e in pre_list if not (isinstance(e, dict) and e.get(SENTINEL_MARKER) is True)]
    removed = before - len(pre_list)
    if removed == 0:
        return {"ok": True, "action": "noop", "reason": "未发现 Sentinel hook", "settings_path": str(p)}

    if pre_list:
        hooks["PreToolUse"] = pre_list
    else:
        # 列表空了就删 key，让 settings.json 干净
        hooks.pop("PreToolUse", None)
        if not hooks:
            data.pop("hooks", None)

    _write_settings(p, data)
    return {
        "ok": True,
        "action": "uninstalled",
        "settings_path": str(p),
        "backup_path": str(backup) if backup else None,
        "removed_count": removed,
    }


def list_supported_clients() -> list[dict[str, Any]]:
    """供 dashboard 显示：每个 client 是否原生支持 hook + 我们的接入策略。"""
    return [
        {
            "client_key": "claude_code",
            "display_name": "Claude Code CLI",
            "native_hook": True,
            "approach": "PreToolUse hook → sentinel-mcp hook-check",
            "covers_internal_tools": True,
            "install_path": str(_settings_path()),
        },
        {
            "client_key": "cursor",
            "display_name": "Cursor",
            "native_hook": False,
            "approach": "MCP wrap（Chunk B 已自动包装 mcpServers）",
            "covers_internal_tools": False,
            "install_path": None,
        },
        {
            "client_key": "cline",
            "display_name": "Cline / Roo Code",
            "native_hook": False,
            "approach": "MCP wrap",
            "covers_internal_tools": False,
            "install_path": None,
        },
        {
            "client_key": "continue_dev",
            "display_name": "Continue.dev",
            "native_hook": False,
            "approach": "MCP wrap",
            "covers_internal_tools": False,
            "install_path": None,
        },
        {
            "client_key": "windsurf",
            "display_name": "Windsurf",
            "native_hook": False,
            "approach": "MCP wrap",
            "covers_internal_tools": False,
            "install_path": None,
        },
        {
            "client_key": "zed",
            "display_name": "Zed",
            "native_hook": False,
            "approach": "MCP wrap (context_servers)",
            "covers_internal_tools": False,
            "install_path": None,
        },
        {
            "client_key": "goose",
            "display_name": "Goose",
            "native_hook": False,
            "approach": "MCP wrap (stdio extensions)",
            "covers_internal_tools": False,
            "install_path": None,
        },
    ]
