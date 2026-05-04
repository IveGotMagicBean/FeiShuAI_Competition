"""运行时模式配置：active / passive / off。

为什么要单独一个文件而不是塞进 config.py：
  这是「**运行中可热更新**」的状态，不是启动配置。dashboard 一键切换 → 写文件
  → 下一次 proxy 检查时立刻生效，全程不重启。所以独立成模块、走独立的 JSON
  文件 (~/.sentinel-mcp/mode.json)，避免和 lark_config.json 等混淆。

模式定义：
  - "active"  ：默认。按规则拦截 / ASK_USER（v0.2 行为）
  - "passive" ：所有调用一律 allow，但全部记录到审计 DB 供事后审阅。
                适合「我新接 AI 工具，先观察一周再决定开拦截」的引入场景。
  - "off"     ：完全透明转发，连记录都不做。基本只用于排查 Sentinel 自己的 bug。

线程安全：每次 read 直接 stat + read，不缓存。频率每个工具调用 1 次（μs 级），
不构成性能瓶颈，但避免了缓存一致性问题。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

ModeName = Literal["active", "passive", "off"]
DEFAULT_MODE: ModeName = "active"

DEFAULT_PATH = Path(os.environ.get(
    "SENTINEL_MODE_FILE", str(Path.home() / ".sentinel-mcp" / "mode.json")
))


def read_mode(path: Path = DEFAULT_PATH) -> ModeName:
    """读取当前运行时模式。文件不存在 / 损坏 → 默认 'active' 防呆。"""
    if not path.exists():
        return DEFAULT_MODE
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return DEFAULT_MODE
    m = (data.get("mode") or "").lower()
    if m in ("active", "passive", "off"):
        return m  # type: ignore[return-value]
    return DEFAULT_MODE


def write_mode(mode: ModeName, path: Path = DEFAULT_PATH) -> Path:
    """写运行时模式。invalid 名字 → ValueError。"""
    if mode not in ("active", "passive", "off"):
        raise ValueError(f"未知 mode: {mode}（必须是 active / passive / off）")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump({"mode": mode}, f)
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def is_passive(path: Path = DEFAULT_PATH) -> bool:
    """proxy 决策点便捷调用：是否处于 passive 模式（一律放行 + 记录）。"""
    return read_mode(path) == "passive"


def is_off(path: Path = DEFAULT_PATH) -> bool:
    """proxy 决策点便捷调用：是否完全关闭（透明转发，不记录）。"""
    return read_mode(path) == "off"
