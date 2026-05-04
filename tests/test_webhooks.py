"""webhooks：增删 + payload 形态 + send 走 mock。

跑：python tests/test_webhooks.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sentinel_mcp import webhooks as wh


def test_empty_when_file_missing():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "wh.json"
        assert wh.list_endpoints(p) == []


def test_add_then_list_then_delete():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "wh.json"
        ep = wh.add_endpoint("slack-team", "slack", "https://hooks.slack.com/x", path=p)
        assert ep["name"] == "slack-team"
        assert ep["kind"] == "slack"
        assert wh.list_endpoints(p) == [ep]
        assert wh.delete_endpoint("slack-team", p) is True
        assert wh.list_endpoints(p) == []


def test_add_overwrites_same_name():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "wh.json"
        wh.add_endpoint("ops", "slack", "https://x", path=p)
        wh.add_endpoint("ops", "discord", "https://y", path=p)
        eps = wh.list_endpoints(p)
        assert len(eps) == 1
        assert eps[0]["kind"] == "discord"


def test_invalid_kind_raises():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "wh.json"
        try:
            wh.add_endpoint("x", "telegram", "https://x", path=p)  # type: ignore[arg-type]
            assert False
        except ValueError as e:
            assert "kind 必须是" in str(e)


def test_invalid_url_raises():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "wh.json"
        try:
            wh.add_endpoint("x", "slack", "ftp://oops", path=p)
            assert False
        except ValueError as e:
            assert "http" in str(e)


def test_slack_payload_has_blocks():
    p = wh._build_payload("slack", "Title!", "Body!")
    assert p["text"] == "Title!"
    assert any(b["type"] == "header" for b in p["blocks"])
    assert any(b["type"] == "section" for b in p["blocks"])


def test_discord_payload_uses_embeds():
    p = wh._build_payload("discord", "T", "B")
    assert p["username"] == "Sentinel-MCP"
    assert p["embeds"][0]["title"] == "T"
    assert p["embeds"][0]["description"] == "B"


def test_custom_payload_is_raw():
    p = wh._build_payload("custom", "T", "B")
    assert p["title"] == "T"
    assert p["body"] == "B"
    assert "ts" in p


def test_send_skips_disabled():
    ep = {"name": "x", "kind": "slack", "url": "https://x", "enabled": False}
    r = wh.send(ep, "T", "B")
    assert r["ok"] is False and r.get("skipped") is True


def test_send_makes_post_with_json_body():
    ep = {"name": "x", "kind": "slack", "url": "https://hooks.slack.com/x", "enabled": True}
    fake_resp = MagicMock()
    fake_resp.status = 200
    fake_resp.__enter__ = lambda self_: fake_resp
    fake_resp.__exit__ = lambda *a: None
    with patch("urllib.request.urlopen", return_value=fake_resp) as urlopen:
        r = wh.send(ep, "Title", "Body")
        assert r["ok"] is True
        assert r["status"] == 200
        # 检查请求体是 JSON
        req = urlopen.call_args[0][0]
        body = json.loads(req.data.decode())
        assert body["text"] == "Title"  # slack schema


def test_send_handles_http_error():
    import urllib.error
    ep = {"name": "x", "kind": "discord", "url": "https://discord.com/api/x", "enabled": True}
    err = urllib.error.HTTPError("https://x", 401, "Unauthorized", {}, None)
    with patch("urllib.request.urlopen", side_effect=err):
        r = wh.send(ep, "T", "B")
        assert r["ok"] is False
        assert r["status"] == 401


def test_send_all_only_enabled():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "wh.json"
        wh.add_endpoint("on", "slack", "https://a", enabled=True, path=p)
        wh.add_endpoint("off", "discord", "https://b", enabled=False, path=p)
        with patch.object(wh, "DEFAULT_PATH", p), \
             patch.object(wh, "send", return_value={"ok": True, "name": "fake"}) as snd:
            results = wh.send_all("T", "B")
            assert len(results) == 1  # disabled 被跳过
            assert snd.call_args[0][0]["name"] == "on"


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
