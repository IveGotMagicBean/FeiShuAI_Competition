"""watcher：新 mcpServer 出现/消失时回调被触发。

跑：python tests/test_watcher.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pwa_dashboard import discovery as d
from pwa_dashboard import watcher as w


def _patch_cursor_to(file_path: Path) -> None:
    a = d.ADAPTERS["cursor"]
    a._paths_per_platform = {sys.platform: [str(file_path)]}


def _write(p: Path, obj: dict) -> None:
    p.write_text(json.dumps(obj))


def test_diff_detects_added_and_removed():
    old = {"k1": {"server_name": "a", "is_protected": False},
           "k2": {"server_name": "b", "is_protected": True}}
    new = {"k2": {"server_name": "b", "is_protected": True},
           "k3": {"server_name": "c", "is_protected": False}}
    diff = w._diff_servers(old, new)
    assert len(diff["added"]) == 1 and diff["added"][0]["server_name"] == "c"
    assert len(diff["removed"]) == 1 and diff["removed"][0]["server_name"] == "a"
    assert len(diff["unprotected_added"]) == 1


def test_diff_no_change_returns_empty():
    same = {"k": {"server_name": "x", "is_protected": False}}
    diff = w._diff_servers(same, same)
    assert diff["added"] == []
    assert diff["removed"] == []


def test_watcher_polling_detects_new_server():
    """完整流程：起 watcher → 模拟新增 server → 回调被触发。"""
    d.reset_adapters()
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "cursor.json"
        _write(cfg, {"mcpServers": {}})
        _patch_cursor_to(cfg)

        events = []
        ev = threading.Event()

        def on_change(evt):
            events.append(evt)
            ev.set()

        # 用很短的 rescan 间隔做测试
        watcher = w.DiscoveryWatcher(on_change, rescan_interval=0.3)
        watcher.start()
        try:
            # 给一点时间初始化快照
            time.sleep(0.4)
            # 写新 server
            _write(cfg, {"mcpServers": {"new_fs": {"command": "npx", "args": ["x"]}}})
            # 等回调
            assert ev.wait(timeout=3), "watcher 应该触发了 on_change"
            assert len(events) >= 1
            diff = events[-1]["diff"]
            assert any(s["server_name"] == "new_fs" for s in diff["added"])
            assert len(diff["unprotected_added"]) == 1
        finally:
            watcher.stop()


def test_watcher_does_not_fire_on_steady_state():
    """没有变化时不应该误报。"""
    d.reset_adapters()
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "cursor.json"
        _write(cfg, {"mcpServers": {"existing": {"command": "x", "args": []}}})
        _patch_cursor_to(cfg)

        events = []
        watcher = w.DiscoveryWatcher(lambda e: events.append(e), rescan_interval=0.3)
        watcher.start()
        try:
            time.sleep(1.2)  # 跑 ~3 个 rescan 周期
            assert events == [], f"不该有事件但收到: {events}"
        finally:
            watcher.stop()


def test_watcher_detects_removal():
    d.reset_adapters()
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "cursor.json"
        _write(cfg, {"mcpServers": {"x": {"command": "npx", "args": []}}})
        _patch_cursor_to(cfg)

        events = []
        ev = threading.Event()

        def on_change(e):
            events.append(e)
            ev.set()

        watcher = w.DiscoveryWatcher(on_change, rescan_interval=0.3)
        watcher.start()
        try:
            time.sleep(0.4)
            # 删 server
            _write(cfg, {"mcpServers": {}})
            assert ev.wait(timeout=3)
            diff = events[-1]["diff"]
            assert any(s["server_name"] == "x" for s in diff["removed"])
        finally:
            watcher.stop()


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            d.reset_adapters()
            t()
            print(f"  ✓ {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ✗ {t.__name__}  ←  {e!r}")
            failed += 1
    d.reset_adapters()
    print(f"\n{passed}/{passed+failed} passed")
    sys.exit(0 if failed == 0 else 1)
