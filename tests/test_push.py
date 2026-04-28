"""WebPushManager 单元测试

不打真实网络（FCM / Mozilla autopush），只测：
  1. VAPID 密钥首次生成 + 二次复用
  2. add / remove / list 订阅
  3. 非法订阅被拒
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
sys.path.insert(0, str(_PROJECT))

from pwa_dashboard.push import WebPushManager


def assert_eq(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"[{label}] expected {expected!r}, got {actual!r}")


def assert_truthy(actual, label: str) -> None:
    if not actual:
        raise AssertionError(f"[{label}] expected truthy, got {actual!r}")


def _mgr(tmp: str) -> WebPushManager:
    return WebPushManager(
        db_path=os.path.join(tmp, "db.db"),
        vapid_path=os.path.join(tmp, "v.json"),
    )


def test_vapid_generate_and_reuse() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        m1 = _mgr(tmp)
        k1 = m1.vapid_public_key_b64url
        assert_truthy(len(k1) > 80, "vapid pub key non-trivial length")

        m2 = _mgr(tmp)  # 二次实例化必须读到同一对密钥
        assert_eq(m2.vapid_public_key_b64url, k1, "vapid persists across instantiation")


def test_add_remove_subscription() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        m = _mgr(tmp)
        sub = {
            "endpoint": "https://fcm.example.com/x/y/z",
            "keys": {"p256dh": "BAAA", "auth": "BBBB"},
        }
        assert_truthy(m.add_subscription(sub, ua="testUA/1.0"), "add ok")
        rows = m.list_subscriptions()
        assert_eq(len(rows), 1, "list size after add")
        assert_eq(rows[0]["endpoint"], sub["endpoint"], "endpoint stored")
        assert_eq(rows[0]["ua"], "testUA/1.0", "ua stored")

        # idempotent: 同 endpoint 再加一次还是 1 条（INSERT OR REPLACE）
        m.add_subscription(sub, ua="testUA/1.0")
        assert_eq(len(m.list_subscriptions()), 1, "duplicate add idempotent")

        assert_truthy(m.remove_subscription(sub["endpoint"]), "remove ok")
        assert_eq(len(m.list_subscriptions()), 0, "list size after remove")


def test_invalid_subscription_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        m = _mgr(tmp)
        bad_payloads = [
            {"endpoint": "x"},                      # no keys
            {"keys": {"p256dh": "a", "auth": "b"}},  # no endpoint
            {"endpoint": "x", "keys": {"p256dh": "a"}},  # no auth
        ]
        for bad in bad_payloads:
            ok = m.add_subscription(bad, ua="bad")
            assert_eq(ok, False, f"reject bad: {bad}")
        assert_eq(len(m.list_subscriptions()), 0, "no rows from bad inputs")


def test_send_to_all_with_zero_subscriptions() -> None:
    """没有订阅时 send_to_all 应该正常返回 sent=0，不抛异常"""
    with tempfile.TemporaryDirectory() as tmp:
        m = _mgr(tmp)
        r = m.send_to_all({"title": "t", "body": "b"})
        assert_eq(r["sent"], 0, "sent=0 when no subs")
        assert_eq(r["total"], 0, "total=0")
        assert_eq(r["errors"], [], "no errors")


def main() -> int:
    cases = [
        ("VAPID generate + reuse",            test_vapid_generate_and_reuse),
        ("add / remove subscription",         test_add_remove_subscription),
        ("reject invalid subscription",       test_invalid_subscription_rejected),
        ("send_to_all with no subscriptions", test_send_to_all_with_zero_subscriptions),
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
