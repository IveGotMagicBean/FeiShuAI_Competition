"""runtime_mode：active / passive / off 三态读写 + 损坏文件防呆。

跑：python tests/test_runtime_mode.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sentinel_mcp import runtime_mode as rm


def test_default_when_file_missing():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "missing.json"
        assert rm.read_mode(p) == "active"
        assert rm.is_passive(p) is False
        assert rm.is_off(p) is False


def test_round_trip_passive():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "mode.json"
        rm.write_mode("passive", p)
        assert rm.read_mode(p) == "passive"
        assert rm.is_passive(p) is True
        assert rm.is_off(p) is False


def test_round_trip_off():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "mode.json"
        rm.write_mode("off", p)
        assert rm.read_mode(p) == "off"
        assert rm.is_off(p) is True


def test_round_trip_active():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "mode.json"
        rm.write_mode("active", p)
        assert rm.read_mode(p) == "active"


def test_corrupt_file_returns_default():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "mode.json"
        p.write_text("{ not json")
        assert rm.read_mode(p) == "active"


def test_unknown_mode_value_returns_default():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "mode.json"
        p.write_text('{"mode": "WAT"}')
        assert rm.read_mode(p) == "active"


def test_write_invalid_mode_raises():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "mode.json"
        try:
            rm.write_mode("dangermode", p)  # type: ignore[arg-type]
            raise AssertionError("应抛 ValueError")
        except ValueError as e:
            assert "未知 mode" in str(e)


def test_chmod_to_600():
    import os
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "mode.json"
        rm.write_mode("passive", p)
        assert os.stat(p).st_mode & 0o777 == 0o600


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✓ {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ✗ {t.__name__}  ←  {e!r}")
            failed += 1
    print(f"\n{passed}/{passed+failed} passed")
    sys.exit(0 if failed == 0 else 1)
