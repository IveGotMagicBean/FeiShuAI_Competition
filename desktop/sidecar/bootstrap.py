"""Tauri sidecar 入口：被 PyInstaller 冻成单文件二进制，由 Tauri 原生壳启动。

为什么要新写这个 bootstrap，而不直接打包 pwa_dashboard.server：
  PyInstaller 不喜欢隐式 import 和 entry point。这里显式把
  pwa_dashboard.server.main() 拉出来，PyInstaller 一看就知道入口在哪。
  同时把模板/静态资源路径注入到 sys._MEIPASS 解出的 bundle 内。

环境变量：
  SENTINEL_PORT      监听端口（默认 8766，被 Tauri 启动时设置）
  SENTINEL_DB        审计 SQLite 路径（默认 ~/.sentinel-mcp/sentinel.db）
  SENTINEL_DESKTOP=1 标记当前是被 desktop 壳拉起的（dashboard 可以借此调整 UI）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _setup_paths() -> None:
    """PyInstaller --onefile 解压临时目录里把仓库结构暴露给 import。"""
    if hasattr(sys, "_MEIPASS"):
        # 冻结模式：bundle 根在 sys._MEIPASS
        bundle_root = Path(sys._MEIPASS)  # type: ignore[attr-defined]
        # 让 from pwa_dashboard.server import main / from guard ... 都能解析
        for sub in ("", "0427_test01"):
            p = bundle_root / sub if sub else bundle_root
            if p.exists() and str(p) not in sys.path:
                sys.path.insert(0, str(p))
    else:
        # 开发模式：直接从 desktop/sidecar 跑
        repo_root = Path(__file__).resolve().parent.parent.parent
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))


def _default_db() -> str:
    """统一的「桌面用户」审计 DB 位置：~/.sentinel-mcp/sentinel.db。
    这样无论 .dmg 装到哪里，审计数据都在用户主目录里好找。"""
    home = Path.home() / ".sentinel-mcp"
    home.mkdir(parents=True, exist_ok=True)
    return str(home / "sentinel.db")


def main() -> int:
    _setup_paths()
    os.environ.setdefault("SENTINEL_PORT", "8766")
    os.environ.setdefault("SENTINEL_DB", _default_db())
    os.environ.setdefault("SENTINEL_DESKTOP", "1")

    # 导入要在 _setup_paths 之后；冻结模式下这些 import 会被 PyInstaller 静态分析到
    from pwa_dashboard.server import main as server_main  # noqa: E402

    server_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
