"""ASK_USER 异步审批闭环 e2e 测试

覆盖三条主路径 + 一条代理集成路径：

    1. approve  → callback 返回 True → Guard 落 ALLOW
    2. deny     → callback 返回 False → Guard 落 DENY
    3. timeout  → callback 阻塞过期 → row='expired' + Guard 落 DENY
    4. proxy    → MCPProxy._handle_client_msg 拿到 write_file 调用，
                  审批通过后 forwarded 等于原 msg，整个 await 不卡死 event loop

运行：
    cd 0427_test01
    python tests/test_ask_user_e2e.py
"""

from __future__ import annotations

import asyncio
import io
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

# 让本测试无论从哪个 cwd 跑都能 import sentinel_mcp + guard
_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
sys.path.insert(0, str(_PROJECT))

from guard import Decision, Guard, ToolCall
from sentinel_mcp.approvals import PendingDecisions
from sentinel_mcp.proxy import MCPProxy

# ---------- 基础设施 -----------------------------------------------

CFG = _PROJECT / "config" / "policies.yaml"


def _fresh_db() -> str:
    """每个 case 一个独立 DB，避免 cross-case 状态污染。"""
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    return f.name


def _make_guard(db_path: str, ask_timeout: float) -> tuple[Guard, PendingDecisions]:
    approvals = PendingDecisions(db_path)
    callback = approvals.make_callback(timeout_seconds=ask_timeout)
    guard = Guard.from_yaml(str(CFG), audit_db_path=db_path, ask_user_callback=callback)
    return guard, approvals


def _wait_pending_id(approvals: PendingDecisions, deadline_s: float = 3.0) -> str:
    """等 SQLite 里出现一条 pending 行，返回它的 id。"""
    end = time.time() + deadline_s
    while time.time() < end:
        rows = approvals.list_pending(limit=1)
        if rows:
            return rows[0]["id"]
        time.sleep(0.05)
    raise TimeoutError("waited > 3s but no pending row appeared")


def assert_eq(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"[{label}] expected {expected!r}, got {actual!r}")


def assert_truthy(actual, label: str) -> None:
    if not actual:
        raise AssertionError(f"[{label}] expected truthy, got {actual!r}")


# ---------- case 1：approve ----------------------------------------

def test_approve_flow() -> None:
    db = _fresh_db()
    guard, approvals = _make_guard(db, ask_timeout=5.0)

    call = ToolCall(tool_name="write_file", args={"path": "/tmp/sentinel_e2e_approve.txt", "content": "hi"})

    box: dict = {}

    def runner():
        box["result"] = guard.check_tool_call(call)

    t = threading.Thread(target=runner, daemon=True)
    t.start()

    pid = _wait_pending_id(approvals)
    ok = approvals.decide(pid, approved=True, by="test")
    assert_truthy(ok, "approve: decide returned True")

    t.join(timeout=2.0)
    assert_truthy(not t.is_alive(), "approve: worker thread joined")
    res = box["result"]
    assert_eq(res.decision, Decision.ALLOW, "approve: final decision")

    # SQLite 里那条记录应该已经是 approved
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT status, decided_by FROM pending_decisions WHERE id=?", (pid,)).fetchone()
    assert_eq(row[0], "approved", "approve: row status")
    assert_eq(row[1], "test", "approve: decided_by")


# ---------- case 2：deny -------------------------------------------

def test_deny_flow() -> None:
    db = _fresh_db()
    guard, approvals = _make_guard(db, ask_timeout=5.0)

    call = ToolCall(tool_name="write_file", args={"path": "/tmp/sentinel_e2e_deny.txt", "content": "hi"})

    box: dict = {}

    def runner():
        box["result"] = guard.check_tool_call(call)

    t = threading.Thread(target=runner, daemon=True)
    t.start()

    pid = _wait_pending_id(approvals)
    approvals.decide(pid, approved=False, by="test")

    t.join(timeout=2.0)
    res = box["result"]
    assert_eq(res.decision, Decision.DENY, "deny: final decision")
    assert_truthy("user_denied" in res.triggered_rules, "deny: rule tagged")


# ---------- case 3：timeout ----------------------------------------

def test_timeout_flow() -> None:
    db = _fresh_db()
    # 给一个很短的超时
    guard, approvals = _make_guard(db, ask_timeout=0.6)

    call = ToolCall(tool_name="write_file", args={"path": "/tmp/sentinel_e2e_timeout.txt"})

    box: dict = {}

    def runner():
        box["result"] = guard.check_tool_call(call)

    t = threading.Thread(target=runner, daemon=True)
    t.start()

    pid = _wait_pending_id(approvals)
    # 故意不 decide，让它过期

    t.join(timeout=2.5)
    assert_truthy(not t.is_alive(), "timeout: worker thread joined")
    res = box["result"]
    assert_eq(res.decision, Decision.DENY, "timeout: final decision is DENY")
    assert_truthy("user_denied" in res.triggered_rules, "timeout: rule tagged")

    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT status, decided_by FROM pending_decisions WHERE id=?", (pid,)).fetchone()
    assert_eq(row[0], "expired", "timeout: row status expired")
    assert_eq(row[1], "auto-expire", "timeout: decided_by auto-expire")


# ---------- case 4：proxy 端到端 -----------------------------------

async def test_proxy_handle_msg_with_approval() -> None:
    """证明 proxy._handle_client_msg 在等审批时不会阻塞 event loop，
    并且批准后能正确把原 msg forward 给上游。"""
    db = _fresh_db()
    guard, approvals = _make_guard(db, ask_timeout=5.0)
    proxy = MCPProxy(upstream_cmd=["true"], guard=guard, log_stream=io.StringIO())

    # 我们不真的启动 upstream / stdout，只测 _handle_client_msg 的返回值
    captured: list[bytes] = []
    async def fake_write(data: bytes) -> None:
        captured.append(data)
    proxy._write_stdout = fake_write  # type: ignore[assignment]

    msg = {
        "jsonrpc": "2.0",
        "id": 99,
        "method": "tools/call",
        "params": {
            "name": "write_file",
            "arguments": {"path": "/tmp/sentinel_e2e_proxy.txt", "content": "x"},
        },
    }

    handle_task = asyncio.create_task(proxy._handle_client_msg(msg))

    # event loop 应该仍然活着 — 我们就在协程里轮询 SQLite 等 pending 出现
    pid = None
    for _ in range(60):  # 最多 3s
        rows = approvals.list_pending(limit=1)
        if rows:
            pid = rows[0]["id"]
            break
        await asyncio.sleep(0.05)
    assert_truthy(pid is not None, "proxy: pending appeared while event loop alive")

    approvals.decide(pid, approved=True, by="test-proxy")

    forwarded = await asyncio.wait_for(handle_task, timeout=2.0)
    assert_eq(forwarded, msg, "proxy: forwarded msg == original msg")
    assert_eq(captured, [], "proxy: nothing written to stdout (would only on DENY)")


async def test_proxy_handle_msg_with_denial() -> None:
    """审批拒绝时，proxy 应该返回 None 并往 stdout 写 -32000 error。"""
    db = _fresh_db()
    guard, approvals = _make_guard(db, ask_timeout=5.0)
    proxy = MCPProxy(upstream_cmd=["true"], guard=guard, log_stream=io.StringIO())

    captured: list[bytes] = []
    async def fake_write(data: bytes) -> None:
        captured.append(data)
    proxy._write_stdout = fake_write  # type: ignore[assignment]

    msg = {
        "jsonrpc": "2.0",
        "id": 100,
        "method": "tools/call",
        "params": {
            "name": "write_file",
            "arguments": {"path": "/tmp/sentinel_e2e_proxy_deny.txt"},
        },
    }

    import json

    handle_task = asyncio.create_task(proxy._handle_client_msg(msg))
    pid = None
    for _ in range(60):
        rows = approvals.list_pending(limit=1)
        if rows:
            pid = rows[0]["id"]
            break
        await asyncio.sleep(0.05)
    assert_truthy(pid is not None, "proxy-deny: pending appeared")

    approvals.decide(pid, approved=False, by="test-proxy")

    forwarded = await asyncio.wait_for(handle_task, timeout=2.0)
    assert_eq(forwarded, None, "proxy-deny: not forwarded")
    assert_eq(len(captured), 1, "proxy-deny: one error frame written")
    err = json.loads(captured[0].decode("utf-8").strip())
    assert_eq(err["jsonrpc"], "2.0", "proxy-deny: jsonrpc field")
    assert_eq(err["id"], 100, "proxy-deny: id echoed")
    assert_truthy("error" in err, "proxy-deny: error field present")


# ---------- 主入口 -------------------------------------------------

async def main() -> int:
    sync_cases = [
        ("approve flow",           test_approve_flow),
        ("deny flow",              test_deny_flow),
        ("timeout flow",           test_timeout_flow),
    ]
    async_cases = [
        ("proxy approve handoff",  test_proxy_handle_msg_with_approval),
        ("proxy deny handoff",     test_proxy_handle_msg_with_denial),
    ]
    failures = 0
    for label, fn in sync_cases:
        try:
            fn()
            print(f"  ✓ {label}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  ✗ {label}: {exc}")
    for label, fn in async_cases:
        try:
            await fn()
            print(f"  ✓ {label}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  ✗ {label}: {exc}")
    total = len(sync_cases) + len(async_cases)
    print()
    print(f"{total - failures}/{total} passed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
