"""一键管理 cloudflared 隧道：让本机 dashboard 暴露给公网（飞书 callback 必需）。

为什么需要：
  飞书 callback / Slack webhook 等需要 https 公网地址。本机 localhost:8766
  打不到。cloudflared tunnel 给一个免费 https://*.trycloudflare.com 域名转
  发到本地，5 秒搞定，不要服务器。

设计：
  - 检测系统是否装了 cloudflared 二进制
  - 启动：fork `cloudflared tunnel --url http://localhost:8766` 子进程
  - 解析它 stderr 里的 https URL（pattern: https://xxx-yyy.trycloudflare.com）
  - 关闭：terminate 子进程
  - 状态：返回当前 tunnel URL / 进程是否活着

线程安全：状态字典 + 锁。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from typing import Any

# trycloudflare URL pattern：subdomain 是 多个由 "-" 拼的英文单词
_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)


class CloudflaredManager:
    """单例式管理 cloudflared 子进程 + tunnel URL。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._url: str | None = None
        self._reader: threading.Thread | None = None
        self._target_port: int = 8766
        self._stderr_buf: list[str] = []

    # ---- 状态 ----

    def status(self) -> dict[str, Any]:
        binary = shutil.which("cloudflared")
        with self._lock:
            running = self._proc is not None and self._proc.poll() is None
            return {
                "binary_path": binary,
                "binary_installed": binary is not None,
                "running": running,
                "url": self._url if running else None,
                "target_port": self._target_port,
                "pid": self._proc.pid if running else None,
                "recent_stderr": self._stderr_buf[-15:],
            }

    # ---- 启停 ----

    def start(self, port: int = 8766, timeout_url: float = 12.0) -> dict[str, Any]:
        """起一个 quick tunnel 转发到 localhost:<port>。等 URL 出现或超时。"""
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return {"ok": False, "reason": "already_running", "url": self._url}
            binary = shutil.which("cloudflared")
            if binary is None:
                return {
                    "ok": False, "reason": "not_installed",
                    "hint": "请先装 cloudflared：\n"
                            "  · macOS: brew install cloudflared\n"
                            "  · Linux: 见 https://pkg.cloudflare.com\n"
                            "  · Windows: scoop install cloudflared",
                }
            self._target_port = port
            self._url = None
            self._stderr_buf = []
            # 不消耗 stdin / 把 stderr 也捕获（cloudflared 把 URL 印到 stderr）
            try:
                self._proc = subprocess.Popen(
                    [binary, "tunnel", "--url", f"http://localhost:{port}", "--no-autoupdate"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1,
                    env={**os.environ, "TERM": "dumb"},
                )
            except Exception as e:
                return {"ok": False, "reason": "spawn_failed", "error": str(e)}

            self._reader = threading.Thread(
                target=self._consume_output, args=(self._proc,),
                daemon=True, name="cloudflared-reader",
            )
            self._reader.start()

        # 等 URL 出现
        deadline = time.time() + timeout_url
        while time.time() < deadline:
            with self._lock:
                if self._url:
                    return {"ok": True, "url": self._url, "pid": self._proc.pid if self._proc else None}
                if self._proc is None or self._proc.poll() is not None:
                    return {
                        "ok": False, "reason": "exited_before_url",
                        "stderr": "\n".join(self._stderr_buf[-15:]),
                    }
            time.sleep(0.2)
        # 没拿到 URL — 但进程可能还活着
        return {"ok": False, "reason": "url_timeout",
                "stderr": "\n".join(self._stderr_buf[-15:])}

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._proc = None
                self._url = None
                return {"ok": True, "reason": "already_stopped"}
            try:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            except Exception as e:
                return {"ok": False, "error": str(e)}
            self._proc = None
            self._url = None
            return {"ok": True}

    # ---- 内部 ----

    def _consume_output(self, proc: subprocess.Popen) -> None:
        if proc.stdout is None:
            return
        for line in proc.stdout:
            line = line.rstrip("\n")
            with self._lock:
                # 限大小，防内存爆
                self._stderr_buf.append(line)
                if len(self._stderr_buf) > 200:
                    del self._stderr_buf[: len(self._stderr_buf) - 200]
                # 找 URL
                if self._url is None:
                    m = _URL_RE.search(line)
                    if m:
                        self._url = m.group(0).lower()


# 模块级单例（dashboard 进程内只允许一个 cloudflared 子进程）
manager = CloudflaredManager()
