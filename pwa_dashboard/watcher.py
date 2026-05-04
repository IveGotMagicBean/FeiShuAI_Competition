"""FS-watch：监听所有已知 MCP client config 文件，发现新增未保护的 mcpServer
就触发回调（dashboard 侧 → SSE 广播 + Web Push 推送）。

为什么不用纯轮询：
  watchdog 监听 inode 变更，O(1) 反应；轮询的话每秒扫 13 个文件解析 JSON/YAML 太重。
  watchdog 不可用时（缺 dep）退化到 30 秒轮询。

为什么不监听整个 home 目录：
  会被 IDE 临时文件 / build 产物淹没。我们只监听 discovery.scan_all() 列出的
  「真实存在」config 文件的父目录，加 PathPattern 过滤。

并发安全：watcher 在自己线程跑；触发回调前对比 last_snapshot 计算 diff，避免重复通知。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable

from pwa_dashboard import discovery


_HAS_WATCHDOG = False
try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    _HAS_WATCHDOG = True
except ImportError:
    Observer = None  # type: ignore[assignment]
    FileSystemEventHandler = object  # type: ignore[misc, assignment]


def _server_key(s: dict) -> str:
    return f"{s.get('client_key')}|{s.get('config_path')}|{s.get('scope')}|{s.get('server_name')}"


def _diff_servers(old: dict[str, dict], new: dict[str, dict]) -> dict:
    """返回 {added: [...], removed: [...], unprotected_added: [...]}"""
    added = [v for k, v in new.items() if k not in old]
    removed = [v for k, v in old.items() if k not in new]
    unprotected_added = [s for s in added if not s.get("is_protected")]
    return {"added": added, "removed": removed, "unprotected_added": unprotected_added}


class WatchEvent(dict):
    """好打印的 dict — for SSE / log。"""
    pass


class DiscoveryWatcher:
    """监听 + 周期 rescan，触发 on_change 回调。

    on_change(event_dict) 接收类似:
      {"kind": "scan", "diff": {added: [...], removed: [...], unprotected_added: [...]}}
    """

    def __init__(
        self,
        on_change: Callable[[dict], None],
        rescan_interval: float = 30.0,
    ) -> None:
        self.on_change = on_change
        self.rescan_interval = rescan_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._observer = None  # watchdog Observer or None
        self._last_snapshot: dict[str, dict] = {}

    def start(self) -> None:
        if self._thread is not None:
            return
        # 初始快照（不触发回调）
        self._last_snapshot = self._take_snapshot()
        # FS watch（best-effort）
        if _HAS_WATCHDOG:
            try:
                self._start_fs_watch()
            except Exception as e:
                print(f"[watcher] fs-watch failed, fallback to polling: {e}")
        # 周期 rescan 兜底（应对 watchdog 漏事件 / 新 client 安装到全新路径）
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="discovery-watcher")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=2)
            except Exception:
                pass

    def _take_snapshot(self) -> dict[str, dict]:
        try:
            scan = discovery.scan_all()
        except Exception as e:
            print(f"[watcher] scan failed: {e}")
            return {}
        return {_server_key(s): s for s in scan.get("servers", [])}

    def _check_diff(self, source: str) -> None:
        new = self._take_snapshot()
        diff = _diff_servers(self._last_snapshot, new)
        # 任意一边非空才推
        if diff["added"] or diff["removed"]:
            try:
                self.on_change(WatchEvent({"kind": "scan", "source": source, "diff": diff,
                                            "snapshot_size": len(new)}))
            except Exception as e:
                print(f"[watcher] on_change failed: {e}")
        self._last_snapshot = new

    def _start_fs_watch(self) -> None:
        from watchdog.observers import Observer

        observer = Observer()
        watched_dirs: set[str] = set()
        for adapter in discovery.ADAPTERS.values():
            for cfg in adapter.list_config_files():
                d = str(cfg.parent.resolve())
                if d in watched_dirs:
                    continue
                watched_dirs.add(d)
                handler = _ConfigChangeHandler(self)
                try:
                    observer.schedule(handler, d, recursive=False)
                except FileNotFoundError:
                    continue
        observer.daemon = True
        observer.start()
        self._observer = observer
        print(f"[watcher] fs-watch started on {len(watched_dirs)} directories")

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            # 等 rescan_interval，期间被 stop() 唤醒就立刻退
            if self._stop.wait(self.rescan_interval):
                return
            self._check_diff("poll")


class _ConfigChangeHandler(FileSystemEventHandler):
    """watchdog 事件回调：任意 mcp 相关文件变 → 让 watcher 重新算 diff。"""

    _DEBOUNCE_SECONDS = 0.5  # 编辑器原子写常常一秒内多次事件

    def __init__(self, watcher: DiscoveryWatcher) -> None:
        super().__init__()
        self._watcher = watcher
        self._last_fired = 0.0
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def _debounced_check(self) -> None:
        with self._lock:
            now = time.time()
            if now - self._last_fired < self._DEBOUNCE_SECONDS:
                if self._timer is not None:
                    self._timer.cancel()
                self._timer = threading.Timer(self._DEBOUNCE_SECONDS, self._do_check)
                self._timer.daemon = True
                self._timer.start()
                return
            self._last_fired = now
        self._do_check()

    def _do_check(self) -> None:
        try:
            self._watcher._check_diff("fs")
        except Exception as e:
            print(f"[watcher] _do_check failed: {e}")

    def on_modified(self, event) -> None:
        if not event.is_directory:
            self._debounced_check()

    def on_created(self, event) -> None:
        if not event.is_directory:
            self._debounced_check()

    def on_deleted(self, event) -> None:
        if not event.is_directory:
            self._debounced_check()
