"""sentinel-mcp-desktop — 一键起后端 + 弹窗的桌面入口

设计：
  - 不强依赖 Rust / Tauri / 任何 webkit dev 库
  - 启动 PWA dashboard 子进程（uvicorn 在 8766），等端口就绪
  - 优先级 (1) pywebview 原生窗口（如果 import 得到）
           (2) 系统默认浏览器（webbrowser.open，所有平台都可用）
  - Ctrl+C 时优雅停掉子进程

用法：
    sentinel-mcp-desktop                  # 默认 8766
    sentinel-mcp-desktop --port 9000
    sentinel-mcp-desktop --no-window      # 只起后端，不弹窗（适合 server 部署）

为什么不直接用 Tauri：
  Tauri 跑得起来当然好（更原生），但桌面包构建对系统依赖要求高
  （Linux 要 webkit2gtk-4.1-dev / Windows 要 WebView2 Runtime / Mac 要 Xcode CLT）。
  本入口让用户**装完 pip 包就能用**，不用碰 Rust 工具链，是 Tauri 包之外的轻量退路。
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
import webbrowser


def _wait_for_port(host: str, port: int, timeout_s: float = 15.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _try_pywebview(url: str, title: str) -> bool:
    """尝试用 pywebview 弹原生窗口。装了就用，没装就 False。"""
    try:
        import webview  # pywebview 包名是 webview
    except ImportError:
        return False
    try:
        webview.create_window(title, url, width=1180, height=780)
        webview.start()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[sentinel-mcp-desktop] pywebview 启动失败：{e}（回落到浏览器）", file=sys.stderr)
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sentinel-mcp-desktop",
        description="Sentinel-MCP 桌面入口：自启 dashboard + 弹窗",
    )
    parser.add_argument("--port", type=int, default=int(os.environ.get("SENTINEL_PORT", "8766")))
    parser.add_argument("--host", default=os.environ.get("SENTINEL_HOST", "127.0.0.1"))
    parser.add_argument("--no-window", action="store_true",
                        help="只起后端，不弹窗（适合 server / docker）")
    parser.add_argument("--no-server", action="store_true",
                        help="不启后端，仅打开窗口（如果你已经在另一个进程里跑了 dashboard）")
    args = parser.parse_args(argv)

    url = f"http://{args.host}:{args.port}/"
    server: subprocess.Popen | None = None

    if not args.no_server:
        env = os.environ.copy()
        env["SENTINEL_PORT"] = str(args.port)
        # 把后端的 uvicorn log 直接打到当前 stderr，方便用户看
        server = subprocess.Popen(
            [sys.executable, "-m", "pwa_dashboard.server"],
            env=env,
        )
        print(f"[sentinel-mcp-desktop] 后端启动中 → {url}", file=sys.stderr)
        if not _wait_for_port(args.host, args.port, timeout_s=15.0):
            print("[sentinel-mcp-desktop] 后端 15s 未就绪，请手动检查", file=sys.stderr)
            if server.poll() is None:
                server.terminate()
            return 1
        print("[sentinel-mcp-desktop] 后端就绪 ✓", file=sys.stderr)

    try:
        if args.no_window:
            # server 模式：阻塞等 server 退出
            if server is not None:
                return server.wait()
            return 0

        if not _try_pywebview(url, "Sentinel-MCP"):
            print(f"[sentinel-mcp-desktop] 用默认浏览器打开 {url}", file=sys.stderr)
            webbrowser.open(url)
            # 如果是 server 模式，阻塞等用户 Ctrl+C
            if server is not None:
                try:
                    server.wait()
                except KeyboardInterrupt:
                    pass
        return 0
    finally:
        if server is not None and server.poll() is None:
            server.send_signal(signal.SIGINT)
            try:
                server.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server.terminate()


if __name__ == "__main__":
    sys.exit(main())
