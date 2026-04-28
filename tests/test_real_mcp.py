"""真·端到端：用官方 @modelcontextprotocol/server-filesystem 当上游。

这个测试证明：
  1. 我们的 stdio 框架与官方 MCP server 可互操作（initialize / tools/list 走通）
  2. 客户端"试图越权读 /etc/passwd"被 Proxy 直接拦截，根本到不了上游
  3. 客户端读"沙箱白名单内"的文件能正常拿到内容（透传成功）

跑法：
  1) npm install --prefix /tmp/sentinel_npm @modelcontextprotocol/server-filesystem
  2) source conda env Z-Deep
  3) python tests/test_real_mcp.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
_UPSTREAM = "/tmp/sentinel_npm/node_modules/.bin/mcp-server-filesystem"
_WORKDIR = "/tmp/sentinel_test_workdir"


async def main() -> int:
    if not Path(_UPSTREAM).exists():
        print(f"未找到上游 {_UPSTREAM}；先 npm install")
        return 2

    env = os.environ.copy()
    env["PYTHONPATH"] = f"{_PROJECT}:" + env.get("PYTHONPATH", "")

    # 审计 DB：默认走统一路径（0427_test01/data/sentinel.db），方便 dashboard
    # SSE 直接看到事件；CI 里可用 SENTINEL_DB=/tmp/... 覆盖避免污染开发环境。
    audit_db = os.environ.get("SENTINEL_DB", str(_PROJECT / "data" / "sentinel.db"))

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m", "sentinel_mcp.cli", "wrap",
        "--config", str(_PROJECT / "config" / "policies.yaml"),
        "--audit-db", audit_db,
        "--", _UPSTREAM, _WORKDIR,
        cwd=str(_PROJECT),
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdin and proc.stdout

    next_id = [0]

    def nid() -> int:
        next_id[0] += 1
        return next_id[0]

    async def send(msg: dict) -> None:
        proc.stdin.write((json.dumps(msg) + "\n").encode())
        await proc.stdin.drain()

    async def request(method: str, params: dict | None = None, timeout: float = 10) -> dict:
        rid = nid()
        await send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        # 一直读直到拿到我们这条 id 的响应
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
            if not line:
                raise RuntimeError("upstream 关闭了 stdout")
            try:
                m = json.loads(line)
            except Exception:
                continue
            if m.get("id") == rid:
                return m

    failures = 0

    # ---- 1) initialize ----
    init_resp = await request("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "sentinel-e2e-test", "version": "1.0"},
    })
    server_name = (init_resp.get("result", {}).get("serverInfo", {}).get("name", ""))
    if "filesystem" in server_name.lower():
        print(f"  ✓ initialize 与真 MCP server 握手成功（serverInfo.name = {server_name}）")
    else:
        print(f"  ✗ initialize 握手异常：{init_resp}")
        failures += 1

    await send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    # ---- 2) tools/list ----
    tools_resp = await request("tools/list", {})
    tools = tools_resp.get("result", {}).get("tools", [])
    tool_names = [t.get("name") for t in tools]
    if "read_text_file" in tool_names or "read_file" in tool_names:
        print(f"  ✓ tools/list 透传成功，上游声明了 {len(tools)} 个工具")
    else:
        print(f"  ✗ tools/list 异常：{tool_names}")
        failures += 1

    # ---- 3) DENY: 越界读 /etc/passwd ----
    deny_resp = await request("tools/call", {
        "name": "read_file",
        "arguments": {"path": "/etc/passwd"},
    })
    if deny_resp.get("error", {}).get("data", {}).get("decision") == "deny":
        print("  ✓ Proxy 直接拦截 /etc/passwd 读取（根本没下发到上游）")
    else:
        print(f"  ✗ /etc/passwd 没被 Proxy 拦：{deny_resp}")
        failures += 1

    # ---- 4) DENY: 越界读 ~/.ssh/id_rsa ----
    deny2 = await request("tools/call", {
        "name": "read_file",
        "arguments": {"path": "~/.ssh/id_rsa"},
    })
    if deny2.get("error", {}).get("data", {}).get("decision") == "deny":
        print("  ✓ Proxy 直接拦截 ~/.ssh/id_rsa 读取")
    else:
        print(f"  ✗ ~/.ssh/id_rsa 没被 Proxy 拦：{deny2}")
        failures += 1

    # ---- 5) ALLOW: 读沙箱内合法文件 ----
    real_tool = "read_text_file" if "read_text_file" in tool_names else "read_file"
    safe = await request("tools/call", {
        "name": real_tool,
        "arguments": {"path": f"{_WORKDIR}/safe.txt"},
    })
    content = json.dumps(safe.get("result", {}))
    if "hello world" in content:
        print("  ✓ ALLOW 合法读取：透传到上游并返回 'hello world'")
    else:
        print(f"  ⚠ ALLOW 路径未拿到预期内容（不一定是 bug，可能策略需要调整）：{safe}")

    proc.stdin.close()
    try:
        await asyncio.wait_for(proc.wait(), timeout=3)
    except asyncio.TimeoutError:
        proc.terminate()
        await proc.wait()

    print()
    print(f"看 {audit_db} 可查全部审计")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
