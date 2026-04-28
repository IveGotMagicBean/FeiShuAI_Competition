"""L4 出向 DLP 测试

覆盖 proxy._maybe_redact_line 的几条路径：

  1. 上游返回带 OpenAI key  → 替换为 [OPENAI_KEY]
  2. 上游返回带 JWT          → 替换为 [JWT]
  3. 上游返回带多种敏感数据  → 一次性全脱敏，类型聚合
  4. 不是 result.content 结构（initialize 响应等）→ 透传
  5. 解析失败的非 JSON 行     → 透传
  6. dlp_outbound=False      → 完全跳过
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
sys.path.insert(0, str(_PROJECT))

from guard import Guard
from sentinel_mcp.proxy import MCPProxy

CFG = _PROJECT / "config" / "policies.yaml"


def _make_proxy(dlp_outbound: bool = True) -> MCPProxy:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    guard = Guard.from_yaml(str(CFG), audit_db_path=db_path)
    return MCPProxy(
        upstream_cmd=["true"],
        guard=guard,
        log_stream=io.StringIO(),
        dlp_outbound=dlp_outbound,
    )


def assert_eq(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"[{label}] expected {expected!r}, got {actual!r}")


def assert_contains(haystack: str, needle: str, label: str) -> None:
    if needle not in haystack:
        raise AssertionError(f"[{label}] expected {needle!r} in {haystack!r}")


def assert_not_contains(haystack: str, needle: str, label: str) -> None:
    if needle in haystack:
        raise AssertionError(f"[{label}] {needle!r} should NOT be in {haystack!r}")


# ---------- cases ----------

def test_redact_openai_key() -> None:
    proxy = _make_proxy()
    line = (json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "result": {"content": [{"type": "text", "text": "your key: sk-abcd1234efgh5678ijkl9012mnop3456qrst"}]},
    }) + "\n").encode()
    out = proxy._maybe_redact_line(line).decode()
    assert_contains(out, "[OPENAI_KEY]", "openai redacted")
    assert_not_contains(out, "sk-abcd1234efgh5678ijkl9012mnop3456qrst", "openai original gone")


def test_redact_jwt() -> None:
    proxy = _make_proxy()
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    line = (json.dumps({
        "jsonrpc": "2.0", "id": 2,
        "result": {"content": [{"type": "text", "text": f"token: {jwt}"}]},
    }) + "\n").encode()
    out = proxy._maybe_redact_line(line).decode()
    assert_contains(out, "[JWT]", "jwt redacted")
    assert_not_contains(out, jwt, "jwt original gone")


def test_redact_multiple() -> None:
    proxy = _make_proxy()
    line = (json.dumps({
        "jsonrpc": "2.0", "id": 3,
        "result": {"content": [
            {"type": "text", "text": "AKIAIOSFODNN7EXAMPLE and 13800138000 plus admin@corp.com"},
        ]},
    }) + "\n").encode()
    out = proxy._maybe_redact_line(line).decode()
    assert_contains(out, "[AWS_ACCESS_KEY]", "aws redacted")
    assert_contains(out, "[PHONE]", "phone redacted")
    assert_contains(out, "[EMAIL]", "email redacted")


def test_passthrough_non_result_content() -> None:
    """initialize / tools/list 响应 — 没有 result.content 结构，原样透传"""
    proxy = _make_proxy()
    line = (json.dumps({
        "jsonrpc": "2.0", "id": 0,
        "result": {"protocolVersion": "2024-11-05", "serverInfo": {"name": "fs"}},
    }) + "\n").encode()
    out = proxy._maybe_redact_line(line)
    assert_eq(out, line, "passthrough non-content response")


def test_passthrough_garbage_line() -> None:
    """非 JSON 行（比如 startup banner）原样透传"""
    proxy = _make_proxy()
    line = b"not json at all\n"
    out = proxy._maybe_redact_line(line)
    assert_eq(out, line, "passthrough garbage")


def test_passthrough_when_disabled() -> None:
    """dlp_outbound=False 时不应该有任何变化（_dlp 为 None）"""
    proxy = _make_proxy(dlp_outbound=False)
    if proxy._dlp is not None:
        raise AssertionError("dlp_outbound=False but _dlp is not None")
    # 这种情况下 _upstream_to_client 直接走原 line，本测试只验证开关状态


# ---------- runner ----------

def main() -> int:
    cases = [
        ("redact OpenAI key",          test_redact_openai_key),
        ("redact JWT",                 test_redact_jwt),
        ("redact multiple types",      test_redact_multiple),
        ("passthrough initialize",     test_passthrough_non_result_content),
        ("passthrough garbage line",   test_passthrough_garbage_line),
        ("respect dlp_outbound flag",  test_passthrough_when_disabled),
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
    sys.exit(main())
