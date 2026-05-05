"""auto_decisions：增删查 + lookup 路径 + 文件级原子。

跑：python tests/test_auto_decisions.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sentinel_mcp import auto_decisions as ad


def test_empty_when_file_missing():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "rules.json"
        assert ad.list_rules(p) == []
        assert ad.lookup_decision("anything", p) is None


def test_add_then_lookup():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "rules.json"
        rule = ad.add_rule("write_file", "allow", path=p)
        assert rule["tool_name"] == "write_file"
        assert rule["decision"] == "allow"
        assert ad.lookup_decision("write_file", p) == "allow"
        assert ad.lookup_decision("read_file", p) is None


def test_add_overwrites_same_tool():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "rules.json"
        ad.add_rule("rm", "allow", path=p)
        ad.add_rule("rm", "deny", path=p)
        rules = ad.list_rules(p)
        assert len(rules) == 1
        assert rules[0]["decision"] == "deny"


def test_delete_rule():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "rules.json"
        ad.add_rule("a", "allow", path=p)
        ad.add_rule("b", "deny", path=p)
        assert ad.delete_rule("a", p) is True
        assert ad.delete_rule("nonexistent", p) is False
        assert {r["tool_name"] for r in ad.list_rules(p)} == {"b"}


def test_invalid_decision_raises():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "rules.json"
        try:
            ad.add_rule("x", "maybe", path=p)  # type: ignore[arg-type]
            raise AssertionError()
        except ValueError as e:
            assert "decision 必须是" in str(e)


def test_empty_tool_name_raises():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "rules.json"
        try:
            ad.add_rule("", "allow", path=p)
            raise AssertionError()
        except ValueError as e:
            assert "tool_name" in str(e)


def test_corrupt_file_returns_empty():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "rules.json"
        p.write_text("{ not json")
        assert ad.list_rules(p) == []
        assert ad.lookup_decision("x", p) is None


def test_chmod_to_600():
    import os
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "rules.json"
        ad.add_rule("x", "allow", path=p)
        assert os.stat(p).st_mode & 0o777 == 0o600


def test_callback_short_circuits_on_auto_decision():
    """integration: approvals.make_callback 应该用 auto_decisions 直接决断。"""
    from unittest.mock import MagicMock, patch

    from sentinel_mcp.approvals import PendingDecisions

    with tempfile.TemporaryDirectory() as td:
        rules_path = Path(td) / "rules.json"
        ad.add_rule("write_file", "allow", path=rules_path)

        db_path = Path(td) / "test.db"
        approvals = PendingDecisions(str(db_path))
        cb = approvals.make_callback(timeout_seconds=0.5)

        call = MagicMock(tool_name="write_file", args={"path": "/tmp/x"}, session_id="s1")
        pending = MagicMock(reason="risky", risk_score=0.8, triggered_rules=["fs:write"])

        # patch DEFAULT_PATH 让 callback 内的 lookup_decision()（不传 path）走我们临时文件
        with patch.object(ad, "DEFAULT_PATH", rules_path):
            result = cb(call, pending)

        assert result is True  # auto-allow

        # 验证仍记了 audit pending（用于追溯）
        recents = approvals.list_recent(limit=10)
        assert len(recents) == 1
        assert recents[0]["status"] == "approved"
        assert recents[0]["decided_by"] == "auto-decision"


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
