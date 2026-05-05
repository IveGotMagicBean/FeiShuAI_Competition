"""一键集成模块单测：检测 / 预览 / 写入（含备份、覆盖保护、不破坏其它字段）。

跑：python tests/test_integrations.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# 让 import 可达项目根
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pwa_dashboard import integrations as integ  # noqa: E402

# ------------------------------------------------------------------ #
# 工具
# ------------------------------------------------------------------ #

def _patch_cursor_path(tmp_dir: Path) -> Path:
    """把 cursor 的 config 路径打到临时目录，避免动到用户真实 config。"""
    cfg = tmp_dir / "cursor_mcp.json"
    integ.CLIENTS["cursor"].config_paths = {
        sys.platform: str(cfg),
    }
    return cfg


# ------------------------------------------------------------------ #
# detect_all
# ------------------------------------------------------------------ #

def test_detect_no_config_returns_not_installed():
    with tempfile.TemporaryDirectory() as td:
        _patch_cursor_path(Path(td))
        result = integ.detect_all()
        cursor = next(c for c in result["clients"] if c["key"] == "cursor")
        assert cursor["supported_on_platform"] is True
        assert cursor["installed"] is False
        assert cursor["config_exists"] is False
        assert cursor["mcp_servers"] == []


def test_detect_existing_config_lists_servers():
    with tempfile.TemporaryDirectory() as td:
        cfg = _patch_cursor_path(Path(td))
        cfg.write_text(json.dumps({
            "mcpServers": {
                "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]},
                "fs-guarded": {"command": "sentinel-mcp", "args": ["wrap", "--", "npx", "..."]},
            }
        }))
        result = integ.detect_all()
        cursor = next(c for c in result["clients"] if c["key"] == "cursor")
        assert cursor["installed"] is True
        assert sorted(cursor["mcp_servers"]) == ["filesystem", "fs-guarded"]
        assert cursor["wrapped_servers"] == ["fs-guarded"]


def test_detect_corrupt_config_marks_parse_error():
    with tempfile.TemporaryDirectory() as td:
        cfg = _patch_cursor_path(Path(td))
        cfg.write_text("{ this is not json")
        result = integ.detect_all()
        cursor = next(c for c in result["clients"] if c["key"] == "cursor")
        assert cursor["parse_error"] is True
        assert cursor["installed"] is True


# ------------------------------------------------------------------ #
# preview / install
# ------------------------------------------------------------------ #

def test_preview_does_not_touch_disk():
    with tempfile.TemporaryDirectory() as td:
        cfg = _patch_cursor_path(Path(td))
        result = integ.preview(
            client_key="cursor",
            server_name="my-fs",
            upstream_command="npx",
            upstream_args=["-y", "@modelcontextprotocol/server-filesystem", "{{HOME}}/work"],
        )
        assert result["server_name"] == "my-fs"
        assert "wrap" in result["entry"]["args"]
        # {{HOME}} 应被替换
        assert any("{{HOME}}" not in a and "/work" in a for a in result["entry"]["args"])
        # 磁盘上 config 文件不应被创建
        assert not cfg.exists()


def test_install_creates_config_when_missing():
    with tempfile.TemporaryDirectory() as td:
        cfg = _patch_cursor_path(Path(td))
        result = integ.install(
            client_key="cursor",
            server_name="fs-guarded",
            upstream_command="npx",
            upstream_args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        )
        assert result["ok"] is True
        assert result["action"] == "created"
        assert result["backup_path"] is None  # 文件之前不存在 → 无备份
        assert cfg.exists()
        written = json.loads(cfg.read_text())
        assert "fs-guarded" in written["mcpServers"]
        entry = written["mcpServers"]["fs-guarded"]
        assert "wrap" in entry["args"]


def test_install_preserves_other_fields():
    """关键：现有 config 里 mcpServers 之外的字段（用户自己加的）必须保留。"""
    with tempfile.TemporaryDirectory() as td:
        cfg = _patch_cursor_path(Path(td))
        cfg.write_text(json.dumps({
            "_my_comment": "这是我的笔记，不要被吞",
            "experimental": {"someFlag": True},
            "mcpServers": {
                "existing-server": {"command": "/bin/echo", "args": ["already-here"]},
            },
        }))
        integ.install(
            client_key="cursor",
            server_name="new-guarded",
            upstream_command="npx",
            upstream_args=["-y", "x"],
        )
        written = json.loads(cfg.read_text())
        assert written["_my_comment"] == "这是我的笔记，不要被吞"
        assert written["experimental"]["someFlag"] is True
        # 旧的 server 还在
        assert "existing-server" in written["mcpServers"]
        # 新的也加上了
        assert "new-guarded" in written["mcpServers"]


def test_install_creates_backup_when_overwriting_existing_file():
    with tempfile.TemporaryDirectory() as td:
        cfg = _patch_cursor_path(Path(td))
        cfg.write_text(json.dumps({"mcpServers": {}}))
        result = integ.install(
            client_key="cursor",
            server_name="fs-guarded",
            upstream_command="npx",
            upstream_args=["x"],
        )
        assert result["backup_path"] is not None
        backup = Path(result["backup_path"])
        assert backup.exists()
        # 备份内容应是写之前的状态
        assert json.loads(backup.read_text()) == {"mcpServers": {}}


def test_install_refuses_overwrite_by_default():
    with tempfile.TemporaryDirectory() as td:
        cfg = _patch_cursor_path(Path(td))
        cfg.write_text(json.dumps({"mcpServers": {"dup": {"command": "/bin/true"}}}))
        try:
            integ.install(
                client_key="cursor",
                server_name="dup",
                upstream_command="npx",
                upstream_args=["x"],
            )
            raise AssertionError("应该 raise ValueError")
        except ValueError as e:
            assert "已存在" in str(e) or "exist" in str(e).lower()


def test_install_overwrite_flag_replaces_entry():
    with tempfile.TemporaryDirectory() as td:
        cfg = _patch_cursor_path(Path(td))
        cfg.write_text(json.dumps({
            "mcpServers": {"dup": {"command": "/bin/true", "args": ["old"]}}
        }))
        result = integ.install(
            client_key="cursor",
            server_name="dup",
            upstream_command="npx",
            upstream_args=["new-arg"],
            overwrite=True,
        )
        assert result["action"] == "replaced"
        written = json.loads(cfg.read_text())
        assert "wrap" in written["mcpServers"]["dup"]["args"]
        assert "old" not in written["mcpServers"]["dup"]["args"]


def test_install_validates_server_name():
    with tempfile.TemporaryDirectory() as td:
        _patch_cursor_path(Path(td))
        for bad in ["", "name with space", "name/slash", "name.dot"]:
            try:
                integ.install(
                    client_key="cursor",
                    server_name=bad,
                    upstream_command="npx",
                    upstream_args=["x"],
                )
                raise AssertionError(f"should reject server_name={bad!r}")
            except ValueError:
                pass


def test_install_unknown_client_raises():
    try:
        integ.install("nonexistent_client", "x", "npx", [])
        raise AssertionError()
    except ValueError as e:
        assert "unknown" in str(e).lower()


# ------------------------------------------------------------------ #
# Runner
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    # 备份原 CLIENTS["cursor"].config_paths，跑完恢复
    _orig_cursor_paths = dict(integ.CLIENTS["cursor"].config_paths)
    try:
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
    finally:
        integ.CLIENTS["cursor"].config_paths = _orig_cursor_paths
