"""统一发现：扫机器上所有 MCP-aware 客户端的 mcpServers，支持「一键全部包装」。

为什么不复用 integrations.py：
  integrations.py 的 ClientSpec 是「单文件 / 单顶层 mcpServers key」假设，
  无法表达 Claude Code CLI（projects.*.mcpServers 嵌套）/ Zed（context_servers
  不同 key）/ Continue（YAML 文件目录）这些客户端。这里用 adapter 模式重写，
  每个 client 一个 adapter，按自己的配置形态实现 enumerate / write。

后续 integrations.py 的「单 server 加预设」UI 仍走老 API，这里只负责「扫描全机
+ 批量包装/解除」。两层并存，互不干扰。

设计原则（与 integrations.py 一致）：
  - 写 config 前先备份到 <path>.sentinel-backup.<timestamp>
  - 只动 mcpServers 下指定条目，**不动其它字段**
  - 写入用 temp + rename 原子操作
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------- #
# 数据类型
# ---------------------------------------------------------------- #


@dataclass
class ServerEntry:
    """一条已发现的 mcpServers 条目（按「(client, config_path, scope, server_name)」唯一确定）。"""

    client_key: str          # "claude_code" / "claude_desktop" / "cursor" / ...
    client_display: str      # UI 显示名
    config_path: str         # 该 server 所在 config 文件绝对路径
    scope: str               # "" 表示文件级；Claude Code 等多 scope 时是 "project:/foo/bar"
    server_name: str         # mcpServers dict 的 key
    upstream_command: str    # 解包后的真实 command（已 wrap 时取 "--" 之后第一个）
    upstream_args: list[str] # 解包后的真实 args
    upstream_env: dict[str, str]
    is_protected: bool       # 当前是否已经被 sentinel-mcp wrap


@dataclass
class WrapResult:
    """批量 wrap/unwrap 单条结果。"""

    client_key: str
    config_path: str
    scope: str
    server_name: str
    ok: bool
    action: str  # "wrapped" / "unwrapped" / "skipped" / "error"
    backup_path: str | None = None
    error: str | None = None


# ---------------------------------------------------------------- #
# 已 wrap 检测 + 解包 / 包装
# ---------------------------------------------------------------- #


_SENTINEL_BIN_NAMES = ("sentinel-mcp", "sentinel_mcp")
_PY_MOD_FORM = "sentinel_mcp.cli"


def is_wrapped(entry: dict[str, Any]) -> bool:
    """判断一条 mcpServers entry 是不是已经被 sentinel-mcp wrap 过了。"""
    if not isinstance(entry, dict):
        return False
    cmd = entry.get("command") or ""
    args = entry.get("args") or []
    if not isinstance(args, list):
        return False
    # 形态 1：command="sentinel-mcp"，args=["wrap", "--", ...]
    base = os.path.basename(cmd)
    if base in _SENTINEL_BIN_NAMES and "wrap" in args:
        return True
    # 形态 2：command="python"，args=["-m","sentinel_mcp.cli","wrap","--",...]
    if base in ("python", "python3", "py") and _PY_MOD_FORM in args and "wrap" in args:
        return True
    # 形态 3：command 路径里含 sentinel-mcp（AppImage / 绝对路径）
    if "sentinel-mcp" in cmd and "wrap" in args:
        return True
    return False


def extract_upstream(entry: dict[str, Any]) -> tuple[str, list[str], dict[str, str]]:
    """从一条 entry 拆出真实上游 command/args/env。

    若 entry 已经 wrap 过：取 args 里 "--" 之后的部分。
    若 entry 是裸的：原样返回。
    """
    cmd = entry.get("command") or ""
    args = list(entry.get("args") or [])
    env = dict(entry.get("env") or {})
    if not is_wrapped(entry):
        return cmd, args, env
    if "--" in args:
        idx = args.index("--")
        upstream = args[idx + 1 :]
        if upstream:
            return upstream[0], list(upstream[1:]), env
    # 兜底：找不到 "--" 就当裸的
    return cmd, args, env


def _resolve_sentinel_command() -> tuple[str, list[str]]:
    """决定写到 client config 里的 command + 前置 args。

    优先 PATH 上的 `sentinel-mcp`；找不到回退到 `python -m sentinel_mcp.cli`。
    """
    found = shutil.which("sentinel-mcp")
    if found:
        return "sentinel-mcp", []
    py = shutil.which("python3") or shutil.which("python") or "python"
    return py, ["-m", "sentinel_mcp.cli"]


def build_wrapped_entry(
    upstream_command: str,
    upstream_args: list[str],
    upstream_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    sent_cmd, sent_prefix = _resolve_sentinel_command()
    out: dict[str, Any] = {
        "command": sent_cmd,
        "args": list(sent_prefix) + ["wrap", "--", upstream_command, *upstream_args],
    }
    if upstream_env:
        out["env"] = dict(upstream_env)
    return out


def build_unwrapped_entry(
    upstream_command: str,
    upstream_args: list[str],
    upstream_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"command": upstream_command, "args": list(upstream_args)}
    if upstream_env:
        out["env"] = dict(upstream_env)
    return out


# ---------------------------------------------------------------- #
# Adapter 基类 + 通用 JSON 实现
# ---------------------------------------------------------------- #


class ClientAdapter:
    """每个 MCP 客户端实现一个 adapter。

    四个职责：
      1. 在本机找到 0..N 个 config 文件路径
      2. 给定 config 文件，列出所有 mcpServers entries（带 scope）
      3. 给定一条 entry 写入位置 (config_path, scope, server_name)，rewrite 它
      4. 提供「人类友好」的 display_name / description
    """

    key: str
    display_name: str
    description: str

    def list_config_files(self) -> list[Path]:
        raise NotImplementedError

    def enumerate(self, config_path: Path) -> list[ServerEntry]:
        raise NotImplementedError

    def write_entry(
        self,
        config_path: Path,
        scope: str,
        server_name: str,
        new_entry: dict[str, Any],
    ) -> Path | None:
        """把 server_name 在指定 scope 下的 entry 替换为 new_entry。返回备份路径（首次写为 None）。"""
        raise NotImplementedError


def _read_json(p: Path) -> dict[str, Any] | None:
    if not p.exists():
        return None
    try:
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _atomic_write_json(p: Path, data: dict[str, Any]) -> Path | None:
    """原子写 JSON。若文件已存在先备份并返回备份路径；否则返回 None。"""
    backup: Path | None = None
    if p.exists():
        backup = p.with_suffix(p.suffix + f".sentinel-backup.{int(time.time())}")
        shutil.copy2(p, backup)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(p)
    return backup


class SimpleJSONAdapter(ClientAdapter):
    """单文件 + 顶层 mcpServers key 的客户端（Claude Desktop / Cursor / Cline / Roo Code 等都属于这类）。"""

    def __init__(
        self,
        *,
        key: str,
        display_name: str,
        description: str,
        config_paths_per_platform: dict[str, list[str]],
        mcp_key: str = "mcpServers",
    ) -> None:
        self.key = key
        self.display_name = display_name
        self.description = description
        self._paths_per_platform = config_paths_per_platform
        self._mcp_key = mcp_key

    def list_config_files(self) -> list[Path]:
        plat = sys.platform
        out: list[Path] = []
        for raw in self._paths_per_platform.get(plat, []):
            expanded = os.path.expandvars(os.path.expanduser(raw))
            # 支持 glob（Continue.dev 的 .continue/mcpServers/*.yaml 类用法，虽然这里走 JSON）
            if any(c in expanded for c in "*?["):
                out.extend(Path(p) for p in sorted(_glob(expanded)))
            else:
                p = Path(expanded)
                if p.exists():
                    out.append(p)
        return out

    def enumerate(self, config_path: Path) -> list[ServerEntry]:
        cfg = _read_json(config_path)
        if not isinstance(cfg, dict):
            return []
        servers = cfg.get(self._mcp_key) or {}
        if not isinstance(servers, dict):
            return []
        out: list[ServerEntry] = []
        for name, entry in servers.items():
            if not isinstance(entry, dict):
                continue
            cmd, args, env = extract_upstream(entry)
            out.append(
                ServerEntry(
                    client_key=self.key,
                    client_display=self.display_name,
                    config_path=str(config_path),
                    scope="",
                    server_name=name,
                    upstream_command=cmd,
                    upstream_args=args,
                    upstream_env=env,
                    is_protected=is_wrapped(entry),
                )
            )
        return out

    def write_entry(
        self,
        config_path: Path,
        scope: str,
        server_name: str,
        new_entry: dict[str, Any],
    ) -> Path | None:
        cfg = _read_json(config_path) or {}
        servers = cfg.setdefault(self._mcp_key, {})
        if not isinstance(servers, dict):
            raise ValueError(f"{config_path}: '{self._mcp_key}' 不是 dict，拒绝改写")
        servers[server_name] = new_entry
        return _atomic_write_json(config_path, cfg)


def _glob(pattern: str) -> Iterable[str]:
    import glob

    return glob.glob(pattern, recursive=True)


# ---------------------------------------------------------------- #
# YAML 通用 adapter（Continue.dev / Goose 等）
# ---------------------------------------------------------------- #


def _read_yaml(p: Path) -> dict[str, Any] | None:
    """读取 YAML config；不存在 / 解析失败 → None。"""
    if not p.exists():
        return None
    try:
        import yaml
    except ImportError:
        return None
    try:
        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except (OSError, Exception):
        return None


def _atomic_write_yaml(p: Path, data: dict[str, Any]) -> Path | None:
    """原子写 YAML；返回备份路径（首次写为 None）。"""
    import yaml

    backup: Path | None = None
    if p.exists():
        backup = p.with_suffix(p.suffix + f".sentinel-backup.{int(time.time())}")
        shutil.copy2(p, backup)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    tmp.replace(p)
    return backup


class SimpleYAMLAdapter(ClientAdapter):
    """单文件 + 顶层指定 key 的 YAML 客户端（Continue.dev 等）。

    与 SimpleJSONAdapter 同行为，只是 IO 走 YAML。
    """

    def __init__(
        self,
        *,
        key: str,
        display_name: str,
        description: str,
        config_paths_per_platform: dict[str, list[str]],
        mcp_key: str = "mcpServers",
    ) -> None:
        self.key = key
        self.display_name = display_name
        self.description = description
        self._paths_per_platform = config_paths_per_platform
        self._mcp_key = mcp_key

    def list_config_files(self) -> list[Path]:
        plat = sys.platform
        out: list[Path] = []
        for raw in self._paths_per_platform.get(plat, []):
            expanded = os.path.expandvars(os.path.expanduser(raw))
            if any(c in expanded for c in "*?["):
                out.extend(Path(p) for p in sorted(_glob(expanded)))
            else:
                p = Path(expanded)
                if p.exists():
                    out.append(p)
        return out

    def enumerate(self, config_path: Path) -> list[ServerEntry]:
        cfg = _read_yaml(config_path)
        if not isinstance(cfg, dict):
            return []
        servers = cfg.get(self._mcp_key) or {}
        if not isinstance(servers, dict):
            return []
        out: list[ServerEntry] = []
        for name, entry in servers.items():
            if not isinstance(entry, dict):
                continue
            cmd, args, env = extract_upstream(entry)
            out.append(
                ServerEntry(
                    client_key=self.key,
                    client_display=self.display_name,
                    config_path=str(config_path),
                    scope="",
                    server_name=name,
                    upstream_command=cmd,
                    upstream_args=args,
                    upstream_env=env,
                    is_protected=is_wrapped(entry),
                )
            )
        return out

    def write_entry(
        self,
        config_path: Path,
        scope: str,
        server_name: str,
        new_entry: dict[str, Any],
    ) -> Path | None:
        cfg = _read_yaml(config_path) or {}
        servers = cfg.setdefault(self._mcp_key, {})
        if not isinstance(servers, dict):
            raise ValueError(f"{config_path}: '{self._mcp_key}' 不是 dict，拒绝改写")
        servers[server_name] = new_entry
        return _atomic_write_yaml(config_path, cfg)


# ---------------------------------------------------------------- #
# Goose 专用 adapter（YAML，extensions 是 list[dict]，schema 不同）
# ---------------------------------------------------------------- #


class GooseAdapter(ClientAdapter):
    """Goose 的 ~/.config/goose/config.yaml 形如：

      extensions:
        - name: filesystem
          type: stdio
          cmd: npx
          args: [-y, @modelcontextprotocol/server-filesystem, /tmp]
        - name: github
          type: builtin   # builtin 类型不是 MCP server，跳过
          ...

    只识别 type=stdio 的 extension 当作 MCP server。
    """

    key = "goose"
    display_name = "Goose"
    description = "Block 开源 agent，CLI 工具"

    def list_config_files(self) -> list[Path]:
        plat = sys.platform
        candidates: list[str] = []
        if plat == "win32":
            candidates = [r"$USERPROFILE\.config\goose\config.yaml", r"$APPDATA\Block\goose\config.yaml"]
        else:
            candidates = ["~/.config/goose/config.yaml"]
        out: list[Path] = []
        for raw in candidates:
            p = Path(os.path.expandvars(os.path.expanduser(raw)))
            if p.exists():
                out.append(p)
        return out

    def enumerate(self, config_path: Path) -> list[ServerEntry]:
        cfg = _read_yaml(config_path)
        if not isinstance(cfg, dict):
            return []
        exts = cfg.get("extensions") or []
        if not isinstance(exts, list):
            return []
        out: list[ServerEntry] = []
        for ext in exts:
            if not isinstance(ext, dict):
                continue
            if (ext.get("type") or "").lower() != "stdio":
                continue  # builtin / sse 类型不是本地 MCP server，跳过
            name = ext.get("name") or "(unnamed)"
            # Goose 字段：cmd + args（不是 command）
            ent = {"command": ext.get("cmd") or "", "args": ext.get("args") or [], "env": ext.get("envs") or {}}
            cmd, args, env = extract_upstream(ent)
            out.append(
                ServerEntry(
                    client_key=self.key,
                    client_display=self.display_name,
                    config_path=str(config_path),
                    scope="",
                    server_name=name,
                    upstream_command=cmd,
                    upstream_args=args,
                    upstream_env=env,
                    is_protected=is_wrapped(ent),
                )
            )
        return out

    def write_entry(
        self,
        config_path: Path,
        scope: str,
        server_name: str,
        new_entry: dict[str, Any],
    ) -> Path | None:
        cfg = _read_yaml(config_path) or {}
        exts = cfg.setdefault("extensions", [])
        if not isinstance(exts, list):
            raise ValueError(f"{config_path}: extensions 不是 list")
        # 找到对应 name 的 stdio extension 并改 cmd/args
        target = None
        for ext in exts:
            if isinstance(ext, dict) and ext.get("name") == server_name and (ext.get("type") or "").lower() == "stdio":
                target = ext
                break
        if target is None:
            raise ValueError(f"{config_path}: 找不到 stdio extension '{server_name}'")
        target["cmd"] = new_entry.get("command", "")
        target["args"] = list(new_entry.get("args") or [])
        if "env" in new_entry:
            target["envs"] = dict(new_entry["env"])
        return _atomic_write_yaml(config_path, cfg)


# ---------------------------------------------------------------- #
# VSCode native MCP（v1.99+）扫工作区目录
# ---------------------------------------------------------------- #


class VSCodeWorkspaceAdapter(ClientAdapter):
    """VS Code 1.99+ 原生 MCP：每个工作区 .vscode/mcp.json。

    扫描策略：从 ~/.claude.json:projects 拿用户最近用过的 project 路径，
    在每个下面找 .vscode/mcp.json。这样不用扫全盘也覆盖到所有"活跃"工作区。
    """

    key = "vscode_native"
    display_name = "VS Code (原生 MCP)"
    description = "VS Code 1.99+ 内置 MCP，每个工作区 .vscode/mcp.json"

    def list_config_files(self) -> list[Path]:
        out: list[Path] = []
        # 1. 从 Claude Code 已知 project 反推（用户最常用的工作区）
        claude_json = Path.home() / ".claude.json"
        if claude_json.exists():
            try:
                with claude_json.open("r", encoding="utf-8") as f:
                    d = json.load(f)
                for proj_path in (d.get("projects") or {}).keys():
                    cfg = Path(proj_path) / ".vscode" / "mcp.json"
                    if cfg.exists():
                        out.append(cfg)
            except (json.JSONDecodeError, OSError):
                pass
        # 2. 也看 cwd（dashboard 启动目录）下面有没有
        cwd_cfg = Path.cwd() / ".vscode" / "mcp.json"
        if cwd_cfg.exists() and cwd_cfg not in out:
            out.append(cwd_cfg)
        return out

    def enumerate(self, config_path: Path) -> list[ServerEntry]:
        cfg = _read_json(config_path)
        if not isinstance(cfg, dict):
            return []
        servers = cfg.get("mcpServers") or cfg.get("servers") or {}
        if not isinstance(servers, dict):
            return []
        out: list[ServerEntry] = []
        for name, entry in servers.items():
            if not isinstance(entry, dict):
                continue
            cmd, args, env = extract_upstream(entry)
            out.append(
                ServerEntry(
                    client_key=self.key,
                    client_display=self.display_name,
                    config_path=str(config_path),
                    scope=f"workspace:{config_path.parent.parent}",
                    server_name=name,
                    upstream_command=cmd,
                    upstream_args=args,
                    upstream_env=env,
                    is_protected=is_wrapped(entry),
                )
            )
        return out

    def write_entry(
        self,
        config_path: Path,
        scope: str,
        server_name: str,
        new_entry: dict[str, Any],
    ) -> Path | None:
        cfg = _read_json(config_path) or {}
        # 兼容两种 key 名
        key = "mcpServers" if "mcpServers" in cfg or "servers" not in cfg else "servers"
        servers = cfg.setdefault(key, {})
        if not isinstance(servers, dict):
            raise ValueError(f"{config_path}: '{key}' 不是 dict")
        servers[server_name] = new_entry
        return _atomic_write_json(config_path, cfg)


# ---------------------------------------------------------------- #
# Claude Code CLI 专用 adapter（projects.*.mcpServers 嵌套）
# ---------------------------------------------------------------- #


class ClaudeCodeAdapter(ClientAdapter):
    """Claude Code CLI 把 mcpServers 同时放在两处：
      - ~/.claude.json:mcpServers          → 全局，所有 project 共享
      - ~/.claude.json:projects.<path>.mcpServers → 单 project 独占

    用 scope 字段区分：scope="" 表示全局；scope="project:<path>" 表示某个 project。
    另外 <project_dir>/.mcp.json 是「随仓库走的」project-shared MCP，先不扫（路径
    不固定，要么从 projects 列表反推扫，要么靠用户主动添加目录；后续可以加）。
    """

    key = "claude_code"
    display_name = "Claude Code CLI"
    description = "Anthropic 官方命令行 — 全平台 (~/.claude.json)"

    def list_config_files(self) -> list[Path]:
        # Claude Code CLI 跨平台都是 ~/.claude.json
        p = Path.home() / ".claude.json"
        return [p] if p.exists() else []

    def enumerate(self, config_path: Path) -> list[ServerEntry]:
        cfg = _read_json(config_path)
        if not isinstance(cfg, dict):
            return []
        out: list[ServerEntry] = []

        # 全局 mcpServers
        global_servers = cfg.get("mcpServers") or {}
        if isinstance(global_servers, dict):
            for name, entry in global_servers.items():
                if not isinstance(entry, dict):
                    continue
                cmd, args, env = extract_upstream(entry)
                out.append(
                    ServerEntry(
                        client_key=self.key,
                        client_display=self.display_name,
                        config_path=str(config_path),
                        scope="",
                        server_name=name,
                        upstream_command=cmd,
                        upstream_args=args,
                        upstream_env=env,
                        is_protected=is_wrapped(entry),
                    )
                )

        # 每个 project 自己的 mcpServers
        projects = cfg.get("projects") or {}
        if isinstance(projects, dict):
            for proj_path, proj_data in projects.items():
                if not isinstance(proj_data, dict):
                    continue
                proj_servers = proj_data.get("mcpServers") or {}
                if not isinstance(proj_servers, dict):
                    continue
                for name, entry in proj_servers.items():
                    if not isinstance(entry, dict):
                        continue
                    cmd, args, env = extract_upstream(entry)
                    out.append(
                        ServerEntry(
                            client_key=self.key,
                            client_display=self.display_name,
                            config_path=str(config_path),
                            scope=f"project:{proj_path}",
                            server_name=name,
                            upstream_command=cmd,
                            upstream_args=args,
                            upstream_env=env,
                            is_protected=is_wrapped(entry),
                        )
                    )
        return out

    def write_entry(
        self,
        config_path: Path,
        scope: str,
        server_name: str,
        new_entry: dict[str, Any],
    ) -> Path | None:
        cfg = _read_json(config_path) or {}
        if scope == "":
            servers = cfg.setdefault("mcpServers", {})
            if not isinstance(servers, dict):
                raise ValueError(f"{config_path}: mcpServers 不是 dict")
            servers[server_name] = new_entry
        elif scope.startswith("project:"):
            proj_path = scope[len("project:") :]
            projects = cfg.setdefault("projects", {})
            if not isinstance(projects, dict):
                raise ValueError(f"{config_path}: projects 不是 dict")
            proj_data = projects.setdefault(proj_path, {})
            if not isinstance(proj_data, dict):
                raise ValueError(f"{config_path}: projects['{proj_path}'] 不是 dict")
            servers = proj_data.setdefault("mcpServers", {})
            if not isinstance(servers, dict):
                raise ValueError(f"{config_path}: projects['{proj_path}'].mcpServers 不是 dict")
            servers[server_name] = new_entry
        else:
            raise ValueError(f"未知 scope: {scope}")
        return _atomic_write_json(config_path, cfg)


# ---------------------------------------------------------------- #
# 注册：P0 五个 client（Chunk A）
# ---------------------------------------------------------------- #


def _build_default_adapters() -> list[ClientAdapter]:
    return [
        # 1. Claude Code CLI（用户日常驱动，P0 第一）
        ClaudeCodeAdapter(),
        # 2. Claude Desktop（mac/win，无 linux 官方版）
        SimpleJSONAdapter(
            key="claude_desktop",
            display_name="Claude Desktop",
            description="Anthropic 官方桌面 app — macOS / Windows",
            config_paths_per_platform={
                "darwin": ["~/Library/Application Support/Claude/claude_desktop_config.json"],
                "win32":  [r"$APPDATA\Claude\claude_desktop_config.json"],
            },
        ),
        # 3. Cursor（编辑器，跨平台）
        SimpleJSONAdapter(
            key="cursor",
            display_name="Cursor",
            description="AI 代码编辑器（基于 VS Code）",
            config_paths_per_platform={
                "darwin": ["~/.cursor/mcp.json"],
                "linux":  ["~/.cursor/mcp.json"],
                "win32":  [r"$USERPROFILE\.cursor\mcp.json"],
            },
        ),
        # 4. Cline（VS Code / Cursor 编辑器插件 saoudrizwan.claude-dev）
        SimpleJSONAdapter(
            key="cline",
            display_name="Cline (VS Code)",
            description="VS Code/Cursor 插件 saoudrizwan.claude-dev",
            config_paths_per_platform={
                "darwin": [
                    "~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json",
                    "~/Library/Application Support/Cursor/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json",
                ],
                "linux": [
                    "~/.config/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json",
                    "~/.config/Cursor/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json",
                    "~/.config/Code - Insiders/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json",
                    "~/.config/VSCodium/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json",
                ],
                "win32": [
                    r"$APPDATA\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json",
                    r"$APPDATA\Cursor\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json",
                ],
            },
        ),
        # 5. Roo Code（Cline fork，扩展 id rooveterinaryinc.roo-cline）
        SimpleJSONAdapter(
            key="roo_code",
            display_name="Roo Code (VS Code)",
            description="VS Code/Cursor 插件 rooveterinaryinc.roo-cline（Cline 的 fork）",
            config_paths_per_platform={
                "darwin": [
                    "~/Library/Application Support/Code/User/globalStorage/rooveterinaryinc.roo-cline/settings/cline_mcp_settings.json",
                    "~/Library/Application Support/Cursor/User/globalStorage/rooveterinaryinc.roo-cline/settings/cline_mcp_settings.json",
                ],
                "linux": [
                    "~/.config/Code/User/globalStorage/rooveterinaryinc.roo-cline/settings/cline_mcp_settings.json",
                    "~/.config/Cursor/User/globalStorage/rooveterinaryinc.roo-cline/settings/cline_mcp_settings.json",
                ],
                "win32": [
                    r"$APPDATA\Code\User\globalStorage\rooveterinaryinc.roo-cline\settings\cline_mcp_settings.json",
                    r"$APPDATA\Cursor\User\globalStorage\rooveterinaryinc.roo-cline\settings\cline_mcp_settings.json",
                ],
            },
        ),
        # ---- Chunk C: P1+P2+P3 client 扩展 ----
        # 6. Continue.dev（YAML 配置）
        SimpleYAMLAdapter(
            key="continue_dev",
            display_name="Continue.dev",
            description="开源 AI 代码助手（VS Code/JetBrains 插件 + CLI），全平台",
            config_paths_per_platform={
                "darwin": ["~/.continue/config.yaml"],
                "linux":  ["~/.continue/config.yaml"],
                "win32":  [r"$USERPROFILE\.continue\config.yaml"],
            },
        ),
        # 7. Windsurf（Codeium 编辑器，类 Cursor）
        SimpleJSONAdapter(
            key="windsurf",
            display_name="Windsurf",
            description="Codeium 出品 AI 编辑器",
            config_paths_per_platform={
                "darwin": ["~/.codeium/windsurf/mcp_config.json"],
                "linux":  ["~/.codeium/windsurf/mcp_config.json"],
                "win32":  [r"$USERPROFILE\.codeium\windsurf\mcp_config.json"],
            },
        ),
        # 8. Zed 编辑器（"context_servers" 不是 mcpServers）
        SimpleJSONAdapter(
            key="zed",
            display_name="Zed",
            description="Rust 写的高性能编辑器，原生 MCP 走 context_servers key",
            config_paths_per_platform={
                "darwin": ["~/.config/zed/settings.json"],
                "linux":  ["~/.config/zed/settings.json"],
                "win32":  [r"$APPDATA\Zed\settings.json"],
            },
            mcp_key="context_servers",
        ),
        # 9. Cline CLI 形态（独立于 VS Code 插件，~/.cline/）
        SimpleJSONAdapter(
            key="cline_cli",
            display_name="Cline CLI",
            description="Cline 的 CLI 形态 (~/.cline/data/settings/)",
            config_paths_per_platform={
                "darwin": ["~/.cline/data/settings/cline_mcp_settings.json"],
                "linux":  ["~/.cline/data/settings/cline_mcp_settings.json"],
                "win32":  [r"$USERPROFILE\.cline\data\settings\cline_mcp_settings.json"],
            },
        ),
        # 10. LM Studio（本地大模型 GUI，新版本支持 MCP）
        SimpleJSONAdapter(
            key="lm_studio",
            display_name="LM Studio",
            description="本地大模型 GUI，0.3.x+ 支持 MCP",
            config_paths_per_platform={
                "darwin": ["~/.lmstudio/mcp.json"],
                "linux":  ["~/.lmstudio/mcp.json"],
                "win32":  [r"$USERPROFILE\.lmstudio\mcp.json"],
            },
        ),
        # 11. Goose（Block 开源 agent，YAML，extensions 自定义 schema）
        GooseAdapter(),
        # 12. VS Code 1.99+ 原生 MCP（每个工作区 .vscode/mcp.json）
        VSCodeWorkspaceAdapter(),
        # ---- Phase 2 扩展：国内 + 海外补全 8 个 ----
        # 13. ChatBox（Electron app，Mac/Win/Linux 跨平台）
        SimpleJSONAdapter(
            key="chatbox",
            display_name="Chat Box",
            description="开源 ChatGPT/Claude 客户端 (Bin Huang)",
            config_paths_per_platform={
                "darwin": ["~/Library/Application Support/xyz.chatboxapp.app/mcp.json"],
                "linux":  ["~/.config/xyz.chatboxapp.app/mcp.json", "~/.config/chatbox/mcp.json"],
                "win32":  [r"$APPDATA\xyz.chatboxapp.app\mcp.json"],
            },
        ),
        # 14. Cherry Studio（中文圈高频）
        SimpleJSONAdapter(
            key="cherry_studio",
            display_name="Cherry Studio",
            description="中文圈热门 LLM 客户端 — Electron",
            config_paths_per_platform={
                "darwin": ["~/Library/Application Support/CherryStudio/data/mcp_servers.json"],
                "linux":  ["~/.config/CherryStudio/data/mcp_servers.json"],
                "win32":  [r"$APPDATA\CherryStudio\data\mcp_servers.json"],
            },
        ),
        # 15. DeepChat
        SimpleJSONAdapter(
            key="deepchat",
            display_name="DeepChat",
            description="国内开源多模型客户端",
            config_paths_per_platform={
                "darwin": ["~/Library/Application Support/DeepChat/mcp_settings.json"],
                "linux":  ["~/.config/DeepChat/mcp_settings.json"],
                "win32":  [r"$APPDATA\DeepChat\mcp_settings.json"],
            },
        ),
        # 16. 5ire（开源 MCP-first 客户端）
        SimpleJSONAdapter(
            key="fire5",
            display_name="5ire",
            description="开源 MCP-first 跨平台客户端",
            config_paths_per_platform={
                "darwin": ["~/Library/Application Support/5ire/servers.json"],
                "linux":  ["~/.config/5ire/servers.json"],
                "win32":  [r"$APPDATA\5ire\servers.json"],
            },
        ),
        # 17. AnythingLLM（Mintplex Labs，本地知识库 + LLM）
        SimpleJSONAdapter(
            key="anythingllm",
            display_name="AnythingLLM",
            description="本地知识库 + LLM 桌面端",
            config_paths_per_platform={
                "darwin": ["~/Library/Application Support/anythingllm-desktop/storage/mcp_settings.json"],
                "linux":  ["~/.config/anythingllm-desktop/storage/mcp_settings.json"],
                "win32":  [r"$APPDATA\anythingllm-desktop\storage\mcp_settings.json"],
            },
        ),
        # 18. Crush（charm.sh 出品，TUI AI agent）
        SimpleYAMLAdapter(
            key="crush",
            display_name="Crush (charm.sh)",
            description="charm.sh TUI AI 助手，YAML 配置",
            config_paths_per_platform={
                "darwin": ["~/.config/crush/crush.yaml", "~/.crush/config.yaml"],
                "linux":  ["~/.config/crush/crush.yaml", "~/.crush/config.yaml"],
                "win32":  [r"$APPDATA\crush\crush.yaml"],
            },
            mcp_key="mcp",
        ),
        # 19. OpenCode（sst.dev 的 SST OpenCode）
        SimpleJSONAdapter(
            key="opencode",
            display_name="OpenCode (SST)",
            description="SST 开源 AI 代码助手",
            config_paths_per_platform={
                "darwin": ["~/.opencode/config.json", "~/.config/opencode/config.json"],
                "linux":  ["~/.opencode/config.json", "~/.config/opencode/config.json"],
                "win32":  [r"$USERPROFILE\.opencode\config.json"],
            },
            mcp_key="mcp",
        ),
        # 20. Open WebUI（服务端，本地 fallback 路径 — 通常是 docker volume）
        SimpleJSONAdapter(
            key="open_webui",
            display_name="Open WebUI",
            description="开源服务端 LLM UI（通常 docker 部署）",
            config_paths_per_platform={
                "darwin": ["~/.open-webui/mcp_servers.json"],
                "linux":  ["~/.open-webui/mcp_servers.json", "/var/lib/open-webui/mcp_servers.json"],
                "win32":  [r"$APPDATA\open-webui\mcp_servers.json"],
            },
        ),
    ]


# 全局 adapter 注册表（单例；测试可清空替换）
ADAPTERS: dict[str, ClientAdapter] = {a.key: a for a in _build_default_adapters()}


def register_adapter(adapter: ClientAdapter) -> None:
    """允许后续 chunks（C/D/...）追加 adapter。"""
    ADAPTERS[adapter.key] = adapter


def reset_adapters() -> None:
    """测试用：清空注册表。"""
    ADAPTERS.clear()
    for a in _build_default_adapters():
        ADAPTERS[a.key] = a


# ---------------------------------------------------------------- #
# 顶层 API：扫描 / 批量 wrap / 批量 unwrap / 备份回滚
# ---------------------------------------------------------------- #


def scan_all() -> dict[str, Any]:
    """扫描所有已注册 client，返回前端能直接渲染的状态。

    返回:
      {
        "platform": "linux",
        "clients": [
          {
            "key": "claude_code",
            "display_name": "Claude Code CLI",
            "description": "...",
            "config_files": ["~/.claude.json"],     # 真实存在的 config
            "installed": True,                       # 至少有一个 config 文件存在
            "server_count": 3,
            "protected_count": 1,
          },
          ...
        ],
        "servers": [ServerEntry-as-dict, ...]      # 全机扁平列表
      }
    """
    out: dict[str, Any] = {"platform": sys.platform, "clients": [], "servers": []}
    for adapter in ADAPTERS.values():
        files = adapter.list_config_files()
        client_servers: list[ServerEntry] = []
        for f in files:
            client_servers.extend(adapter.enumerate(f))
        out["clients"].append(
            {
                "key": adapter.key,
                "display_name": adapter.display_name,
                "description": adapter.description,
                "config_files": [str(f) for f in files],
                "installed": len(files) > 0,
                "server_count": len(client_servers),
                "protected_count": sum(1 for s in client_servers if s.is_protected),
            }
        )
        out["servers"].extend(asdict(s) for s in client_servers)
    return out


def _select_one(
    client_key: str, config_path: str, scope: str, server_name: str
) -> tuple[ClientAdapter, ServerEntry]:
    """根据选择四元组定位 adapter + 该 server 的 ServerEntry（含 is_protected / upstream_*）。

    走 adapter 自己的 enumerate（自动支持 JSON / YAML / 嵌套 / 自定义 schema），
    避免在这里写「JSON vs YAML 该用哪个 reader」的分支。
    """
    adapter = ADAPTERS.get(client_key)
    if adapter is None:
        raise ValueError(f"未知 client: {client_key}")
    p = Path(config_path)
    if not p.exists():
        raise ValueError(f"config 不存在: {config_path}")
    for e in adapter.enumerate(p):
        if e.scope == scope and e.server_name == server_name:
            return adapter, e
    raise ValueError(f"在 {config_path} 中找不到 {scope}/{server_name}")


def wrap_servers(selections: list[dict[str, str]]) -> list[WrapResult]:
    """批量 wrap。selections 里每条是 {client_key, config_path, scope, server_name}。

    幂等：已 wrap 的会被标 "skipped"。
    每条独立处理：一条失败不影响别的。
    每个 config_path 第一次写入会留备份；同一文件后续写入复用同一 backup。
    """
    out: list[WrapResult] = []
    backup_recorded: dict[str, str | None] = {}  # config_path -> backup_path
    for sel in selections:
        client_key = sel.get("client_key", "")
        config_path = sel.get("config_path", "")
        scope = sel.get("scope", "")
        server_name = sel.get("server_name", "")
        try:
            adapter, entry = _select_one(client_key, config_path, scope, server_name)
            if entry.is_protected:
                out.append(WrapResult(client_key, config_path, scope, server_name, ok=True, action="skipped"))
                continue
            new_entry = build_wrapped_entry(
                entry.upstream_command, entry.upstream_args, entry.upstream_env or None
            )
            backup = adapter.write_entry(Path(config_path), scope, server_name, new_entry)
            # 同一 config 多个 server 时，只保留第一次的备份路径
            if config_path not in backup_recorded:
                backup_recorded[config_path] = str(backup) if backup else None
            out.append(
                WrapResult(
                    client_key, config_path, scope, server_name,
                    ok=True, action="wrapped", backup_path=backup_recorded[config_path],
                )
            )
        except Exception as e:
            out.append(WrapResult(client_key, config_path, scope, server_name, ok=False, action="error", error=str(e)))
    return out


def unwrap_servers(selections: list[dict[str, str]]) -> list[WrapResult]:
    """批量 unwrap：把 wrap 过的 entry 还原成裸 upstream。"""
    out: list[WrapResult] = []
    backup_recorded: dict[str, str | None] = {}
    for sel in selections:
        client_key = sel.get("client_key", "")
        config_path = sel.get("config_path", "")
        scope = sel.get("scope", "")
        server_name = sel.get("server_name", "")
        try:
            adapter, entry = _select_one(client_key, config_path, scope, server_name)
            if not entry.is_protected:
                out.append(WrapResult(client_key, config_path, scope, server_name, ok=True, action="skipped"))
                continue
            new_entry = build_unwrapped_entry(
                entry.upstream_command, entry.upstream_args, entry.upstream_env or None
            )
            backup = adapter.write_entry(Path(config_path), scope, server_name, new_entry)
            if config_path not in backup_recorded:
                backup_recorded[config_path] = str(backup) if backup else None
            out.append(
                WrapResult(
                    client_key, config_path, scope, server_name,
                    ok=True, action="unwrapped", backup_path=backup_recorded[config_path],
                )
            )
        except Exception as e:
            out.append(WrapResult(client_key, config_path, scope, server_name, ok=False, action="error", error=str(e)))
    return out


def restore_backup(backup_path: str) -> dict[str, Any]:
    """把一个 .sentinel-backup.<ts> 文件还原回原 config 路径。

    backup 文件名形如 mcp.json.sentinel-backup.1714800000，原文件就是去掉
    `.sentinel-backup.<ts>` 后缀的路径。
    """
    bp = Path(backup_path)
    if not bp.exists():
        raise ValueError(f"备份不存在: {backup_path}")
    name = bp.name
    # 找到 .sentinel-backup. 的位置
    marker = ".sentinel-backup."
    if marker not in name:
        raise ValueError(f"非 sentinel 备份文件: {backup_path}")
    orig_name = name[: name.index(marker)]
    orig_path = bp.parent / orig_name
    # 写当前为新备份再覆盖（双重防护）
    cur_backup: Path | None = None
    if orig_path.exists():
        cur_backup = orig_path.with_suffix(orig_path.suffix + f".sentinel-restore-prev.{int(time.time())}")
        shutil.copy2(orig_path, cur_backup)
    shutil.copy2(bp, orig_path)
    return {
        "ok": True,
        "restored_path": str(orig_path),
        "from_backup": str(bp),
        "previous_backup": str(cur_backup) if cur_backup else None,
    }
