"""首次启动准备：把 `sentinel-mcp` 命令装到 ~/.local/bin/。

为什么需要：
  我们 wrap MCP server config 时往 client config 里写了 `command: sentinel-mcp`。
  这要求用户 PATH 上能找到 sentinel-mcp 命令。
    - pip 装：自动有（pyproject 的 entry_points）
    - AppImage 装：AppImage 内部有 `sentinel-mcp`，但用户 PATH 不知道，得装个 shim
    - dev 模式：通常已经 `pip install -e .` 过，PATH 上有

策略（idempotent，每次启动都跑，无副作用）：
  1. 已有 `sentinel-mcp` 在 PATH 且不是我们装的 shim → 啥也不做（用户已自己处理）
  2. 否则：往 ~/.local/bin/sentinel-mcp 写 shim 脚本
       - AppImage 环境（`$APPIMAGE` set）→ shim 调 `$APPIMAGE --internal-mcp "$@"`
       - 否则 → shim 调 `python3 -m sentinel_mcp.cli "$@"`
  3. 提示用户把 ~/.local/bin 加进 PATH（macOS / Linux 默认就有）
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

SHIM_DIR = Path.home() / ".local" / "bin"
SHIM_PATH = SHIM_DIR / "sentinel-mcp"

SHIM_MARKER = "# sentinel-mcp-shim v1"  # 用来识别「这是我们装的 shim」防误覆盖


def _read_shim_target(path: Path) -> str | None:
    """读现有 shim 的目标 cmd（用于幂等检测）。不是我们装的 → None。"""
    if not path.exists() or not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if SHIM_MARKER not in text:
        return None  # 第三方文件，别动
    # 提取 exec 行的目标 — 简单 grep
    for line in text.splitlines():
        if line.startswith("exec "):
            return line.partition("exec ")[2].strip()
    return None


def _shim_body(target_cmd: str, prefix_args: list[str]) -> str:
    """生成 shim bash 脚本。"""
    args_quoted = " ".join(f'"{a}"' for a in prefix_args)
    if args_quoted:
        args_quoted = " " + args_quoted
    return (
        "#!/bin/bash\n"
        f"{SHIM_MARKER}\n"
        "# 自动生成。Sentinel-MCP dashboard 每次启动会刷新此文件。\n"
        "# 删除此文件 = 取消 shim。\n"
        f'exec "{target_cmd}"{args_quoted} "$@"\n'
    )


def _decide_target() -> tuple[str, list[str], str]:
    """决定 shim 应该 exec 什么。返回 (cmd, prefix_args, source_label)。"""
    # 1. AppImage 环境
    appimage = os.environ.get("APPIMAGE")
    if appimage and Path(appimage).exists():
        return appimage, ["--internal-mcp"], "appimage"
    # 2. PATH 上已有真实 sentinel-mcp（且不是我们装的 shim 自己）
    found = shutil.which("sentinel-mcp")
    if found and Path(found).resolve() != SHIM_PATH.resolve():
        # 已经 pip 装好，不需要 shim
        return found, [], "path"
    # 3. 兜底：python -m sentinel_mcp.cli（dev 模式 / 没装 entry point）
    py = shutil.which("python3") or shutil.which("python") or "python3"
    return py, ["-m", "sentinel_mcp.cli"], "python-module"


def ensure_shim(force: bool = False, log=print) -> dict:
    """幂等：保证 ~/.local/bin/sentinel-mcp 存在且指向正确目标。

    返回:
      {action: 'created'|'updated'|'skipped'|'foreign-file', path, target, source, in_path}
    """
    target_cmd, prefix_args, source = _decide_target()
    in_path = SHIM_DIR.as_posix() in os.environ.get("PATH", "").split(":")

    # 已有别人的文件 — 别覆盖
    existing_target = _read_shim_target(SHIM_PATH) if SHIM_PATH.exists() else None
    if SHIM_PATH.exists() and existing_target is None:
        return {
            "action": "foreign-file",
            "path": str(SHIM_PATH),
            "target": target_cmd,
            "source": source,
            "in_path": in_path,
            "note": "~/.local/bin/sentinel-mcp 不是 sentinel 的 shim — 已跳过，避免误覆盖",
        }

    # source==path 且现有 shim 也指向同一 cmd → 没必要再写
    if source == "path" and not force:
        # 用户的 PATH 里已经有真实 sentinel-mcp，shim 不必要
        # 如果之前装过 shim 也保留（指向同一 cmd 不冲突）
        if existing_target == target_cmd:
            return {"action": "skipped", "path": str(SHIM_PATH), "target": target_cmd,
                    "source": source, "in_path": in_path,
                    "note": "PATH 上已有 sentinel-mcp，shim 与之一致，无需操作"}

    # 否则写 / 更新 shim
    SHIM_DIR.mkdir(parents=True, exist_ok=True)
    body = _shim_body(target_cmd, prefix_args)
    action = "updated" if SHIM_PATH.exists() else "created"
    SHIM_PATH.write_text(body, encoding="utf-8")
    SHIM_PATH.chmod(0o755)

    result = {
        "action": action,
        "path": str(SHIM_PATH),
        "target": target_cmd + (" " + " ".join(prefix_args) if prefix_args else ""),
        "source": source,
        "in_path": in_path,
    }
    if not in_path:
        result["warning"] = (
            "~/.local/bin 不在 $PATH 里。请加一行到 ~/.bashrc 或 ~/.zshrc："
            ' export PATH="$HOME/.local/bin:$PATH"'
        )
    log(f"[bootstrap] shim {action} → {result['target']}  (source={source}, in_path={in_path})")
    return result


if __name__ == "__main__":
    # 命令行手动跑
    import json as _j
    print(_j.dumps(ensure_shim(force=True), ensure_ascii=False, indent=2))
