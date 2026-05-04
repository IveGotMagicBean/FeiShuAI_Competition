"""CLI 入口：

    sentinel-mcp wrap [--config <yaml>] -- <upstream-cmd> [<args>...]

例：

    sentinel-mcp wrap --config config/policies.yaml -- \
        npx -y @modelcontextprotocol/server-filesystem /home/me/work
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from guard import Guard
from sentinel_mcp.approvals import PendingDecisions
from sentinel_mcp.proxy import MCPProxy

# 默认策略：包内自带 sentinel_mcp/config/policies.yaml，pip install 之后也能找到。
# 默认审计 DB：放当前 cwd 下的 data/sentinel.db；环境变量 SENTINEL_DB 优先。
_PKG_ROOT = Path(__file__).resolve().parent  # sentinel_mcp/
_DEFAULT_POLICY = _PKG_ROOT / "config" / "policies.yaml"
_DEFAULT_DB = Path.cwd() / "data" / "sentinel.db"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sentinel-mcp",
        description="MCP 工具调用安全代理（Sentinel-MCP v0.2）",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    wrap = sub.add_parser("wrap", help="包装一个上游 MCP server")
    wrap.add_argument(
        "--config",
        default=str(_DEFAULT_POLICY),
        help="Guard 策略 YAML（默认复用 v0.1 的 config/policies.yaml）",
    )
    wrap.add_argument(
        "--audit-db",
        default=os.environ.get("SENTINEL_DB", str(_DEFAULT_DB)),
        help="审计 SQLite 路径（默认 0427_test01/data/sentinel.db；可用 SENTINEL_DB 覆盖）",
    )
    wrap.add_argument(
        "--ask-timeout",
        type=float,
        default=float(os.environ.get("SENTINEL_ASK_TIMEOUT", "60")),
        help="ASK_USER 等待审批的超时秒数（超时按拒绝处理；默认 60s，可用 SENTINEL_ASK_TIMEOUT 覆盖）",
    )
    wrap.add_argument(
        "upstream",
        nargs=argparse.REMAINDER,
        help="位于 -- 之后的上游 MCP server 命令",
    )

    sub.add_parser("version", help="显示版本")

    # hook-check：Claude Code PreToolUse hook 入口（单进程模式，stdin 收 JSON 决策一次）
    hook = sub.add_parser(
        "hook-check",
        help="作为 Claude Code 等客户端的 PreToolUse hook 被调用 — 从 stdin 读 JSON，按 policy 拒绝/放行",
    )
    hook.add_argument("--config", default=str(_DEFAULT_POLICY), help="Guard 策略 YAML")
    hook.add_argument(
        "--audit-db",
        default=os.environ.get("SENTINEL_DB", str(_DEFAULT_DB)),
        help="审计 SQLite 路径",
    )
    hook.add_argument(
        "--structured-output", action="store_true",
        help="输出 JSON 而不仅是退出码（Claude Code v0.3+ 支持）",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "version":
        from sentinel_mcp import __version__
        print(f"sentinel-mcp {__version__}")
        return 0

    if args.cmd == "hook-check":
        from sentinel_mcp.hook import run_hook_check
        return run_hook_check(
            config_path=args.config,
            audit_db=args.audit_db,
            structured_output=args.structured_output,
        )

    upstream = list(args.upstream or [])
    if upstream and upstream[0] == "--":
        upstream = upstream[1:]
    if not upstream:
        parser.error("缺少上游命令；用法：sentinel-mcp wrap -- <cmd> [args...]")

    cfg = Path(args.config)
    if not cfg.exists():
        parser.error(f"策略文件不存在：{cfg}")

    Path(args.audit_db).parent.mkdir(parents=True, exist_ok=True)

    # 审批队列与审计共用同一个 SQLite 文件，让 dashboard 在一个 DB 里就能看到
    # 审计 + pending 全套；多 Proxy 实例并发写也由 SQLite WAL 模式保证安全。
    approvals = PendingDecisions(args.audit_db)
    ask_callback = approvals.make_callback(timeout_seconds=args.ask_timeout)

    guard = Guard.from_yaml(
        str(cfg),
        audit_db_path=args.audit_db,
        ask_user_callback=ask_callback,
    )
    proxy = MCPProxy(upstream_cmd=upstream, guard=guard)

    try:
        return asyncio.run(proxy.run())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
