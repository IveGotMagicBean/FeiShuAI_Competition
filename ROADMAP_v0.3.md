# Sentinel-MCP v0.3 路线图（赛后 5/9 起）

> v0.2 的飞书 AI 校园挑战赛版本提交后（5/8 截止），下周开始做 v0.3。
>
> 本文是**给评委看的「我们后续要做什么」清单**，也是赛后实际开发的施工蓝图。

## 整体定位

v0.2 = **演示版 / 评委可装可跑可看**
v0.3 = **真实可日用 / 工程级稳定**

把 demo 级粗糙的地方都抹平，每条都对应 v0.2 在使用时被吐槽的痛点。

---

## 三大重点（按价值排）

### 🎯 重点 1：手机端二次升级

v0.2 手机端走通了 .apk 安装 + dashboard 镜像，但 Web Push 锁屏推送没跑通、TWA 顶部还有 URL 栏、小屏 layout 有挤。这一块是「评委演示之外，真实用户最先抱怨」的体验黑洞。

| 子任务 | 工作量 | 价值 |
|---|---|---|
| **修通 Web Push 锁屏推送** | 1 个晚上 | 真正实现「桌面拦截 → 手机锁屏弹」无飞书也能用 |
| **assetlinks.json 校验**：让 .apk 顶部 URL 栏隐藏，看起来像原生 app | 30 分钟 | 视觉上跟原生 app 等价，从「网页壳」升到「真 app」 |
| **响应式 UI 优化**：统计卡片 / 按钮 / modal 在 360px 宽屏上不挤 | 半天 | 手机直接刷 dashboard 也好用，不依赖 PWA 安装 |
| **iOS Safari 兼容性**：Web Push 在 iOS 16.4+ 才支持，需要单独路径 | 1 天 | 评委如果是 iPhone 用户也能完整体验 |
| **离线模式**：Service Worker 缓存 dashboard 静态资源，断网仍能查最近事件 | 1 天 | PWA 标准能力，提升完整度 |

---

### 🎯 重点 2：注入检测从「正则」升级为「PromptGuard 模型驱动」

v0.2 用 33 条正则规则，对**已知模式**全覆盖（53/53 攻击集都过），但**漏检**对抗性 prompt（精心包装的越狱）。

| 子任务 | 工作量 | 价值 |
|---|---|---|
| **接入 Meta `PromptGuard-86M`**：作为正则之外的第二道防线 | 1 个晚上 | 抓正则漏掉的语义层面攻击 |
| **加 30 条对抗性攻击集**：DAN / 角色扮演 / Token smuggling / 多轮诱导 / Unicode homograph 等 | 1 天 | 校园挑战赛验收时 53 条偏「显式攻击」，对抗性测试集是关键差异化 |
| **决策融合策略**：rule_score + model_score 怎么组合（取 max？加权平均？） | 半天 | 调阈值避免误杀正常请求 |
| **性能基准**：模型 inference 延迟 vs 现在 4ms 平均，能不能控在 10ms 内 | 半天 | 不能让安全代理变性能瓶颈 |
| **可选模型**：除了 86M，也支持更大的 IBM `granite-guardian-8b` 给重要场景 | 1 天 | 给用户「精度 vs 速度」二选一 |

技术细节见 `docs/01_TECHNICAL_DESIGN.md`「2.4 注入检测器」章节（v0.3 会补这一节）。

---

### 🎯 重点 3：真实本地 Agent 端到端测试

v0.2 全靠 `cat` 假上游 + `red_blue_demo.py` 模拟攻击。**没接过任何真实 LLM driving 的 Agent**，理论可用 vs 真用还有 gap。

| 子任务 | 工作量 | 价值 |
|---|---|---|
| **接 Cursor 实测**：把 Sentinel 包过的 filesystem MCP 配进 Cursor，让 Claude/GPT 真的去调 → 观察完整事件流 | 1 个晚上 | 验证「AI Agent 输出 tool_call → MCP → Sentinel → 上游」整链路在真模型驱动下不卡 |
| **接 Claude Desktop 实测**：同上，验证另一个客户端 | 1 个晚上 | 多客户端兼容性确认 |
| **接 Cline / Continue / VS Code MCP 插件实测** | 1 天 | 完整覆盖主流 MCP 客户端 |
| **接本地 LLM Agent**：Ollama + LangChain 自己写一个 agent，让它通过 MCP 调 Sentinel 包过的工具 | 1 天 | 证明不依赖云 LLM 也行；隐私敏感场景可选 |
| **录「真 Agent 攻击」对照视频**：让 Cursor 在被注入 prompt 后真的去读 SSH 私钥，看 Sentinel 拦下 | 1 天 | 比 mock 攻击有说服力 10 倍，是 v0.3 release demo 的 highlight |
| **写性能 / 准确率报告**：在真 Agent 流量下 P99 延迟 / FP rate / FN rate | 1 天 | 给企业用户决策依据 |

---

## 次重点（看时间）

### 拦截 lark-cli 的 shell 调用

v0.2 调研发现 lark-cli 是 shell 命令而不是 MCP 协议，AI Agent 通过 lark-cli 调飞书绕过 Sentinel。

**方案**：在 `guard/sandbox.py` 加 shell-level 模式识别 — 命令行匹配 `lark-cli * +messages-send` 等 → 提取参数 → 走 Guard 决策。

工作量：1 周（要解析 lark-cli 全部 200+ 命令的参数语义）。

### Cloudflared 命名隧道 + 自有域名

替换 trycloudflare 临时 URL，让回调 URL 稳定不变。需要买个域名（10 元/年的也行）。

工作量：1 个晚上。

### OS Keychain 存凭证

App Secret / Encrypt Key 当前明文存 `~/.sentinel-mcp/lark_config.json`（chmod 0o600）。生产级要接 macOS Keychain / Windows Credential Manager / Linux Secret Service。

工作量：2 天。

### 多用户 / 团队共享审计 DB

当前 audit DB 是本地 SQLite。企业场景要多人共享一份审计日志（比如团队的 Cursor 调用都集中到一个安全团队的看板）。

方案：可选 PostgreSQL 后端 + 行级权限。

工作量：1 周。

### 飞书多维表格审计同步

把 Sentinel 事件实时同步到一份飞书多维表格，团队可以用飞书原生 BI 看 / 筛 / 报警。

工作量：3 天。

---

## 时间表（v0.3 4 周）

| 周 | 重点 |
|---|---|
| **W1** 5/9 - 5/15 | 手机端三件事（Web Push / assetlinks / 响应式）+ 真接 Cursor + Claude Desktop |
| **W2** 5/16 - 5/22 | PromptGuard 接入 + 30 条对抗性攻击集 + 决策融合 + 性能基准 |
| **W3** 5/23 - 5/29 | 录 v0.3 release demo 视频（真 Agent 攻击对照）+ 写注入检测设计文档 |
| **W4** 5/30 - 6/5 | 自由次重点：lark-cli sandbox / OS Keychain / 命名隧道 选 1-2 项 |

5/9 启动 → 6/5 v0.3 发布。

---

## 不在 v0.3 范围（v0.4+ 再说）

- 跨浏览器 Web Extension（Chrome/Firefox extension 形态接管浏览器内 Agent）
- 商业 SaaS 模式（多租户 / 计费 / SLA）
- 跟其它框架深度集成（LangChain / LlamaIndex / Semantic Kernel native plugins）
- Mobile native app（替代 PWA / TWA，纯 Swift / Kotlin 重写）

---

## 一句话总结

**v0.2 = 「能装能跑能看」**，**v0.3 = 「能日用」** —— 修通手机端 + 上模型检测 + 接真 Agent 跑通完整链路，从演示工具升级成真实产品。
