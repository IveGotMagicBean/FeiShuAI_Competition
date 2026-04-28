"""sentinel-mcp-desktop 入口测试

不真启 server，只测：
  1. CLI argparse 接受预期参数
  2. _wait_for_port 立刻命中已 listen 的端口（no-op 路径）
  3. _wait_for_port 在端口不通时按时返回 False
"""

from __future__ import annotations

import socket
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
sys.path.insert(0, str(_PROJECT))

from sentinel_mcp.desktop import _wait_for_port, main


def assert_eq(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"[{label}] expected {expected!r}, got {actual!r}")


def assert_truthy(actual, label: str) -> None:
    if not actual:
        raise AssertionError(f"[{label}] expected truthy, got {actual!r}")


def test_wait_for_port_hit() -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.listen(1)
    try:
        ok = _wait_for_port("127.0.0.1", port, timeout_s=2.0)
        assert_truthy(ok, "should detect listening port")
    finally:
        sock.close()


def test_wait_for_port_miss() -> None:
    """0 是非法端口；timeout=0.3 让测试快速通过失败分支"""
    start = time.time()
    ok = _wait_for_port("127.0.0.1", 1, timeout_s=0.3)
    elapsed = time.time() - start
    assert_eq(ok, False, "should fail on port 1")
    assert_truthy(elapsed < 1.0, "should respect 0.3s timeout")


def test_help_does_not_crash() -> None:
    """--help 会 SystemExit(0)；只要不抛别的异常就算过"""
    try:
        main(["--help"])
    except SystemExit as e:
        assert_eq(e.code, 0, "help exits 0")


def test_unknown_arg_rejected() -> None:
    try:
        main(["--definitely-not-a-flag"])
    except SystemExit as e:
        assert_truthy(e.code != 0, "bad arg exits non-zero")


def main_runner() -> int:
    cases = [
        ("wait_for_port hit",        test_wait_for_port_hit),
        ("wait_for_port miss",       test_wait_for_port_miss),
        ("--help exits 0",           test_help_does_not_crash),
        ("unknown arg rejected",     test_unknown_arg_rejected),
    ]
    failures = 0
    for label, fn in cases:
        try:
            fn()
            print(f"  ✓ {label}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  ✗ {label}: {exc}")
    print()
    print(f"{len(cases) - failures}/{len(cases)} passed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main_runner())
