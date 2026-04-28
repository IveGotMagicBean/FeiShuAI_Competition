# Sentinel-MCP · 安装指南

> 适用版本：0.2.0

## 系统要求

- Python ≥ 3.10（推荐 3.12）
- 任意支持 stdio MCP 的客户端（Cursor / Claude Desktop / Continue / 自研 Agent）
- 可选：Node.js（如要用 `@modelcontextprotocol/server-filesystem` 之类官方上游）

## 方式 A — 从 PyPI 安装（推荐普通用户）

```bash
pip install "sentinel-mcp[dashboard]"
```

只装代理（不装 PWA dashboard 依赖）：

```bash
pip install sentinel-mcp
```

验证：

```bash
sentinel-mcp version
# → sentinel-mcp 0.2.0-dev
```

## 方式 B — 从源码安装（推荐开发者）

```bash
git clone https://github.com/IveGotMagicBean/FeiShuAI_Competition.git
cd FeiShuAI_Competition/0427_test01
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dashboard,dev]"
```

跑测试：

```bash
python tests/test_proxy.py            # 5/5
python tests/test_ask_user_e2e.py     # 5/5
python tests/test_e2e_smoke.py        # 4/4
python -m tests.attack_cases.run_all  # 53/53
```

## 方式 C — Tauri 桌面应用

> 该方式仍在 W2 周期内，scaffold 已就绪但 release 安装包尚未发布。
> 关注 [GitHub Releases](https://github.com/IveGotMagicBean/FeiShuAI_Competition/releases)。

## 配置 MCP 客户端

### Claude Desktop

打开 `~/Library/Application Support/Claude/claude_desktop_config.json`（macOS）或
`%APPDATA%/Claude/claude_desktop_config.json`（Windows），把原来的：

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/Users/me/work"]
    }
  }
}
```

改成：

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "sentinel-mcp",
      "args": ["wrap", "--", "npx", "-y", "@modelcontextprotocol/server-filesystem", "/Users/me/work"]
    }
  }
}
```

重启 Claude Desktop。从此所有 `tools/call` 都会经过 Sentinel 代理。

### Cursor / Continue / 其他

类似：找到 MCP server 启动命令，前面加 `sentinel-mcp wrap -- ` 即可。

## 启动 Dashboard

```bash
python -m pwa_dashboard.server
# → http://localhost:8766
```

把这个 URL 在手机浏览器打开（同 Wi-Fi 下用本机 IP 替换 localhost），
点「添加到主屏」即可装为 PWA。

## 自定义策略

复制 `sentinel_mcp/config/policies.yaml`（pip 装包后位于 site-packages 里）改一份，
然后启动时加 `--config /path/to/your.yaml`：

```bash
sentinel-mcp wrap --config ./my_policies.yaml -- <upstream-cmd>
```

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `SENTINEL_DB` | `./data/sentinel.db` | 审计 SQLite 路径（CLI + Dashboard 共用） |
| `SENTINEL_ASK_TIMEOUT` | `60` | ASK_USER 等待秒数；超时按拒绝 |
| `SENTINEL_PORT` | `8766` | Dashboard 监听端口 |
| `SENTINEL_SSE_INTERVAL` | `0.5` | Dashboard SSE 轮询间隔（秒） |

## 卸载

```bash
pip uninstall sentinel-mcp
rm -rf ./data/sentinel.db    # 可选：清审计数据
```
