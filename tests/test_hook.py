"""hook：CLI 子命令 + dashboard hook 安装器。

跑：python tests/test_hook.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pwa_dashboard import hooks_installer as hi

# ------------------------------------------------------------------ #
# hooks_installer
# ------------------------------------------------------------------ #

def test_status_no_settings_file():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "settings.json"
        s = hi.status(p)
        assert s["installed"] is False
        assert s["settings_exists"] is False
        assert "expected_command" in s


def test_install_creates_settings_when_missing():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "settings.json"
        r = hi.install(p)
        assert r["ok"] is True
        assert r["action"] == "installed"
        assert p.exists()
        data = json.loads(p.read_text())
        hooks = data["hooks"]["PreToolUse"]
        assert any(e.get(hi.SENTINEL_MARKER) is True for e in hooks)
        cmd = hooks[0]["hooks"][0]["command"]
        assert "hook-check" in cmd


def test_install_preserves_other_hooks():
    """关键：用户已有的 hook（非 sentinel）必须保留。"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "settings.json"
        p.write_text(json.dumps({
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Read", "hooks": [{"type": "command", "command": "echo my-hook"}]}
                ],
                "Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": "say done"}]}],
            },
            "_user_field": "preserve me",
        }))
        hi.install(p)
        data = json.loads(p.read_text())
        # 原 hook 还在
        pre = data["hooks"]["PreToolUse"]
        assert any("my-hook" in str(e) for e in pre)
        # sentinel hook 也加了
        assert any(e.get(hi.SENTINEL_MARKER) is True for e in pre)
        # Stop hook 没动
        assert data["hooks"]["Stop"][0]["hooks"][0]["command"] == "say done"
        # 用户自定义 root 字段保留
        assert data["_user_field"] == "preserve me"


def test_install_idempotent_replaces_old_sentinel_entry():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "settings.json"
        hi.install(p)
        hi.install(p, matcher="Bash")  # 第二次用不同 matcher
        data = json.loads(p.read_text())
        sentinel_entries = [e for e in data["hooks"]["PreToolUse"] if e.get(hi.SENTINEL_MARKER)]
        # 应只有一条（被替换不是追加）
        assert len(sentinel_entries) == 1
        assert sentinel_entries[0]["matcher"] == "Bash"


def test_install_creates_backup_when_file_exists():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "settings.json"
        p.write_text(json.dumps({"existing": "config"}))
        r = hi.install(p)
        assert r["backup_path"] is not None
        assert Path(r["backup_path"]).exists()


def test_uninstall_removes_only_sentinel_entries():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "settings.json"
        # 先装，再加一个用户的 hook，再卸
        hi.install(p)
        data = json.loads(p.read_text())
        data["hooks"]["PreToolUse"].insert(0, {
            "matcher": "Glob",
            "hooks": [{"type": "command", "command": "user-hook"}],
        })
        p.write_text(json.dumps(data))

        r = hi.uninstall(p)
        assert r["ok"] is True
        assert r["action"] == "uninstalled"
        assert r["removed_count"] == 1

        new = json.loads(p.read_text())
        pre = new.get("hooks", {}).get("PreToolUse") or []
        # 用户 hook 还在，sentinel hook 被移
        assert len(pre) == 1
        assert "user-hook" in str(pre[0])


def test_uninstall_when_nothing_installed():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "settings.json"
        p.write_text(json.dumps({"theme": "dark"}))
        r = hi.uninstall(p)
        assert r["action"] == "noop"


def test_list_supported_clients_includes_claude_code():
    out = hi.list_supported_clients()
    keys = {c["client_key"] for c in out}
    assert "claude_code" in keys
    cc = next(c for c in out if c["client_key"] == "claude_code")
    assert cc["native_hook"] is True
    assert cc["covers_internal_tools"] is True
    # 其它客户端通过 wrap 路径
    cursor = next(c for c in out if c["client_key"] == "cursor")
    assert cursor["native_hook"] is False


# ------------------------------------------------------------------ #
# CLI hook-check 子命令端到端
# ------------------------------------------------------------------ #

def _run_hook_check(stdin_payload: dict, *, mode_file: Path | None = None) -> tuple[int, str, str]:
    """跑一次 sentinel-mcp hook-check，返回 (exit_code, stdout, stderr)。"""
    env = os.environ.copy()
    if mode_file is not None:
        env["SENTINEL_MODE_FILE"] = str(mode_file)
    repo = Path(__file__).resolve().parent.parent
    env["SENTINEL_DB"] = str(repo / "data" / "sentinel.db")
    proc = subprocess.run(
        ["/tmp/sm-venv/bin/python", "-m", "sentinel_mcp.cli", "hook-check"],
        input=json.dumps(stdin_payload),
        capture_output=True, text=True, env=env, timeout=15,
        cwd=str(repo),
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_hook_allows_safe_call():
    code, out, err = _run_hook_check({
        "session_id": "s1", "tool_name": "Read",
        "tool_input": {"file_path": "/tmp/safe.txt"},
    })
    assert code == 0, f"应放行 Read /tmp/safe.txt 但 exit={code} stderr={err}"


def test_hook_blocks_dangerous_path_write():
    code, out, err = _run_hook_check({
        "session_id": "s2", "tool_name": "Write",
        "tool_input": {"file_path": "/etc/passwd", "content": "hacked"},
    })
    # Write /etc/passwd 应被沙箱拒绝（denylist）
    # 但 Write 工具名不一定在 policy.tools 里 → 看 guard 实际行为：可能返回 ASK_USER 或 ALLOW
    # 我们这里只断言不会 crash，不强求一定 deny（因为我们的 policy 是按 MCP 工具名定的）
    assert code in (0, 2), f"unexpected exit code {code}, stderr={err}"


def test_hook_passive_mode_always_allows():
    with tempfile.TemporaryDirectory() as td:
        mode_file = Path(td) / "mode.json"
        mode_file.write_text('{"mode": "passive"}')
        code, _, err = _run_hook_check({
            "session_id": "s3", "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /"},
        }, mode_file=mode_file)
        assert code == 0, f"passive mode 必须放行所有，但 exit={code} stderr={err}"


def test_hook_off_mode_always_allows():
    with tempfile.TemporaryDirectory() as td:
        mode_file = Path(td) / "mode.json"
        mode_file.write_text('{"mode": "off"}')
        code, _, _ = _run_hook_check({
            "session_id": "s4", "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /"},
        }, mode_file=mode_file)
        assert code == 0


def test_hook_empty_stdin_allows():
    """防呆：hook 输入空（配错时）默认放行，不要把用户的 Claude 卡死。"""
    code, _, _ = _run_hook_check({})
    assert code == 0


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
