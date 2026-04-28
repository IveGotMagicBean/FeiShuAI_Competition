# Sentinel-MCP · 技术方案

> 适用版本：0.2.0 · 最后更新：2026-04-28

## 1. 问题定义

> AI Agent 已经从单纯的「问答模型」演进为可调用真实工具（文件、网络、Shell、剪贴板）的执行者。
> 一旦上下文中混入恶意输入（用户输入 / RAG 文档 / 工具返回），Agent 可能被劫持去做：
> 偷敏感文件、外发数据、远程执行任意代码、扫内网。
>
> 现有缓解手段大多依赖**模型对齐**或**应用层过滤**——前者不可证、后者依赖每个应用单独实现。
> Sentinel-MCP 的目标是：在**协议层**（MCP stdio）插一个**统一可观测、可策略化、可人在回路审批**的安全代理。

## 2. 系统架构

```
  ┌─ Cursor / Claude Desktop / 自研 Agent ──┐
  │                                          │
  └────────────┬─────────────────────────────┘
               │ stdin / stdout (JSON-RPC 2.0, newline-delimited)
               ▼
  ┌──────────────────────────────────────────────────────┐
  │  Sentinel-MCP Proxy（sentinel_mcp/proxy.py）          │
  │  ──────────────────────────────────────────────────  │
  │  client_to_upstream():    解析 → 拦截 → 决策 → 转发   │
  │  upstream_to_client():    透传（v0.3 接 L4 DLP）       │
  └──────────────┬───────────────────────────────────────┘
                 │ check_tool_call(call)
                 ▼
  ┌──────────────────────────────────────────────────────┐
  │  Guard 决策引擎（guard/core.py）                       │
  │  ┌──────────┐  ┌────────────┐  ┌──────────────┐       │
  │  │ L1 注入  │→ │ L2 策略    │→ │ L3 沙箱     │       │
  │  │ 检测     │  │ 决策       │  │ 执行验证    │       │
  │  └──────────┘  └────────────┘  └──────────────┘       │
  │            │       │                  │              │
  │            ▼       ▼                  ▼              │
  │     ┌─────────────────────────────────────┐          │
  │     │  GuardResult { decision, risk, … }  │          │
  │     │  decision ∈ {ALLOW, DENY, REDACT,   │          │
  │     │              ASK_USER}              │          │
  │     └─────────────────────────────────────┘          │
  └────────────┬─────────────────────────┬───────────────┘
               │ ALLOW/REDACT            │ ASK_USER
               ▼                         ▼
       上游 MCP server          ┌────────────────────────┐
                                │ PendingDecisions       │
                                │ (SQLite WAL 共享队列)  │
                                └─────────┬──────────────┘
                                          │ SSE 推送
                                          ▼
                                ┌────────────────────────┐
                                │ PWA Dashboard          │
                                │ - 实时事件             │
                                │ - 待审批面板           │
                                │ - 手机端通知           │
                                └────────────────────────┘
```

## 3. 关键设计决策

### 3.1 为什么是 stdio 代理而不是装饰器 SDK？

v0.1 我们用 Python 装饰器 `@guard.protected("filesystem")` 包工具函数，
缺点是**侵入式**：每个 Agent 框架都要重新接一遍，且只能保护 Python 工具。

MCP 是 Anthropic 推的工具调用开放协议，已被 Cursor、Claude Desktop、
Continue 等主流客户端采用。**在 MCP stdio 这层做代理**，等于一次接入、所有
MCP-aware 的 Agent / 客户端 / 服务端组合都能受益，且**对应用零改造**。

### 3.2 决策的四态而不是二态

二态（允许 / 拒绝）会迫使所有边界情况被一刀切处理：要么过度宽松、要么过度严格。
我们引入：

| 决策 | 触发条件 | 处理 |
|---|---|---|
| `ALLOW` | 通过所有沙箱检查、无需用户授权 | 透传给上游 |
| `DENY` | 命中 denylist / 注入分 ≥ 0.5 / 速率超限 | 直接给客户端回 -32000 error |
| `REDACT` | 命中输出 DLP（v0.3 出向接） | 改写参数 / 输出后转发 |
| `ASK_USER` | 工具配置 `require_user_authz: true` | 写 SQLite pending 行，阻塞等审批 |

### 3.3 ASK_USER 异步审批的实现挑战

**问题**：Guard.check_tool_call 是同步函数；用户审批需要跨进程（Dashboard 在另一个进程）。
naive 实现会让 asyncio event loop 挂死。

**方案**：

1. **共享 SQLite (WAL 模式)** 当跨进程消息总线 — 不需要管连接、能持久化历史
2. **callback 内部 sleep-poll** SQLite，状态变 `approved` / `denied` / `expired` 即返回
3. **proxy 用 `loop.run_in_executor`** 把同步 Guard 调用挪到线程池，event loop 仍能收发其他消息

```python
# proxy.py - 关键一行
loop = asyncio.get_running_loop()
result = await loop.run_in_executor(None, self.guard.check_tool_call, call)
```

### 3.4 实时事件流：SSE 而不是 WebSocket

Dashboard 是单向推送（server → browser），SSE 原生有自动重连，
比 WS 简单一半。每个客户端各自维护游标轮询新行；500ms 间隔；闲时
`: ping\n\n` 心跳防中间代理掐断。

事件类型：`hello` / `event`（audit）/ `pending`（新待审批）/ `decided`。

## 4. 防御层细节

### L1 — 输入层（Prompt Injection）

`guard/detectors/prompt_injection.py` 维护规则库（**33 条**），覆盖：

- 角色覆写 (R-INJ-001..005)：ignore previous / 忽略前面
- 越狱 (R-INJ-010..019, 060..064)：DAN / developer mode / 扮演奶奶 / 自称管理员
- 系统消息伪造 (R-INJ-020..023)：`[SYSTEM]:` / `<|im_start|>` / `</user><system>`
- 工具特征 (R-INJ-030..034, 070..071)：敏感文件读取意图、外发意图、续写攻击
- 编码混淆 (R-INJ-040..042)：base64、hex、零宽字符
- 边界欺骗 (R-INJ-050..051)：`### NEW INSTRUCTION` / `---`

每条规则带权重，累加到风险分；`risk ≥ 0.5` 触发 DENY。

### L2 — 工具调用层（策略）

`config/policies.yaml` 的 `tools.<name>` 段：

- `policy: filesystem | network | shell` 选用沙箱
- `require_user_authz: bool` 是否触发 ASK_USER

### L3 — 沙箱层

| 沙箱 | 实现 | 关键字段 |
|---|---|---|
| 文件系统 | `guard/sandbox.py:FilesystemSandbox` | `allowlist` + `denylist`（glob） |
| 网络 | `guard/sandbox.py:NetworkSandbox` | `allowed_domains` + `blocked_domains` + `block_private_ip` |
| Shell | `guard/sandbox.py:ShellSandbox` | `allowlist` + `blocked_patterns` (regex) |

### L4 — 输出层（DLP）

13 个内置敏感模式：API key（OpenAI / Anthropic / Google）、JWT、AWS Access Key、
Stripe key、PEM 私钥块、邮箱、身份证、信用卡、SSN、IP 地址、手机号、UUID。
当前在 dashboard 层使用；proxy 出向脱敏列入 0.3 计划。

## 5. 性能数据

53 条攻击集回归（本机 WSL2 / Ubuntu 22.04 / Python 3.12）：

- 通过率 **53/53 = 100%**
- 平均决策耗时 **4.09 ms**
- P99 **7.86 ms**

## 6. 已知限制

- Sentinel-MCP 是**深度防御**层，不是银弹。要求 Agent 调用的所有工具都走 MCP；
  否则旁路 MCP 直接执行 Python 代码不在保护范围内。
- Prompt Injection 检测是**规则 + 启发式**，不能 100% 覆盖。后续会加入小模型分类器作为 L1 补强。
- 当前 ASK_USER 没有 Web Push（手机锁屏推送），手机要打开浏览器 Tab 才能收到 Notification。
  W2 将接 VAPID + Cloudflare Workers relay。
