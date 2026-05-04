"""bootstrap：~/.local/bin/sentinel-mcp shim 安装幂等性 + 各种环境检测。

跑：python tests/test_bootstrap.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pwa_dashboard import bootstrap as bs


def _patch_paths(tmp_dir: Path):
    """把模块级 SHIM_DIR / SHIM_PATH 重定向到临时目录。"""
    new_dir = tmp_dir / ".local" / "bin"
    new_path = new_dir / "sentinel-mcp"
    return patch.multiple(bs, SHIM_DIR=new_dir, SHIM_PATH=new_path)


def test_creates_shim_when_missing():
    with tempfile.TemporaryDirectory() as td:
        with _patch_paths(Path(td)):
            r = bs.ensure_shim(log=lambda _: None)
            assert r["action"] in ("created", "updated")
            shim = bs.SHIM_PATH
            assert shim.exists()
            content = shim.read_text()
            assert bs.SHIM_MARKER in content
            assert "exec " in content
            # executable
            mode = os.stat(shim).st_mode
            assert mode & 0o111  # at least x for someone


def test_idempotent_when_already_correct():
    """连跑两次 → 第二次不应该报错或重写为不同内容。"""
    with tempfile.TemporaryDirectory() as td:
        with _patch_paths(Path(td)):
            bs.ensure_shim(log=lambda _: None)
            content1 = bs.SHIM_PATH.read_text()
            mtime1 = bs.SHIM_PATH.stat().st_mtime
            import time as _t; _t.sleep(0.05)  # 让 mtime 有差
            r2 = bs.ensure_shim(log=lambda _: None)
            content2 = bs.SHIM_PATH.read_text()
            # 内容应一致
            assert content1 == content2 or r2["action"] == "skipped"


def test_does_not_overwrite_foreign_file():
    """如果 ~/.local/bin/sentinel-mcp 是用户自己写的（无 marker），别动。"""
    with tempfile.TemporaryDirectory() as td:
        with _patch_paths(Path(td)):
            bs.SHIM_PATH.parent.mkdir(parents=True, exist_ok=True)
            bs.SHIM_PATH.write_text("#!/bin/bash\necho 'my own custom thing'\n")
            r = bs.ensure_shim(log=lambda _: None)
            assert r["action"] == "foreign-file"
            # 内容不应被改
            assert "my own custom thing" in bs.SHIM_PATH.read_text()


def test_appimage_target_when_env_set():
    """APPIMAGE 环境变量存在 → shim 调它。"""
    with tempfile.TemporaryDirectory() as td:
        appimg = Path(td) / "fake.AppImage"
        appimg.write_text("dummy")  # 必须存在才被 _decide_target 接受
        appimg.chmod(0o755)
        with _patch_paths(Path(td)), patch.dict(os.environ, {"APPIMAGE": str(appimg)}, clear=False):
            r = bs.ensure_shim(log=lambda _: None)
            assert r["source"] == "appimage"
            assert str(appimg) in r["target"]
            assert "--internal-mcp" in r["target"]
            content = bs.SHIM_PATH.read_text()
            assert str(appimg) in content


def test_python_module_fallback_when_no_appimage_no_path():
    """没装 AppImage 也没 sentinel-mcp 在 PATH → 兜底 python -m sentinel_mcp.cli。"""
    with tempfile.TemporaryDirectory() as td:
        with _patch_paths(Path(td)), \
             patch.dict(os.environ, {"PATH": td}, clear=False), \
             patch("shutil.which", return_value=None):
            # 重新让 shutil.which 找 python3 也失败，bootstrap 兜底为 'python3' literal
            r = bs.ensure_shim(log=lambda _: None)
            assert r["source"] == "python-module"
            assert "sentinel_mcp.cli" in r["target"]


def test_warns_when_not_in_path():
    """~/.local/bin 不在 PATH 时返回 warning。"""
    with tempfile.TemporaryDirectory() as td:
        # PATH 故意不包含 td/.local/bin
        with _patch_paths(Path(td)), patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=False):
            r = bs.ensure_shim(log=lambda _: None)
            assert r["in_path"] is False
            assert "warning" in r


def test_no_warning_when_in_path():
    with tempfile.TemporaryDirectory() as td:
        target_dir = Path(td) / ".local" / "bin"
        env_path = f"/usr/bin:{target_dir}"
        with _patch_paths(Path(td)), patch.dict(os.environ, {"PATH": env_path}, clear=False):
            r = bs.ensure_shim(log=lambda _: None)
            assert r["in_path"] is True


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
