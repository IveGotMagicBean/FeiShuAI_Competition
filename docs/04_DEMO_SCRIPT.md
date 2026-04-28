# Sentinel-MCP · 演示脚本（5 分钟）

> 给评委 / 投资人 / 同事看的现场演示流程。
> 目标：在 5 分钟内让人**直观**理解「我们解决了什么问题、怎么解决的、效果多明显」。

## 0. 准备（演示前 5 分钟）

### 终端 A — Dashboard

```bash
cd 0427_test01
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
source .venv/bin/activate
python -m pwa_dashboard.server
```

浏览器打开 `http://localhost:8766` 留着，缩小到屏幕一半。
在手机上同样的 URL（替换为本机 IP）打开，点「添加到主屏」。

### 终端 B — Agent / 客户端模拟

预先在 Claude Desktop 里把 filesystem MCP server 包进 sentinel-mcp wrap（见安装指南）。
或者用：

```bash
sentinel-mcp wrap -- npx -y @modelcontextprotocol/server-filesystem ~/work
```

### 终端 C — 直接管道演示

```bash
# 演示用，留着不要执行
echo '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"read_file","arguments":{"path":"~/.ssh/id_rsa"}}}' | sentinel-mcp wrap -- cat
```

## 1. 痛点（30 秒）

> 「现在主流 AI Agent 框架，比如 Cursor、Claude Desktop，已经能调用真实工具——
> 读你的文件、跑 shell、发 HTTP 请求。一旦上下文里混进恶意输入，
> 不论是用户输入、爬下来的网页内容，还是工具自己返回的内容，
> Agent 就有可能被劫持去做超出用户授权的事——比如把你的 SSH 私钥发到外网。
> 这不是理论上的攻击，已经有真实案例了。」

切到 OWASP LLM Top 10 截图（如果有）；或者口述。

## 2. 现场演示越权拦截（90 秒）

切到终端 C，回车跑那条命令。

> 「我现在让 Agent 读 `~/.ssh/id_rsa`。
> 用 Sentinel-MCP 包了一层之后看会发生什么：」

终端会立刻打印：

```
[sentinel-mcp] tool_call name=read_file id=1 decision=deny risk=1.00 rules=['filesystem']
{"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "[Sentinel-MCP] blocked: 命中 denylist：~/.ssh/**", ...
```

> 「请求**根本没有下发到上游**——直接被代理拦了。
> 同时 Dashboard 立刻收到这条事件——」

切到 Dashboard 浏览器：左边 audit 列表多了一条红色 DENY，
右上角风险分 1.00。

## 3. 三色决策（60 秒）

> 「Sentinel 的决策不是简单的允许/拒绝。它有四种状态：」

| 颜色 | 决策 | 演示 |
|---|---|---|
| 绿 | ALLOW | 读 `/tmp/test.txt`，正常回显 |
| 红 | DENY | 刚才的 SSH 私钥 |
| 黄 | REDACT | （v0.2 演示时跳过） |
| 橙 | ASK_USER | 下一步 |

## 4. 人在回路审批 — 手机批准（90 秒）

> 「最有意思的是 ASK_USER。比如 `write_file` 是中危——
> Agent 想往磁盘写东西，我希望我*亲自*确认一下。」

在终端 C 跑：

```bash
echo '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"write_file","arguments":{"path":"/tmp/demo.txt","content":"hello"}}}' | sentinel-mcp wrap -- cat
```

终端会**挂住等待**。同时：

- Dashboard 浏览器右下「待审批队列」橙色卡片立刻冒出来
- **手机锁屏弹通知**「Sentinel-MCP - write_file 请求授权」（前提是 PWA 已装到主屏）

> 「现在我在**手机上**点 Approve——」

点击。手机和浏览器同步状态变绿，终端 C 立即解锁，工具被转发到上游。

## 5. 性能数字（30 秒）

切到 `data/attack_report.md`：

> 「我们维护了 53 条攻击用例，覆盖 OWASP LLM Top 10 里的注入、越权、SSRF、
> 命令注入等核心类别——目前是 **53/53 全过**。决策延迟平均 4 ms，P99 7.8 ms，
> 远小于 LLM 调用本身的几百毫秒。」

## 6. 收尾（30 秒）

> 「Sentinel-MCP 是**协议层**的安全代理，对 Agent 应用零侵入；
> 装一次，所有走 MCP 的 Cursor / Claude Desktop / 自研 Agent 都受保护。
>
> 它已经在 PyPI 上：`pip install sentinel-mcp`。
> 开源 Apache-2.0，仓库在 GitHub。
>
> 路线图上下一步是 W2 接 Web Push（让手机锁屏也能弹推送）和 Tauri 桌面壳；
> W3 把攻击集扩到 100+，加上模型类检测器。
>
> 谢谢。」

## 备用 Q & A

- **Q: 性能在生产环境怎么样？** A: 决策走纯 Python 规则 + SQLite，4 ms 平均。
  瓶颈不在 Sentinel，而在 LLM 调用本身。

- **Q: 模型怎么知道哪些是敏感操作？** A: 模型不知道——是 YAML 策略明确声明的。
  这恰恰是优点：策略可审计、可版本化、可由安全团队管控，不依赖模型对齐。

- **Q: 如果 Agent 直接调用 Python `os.system` 不走 MCP？** A: 那就保护不到——
  这是深度防御层不是银弹。需要配合应用层的代码沙箱（gVisor / Firejail）。

- **Q: ASK_USER 不会被 Agent 滥用 DDoS 用户吗？** A: 有速率限制
  （`rate_limits.<tool>.max`）+ 「本会话一次性授权」缓存，不会反复弹同一个工具。

- **Q: 和 LangChain / LlamaIndex 的区别？** A: 那些是 Agent 框架，
  我们是给任意 Agent 框架包一层外壳的安全代理。可叠加使用。
