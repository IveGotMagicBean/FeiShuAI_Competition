# Sentinel-MCP Proxy

> 在 Cursor / Claude Desktop 和上游 MCP server 之间插一层安全代理。
> 所有 `tools/call` 经过 Guard 决策；DENY 直接返给客户端错误响应不下发；REDACT 改写参数后下发；ALLOW 透传。

## 工作原理

```
┌──────────────┐  stdio (JSON-RPC)  ┌──────────────────┐  stdio  ┌──────────────────┐
│ Cursor /     │ ─────────────────► │ sentinel-mcp     │ ──────► │ upstream MCP     │
│ Claude       │ ◄───────────────── │ proxy            │ ◄────── │ server           │
│ Desktop      │                    │  · Guard 决策    │         │ (filesystem,     │
└──────────────┘                    │  · 审计落库       │         │  github, ...)    │
                                    └──────────────────┘         └──────────────────┘
                                              │
                                              ▼
                                    ┌──────────────────┐
                                    │ SQLite audit log │
                                    └──────────────────┘
```

- MCP stdio transport：每条 JSON-RPC 2.0 消息一行（newline-delimited）
- 只拦截 `tools/call` 方法；其他 RPC（initialize / tools/list / resources/* …）原样透传
- 上游 stdout → 客户端方向当前透传；W2 加 L4 DLP 输出脱敏

## 安装（开发期）

需要 Python ≥ 3.10。

```bash
# 项目根目录
pip install pyyaml fastapi rich              # v0.1 已有的依赖
# v0.2 W1 末打包成 PyPI 后可改为：pip install sentinel-mcp
```

## 快速验证

```bash
cd 0427_test01
python tests/test_proxy.py
# 期望输出：5/5 passed
```

## CLI 用法

```bash
sentinel-mcp wrap [--config <yaml>] [--audit-db <path>] -- <upstream-cmd> [args...]
```

例：包装官方 filesystem MCP server，限制只能访问 `~/work`：

```bash
sentinel-mcp wrap \
    --config 0425_test01/config/policies.yaml \
    -- npx -y @modelcontextprotocol/server-filesystem ~/work
```

直接 `python -m sentinel_mcp.cli wrap -- ...` 也行。

## 接入 Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)：

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "sentinel-mcp",
      "args": [
        "wrap",
        "--config", "/Users/you/sentinel/policies.yaml",
        "--",
        "npx", "-y", "@modelcontextprotocol/server-filesystem", "/Users/you/work"
      ]
    }
  }
}
```

完整示例见 `examples/claude_desktop_config.json`。

## 接入 Cursor

`~/.cursor/mcp.json`：同上结构。

## 决策语义

| Guard.check_tool_call 返回 | Proxy 行为 |
|---------------------------|-----------|
| `Decision.ALLOW` | 透传给上游 |
| `Decision.REDACT` | 改写 `params.arguments` 为 `redacted_args` 后透传 |
| `Decision.DENY` | 不下发上游；直接给客户端回 `error: -32000` |
| `Decision.ASK_USER` | **W1 暂当 DENY**；W3 改成异步等手机审批回调 |

## 审计日志

每次 `tools/call` 都会写一条到 `--audit-db`（默认 `./data/sentinel_mcp_audit.db`）。
启动 v0.1 dashboard（`bash 0425_test01/scripts/run_dashboard.sh`）可实时看。

## v0.2 路线（W1）

| 已完成 | 待做 |
|-------|-----|
| ✅ stdio 透传 + tools/call 拦截 | ⏳ L4 DLP 输出脱敏（W2） |
| ✅ DENY / REDACT / ALLOW 三路决策 | ⏳ ASK_USER 异步化（W3 + 移动端审批） |
| ✅ 审计 SQLite | ⏳ pip 包发布（W1 末） |
| ✅ 5/5 单测 | ⏳ 端到端集成测试（W1 末，配真实 Cursor） |

## 已知限制

- ASK_USER 当前等同 DENY（W3 完成移动端推送+审批回调后修复）
- 上游 stderr 直接透传，未走 Guard
- 单条 JSON-RPC 消息最大长度受 `asyncio.StreamReader.readline()` 默认限（64KB），大文件传输需调高
- Windows 下 `asyncio.connect_read_pipe(stdin)` 兼容性需 W2 验证
