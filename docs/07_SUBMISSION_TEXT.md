# 飞书 AI 校园挑战赛 · 提交文案草稿

> 提交表单的字段我没看过——以下按常见字段准备。
> 真到提交时打开表单，按字段挑对应段落复制即可。

---

## 项目名称
Sentinel-MCP

## 一句话简介（≤ 30 字）
客户端侧 AI Agent 安全框架，让任何 MCP 客户端零改造拦下越权工具调用

## 副标题 / Slogan
Guard every tool call · 协议层中间人代理 + 策略沙箱 + 人在回路审批

---

## 项目描述（200 - 300 字）

Sentinel-MCP 是一个**客户端侧** AI Agent 安全框架。它在任何 MCP（Model Context Protocol）
客户端和上游 MCP server 之间插一层透明的中间人代理，把每一次 `tools/call` 都过一遍 Guard 决策引擎，
按可配置 YAML 策略输出 ALLOW / DENY / REDACT / ASK_USER 四态决策。

技术上分为四层防御：L1 输入注入检测（33 条规则覆盖 OWASP LLM Top 10）、
L2 工具调用策略沙箱（filesystem / network / shell 三沙箱）、L3 跨进程异步审批（SQLite WAL 队列 + Web Push 锁屏推送）、
L4 出向 DLP（13 个敏感数据模式原地脱敏）。所有事件实时推送到一个 PWA Dashboard，
手机加到主屏后**锁屏也能弹出审批请求**——「我亲自批准这个写文件操作」。

**用户接入方式**：在 Cursor / Claude Desktop / Cline / Continue 等任意 MCP 客户端的 config 里，
把 `npx ...` 换成 `sentinel-mcp wrap -- npx ...`，**应用零修改**全部受保护。

---

## 技术亮点（评委关心的差异化）

1. **协议层中间人代理，不是 SDK 装饰器**
   - 行业类似方案（LangChain Guards、Garak）都是 Python 装饰器，只能保护「自己代码包过 `@guard` 的工具」
   - 我们做在 MCP stdio JSON-RPC 协议这一层，**任何 MCP-aware 客户端零改造接入**

2. **跨进程异步审批闭环 + 锁屏推送**
   - SQLite WAL + sleep-poll 实现「proxy 进程发起审批 → dashboard 决断 → proxy 解锁」
   - VAPID Web Push 直推 FCM / Mozilla autopush，**手机锁屏弹通知**——这是行业内独有
   - 解决了 ASK_USER 在 asyncio 协程里调同步 Guard 会死锁的陷阱（用 `loop.run_in_executor` 把同步调用挪到线程池）

3. **端到端发布流水线，零 Mac 出 Mac 包**
   - 一份 `release.yml` 在 GitHub Actions 三平台 runner 上自动跑 PyInstaller + Tauri build
   - **push 一个 tag → 15 分钟后 Releases 页有 dmg / msi / AppImage / deb / rpm + Android .apk 全套**
   - 评委下载双击就能装，不需要任何前置依赖

---

## 量化指标

- 红队攻击用例 **53 条 100% 拦截**（注入 12 / 文件 13 / 网络 11 / 命令 11 / 兜底 6）
- 单元 + 集成测试 **86 / 86 通过**
- 决策延迟 **均值 4.09 ms / P99 7.86 ms**
- 代码量 ~6500 行 Python + Rust + TypeScript
- 30+ 次本地提交 · 5 篇交付文档

---

## 软件包形态（赛题硬性要求对照）

| 平台 | 文件 | 状态 |
|---|---|---|
| macOS Apple Silicon | `Sentinel-MCP_0.2.0_aarch64.dmg` | ✓ Releases v0.2.0 |
| Windows | `Sentinel-MCP_0.2.0_x64_en-US.msi` | ✓ Releases v0.2.0 |
| Windows | `Sentinel-MCP_0.2.0_x64-setup.exe` | ✓ Releases v0.2.0 |
| Linux | `Sentinel-MCP_0.2.0_amd64.AppImage` | ✓ Releases v0.2.0 |
| Linux | `Sentinel-MCP_0.2.0_amd64.deb` | ✓ Releases v0.2.0 |
| Linux | `Sentinel-MCP-0.2.0-1.x86_64.rpm` | ✓ Releases v0.2.0 |
| macOS app bundle | `Sentinel-MCP.app.tar.gz` | ✓ Releases v0.2.0 |
| **Android** | `sentinel-mcp.apk` | 🚧 cloudflared + Bubblewrap，预计 5/3 前完成 |
| Python wheel | `sentinel_mcp-0.2.0-py3-none-any.whl` | ✓ Releases v0.2.0 |
| Python sdist | `sentinel_mcp-0.2.0.tar.gz` | ✓ Releases v0.2.0 |

---

## 链接

| 项目 | URL |
|---|---|
| GitHub 仓库 | https://github.com/IveGotMagicBean/FeiShuAI_Competition |
| Releases 下载 | https://github.com/IveGotMagicBean/FeiShuAI_Competition/releases/tag/v0.2.0 |
| 技术方案 | https://github.com/IveGotMagicBean/FeiShuAI_Competition/blob/main/docs/01_TECHNICAL_DESIGN.md |
| 安装指南 | https://github.com/IveGotMagicBean/FeiShuAI_Competition/blob/main/docs/02_INSTALL_GUIDE.md |
| 测试报告 | https://github.com/IveGotMagicBean/FeiShuAI_Competition/blob/main/docs/03_TEST_REPORT.md |
| 演示脚本 | https://github.com/IveGotMagicBean/FeiShuAI_Competition/blob/main/docs/04_DEMO_SCRIPT.md |
| 提交对照 | https://github.com/IveGotMagicBean/FeiShuAI_Competition/blob/main/SUBMISSION_CHECKLIST.md |
| 5 分钟演示视频 | TODO（5/5 前录完上传 B 站/YouTube） |
| 飞书周期日报 | https://jcneyh7qlo8i.feishu.cn/wiki/Ys43ww4pGiZR6akyDbBclag0ndb |

---

## 团队 / 个人信息

- **作者**：[你的名字]
- **GitHub**：IveGotMagicBean
- **联系**：542058929@qq.com
- **角色**：独立开发者
- **开发周期**：2026-04-25 ~ 2026-05-27（约 4 周 / 3 个迭代周期）

---

## 开源许可

Apache-2.0 ——商业可用、专利授权完整、要求保留版权声明。

---

## 提交清单（你提交前自检）

- [ ] 项目名称栏：Sentinel-MCP
- [ ] 简介栏：上面那句 30 字以内
- [ ] 详细介绍：项目描述 + 技术亮点段落
- [ ] GitHub URL：填仓库地址
- [ ] 演示视频：录完上传后填 URL（YouTube/B 站皆可）
- [ ] Android .apk：构建完上传到 Releases 后再勾上「移动端可装」
- [ ] 团队信息：你的真实姓名 + 联系方式
- [ ] 协议同意 / 知识产权声明：勾上
