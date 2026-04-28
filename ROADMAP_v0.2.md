---
项目: Sentinel-MCP（原 Agent Guard，团队 Sentinel）
版本: v0.2.0 (代号 "Public Alpha")
域名: sentinel-mcp.dev（待抢注）
GitHub: git@github.com:IveGotMagicBean/FeiShuAI_Competition.git
路线图制定日期: 2026-04-27
目标发布日期: 2026-05-27 (T+30 days)
路径: A · 务实版
---

# Sentinel-MCP · v0.2 路线图

> **2026-04-27 决策定档**：项目品牌 = Sentinel-MCP，域名 = sentinel-mcp.dev，
> 移动端选 PWA 走 P0+P1+P2 全做（推送/审批/只读 dashboard/策略编辑/多设备绑定）。
> 这两个决定让 W3 工作量增加 ~5 天，从 W4 缓冲日借。

## 0. 文档定位

本文档是从 v0.1 (内部 MVP) → v0.2 (公开 Alpha) 的 30 天工程交付计划。
不是产品愿景文档，是**可执行的周级落地清单**。

v0.1 已在 `0425_test01/` 完成（22/22 攻击用例通过，红蓝对照 Demo 可复现）。
v0.2 在此基础上，按**真实开源产品**标准推进。

---

## 1. 目标

> **30 天内发布 v0.2.0 公开 Alpha：开发者能 `pip install` / `brew install` 装上、
> 终端用户能下载签名安装包用、移动端能在手机上接收高风险事件推送并远程批准/拒绝。**

成功指标：
- 公开 GitHub 仓库 (Apache 2.0)，README + 文档站 + 至少 5 个真实 Star
- 三平台（macOS / Windows / Linux）签名安装包可下载
- MCP Proxy 接入 Cursor / Claude Desktop 验证可用
- PWA 移动端推送通知 + 审批回调闭环跑通
- 至少 3 个真实开发者用户装上并跑通自己的 Agent

---

## 2. 用户画像

| 角色 | 关心什么 | 接触点 |
|------|---------|--------|
| **开发者**（写 Agent 的人） | 代码侵入小、SDK 装得上、文档清晰、能扩展 | `pip install` / SDK / MCP Proxy / GitHub |
| **终端用户**（用 Cursor / Claude Desktop 的人） | 一键装、托盘常驻、看得懂 GUI、不卡 | 安装包 / 桌面 GUI / 通知 |

**v0.2 优先级**：开发者 > 终端用户。
开发者是早期用户，给反馈、写 PR；终端用户量上得来要等 v0.3 文档站 + 教学视频做厚之后。

---

## 3. v0.2.0 范围（要做的）

### 3.1 开发者侧
- ✅ MCP Proxy（让 Cursor / Claude Desktop 复制一行配置就接入）
- ✅ Python SDK 完善 + 发布到 PyPI（`pip install agent-guard`）
- ✅ CLI（`agent-guard init / run / dashboard`）
- ✅ 三个集成示例：Cursor、Claude Desktop、自定义 Python Agent

### 3.2 终端用户侧
- ✅ Tauri 2.0 桌面壳（macOS / Windows / Linux）
- ✅ 系统托盘 + 启动项 + 高风险事件原生通知
- ✅ GUI：策略编辑器 + 实时事件流 + 历史回放
- ✅ 安装包：`.dmg` / `.msi` / `.deb` / `.AppImage`
- ✅ Mac Notarization + Windows Authenticode 签名

### 3.3 移动端（PWA · P0+P1+P2 全做）
- ✅ PWA（Progressive Web App）方案，**不做原生 iOS/Android**
- ✅ **P0** Web Push API 推送高风险事件
- ✅ **P0** 手机点通知 → PWA 打开审批页 → 批准/拒绝回调
- ✅ **P1** 只读 dashboard：实时事件流 + 历史回放 + 风险卡片
- ✅ **P2** 策略编辑器：手机改 YAML 策略 + diff 预览 + 一键下发
- ✅ **P2** 多设备绑定：一个 PWA 账户管多台桌面探针
- ✅ 公共中继（Cloudflare Workers，免费层）+ 自部署 Docker 镜像

### 3.4 开源治理
- ✅ Apache 2.0 LICENSE
- ✅ CONTRIBUTING.md / CODE_OF_CONDUCT.md / SECURITY.md
- ✅ Issue / PR 模板
- ✅ GitHub Actions CI（lint + test + build × 3 平台）
- ✅ 发布流程：semver / CHANGELOG / GitHub Release / 自动签名打包
- ✅ 文档站（mkdocs-material 或 docusaurus）

---

## 4. v0.2.0 明确不做（推迟到 v0.3+）

| 推迟项 | 原因 |
|--------|------|
| 原生 iOS / Android app | App Store/Play Store 审核 1~2 周，纯开源 + 上架成本高 |
| 模型驱动的注入分类器（神经网络） | 规则引擎已经覆盖 95% 常见攻击，模型版放 v0.3 |
| 企业版（多用户/SSO/集中策略） | 当前面向个人开发者 + 终端用户 |
| Windows ARM64 / macOS Intel | 只构建 macOS Apple Silicon + Windows x86_64 + Linux x86_64 |
| 浏览器扩展形态 | 当前不支持 web-based Agent |
| 多语言 SDK（Node.js / Go / Rust） | 仅 Python，其他语言走 MCP Proxy |
| 飞书机器人 | 路径定下来，不做 |

---

## 5. 四周里程碑

### W1 · 4/28 ~ 5/4 · 开源治理 + 开发者闭环

**主线：让开发者能用上 v0.1 的能力。**

| Day | Owner | 交付 |
|-----|-------|------|
| 4/28 | you | 把 0425_test01 复制为 agent-guard/，git init，推到 GitHub（私有先） |
| 4/29 | you | LICENSE / README 大改 / CONTRIBUTING / SECURITY / CoC |
| 4/30 | you | GitHub Actions：lint (ruff) + test (pytest) + 跨平台构建矩阵 |
| 5/1  | you | MCP Proxy 设计 + 起骨架（基于 mcp Python SDK） |
| 5/2  | you | MCP Proxy 完成：能拦截 Cursor / Claude Desktop 的工具调用 |
| 5/3  | you | CLI 命令：`agent-guard init / run / dashboard` |
| 5/4  | you | 发布到 PyPI（test PyPI 先），写 5 个真实集成示例 |

**W1 验收**：在自己机器上用 Cursor 接 MCP Proxy，触发一次注入攻击，事件能落到 dashboard。

### W2 · 5/5 ~ 5/11 · 桌面壳 + 三平台分发

**主线：终端用户能下载装上。**

| Day | 交付 |
|-----|------|
| 5/5  | Tauri 2.0 项目脚手架，把 dashboard 嵌进去 |
| 5/6  | 系统托盘 + 启动项 + 退出确认 |
| 5/7  | 原生通知（macOS / Windows / Linux 各跑通一次） |
| 5/8  | 三平台打包流水线（GitHub Actions × 3 runner） |
| 5/9  | Apple Developer Program 注册（$99）+ Mac 公证 |
| 5/10 | Windows 代码签名证书购买（Azure Trusted Signing 或 SSL.com，~$200/年）+ 配置 |
| 5/11 | Linux 仓库（GitHub Releases + AppImage + .deb） |

**W2 验收**：朋友能下载 .dmg / .msi / .AppImage 双击装上，没有 Gatekeeper / SmartScreen 警告。

### W3 · 5/12 ~ 5/22 · 移动端 PWA 全功能 + 审批闭环 ⚠️ 11 天（P0+P1+P2 全做）

**主线：移动端能收推送 / 远程审批 / 看 dashboard / 改策略 / 管多设备。**

| Day | 交付 | 优先级 |
|-----|------|------|
| 5/12 | dashboard 加 manifest.json + Service Worker，可"添加到主屏" | P0 |
| 5/13 | Web Push 后端（VAPID keys + 订阅管理）+ Cloudflare Workers 中继骨架 | P0 |
| 5/14 | 高风险事件 → 推送到手机（PWA 离线也能收） | P0 |
| 5/15 | Guard `ASK_USER` 决策异步化：推送 + 等待回调 + 超时 fallback | P0 |
| 5/16 | 审批页 UI（手机端）+ 回调闭环 + 端到端联调 | P0 |
| 5/17 | 移动端只读 dashboard：实时事件流 + 历史 + 风险卡片 | P1 |
| 5/18 | 移动端 dashboard 性能优化（虚拟滚动 + 增量推送） | P1 |
| 5/19 | 策略编辑器（手机端 YAML 编辑 + diff 预览） | P2 |
| 5/20 | 策略下发协议（中继 → 桌面探针，签名校验） | P2 |
| 5/21 | 多设备绑定（账户体系 + 设备配对码 + 设备列表） | P2 |
| 5/22 | 全功能联调（一台手机管两台桌面，触发攻击 → 看推送/审批/历史/改策略） | 全 |

**W3 验收**：自己用一部手机管两台不同 OS 的桌面（Mac + Linux），完成推送→审批→改策略→看历史的完整闭环。

### W4 · 5/23 ~ 5/27 · 文档 + 灰度 + 公开发布 ⚠️ 5 天（被 W3 借走 4 天，缓冲压缩）

**主线：让真实用户用得起来。**

| Day | 交付 |
|-----|------|
| 5/23 | 文档站（mkdocs-material）：架构 / 安装 / 集成 / 移动端 / FAQ；真实 Agent 集成压测；找 3 个种子用户灰度 |
| 5/24 | 5 篇教程文章（Cursor / Claude Desktop / 自定义 Agent / 移动端配对 / 故障排查）+ Bug fix |
| 5/25 | v0.2.0-rc1 候选版发布 + 内测 |
| 5/26 | 修 rc1 反馈出的 bug + 写发布文案（ProductHunt / Hacker News / 知乎 / X） |
| 5/27 | **公开发布 v0.2.0**（无缓冲日，意外可能滑到 5/28~5/29） |

**W4 验收**：v0.2.0 公开发布，至少 5 个真实 Star，3 个外部 Issue。

⚠️ **风险声明**：P0+P1+P2 全做让 W3 吃掉 W4 4 天缓冲。任何一个 W1/W2 的延期都会让发布日滑到 6 月初。强烈建议每周末做一次"砍 P2"的判断（多设备绑定 + 策略编辑器是 W3 最容易砍的）。

---

## 6. 跨平台分发清单

### macOS
- 工具链：Xcode CLI Tools + Tauri 2.0
- 签名：Apple Developer Program ($99/年)
- 公证：`xcrun notarytool`
- 分发：`.dmg` (Universal 或 ARM64-only)
- Gatekeeper 兼容：必须公证 + 钉合 (stapling)

### Windows
- 工具链：MSVC + Tauri 2.0
- 签名：Azure Trusted Signing (~$200/年) 或 SSL.com EV ($349~$700)
- 分发：`.msi` (WiX) 或 `.exe` (NSIS)
- SmartScreen 兼容：EV 证书 + 签名后等积累信誉（约 1~2 周）
- 备选：先发未签名 + 警告，等 v0.3 再签

### Linux
- 工具链：标准 GCC + Tauri 2.0
- 签名：可选（GPG repo 签名）
- 分发：`.AppImage` (主) + `.deb` (Ubuntu/Debian) + `.rpm` (Fedora，v0.3)
- 仓库：GitHub Releases 直接挂

---

## 7. 开源治理清单

- [ ] LICENSE: Apache 2.0（含 patent grant，企业用户更放心）
- [ ] README.md：演示 GIF + 一行安装 + 5 行 Quick Start
- [ ] CONTRIBUTING.md：开发环境 / 代码风格 / PR 流程 / 签 DCO
- [ ] CODE_OF_CONDUCT.md：Contributor Covenant 2.1
- [ ] SECURITY.md：漏洞披露邮箱 + 响应 SLA
- [ ] `.github/ISSUE_TEMPLATE/` × 3 (bug / feature / question)
- [ ] `.github/PULL_REQUEST_TEMPLATE.md`
- [ ] `.github/workflows/`：ci.yml / release.yml / docs.yml
- [ ] `CHANGELOG.md`：Keep a Changelog 格式
- [ ] semver：v0.x.y 阶段允许破坏性变更但要 minor bump
- [ ] 文档站域名：`agentguard.dev` 或 `guard.<你的域名>` (待你定)

---

## 8. 移动端 PWA 方案（建议方案，待你确认）

**选 PWA 不选原生的理由**：
- 一份代码三平台都跑（iOS / Android / 桌面浏览器）
- 不用 App Store 审核（iOS 16+ 全面支持 PWA + Web Push）
- 纯开源不用买 Apple Developer / Google Play 上架费
- 30 天内可落地

**v0.2 移动端能做什么（2026-04-27 已确认 P0+P1+P2 全做）**：
- [x] **P0 · 推送通知**：高风险事件推到手机
- [x] **P0 · 远程审批**：手机点"批准/拒绝" → 回调桌面 Guard
- [x] **P1 · 只读 dashboard**：手机看实时事件流 + 历史
- [x] **P2 · 策略编辑**：手机改 YAML 策略
- [x] **P2 · 多设备绑定**：一个手机管多台桌面

**砍件预案（如果 W3 卡住）**：先砍多设备绑定，再砍策略编辑器，最后保 P0+P1。

---

## 9. 真实成本（USD，30 天内必须支出）

| 项 | 费用 | 时机 |
|---|------|------|
| Apple Developer Program | $99/年 | W2 第二天注册（审批 1~2 天） |
| Windows 代码签名证书 | $200~$400/年 | W2 中段（Azure Trusted Signing 最便宜） |
| 域名（例 agentguard.dev） | $10~$30/年 | 任意 |
| Cloudflare Workers | $0（免费层够用） | W3 |
| GitHub Actions | $0（开源仓库免费） | 全程 |
| **合计** | **~$310~$530** | |

**省钱建议**：W2 先发未签名版本，挂 Release 时写明"代码签名证书审批中"，给自己留 2 周缓冲再加签名。SmartScreen 警告短期内可接受。

---

## 10. 风险与兜底

| 风险 | 概率 | 影响 | 兜底 |
|------|------|------|------|
| Apple 公证申请被打回（首次常见） | 中 | W2 阻塞 2~3 天 | 提前到 W1 注册 + 跑一次空白测试包 |
| Windows 代码签名 EV 验证漫长 | 中 | W2 阻塞 1 周 | 先发未签名 alpha；v0.3 再签 |
| iOS Safari Web Push 行为不一致 | 高 | 移动端推送丢 | iOS 必须用 PWA "添加到主屏"才能收 push（已知限制，写进 FAQ） |
| 中继服务被滥用 | 低 | 公共实例被 ban | 加 rate limit + 自部署文档兜底 |
| MCP 协议变动 | 中 | Proxy 重写 | 锁定 MCP SDK 版本 + 监听 changelog |
| 个人精力不足 | 高 | 进度滑 | W4 留缓冲日；P2 可以砍 |

---

## 11. 验收标准（v0.2.0 公开发布前必须满足）

- [ ] 三平台安装包能下载并跑起来（其中至少 macOS + Windows 一个签了名）
- [ ] `pip install agent-guard` 在 3.10/3.11/3.12 都跑通
- [ ] MCP Proxy 在 Cursor 和 Claude Desktop 都验过
- [ ] PWA 在 iOS 17+ 和 Android Chrome 都收到过推送
- [ ] 22/22 攻击用例继续通过 + 至少 10 条新用例
- [ ] 文档站上线，至少 8 篇页面
- [ ] 至少 3 个外部用户独立装上并提反馈
- [ ] CHANGELOG / Release Notes 写完
- [ ] License headers 全文件覆盖
- [ ] 公开发布前一天彩排过一次完整流程

---

## 12. 决策记录（2026-04-27）

| 项 | 状态 | 决定 |
|----|------|------|
| 移动端 P0/P1/P2 优先级 | ✅ 已定 | P0+P1+P2 全做，W3 扩到 11 天，吃 W4 4 天缓冲 |
| 项目品牌名 | ✅ 已定 | **Sentinel-MCP**（团队 Sentinel + MCP 生态定位） |
| 域名 | ✅ 已定（待抢注） | **sentinel-mcp.dev**（现在去 Cloudflare Registrar 抢） |
| GitHub 仓库 | ✅ 用户已有 | `git@github.com:IveGotMagicBean/FeiShuAI_Competition.git`（用户自己 push） |
| 工作目录组织 | ✅ 已定 | 沿用「每日 test 目录」习惯，不替用户做 git init/push |

## 13. 仍待澄清（不阻塞 W1，但越早定越好）

- **PyPI 包名**：`sentinel-mcp` / `sentinel-guard` / 其他？（`sentinel` 已被占）
- **CLI 命令名**：`sentinel` / `smcp` / 其他？（`sentinel` 短但重名）
- **Logo / 视觉风格**：先用 emoji 占位（🛡️），W4 找设计资源
- **是否做 Discord / 微信群**给早期用户：建议 W3 末再开
