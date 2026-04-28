"""模拟工具集合：用于演示 Agent 调用本地资源的真实场景。

为了在不依赖真实 LLM 的情况下也能跑红蓝对抗 Demo，这里把"工具"实现成普通函数。
未防护版本（unprotected_agent.py）直接调用；防护版本（protected_agent.py）会用
Guard.protected 装饰器包一层。
"""

from __future__ import annotations

import os
import socket
import urllib.request
from pathlib import Path


def read_file(path: str) -> str:
    """读取文件内容。"""
    p = Path(path).expanduser()
    return p.read_text(errors="replace")


def write_file(path: str, content: str) -> str:
    """写入文件内容。"""
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"已写入 {len(content)} 字符到 {p}"


def list_dir(path: str) -> list[str]:
    """列出目录内容。"""
    p = Path(path).expanduser()
    return sorted([str(x.name) for x in p.iterdir()])


def http_request(url: str, data: str = "") -> str:
    """发起一个 HTTP 请求（GET 或带 data 时为 POST）。

    为了避免演示时真的发包到外部，这里只在 dry_run=False 模式下真发。
    默认 dry_run=True，仅返回模拟响应。
    """
    if os.environ.get("AGENT_GUARD_HTTP_DRYRUN", "1") == "1":
        return f"[DRY-RUN] 将发起请求 → {url}（payload {len(data)} bytes）"
    try:
        req = urllib.request.Request(url, data=data.encode() if data else None)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.read(2048).decode("utf-8", errors="replace")
    except Exception as e:
        return f"请求失败：{e}"


def shell_exec(cmd: str) -> str:
    """执行 shell 命令（演示用，真跑请谨慎）。"""
    if os.environ.get("AGENT_GUARD_SHELL_DRYRUN", "1") == "1":
        return f"[DRY-RUN] 将执行命令 → {cmd}"
    import subprocess

    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
    return proc.stdout + proc.stderr


def clipboard_write(content: str) -> str:
    """模拟写入剪贴板。"""
    return f"[CLIPBOARD] 已写入 {len(content)} 字符"


def dns_query(name: str) -> str:
    """模拟 DNS 查询，常被用作隐蔽数据外泄通道。"""
    try:
        return socket.gethostbyname(name) if os.environ.get("AGENT_GUARD_DNS_DRYRUN", "1") != "1" else f"[DRY-RUN] 将查询 {name}"
    except Exception as e:
        return f"DNS 查询失败：{e}"
