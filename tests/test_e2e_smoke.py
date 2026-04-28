"""端到端冒烟：把 sentinel-mcp 当 stdio 子进程跑，用 `cat` 当假上游。

cat 会把收到的所有 stdin 原样回显到 stdout，所以：
- ALLOW 路径：proxy 把消息转给 cat → cat 回显 → proxy 转给我们
- DENY 路径：proxy 不转给 cat，直接给我们写一个 error 帧
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent


async def main() -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{_PROJECT}:" + env.get("PYTHONPATH", "")

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m", "sentinel_mcp.cli", "wrap",
        "--config", str(_PROJECT / "config" / "policies.yaml"),
        "--audit-db", "/tmp/sentinel_e2e_audit.db",
        "--", "cat",
        cwd=str(_PROJECT),
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdin and proc.stdout and proc.stderr

    async def send(msg: dict) -> None:
        proc.stdin.write((json.dumps(msg) + "\n").encode())
        await proc.stdin.drain()

    async def recv(timeout: float = 5.0) -> dict:
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
        return json.loads(line.decode())

    failures = 0

    # 1) DENY: 读 ~/.ssh/id_rsa
    await send({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "read_file", "arguments": {"path": "~/.ssh/id_rsa"}},
    })
    resp = await recv()
    if resp.get("error", {}).get("data", {}).get("decision") == "deny":
        print("  ✓ DENY ~/.ssh/id_rsa  →  proxy 直接返 error")
    else:
        print(f"  ✗ DENY ~/.ssh/id_rsa  →  期望 error.deny, 实际 {resp}")
        failures += 1

    # 2) DENY: shell rm -rf
    await send({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "shell_exec", "arguments": {"command": "rm -rf /tmp"}},
    })
    resp = await recv()
    if resp.get("error", {}).get("data", {}).get("decision") == "deny":
        print("  ✓ DENY rm -rf  →  proxy 直接返 error")
    else:
        print(f"  ✗ DENY rm -rf  →  {resp}")
        failures += 1

    # 3) ALLOW: 安全路径，cat 回显
    safe_msg = {
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "read_file", "arguments": {"path": "/tmp/some_safe_demo.txt"}},
    }
    await send(safe_msg)
    resp = await recv()
    if resp.get("id") == 3 and resp.get("method") == "tools/call":
        print("  ✓ ALLOW /tmp/...  →  转给上游 cat 后回显")
    else:
        print(f"  ✗ ALLOW /tmp/...  →  {resp}")
        failures += 1

    # 4) PASSTHROUGH: 非 tools/call
    await send({"jsonrpc": "2.0", "id": 4, "method": "initialize", "params": {}})
    resp = await recv()
    if resp.get("id") == 4 and resp.get("method") == "initialize":
        print("  ✓ PASSTHROUGH initialize  →  转给上游 cat 后回显")
    else:
        print(f"  ✗ PASSTHROUGH initialize  →  {resp}")
        failures += 1

    proc.stdin.close()
    try:
        await asyncio.wait_for(proc.wait(), timeout=3)
    except asyncio.TimeoutError:
        proc.terminate()
        await proc.wait()

    total = 4
    print()
    print(f"{total - failures}/{total} passed (e2e)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
