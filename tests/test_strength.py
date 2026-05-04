"""strength：5 档预设读写 + custom override + 全局白/黑名单。

跑：python tests/test_strength.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sentinel_mcp import strength as st


def _patched(td):
    return patch.object(st, "DEFAULT_PATH", Path(td) / "strength.json")


def test_default_level_is_balanced():
    with tempfile.TemporaryDirectory() as td, _patched(td):
        assert st.read_level() == "balanced"


def test_round_trip_each_preset():
    with tempfile.TemporaryDirectory() as td, _patched(td):
        for lvl in ["strict", "strong", "balanced", "lenient", "permissive", "custom"]:
            st.write_level(lvl)
            assert st.read_level() == lvl


def test_invalid_level_raises():
    with tempfile.TemporaryDirectory() as td, _patched(td):
        try:
            st.write_level("HACK")  # type: ignore[arg-type]
            assert False
        except ValueError as e:
            assert "未知 level" in str(e)


def test_strict_overrides_lower_threshold_and_extra_authz():
    with tempfile.TemporaryDirectory() as td, _patched(td):
        st.write_level("strict")
        eff = st.effective_overrides()
        assert eff["thresholds"]["prompt_injection"] == 0.3
        assert "http_request" in eff["tools_extra_authz"]
        assert any(d.endswith(".bashrc") for d in eff["filesystem_denylist_extra"])


def test_balanced_has_empty_overrides():
    """Balanced = policy.yaml 原值，overrides 应该是空（除了 level / 空容器）。"""
    with tempfile.TemporaryDirectory() as td, _patched(td):
        st.write_level("balanced")
        eff = st.effective_overrides()
        assert eff["thresholds"] == {}
        assert eff["tools_extra_authz"] == []


def test_permissive_disables_dlp():
    with tempfile.TemporaryDirectory() as td, _patched(td):
        st.write_level("permissive")
        eff = st.effective_overrides()
        assert eff["thresholds"].get("dlp_enabled") is False
        # 大量工具被放
        assert "shell_exec" in eff["tools_no_authz"]


def test_custom_overrides_supplant_preset():
    with tempfile.TemporaryDirectory() as td, _patched(td):
        st.write_level("custom")
        st.set_custom_override("detectors.prompt_injection.threshold", 0.55)
        st.set_custom_override("detectors.dlp.enabled", False)
        eff = st.effective_overrides()
        assert eff["thresholds"]["prompt_injection"] == 0.55
        assert eff["thresholds"]["dlp_enabled"] is False


def test_tool_allowlist_globally_allows():
    with tempfile.TemporaryDirectory() as td, _patched(td):
        st.set_tool_allowlist(["read_file", "list_dir"])
        assert st.is_tool_globally_allowed("read_file") is True
        assert st.is_tool_globally_allowed("write_file") is False


def test_tool_denylist_globally_blocks():
    with tempfile.TemporaryDirectory() as td, _patched(td):
        st.set_tool_denylist(["http_request"])
        assert st.is_tool_globally_blocked("http_request") is True
        assert st.is_tool_globally_blocked("read_file") is False


def test_get_state_returns_full_view():
    with tempfile.TemporaryDirectory() as td, _patched(td):
        st.write_level("strong")
        st.set_tool_allowlist(["a", "b"])
        s = st.get_state()
        assert s["level"] == "strong"
        assert s["tool_allowlist"] == ["a", "b"]
        assert "presets" in s and "strict" in s["presets"]
        assert s["effective"]["thresholds"]["prompt_injection"] == 0.4


def test_chmod_to_600():
    import os
    with tempfile.TemporaryDirectory() as td, _patched(td):
        st.write_level("strict")
        assert os.stat(st.DEFAULT_PATH).st_mode & 0o777 == 0o600


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
