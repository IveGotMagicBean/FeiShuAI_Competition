"""飞书通知模块单测：配置持久化 / 卡片渲染 / 回调解析 / 签名验证。
真实 SDK 调用通过 mock 验证；无需真飞书 creds。

跑：python tests/test_lark_notifier.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sentinel_mcp import lark_notifier as ln


# ------------------------------------------------------------------ #
# 配置持久化
# ------------------------------------------------------------------ #

def test_load_config_missing_returns_none():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "missing.json"
        assert ln.load_config(p) is None


def test_save_then_load_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "cfg.json"
        cfg = ln.LarkConfig(
            app_id="cli_abc", app_secret="sec123",
            target_chat_id="oc_xyz", encrypt_key="ek", verification_token="vt",
        )
        ln.save_config(cfg, p)
        loaded = ln.load_config(p)
        assert loaded is not None
        assert loaded.app_id == "cli_abc"
        assert loaded.target_chat_id == "oc_xyz"
        assert loaded.encrypt_key == "ek"


def test_save_chmods_to_600():
    import os, stat
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "cfg.json"
        ln.save_config(ln.LarkConfig("a", "b"), p)
        mode = os.stat(p).st_mode & 0o777
        assert mode == 0o600, f"expected 600, got {oct(mode)}"


def test_load_config_missing_required_returns_none():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "bad.json"
        p.write_text(json.dumps({"app_id": "", "app_secret": "x"}))
        assert ln.load_config(p) is None


def test_load_corrupt_returns_none():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "bad.json"
        p.write_text("{ not json")
        assert ln.load_config(p) is None


# ------------------------------------------------------------------ #
# 卡片渲染
# ------------------------------------------------------------------ #

def test_pending_card_has_action_buttons():
    card = ln.build_pending_card({
        "id": "pid_1", "tool_name": "write_file",
        "reason": "敏感路径", "risk_score": 0.8, "args": {"path": "/etc/passwd"},
    })
    assert card["header"]["template"] == "orange"
    # 找到 action 元素
    actions = next(e for e in card["elements"] if e["tag"] == "action")
    btn_actions = [a["value"]["action"] for a in actions["actions"]]
    assert "approve" in btn_actions
    assert "deny" in btn_actions
    # pid 必须传到 button value 里
    pids = {a["value"]["pid"] for a in actions["actions"]}
    assert pids == {"pid_1"}


def test_pending_card_truncates_long_args():
    huge_args = {"path": "x" * 1000}
    card = ln.build_pending_card({
        "id": "p", "tool_name": "t", "reason": "r", "risk_score": 0.1, "args": huge_args
    })
    args_div = next(e for e in card["elements"] if "调用参数" in str(e))
    content = args_div["text"]["content"]
    assert "..." in content
    assert len(content) < 350


def test_decided_card_uses_status_template():
    p = {"id": "x", "tool_name": "read_file"}
    approved = ln.build_decided_card(p, True, "user_a")
    denied = ln.build_decided_card(p, False, "user_b")
    assert approved["header"]["template"] == "green"
    assert denied["header"]["template"] == "red"
    assert "已批准" in approved["header"]["title"]["content"]
    assert "已拒绝" in denied["header"]["title"]["content"]


# ------------------------------------------------------------------ #
# 回调解析
# ------------------------------------------------------------------ #

def test_parse_card_action_schema_v2():
    payload = {
        "header": {"event_type": "card.action.trigger"},
        "event": {
            "operator": {"open_id": "ou_abc"},
            "action": {"tag": "button", "value": {"action": "approve", "pid": "p1"}},
        }
    }
    pid, action, by, _ = ln.parse_card_action(payload)
    assert pid == "p1"
    assert action == "approve"
    assert by == "ou_abc"


def test_parse_card_action_schema_v1_legacy():
    payload = {
        "action": {"value": {"action": "deny", "pid": "p2"}},
        "operator": {"user_id": "uid_x"},
    }
    pid, action, by, _ = ln.parse_card_action(payload)
    assert pid == "p2"
    assert action == "deny"
    assert by == "uid_x"


def test_parse_card_action_returns_none_for_unrelated_event():
    assert ln.parse_card_action({"event": {"action": {"value": {}}}}) is None
    # 注意：v1 schema 的兜底把缺 pid 的 v2 也认成空 pid，所以这里测「连 action.value 都没有」
    assert ln.parse_card_action({"event": {}}) is None


# ------------------------------------------------------------------ #
# URL 验证 / 签名
# ------------------------------------------------------------------ #

def test_url_challenge_legacy_format():
    cfg = ln.LarkConfig("", "")
    r = ln.verify_url_challenge({"type": "url_verification", "challenge": "xyz"}, cfg)
    assert r == {"challenge": "xyz"}


def test_url_challenge_normal_event_returns_none():
    cfg = ln.LarkConfig("", "")
    r = ln.verify_url_challenge({"event": {"action": {}}}, cfg)
    assert r is None


def test_signature_passes_when_no_encrypt_key():
    cfg = ln.LarkConfig("a", "b")  # encrypt_key empty
    assert ln.verify_signature("ts", "nonce", b"body", "anysig", cfg) is True


def test_signature_validates_when_encrypt_key_set():
    import hashlib
    cfg = ln.LarkConfig("a", "b", encrypt_key="my_secret")
    body = b'{"foo":"bar"}'
    ts, nonce = "1700000000", "abc"
    expected = hashlib.sha256(
        (ts + nonce + cfg.encrypt_key).encode() + body
    ).hexdigest()
    assert ln.verify_signature(ts, nonce, body, expected, cfg) is True
    assert ln.verify_signature(ts, nonce, body, "wrong", cfg) is False


def test_decrypt_payload_roundtrip():
    """对照飞书的加密协议 (AES-256-CBC + sha256(key) + IV-prefix + PKCS7)，自己加密解密一遍。"""
    import hashlib, json as _json, os, base64
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    encrypt_key = "test_encrypt_key_123"
    plaintext = _json.dumps({"type": "url_verification", "challenge": "abc", "token": "tk"}).encode()
    key = hashlib.sha256(encrypt_key.encode()).digest()
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    encrypt_b64 = base64.b64encode(iv + ciphertext).decode()

    decrypted = ln.decrypt_payload(encrypt_b64, encrypt_key)
    assert decrypted["challenge"] == "abc"
    assert decrypted["token"] == "tk"


def test_maybe_decrypt_passthrough_when_not_encrypted():
    cfg = ln.LarkConfig("a", "b", encrypt_key="anything")
    payload = {"type": "url_verification", "challenge": "xyz"}
    assert ln.maybe_decrypt(payload, cfg) == payload  # 没有 encrypt 字段直接透传


def test_maybe_decrypt_decrypts_when_encrypt_field_present():
    import hashlib, json as _json, os, base64
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    cfg = ln.LarkConfig("a", "b", encrypt_key="ek")
    plaintext = _json.dumps({"event": {"action": {"value": {"action": "approve", "pid": "p1"}}}}).encode()
    key = hashlib.sha256(cfg.encrypt_key.encode()).digest()
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    ciphertext = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor().update(padded) + \
                 Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor().finalize()
    # 重新算一次，避免 finalize 后状态污染
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    encrypted = base64.b64encode(iv + ciphertext).decode()

    result = ln.maybe_decrypt({"encrypt": encrypted}, cfg)
    assert result["event"]["action"]["value"]["action"] == "approve"


def test_url_challenge_rejects_wrong_token():
    cfg = ln.LarkConfig("a", "b", verification_token="correct_token")
    try:
        ln.verify_url_challenge({"type": "url_verification", "challenge": "x", "token": "wrong"}, cfg)
        assert False, "应抛 ValueError"
    except ValueError:
        pass


def test_url_challenge_accepts_correct_token():
    cfg = ln.LarkConfig("a", "b", verification_token="tk")
    r = ln.verify_url_challenge({"type": "url_verification", "challenge": "x", "token": "tk"}, cfg)
    assert r == {"challenge": "x"}


# ------------------------------------------------------------------ #
# LarkNotifier with mocked SDK
# ------------------------------------------------------------------ #

def test_notifier_send_pending_calls_sdk_with_correct_args():
    mock_resp = MagicMock()
    mock_resp.success.return_value = True
    mock_resp.data.message_id = "om_test_123"

    with patch.object(ln, "_LARK_AVAILABLE", True), \
         patch.object(ln, "lark") as mock_lark:
        mock_client = MagicMock()
        mock_client.im.v1.message.create.return_value = mock_resp
        mock_lark.Client.builder.return_value.app_id.return_value.app_secret.return_value.build.return_value = mock_client

        cfg = ln.LarkConfig("cli_x", "sec_y", target_chat_id="oc_test")
        notifier = ln.LarkNotifier(cfg)

        msg_id = notifier.send_pending({
            "id": "pid_42", "tool_name": "write_file",
            "reason": "test", "risk_score": 0.5, "args": {}
        })

        assert msg_id == "om_test_123"
        # 验 SDK 真的被调了
        assert mock_client.im.v1.message.create.called


def test_notifier_send_pending_raises_on_sdk_failure():
    mock_resp = MagicMock()
    mock_resp.success.return_value = False
    mock_resp.code = 99991
    mock_resp.msg = "permission denied"

    with patch.object(ln, "_LARK_AVAILABLE", True), \
         patch.object(ln, "lark") as mock_lark:
        mock_client = MagicMock()
        mock_client.im.v1.message.create.return_value = mock_resp
        mock_lark.Client.builder.return_value.app_id.return_value.app_secret.return_value.build.return_value = mock_client

        cfg = ln.LarkConfig("cli_x", "sec_y", target_chat_id="oc_test")
        notifier = ln.LarkNotifier(cfg)

        try:
            notifier.send_pending({"id": "p", "tool_name": "t", "args": {}, "risk_score": 0, "reason": ""})
            assert False, "应抛 RuntimeError"
        except RuntimeError as e:
            assert "99991" in str(e)


def test_infer_id_type():
    assert ln.LarkNotifier._infer_id_type("oc_abc") == "chat_id"
    assert ln.LarkNotifier._infer_id_type("ou_xyz") == "open_id"
    assert ln.LarkNotifier._infer_id_type("on_xxx") == "union_id"
    assert ln.LarkNotifier._infer_id_type("anything_else") == "open_id"


def test_notifier_requires_target():
    with patch.object(ln, "_LARK_AVAILABLE", True), patch.object(ln, "lark"):
        cfg = ln.LarkConfig("a", "b", target_chat_id="")
        notifier = ln.LarkNotifier(cfg)
        try:
            notifier.send_pending({"id": "p", "tool_name": "t", "args": {}, "risk_score": 0, "reason": ""})
            assert False
        except ValueError as e:
            assert "target_chat_id" in str(e)


def test_notifier_raises_when_sdk_missing():
    with patch.object(ln, "_LARK_AVAILABLE", False):
        try:
            ln.LarkNotifier(ln.LarkConfig("a", "b"))
            assert False, "应抛 LarkNotifierUnavailable"
        except ln.LarkNotifierUnavailable:
            pass


# ------------------------------------------------------------------ #
# Runner
# ------------------------------------------------------------------ #

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
