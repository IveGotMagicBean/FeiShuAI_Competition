"""discovery 模块单测：扫描 / 批量 wrap / 批量 unwrap / 备份回滚。

覆盖：
  - SimpleJSONAdapter（Claude Desktop / Cursor / Cline / Roo Code 通用形态）
  - ClaudeCodeAdapter（projects.*.mcpServers 嵌套）
  - is_wrapped / extract_upstream（含 sentinel-mcp / python -m / AppImage 三形态）
  - wrap_servers 幂等、报错隔离、备份去重
  - unwrap_servers 还原成裸 entry
  - restore_backup 文件名解析

跑：python tests/test_discovery.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pwa_dashboard import discovery as d  # noqa: E402

# ---------------------------------------------------------------- #
# helpers
# ---------------------------------------------------------------- #


def _write_json(p: Path, obj: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj))


def _patch_simple_adapter(key: str, file_path: Path) -> None:
    """把某个 SimpleJSONAdapter 的搜索路径改成临时文件。"""
    a = d.ADAPTERS[key]
    # 重置成只看这个文件
    a._paths_per_platform = {sys.platform: [str(file_path)]}


def _patch_claude_code_to(file_path: Path) -> None:
    """把 ClaudeCodeAdapter.list_config_files 改成只看临时文件。"""
    a = d.ADAPTERS["claude_code"]
    a.list_config_files = lambda fp=file_path: [fp] if fp.exists() else []  # type: ignore[method-assign]


# ---------------------------------------------------------------- #
# is_wrapped / extract_upstream
# ---------------------------------------------------------------- #


def test_is_wrapped_recognizes_sentinel_mcp():
    assert d.is_wrapped({"command": "sentinel-mcp", "args": ["wrap", "--", "npx", "x"]}) is True


def test_is_wrapped_recognizes_python_module_form():
    e = {"command": "python3", "args": ["-m", "sentinel_mcp.cli", "wrap", "--", "npx", "x"]}
    assert d.is_wrapped(e) is True


def test_is_wrapped_recognizes_appimage_path():
    e = {"command": "/home/u/Apps/Sentinel-MCP-0.2.0.AppImage/sentinel-mcp", "args": ["wrap", "--", "npx", "x"]}
    assert d.is_wrapped(e) is True


def test_is_wrapped_false_for_plain_npx():
    assert d.is_wrapped({"command": "npx", "args": ["-y", "@x/y"]}) is False


def test_is_wrapped_false_for_garbage():
    assert d.is_wrapped({}) is False
    assert d.is_wrapped({"command": "sentinel-mcp"}) is False  # 缺 wrap 关键字
    assert d.is_wrapped("not a dict") is False  # type: ignore[arg-type]


def test_extract_upstream_from_wrapped():
    cmd, args, env = d.extract_upstream({
        "command": "sentinel-mcp",
        "args": ["wrap", "--", "npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        "env": {"FOO": "bar"},
    })
    assert cmd == "npx"
    assert args == ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    assert env == {"FOO": "bar"}


def test_extract_upstream_passthrough_for_unwrapped():
    cmd, args, env = d.extract_upstream({"command": "uvx", "args": ["mcp-server-time"]})
    assert (cmd, args, env) == ("uvx", ["mcp-server-time"], {})


# ---------------------------------------------------------------- #
# SimpleJSONAdapter（用 cursor 当代表，所有 simple 客户端都走同一类）
# ---------------------------------------------------------------- #


def test_simple_adapter_no_config_returns_empty():
    d.reset_adapters()
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "mcp.json"
        _patch_simple_adapter("cursor", cfg)
        assert d.ADAPTERS["cursor"].list_config_files() == []


def test_simple_adapter_lists_servers_with_protected_flag():
    d.reset_adapters()
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "mcp.json"
        _write_json(cfg, {
            "mcpServers": {
                "fs": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]},
                "fs-guarded": {"command": "sentinel-mcp", "args": ["wrap", "--", "npx", "-y", "@x/y"]},
            }
        })
        _patch_simple_adapter("cursor", cfg)
        entries = d.ADAPTERS["cursor"].enumerate(cfg)
        assert len(entries) == 2
        by_name = {e.server_name: e for e in entries}
        assert by_name["fs"].is_protected is False
        assert by_name["fs-guarded"].is_protected is True
        # extract_upstream 在 enumerate 里跑过了
        assert by_name["fs-guarded"].upstream_command == "npx"
        assert by_name["fs-guarded"].upstream_args == ["-y", "@x/y"]


def test_simple_adapter_write_entry_creates_backup():
    d.reset_adapters()
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "mcp.json"
        _write_json(cfg, {"mcpServers": {"fs": {"command": "npx", "args": ["x"]}}})
        backup = d.ADAPTERS["cursor"].write_entry(
            cfg, "", "fs", {"command": "sentinel-mcp", "args": ["wrap", "--", "npx", "x"]}
        )
        assert backup is not None
        assert backup.exists()
        assert json.loads(backup.read_text()) == {"mcpServers": {"fs": {"command": "npx", "args": ["x"]}}}
        # 主文件已更新
        new = json.loads(cfg.read_text())
        assert new["mcpServers"]["fs"]["command"] == "sentinel-mcp"


def test_simple_adapter_preserves_other_top_level_fields():
    d.reset_adapters()
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "mcp.json"
        _write_json(cfg, {
            "_user_note": "do not eat my notes",
            "experimental": {"flag": True},
            "mcpServers": {"fs": {"command": "npx", "args": ["x"]}},
        })
        d.ADAPTERS["cursor"].write_entry(
            cfg, "", "fs", {"command": "sentinel-mcp", "args": ["wrap", "--", "npx", "x"]}
        )
        new = json.loads(cfg.read_text())
        assert new["_user_note"] == "do not eat my notes"
        assert new["experimental"] == {"flag": True}


# ---------------------------------------------------------------- #
# ClaudeCodeAdapter（projects.*.mcpServers 嵌套）
# ---------------------------------------------------------------- #


def test_claude_code_lists_global_and_per_project():
    d.reset_adapters()
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / ".claude.json"
        _write_json(cfg, {
            "mcpServers": {
                "global-fs": {"command": "npx", "args": ["-y", "@x/global"]},
            },
            "projects": {
                "/home/u/repo-a": {
                    "mcpServers": {"a-github": {"command": "npx", "args": ["-y", "@x/gh"]}},
                },
                "/home/u/repo-b": {
                    "mcpServers": {
                        "b-fs-guarded": {"command": "sentinel-mcp", "args": ["wrap", "--", "npx", "-y", "@x/fs"]},
                    },
                },
                "/home/u/repo-c": {},  # 没有 mcpServers
            },
        })
        _patch_claude_code_to(cfg)
        entries = d.ADAPTERS["claude_code"].enumerate(cfg)
        assert len(entries) == 3
        by_key = {(e.scope, e.server_name): e for e in entries}
        # 全局
        assert by_key[("", "global-fs")].is_protected is False
        # project A
        assert by_key[("project:/home/u/repo-a", "a-github")].is_protected is False
        # project B（已 wrap）
        b = by_key[("project:/home/u/repo-b", "b-fs-guarded")]
        assert b.is_protected is True
        assert b.upstream_command == "npx"


def test_claude_code_write_entry_into_project_scope():
    d.reset_adapters()
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / ".claude.json"
        _write_json(cfg, {
            "projects": {"/repo-a": {"mcpServers": {"gh": {"command": "npx", "args": ["x"]}}}},
        })
        backup = d.ADAPTERS["claude_code"].write_entry(
            cfg, "project:/repo-a", "gh",
            {"command": "sentinel-mcp", "args": ["wrap", "--", "npx", "x"]},
        )
        assert backup is not None
        new = json.loads(cfg.read_text())
        entry = new["projects"]["/repo-a"]["mcpServers"]["gh"]
        assert entry["command"] == "sentinel-mcp"
        assert "wrap" in entry["args"]


def test_claude_code_write_entry_into_global_scope():
    d.reset_adapters()
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / ".claude.json"
        _write_json(cfg, {"mcpServers": {}, "projects": {"/x": {}}})
        d.ADAPTERS["claude_code"].write_entry(
            cfg, "", "fs",
            {"command": "sentinel-mcp", "args": ["wrap", "--", "npx", "fs"]},
        )
        new = json.loads(cfg.read_text())
        assert new["mcpServers"]["fs"]["command"] == "sentinel-mcp"
        # projects 字段没被踩
        assert "projects" in new and "/x" in new["projects"]


# ---------------------------------------------------------------- #
# scan_all（端到端 — 用临时文件替换所有 adapter 路径）
# ---------------------------------------------------------------- #


def test_scan_all_reports_per_client_counts():
    d.reset_adapters()
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        cursor_cfg = td / "cursor.json"
        cline_cfg = td / "cline.json"
        cc_cfg = td / "claude.json"
        _write_json(cursor_cfg, {
            "mcpServers": {
                "fs": {"command": "npx", "args": ["x"]},
                "gh-guarded": {"command": "sentinel-mcp", "args": ["wrap", "--", "npx", "y"]},
            }
        })
        _write_json(cline_cfg, {"mcpServers": {"x": {"command": "npx", "args": ["x"]}}})
        _write_json(cc_cfg, {
            "mcpServers": {"global-x": {"command": "npx", "args": ["x"]}},
            "projects": {"/r": {"mcpServers": {"r-x": {"command": "npx", "args": ["x"]}}}},
        })
        _patch_simple_adapter("cursor", cursor_cfg)
        _patch_simple_adapter("cline", cline_cfg)
        _patch_claude_code_to(cc_cfg)
        # claude_desktop / roo_code 留空就行 — 它们看不到任何文件

        out = d.scan_all()
        clients = {c["key"]: c for c in out["clients"]}
        assert clients["cursor"]["server_count"] == 2
        assert clients["cursor"]["protected_count"] == 1
        assert clients["cline"]["server_count"] == 1
        assert clients["claude_code"]["server_count"] == 2
        # 全机扁平列表
        assert len(out["servers"]) == 5


# ---------------------------------------------------------------- #
# wrap_servers / unwrap_servers
# ---------------------------------------------------------------- #


def test_wrap_servers_wraps_then_skips_already_wrapped():
    d.reset_adapters()
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "cursor.json"
        _write_json(cfg, {"mcpServers": {
            "fs": {"command": "npx", "args": ["-y", "@x/fs"]},
            "gh": {"command": "sentinel-mcp", "args": ["wrap", "--", "npx", "@x/gh"]},
        }})
        _patch_simple_adapter("cursor", cfg)

        # 第一次 wrap 两个：fs 应该被 wrap，gh 已 wrap → skipped
        results = d.wrap_servers([
            {"client_key": "cursor", "config_path": str(cfg), "scope": "", "server_name": "fs"},
            {"client_key": "cursor", "config_path": str(cfg), "scope": "", "server_name": "gh"},
        ])
        assert results[0].action == "wrapped"
        assert results[0].backup_path is not None
        assert results[1].action == "skipped"

        # 状态：两个 server 都 wrap 了
        new = json.loads(cfg.read_text())
        assert d.is_wrapped(new["mcpServers"]["fs"]) is True
        assert d.is_wrapped(new["mcpServers"]["gh"]) is True


def test_wrap_servers_one_failure_does_not_block_others():
    d.reset_adapters()
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "cursor.json"
        _write_json(cfg, {"mcpServers": {"fs": {"command": "npx", "args": ["x"]}}})
        _patch_simple_adapter("cursor", cfg)
        results = d.wrap_servers([
            {"client_key": "cursor", "config_path": str(cfg), "scope": "", "server_name": "fs"},
            # 第二条故意写错 server_name，触发 ValueError
            {"client_key": "cursor", "config_path": str(cfg), "scope": "", "server_name": "ghost"},
        ])
        assert results[0].ok is True and results[0].action == "wrapped"
        assert results[1].ok is False and "找不到" in (results[1].error or "")


def test_wrap_then_unwrap_round_trip():
    d.reset_adapters()
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "cursor.json"
        original = {
            "mcpServers": {
                "fs": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"], "env": {"FOO": "bar"}},
            }
        }
        _write_json(cfg, original)
        _patch_simple_adapter("cursor", cfg)

        d.wrap_servers([{"client_key": "cursor", "config_path": str(cfg), "scope": "", "server_name": "fs"}])
        d.unwrap_servers([{"client_key": "cursor", "config_path": str(cfg), "scope": "", "server_name": "fs"}])
        # 还原后 entry 应等价于原始
        new = json.loads(cfg.read_text())
        e = new["mcpServers"]["fs"]
        assert e["command"] == "npx"
        assert e["args"] == ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        assert e["env"] == {"FOO": "bar"}


def test_wrap_servers_on_claude_code_project_scope():
    d.reset_adapters()
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / ".claude.json"
        _write_json(cfg, {
            "projects": {"/repo-a": {"mcpServers": {"gh": {"command": "npx", "args": ["x"]}}}},
        })
        _patch_claude_code_to(cfg)

        results = d.wrap_servers([{
            "client_key": "claude_code",
            "config_path": str(cfg),
            "scope": "project:/repo-a",
            "server_name": "gh",
        }])
        assert results[0].action == "wrapped"
        new = json.loads(cfg.read_text())
        assert d.is_wrapped(new["projects"]["/repo-a"]["mcpServers"]["gh"]) is True


# ---------------------------------------------------------------- #
# restore_backup
# ---------------------------------------------------------------- #


def test_restore_backup_restores_original():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        cfg = td / "mcp.json"
        original = {"mcpServers": {"a": {"command": "npx", "args": ["a"]}}}
        cfg.write_text(json.dumps(original))
        # 用 atomic_write_json 触发备份
        d._atomic_write_json(cfg, {"mcpServers": {"a": {"command": "MUTATED", "args": []}}})
        # 找到刚生成的 backup
        backups = sorted(td.glob("mcp.json.sentinel-backup.*"))
        assert len(backups) == 1
        # 还原
        result = d.restore_backup(str(backups[0]))
        assert result["ok"] is True
        restored = json.loads(cfg.read_text())
        assert restored == original


def test_restore_backup_rejects_non_sentinel_files():
    with tempfile.TemporaryDirectory() as td:
        bogus = Path(td) / "random.bak"
        bogus.write_text("{}")
        try:
            d.restore_backup(str(bogus))
            raise AssertionError("应抛 ValueError")
        except ValueError as e:
            assert "非 sentinel 备份文件" in str(e)


# ---------------------------------------------------------------- #
# Chunk C: YAML / Goose / Zed (context_servers key) / VSCode workspace
# ---------------------------------------------------------------- #


def _patch_yaml_adapter(key: str, file_path: Path) -> None:
    a = d.ADAPTERS[key]
    a._paths_per_platform = {sys.platform: [str(file_path)]}


def test_yaml_adapter_continue_dev_lists_servers():
    d.reset_adapters()
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "config.yaml"
        # Continue.dev 的 YAML schema 关键就是顶层 mcpServers
        cfg.write_text(
            "name: my-config\n"
            "mcpServers:\n"
            "  fs:\n"
            "    command: npx\n"
            "    args:\n"
            "      - -y\n"
            "      - '@modelcontextprotocol/server-filesystem'\n"
            "      - /tmp\n"
            "  gh-guarded:\n"
            "    command: sentinel-mcp\n"
            "    args:\n"
            "      - wrap\n"
            "      - --\n"
            "      - npx\n"
            "      - '@x/gh'\n"
        )
        _patch_yaml_adapter("continue_dev", cfg)
        entries = d.ADAPTERS["continue_dev"].enumerate(cfg)
        assert len(entries) == 2
        by_name = {e.server_name: e for e in entries}
        assert by_name["fs"].is_protected is False
        assert by_name["gh-guarded"].is_protected is True
        assert by_name["fs"].upstream_args[0] == "-y"


def test_yaml_adapter_write_then_reload_round_trip():
    d.reset_adapters()
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "config.yaml"
        cfg.write_text("name: cfg\nmcpServers:\n  fs:\n    command: npx\n    args: [x]\n")
        _patch_yaml_adapter("continue_dev", cfg)
        # wrap fs
        d.wrap_servers([{"client_key": "continue_dev", "config_path": str(cfg), "scope": "", "server_name": "fs"}])
        # 重新扫
        entries = d.ADAPTERS["continue_dev"].enumerate(cfg)
        by = {e.server_name: e for e in entries}
        assert by["fs"].is_protected is True
        # name: cfg 顶层字段没被吞
        import yaml
        with open(cfg) as f:
            data = yaml.safe_load(f)
        assert data["name"] == "cfg"


def test_zed_uses_context_servers_key_not_mcp_servers():
    d.reset_adapters()
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "settings.json"
        # Zed 的 settings.json：放 mcpServers 里的不算，放 context_servers 里的才算
        _write_json(cfg, {
            "theme": "Solarized",
            "mcpServers": {"trap": {"command": "rm", "args": ["-rf"]}},  # 不该被识别
            "context_servers": {
                "fs": {"command": "npx", "args": ["-y", "@x/fs"]},
            },
        })
        d.ADAPTERS["zed"]._paths_per_platform = {sys.platform: [str(cfg)]}
        entries = d.ADAPTERS["zed"].enumerate(cfg)
        assert len(entries) == 1
        assert entries[0].server_name == "fs"


def test_zed_write_preserves_other_settings():
    d.reset_adapters()
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "settings.json"
        _write_json(cfg, {
            "theme": "Solarized",
            "context_servers": {"fs": {"command": "npx", "args": ["a"]}},
        })
        d.ADAPTERS["zed"]._paths_per_platform = {sys.platform: [str(cfg)]}
        d.wrap_servers([{"client_key": "zed", "config_path": str(cfg), "scope": "", "server_name": "fs"}])
        new = json.loads(cfg.read_text())
        assert new["theme"] == "Solarized"
        assert d.is_wrapped(new["context_servers"]["fs"]) is True


def test_goose_lists_only_stdio_extensions():
    d.reset_adapters()
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "config.yaml"
        cfg.write_text(
            "extensions:\n"
            "  - name: filesystem\n"
            "    type: stdio\n"
            "    cmd: npx\n"
            "    args: [-y, '@modelcontextprotocol/server-filesystem', /tmp]\n"
            "  - name: developer\n"
            "    type: builtin\n"  # builtin 不是 MCP server，应被过滤
            "  - name: github-guarded\n"
            "    type: stdio\n"
            "    cmd: sentinel-mcp\n"
            "    args: [wrap, --, npx, '@x/gh']\n"
        )
        # 把 Goose adapter 的搜索路径换成临时
        d.ADAPTERS["goose"].list_config_files = lambda fp=cfg: [fp]  # type: ignore[method-assign]
        entries = d.ADAPTERS["goose"].enumerate(cfg)
        # 应只列 2 个 stdio，不列 builtin
        assert len(entries) == 2
        names = {e.server_name for e in entries}
        assert names == {"filesystem", "github-guarded"}
        by = {e.server_name: e for e in entries}
        assert by["filesystem"].is_protected is False
        assert by["github-guarded"].is_protected is True
        # extract_upstream 拿到原 npx
        assert by["github-guarded"].upstream_command == "npx"


def test_goose_wrap_writes_back_to_extensions_list():
    d.reset_adapters()
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "config.yaml"
        cfg.write_text(
            "extensions:\n"
            "  - name: fs\n"
            "    type: stdio\n"
            "    cmd: npx\n"
            "    args: [-y, '@x/fs']\n"
        )
        d.ADAPTERS["goose"].list_config_files = lambda fp=cfg: [fp]  # type: ignore[method-assign]
        d.wrap_servers([{"client_key": "goose", "config_path": str(cfg), "scope": "", "server_name": "fs"}])
        # 重新读 — fs extension 的 cmd 应变成 sentinel-mcp
        import yaml
        with open(cfg) as f:
            data = yaml.safe_load(f)
        ext = data["extensions"][0]
        assert ext["name"] == "fs"
        assert ext["type"] == "stdio"
        assert "sentinel" in ext["cmd"] or "wrap" in ext["args"]


def test_vscode_workspace_adapter_finds_via_claude_projects():
    """VSCode adapter 通过 ~/.claude.json:projects 反推工作区，再找 .vscode/mcp.json。"""
    d.reset_adapters()
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        # 模拟一个工作区
        ws = td / "my_workspace"
        (ws / ".vscode").mkdir(parents=True)
        ws_cfg = ws / ".vscode" / "mcp.json"
        _write_json(ws_cfg, {"mcpServers": {"fs": {"command": "npx", "args": ["x"]}}})
        # 写一个伪 ~/.claude.json 让 VSCode adapter 找到这个 workspace
        fake_claude = td / "claude.json"
        _write_json(fake_claude, {"projects": {str(ws): {}}})
        # patch list_config_files 改读伪 claude.json
        def fake_list(self_=None, _cj=fake_claude):
            import json as _j
            with _cj.open() as f:
                d_ = _j.load(f)
            out = []
            for proj_path in (d_.get("projects") or {}).keys():
                p = Path(proj_path) / ".vscode" / "mcp.json"
                if p.exists():
                    out.append(p)
            return out
        d.ADAPTERS["vscode_native"].list_config_files = fake_list  # type: ignore[method-assign]
        files = d.ADAPTERS["vscode_native"].list_config_files()
        assert len(files) == 1
        entries = d.ADAPTERS["vscode_native"].enumerate(files[0])
        assert len(entries) == 1
        assert entries[0].server_name == "fs"
        assert entries[0].scope.startswith("workspace:")


def test_scan_all_includes_all_chunk_c_clients():
    """注册表里至少包含 12 个 client（5 P0 + 7 P1）。"""
    d.reset_adapters()
    keys = set(d.ADAPTERS.keys())
    for required in {"claude_code", "claude_desktop", "cursor", "cline", "roo_code",
                     "continue_dev", "windsurf", "zed", "cline_cli", "lm_studio", "goose", "vscode_native"}:
        assert required in keys, f"missing adapter: {required}"


def test_scan_all_includes_phase2_clients():
    """Phase 2 扩展：再加 8 个 client，总数 20+。"""
    d.reset_adapters()
    keys = set(d.ADAPTERS.keys())
    for required in {"chatbox", "cherry_studio", "deepchat", "fire5", "anythingllm",
                     "crush", "opencode", "open_webui"}:
        assert required in keys, f"missing phase 2 adapter: {required}"
    assert len(keys) >= 20, f"expected ≥20 adapters, got {len(keys)}"


# ---------------------------------------------------------------- #
# Runner
# ---------------------------------------------------------------- #


if __name__ == "__main__":
    # 备份初始 ADAPTERS，每个 test 后 reset；最终全部 reset
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
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
