# Sentinel-MCP · 5 分钟演示视频逐字稿

> 这是 `04_DEMO_SCRIPT.md` 的录屏可执行版。
> **左栏**：你说的话（直接念）。**右栏**：画面切换提示。
> 总时长目标：4'30" — 5'00"。

## 录屏前的准备（一次性，5 分钟）

1. **OBS Studio 装好**（https://obsproject.com/）
2. **场景**：建 1 个场景，加 3 个源
   - 「显示器捕获」全屏
   - 「窗口捕获」一个浏览器窗口（dashboard）
   - 「视频采集设备」（手机投屏，用 scrcpy 或 OBS Mobile 插件）
3. **三窗口排好**：
   - 左半屏：终端 A（dashboard 服务）
   - 右上：浏览器（dashboard 页）
   - 右下：终端 C（demo 命令）
   - 手机投屏：右上角小窗
4. **预跑一遍 0 节准备**让 dashboard 跑起来，关掉所有不必要的通知

---

## 0:00 — 0:15 · 开场（15 秒）

| 念稿 | 画面 |
|---|---|
| 「大家好，我是 [你的名字]，给大家演示 **Sentinel-MCP**——一个客户端侧的 AI Agent 安全框架。」 | 标题卡：项目 logo + 名字 + 口号「Guard every tool call」（OBS 文字源 5 秒） |
| 「5 分钟，看完你会知道：它解决了什么问题、怎么解决的、效果有多明显。」 | 切到桌面 |

---

## 0:15 — 0:45 · 痛点（30 秒）

| 念稿 | 画面 |
|---|---|
| 「现在主流的 AI Agent，比如 Cursor、Claude Desktop，已经能直接调用真实工具——读你的文件、跑 shell、发 HTTP 请求。」 | 切到 Cursor 窗口（如果有），或 OWASP LLM Top 10 截图 |
| 「问题是，一旦上下文里混进恶意输入——可能是用户输入、爬下来的网页、甚至工具自己返回的内容——Agent 就有可能被劫持去做超出你授权的事。比如把你的 SSH 私钥发到外网。」 | 维持画面 |
| 「这不是理论攻击，已经有真实案例。Sentinel-MCP 在 Agent 和工具之间插一层代理，把每次工具调用都过一遍策略引擎。」 | 切到架构图（README 里的 ASCII 图） |

---

## 0:45 — 2:15 · 现场拦截 SSH 私钥读取（90 秒）

| 念稿 | 画面 |
|---|---|
| 「先看一个最直接的攻击。我现在让 AI Agent 读 `~/.ssh/id_rsa`——这是 SSH 私钥，绝对不能泄露。」 | 切到终端 C，输入命令但还不回车： |
|  | `echo '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"read_file","arguments":{"path":"~/.ssh/id_rsa"}}}' \| sentinel-mcp wrap -- cat` |
| 「我用 `sentinel-mcp wrap` 在前面包了一层。这一层就是 Sentinel——它会把请求拦下来，过策略，再决定要不要透传。回车——」 | **回车** |
| （终端立刻打印拦截信息）「看，请求**根本没下发**——`decision=deny`，命中了 `filesystem` 沙箱的私钥保护规则。返回给 Agent 的是一条 JSON-RPC 错误。」 | 高亮终端输出红字部分 |
| 「同时，dashboard 立即收到这条事件——」 | 切到浏览器 dashboard |
| 「左侧 audit 列表多了一条红色 DENY，右上角风险分 1.00。整个过程从拦截到展示，**不到 100 毫秒**。」 | 鼠标点一下那条 DENY 卡片，让详情展开 |

---

## 2:15 — 3:00 · 三色决策快览（45 秒）

| 念稿 | 画面 |
|---|---|
| 「Sentinel 不是简单的允许/拒绝。它有四种决策状态：」 | 切到 dashboard 右上角图例 |
| 「**绿色 ALLOW** ——白名单内的安全调用，比如读 `/tmp/test.txt`，直接放行。」 | 鼠标依次悬停每个 pill |
| 「**红色 DENY** ——刚才看到的，敏感路径直接拒。」 |  |
| 「**蓝色 REDACT** ——比如返回内容里夹着 OpenAI key，原地脱敏成 `sk-***`，调用照常。」 |  |
| 「**橙色 ASK_USER** ——中危操作，等你亲自确认。下一步演示这个。」 |  |

---

## 3:00 — 4:30 · 人在回路 + 手机锁屏批准（90 秒，**最高潮**）

| 念稿 | 画面 |
|---|---|
| 「最有意思的是 ASK_USER。比如 `write_file` 是中危——Agent 想往磁盘写东西，我希望我*亲自*确认。」 | 切回终端 C |
| 「现在跑这个——」 | 输入并回车： |
|  | `echo '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"write_file","arguments":{"path":"/tmp/demo.txt","content":"hello"}}}' \| sentinel-mcp wrap -- cat` |
| （终端挂住）「看，终端**挂住等待**了。Sentinel 发出审批请求，挂起整个调用链。」 | 镜头停留在挂住的终端 1-2 秒 |
| 「同时——」 | 切到 dashboard 「待审批」tab |
| 「dashboard 待审批队列里立刻冒出橙色卡片。**而且**——」 | 切到手机投屏画面 |
| 「我手机锁屏弹出通知，『Sentinel-MCP - write_file 请求授权』。」 | 等手机推送弹出（3-5 秒），用慢动作或停帧强调 |
| 「我现在直接在手机上点 Approve——」 | **手机上点 Approve** |
| 「看——」 | 切到三屏拼接：终端解锁 + dashboard 卡片变绿 + 手机状态变绿 |
| 「手机一点，桌面终端解锁、调用透传到上游、dashboard 状态同步变绿。**全程低于 1 秒**，跨设备、跨进程，零网络配置。」 | 镜头扫过三屏 |

---

## 4:30 — 4:55 · 数字 + 收尾（25 秒）

| 念稿 | 画面 |
|---|---|
| 「最后看一组数字。我维护了 **53 条**红队攻击用例，覆盖 OWASP LLM Top 10 主要类别——目前 **53/53 全过**。决策延迟平均 **4 毫秒**，P99 7.86 毫秒。」 | 切到 README 的攻击徽章 / 性能表 |
| 「Sentinel-MCP 是协议层的安全代理，对 Agent 应用**零侵入**。装一次，所有走 MCP 的客户端都受保护——Cursor、Claude Desktop、Cline、Continue 等等。」 | 切到 GitHub Releases 页 |
| 「桌面三平台 + Android 全套安装包都在 Releases 页，开源 Apache-2.0。谢谢。」 | 收屏到 logo + GitHub URL |

---

## 录屏后剪辑提示

- **剪掉**：所有「等待响应」的死时间（>2 秒就剪）
- **保留**：手机推送弹出那一刻——这是全片最强镜头，不要剪
- **加字幕**：所有终端命令 + 决策结果（白底黑字）
- **加 BGM**：低音量节奏感的 lo-fi 即可（YouTube Audio Library 有免费的）
- **导出**：1080p 30fps，时长 4'30"-5'00"

---

## 演示备用 Q & A（评委可能问）

| Q | A |
|---|---|
| 性能在生产环境怎么样？ | 决策走纯 Python 规则 + SQLite，平均 4ms。瓶颈在 LLM 调用本身，不在 Sentinel。 |
| 模型怎么知道哪些是敏感操作？ | 模型不知道——是 YAML 策略明确声明。这恰是优点：策略可审计、可版本化、不依赖模型对齐。 |
| 如果 Agent 直接调 `os.system` 不走 MCP？ | 那保护不到——这是深度防御层不是银弹。需要配合代码沙箱（gVisor/Firejail）。 |
| 和 LangChain / LlamaIndex 区别？ | 那些是 Agent 框架，我们是给任意框架包外壳的安全代理。可叠加。 |
| 移动端 .apk 怎么和桌面通信？ | TWA 加载 cloudflared 隧道暴露的 dashboard URL，桌面 Sentinel 写本地 SQLite，dashboard 通过 SSE 推到手机。 |
