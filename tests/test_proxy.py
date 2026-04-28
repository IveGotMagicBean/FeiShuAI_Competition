"""Sentinel-MCP Proxy 单元测试

不依赖 pytest，直接 `python tests/test_proxy.py` 跑。
覆盖 _handle_client_msg 的四种路径：ALLOW / DENY / REDACT / 非 tools-call 透传。
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import tempfile
from pathlib import Path

# 让本测试无论从哪个 cwd 跑都能 import sentinel_mcp + guard
_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
sys.path.insert(0, str(_PROJECT))

from guard import Guard
from sentinel_mcp.proxy import MCPProxy

# ---------- 测试基础设施 -------------------------------------------

def make_proxy() -> tuple[MCPProxy, list[bytes]]:
    """构造代理 + 捕获 stdout 写入的容器。"""
    cfg = _PROJECT / "config" / "policies.yaml"
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    guard = Guard.from_yaml(str(cfg), audit_db_path=db_path)

    proxy = MCPProxy(upstream_cmd=["true"], guard=guard, log_stream=io.StringIO())

    captured: list[bytes] = []

    async def fake_write(data: bytes) -> None:
        captured.append(data)

    proxy._write_stdout = fake_write  # type: ignore[assignment]
    return proxy, captured


def assert_eq(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"[{label}] expected {expected!r}, got {actual!r}")


def assert_truthy(actual, label: str) -> None:
    if not actual:
        raise AssertionError(f"[{label}] expected truthy, got {actual!r}")


# ---------- 测试用例 ------------------------------------------------

async def test_passthrough_non_tool_call() -> None:
    proxy, _ = make_proxy()
    msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    result = await proxy._handle_client_msg(msg)
    assert_eq(result, msg, "passthrough non tools/call")


async def test_allow_safe_read() -> None:
    proxy, captured = make_proxy()
    msg = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "read_file", "arguments": {"path": "/tmp/some_safe.txt"}},
    }
    result = await proxy._handle_client_msg(msg)
    assert_truthy(result is not None, "allow: forwarded")
    assert_eq(captured, [], "allow: nothing written to stdout")


async def test_deny_ssh_read() -> None:
    proxy, captured = make_proxy()
    msg = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "read_file", "arguments": {"path": "~/.ssh/id_rsa"}},
    }
    result = await proxy._handle_client_msg(msg)
    assert_eq(result, None, "deny: not forwarded")
    assert_truthy(len(captured) == 1, "deny: one error frame written")
    err = json.loads(captured[0].decode("utf-8").strip())
    assert_eq(err["jsonrpc"], "2.0", "deny: jsonrpc field")
    assert_eq(err["id"], 3, "deny: id echoed")
    assert_truthy("error" in err, "deny: error field present")
    assert_eq(err["error"]["data"]["decision"], "deny", "deny: decision in data")


async def test_deny_shell_rm_rf() -> None:
    proxy, captured = make_proxy()
    msg = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "shell_exec", "arguments": {"command": "rm -rf /tmp"}},
    }
    result = await proxy._handle_client_msg(msg)
    assert_eq(result, None, "shell deny: not forwarded")
    assert_truthy(len(captured) == 1, "shell deny: error frame")


async def test_deny_etc_passwd() -> None:
    proxy, captured = make_proxy()
    msg = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {"name": "read_file", "arguments": {"path": "/etc/passwd"}},
    }
    result = await proxy._handle_client_msg(msg)
    assert_eq(result, None, "etc/passwd: blocked")


# ---------- 主入口 --------------------------------------------------

async def main() -> int:
    cases = [
        ("non-tool-call passthrough", test_passthrough_non_tool_call),
        ("ALLOW safe /tmp read",       test_allow_safe_read),
        ("DENY ~/.ssh/id_rsa read",    test_deny_ssh_read),
        ("DENY rm -rf shell",          test_deny_shell_rm_rf),
        ("DENY /etc/passwd read",      test_deny_etc_passwd),
    ]
    failures = 0
    for label, fn in cases:
        try:
            await fn()
            print(f"  ✓ {label}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  ✗ {label}: {exc}")
    print()
    print(f"{len(cases) - failures}/{len(cases)} passed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
