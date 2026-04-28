# Sentinel-MCP · 飞书黑客松提交对照清单

> 把赛题原文逐字摘出来，对应到本仓库的具体文件 / 截图 / 视频 / 命令。
> 评委 30 秒内能对齐：「他们要 X，仓库里在 Y 这」。

---

## A. 必须完成项 (Must-have)

### A.1 通用框架设计

> **赛题原文**：「设计一套与具体业务逻辑解耦的安全框架，能够以低侵入性的方式（如 SDK、AOP）集成到现有的客户端应用中。」

| 形态 | 文件 | 集成方式 |
|---|---|---|
| **MCP stdio 代理**（推荐） | `sentinel_mcp/proxy.py` + `sentinel_mcp/cli.py` | 把 `npx ...` 换成 `sentinel-mcp wrap -- npx ...`，**应用零改造** |
| **Python SDK 装饰器** | `guard/core.py:Guard.protected()` | `@guard.protected("filesystem")` 包工具函数 |

证据：[`docs/01_TECHNICAL_DESIGN.md`](./docs/01_TECHNICAL_DESIGN.md) §2 系统架构 + §3.1「为什么是 stdio 代理而不是装饰器 SDK」

### A.2.1 Prompt Injection 防御（≥2 种机制）

> **赛题原文**：「实现至少两种检测和防御提示注入攻击的机制（如输入清洗、指令边界识别、输出编码等）。」

| 机制 | 文件 | 规则数 |
|---|---|---|
| 1. 规则匹配 | `guard/detectors/prompt_injection.py:_RAW_RULES` | 33 条权重正则（角色覆写 / 越狱 / 系统消息伪造 / 工具特征 / 编码混淆 / 边界欺骗） |
| 2. 对话边界识别 | 同上 `_has_boundary_spoof()` | 检测 `</user>`、`<\|im_start\|>` 等伪造对话标签 |
| 3. 工具调用层兜底 | `guard/sandbox.py` + `config/policies.yaml` | 即使 prompt 检测漏掉，沙箱仍会拦 |

### A.2.2 工具调用权限管控

> **赛题原文**：「实现一个可配置的工具调用权限清单，并要求在首次调用敏感工具时，必须明确弹窗向用户请求授权。」

| 项 | 实现 |
|---|---|
| 可配置工具清单 | `config/policies.yaml` 的 `tools.<name>.{policy, require_user_authz}` |
| 首次调用弹窗授权 | `Decision.ASK_USER` → `sentinel_mcp/approvals.py:PendingDecisions` 写 SQLite → Dashboard 弹「待审批」面板 + 浏览器/Web Push 通知 → 用户在 dashboard / 手机批准或拒绝 |
| 「本会话一次性授权」缓存 | `guard/core.py:_authorized_once` |
| 超时按拒绝 | `--ask-timeout` 默认 60s，超时 SQLite 标 expired |

> **赛题原文**：「策略沙箱：基于可配置的策略（如限制文件访问路径、限制网络访问域名），约束 Agent 的工具调用行为。」

| 沙箱 | 实现 |
|---|---|
| 文件系统 | `guard/sandbox.py:FilesystemSandbox` — `allowlist` + `denylist`（glob） |
| 网络 | `guard/sandbox.py:NetworkSandbox` — 域名白/黑名单 + `block_private_ip`（SSRF） |
| Shell | `guard/sandbox.py:ShellSandbox` — 命令白名单 + `blocked_patterns`（regex） |

### A.2.3 数据防外泄 (DLP)

> **赛题原文**：「监控并阻断通过剪贴板、文件写入、网络请求等方式的未授权数据外泄。」

| 通道 | 拦截方式 |
|---|---|
| 剪贴板 | `clipboard_write` 工具 → `require_user_authz: true` → 必须用户批准 |
| 文件写入 | `write_file` 工具 → 同上 |
| 网络请求 | `http_request` 工具 → 同上；外加 `network` 沙箱阻断 attacker.com / pastebin / ngrok / webhook.site / 私网 IP |

> **赛题原文**：「对日志或输出内容中的敏感信息（如手机号、邮箱、身份证号）进行自动脱敏处理。」

| 通道 | 实现 |
|---|---|
| 上游 → 客户端响应（**L4 出向 DLP**） | `sentinel_mcp/proxy.py:_maybe_redact_line` 扫描 `result.content[*].text`，命中即原地替换 |
| 13 个内置模式 | `guard/detectors/dlp.py:_DEFAULT_RULES` — 手机号 / 邮箱 / 身份证 / 银行卡 / AWS Key / OpenAI Key / GitHub Token / Slack Token / PEM 私钥 / JWT / 通用 API Key / SSN |
| Dashboard 出向脱敏 | `guard/core.py:check_output()` |

### A.3 攻击用例 + 可观测性

> **赛题原文**：「构建一个攻击用例套件，用于演示你的框架如何有效防御上述攻击。」

| 项 | 数量 / 文件 |
|---|---|
| 攻击用例套件 | **53 条** · `tests/attack_cases/cases.py` |
| 一键回归脚本 | `python -m tests.attack_cases.run_all` → 自动产出 `data/attack_report.md` + `attack_report.json` |
| 通过率 | **53/53 = 100%** · 平均决策耗时 4.09 ms · P99 7.86 ms |

> **赛题原文**：「提供一个可观测性面板或日志系统，能清晰展示 Agent 的工具调用历史、安全告警以及被阻断的恶意行为。」

| 项 | 实现 |
|---|---|
| 实时面板 | `pwa_dashboard/` PWA — 端口 8766 |
| 实时事件流 | SSE `/api/events/stream`，500ms 增量推送 |
| 待审批面板 | 高亮琥珀色，可在 dashboard / 手机一键批准 |
| 审批历史面板 | tab 切换；approved/denied/expired 三色标记 |
| 持久化审计 | SQLite WAL，跨进程多 Proxy 共享 |

---

## B. 可选加分项 (Good-to-have)

| 加分项 | 状态 |
|---|---|
| 跨平台 — **桌面**（mac / win / linux） | ✅ Tauri 2.0 包出 .dmg / .msi / .AppImage（GitHub Actions release.yml 自动产出） |
| 跨平台 — **移动**（Android） | 🟡 PWA 已支持「添加到主屏」；APK 待 Bubblewrap 打包（需公网 HTTPS 域名） |
| 模型驱动风险检测 | ❌ 未做（v0.3 计划接 PromptGuard / Granite-Guardian 小模型分类器） |
| 性能与开销评估 | ✅ [`docs/03_TEST_REPORT.md`](./docs/03_TEST_REPORT.md) §性能基准 — avg 4.09 ms / P99 7.86 ms |
| 可扩展性 | ✅ YAML 策略 + detector 规则均可自定义；`extra_patterns` 支持外部 DLP 模式注入 |

---

## C. 通用交付物清单

### C.1 源代码

> **赛题原文**：「完整的、可独立编译运行的前后端源代码。清晰的目录结构与代码注释。」

| 项 | 位置 |
|---|---|
| 前后端源代码 | 整个 `0427_test01/` 目录 |
| 目录结构 | `README.md` §文档 / [`docs/01_TECHNICAL_DESIGN.md`](./docs/01_TECHNICAL_DESIGN.md) §2 |
| GitHub | `IveGotMagicBean/FeiShuAI_Competition`（push 后） |

### C.2 可运行程序包

> **赛题原文**：「桌面端（macOS 或 Windows）与移动端（iOS 或 Android）的可直接安装运行的程序包。」

| 平台 | 产物 | 怎么拿 |
|---|---|---|
| **macOS** (Intel + arm64) | `.dmg` | GitHub Actions 自动产出 → Release 页 |
| **Windows** | `.msi` + `.exe` | GitHub Actions 自动产出 → Release 页 |
| Linux（加分项） | `.AppImage` / `.deb` / `.rpm` | GitHub Actions 自动产出 → Release 页 |
| **Android** | `.apk`（待打） | Bubblewrap CLI 把 PWA 包成 TWA |
| Python（开发者快速试用） | `.whl` + `.tar.gz` | `dist/` 已构建；Release 页同时挂；亦可 `pip install sentinel-mcp` |

**怎么触发自动产出**：

```bash
git tag v0.2.0
git push origin v0.2.0
# 等 ~15 min，GitHub Actions release workflow 自动跑完，
# 在仓库 Releases 页能看到三平台安装包 + checksums。
```

详细文档：[`docs/05_DESKTOP_BUILD.md`](./docs/05_DESKTOP_BUILD.md) + [`.github/workflows/release.yml`](./.github/workflows/release.yml)

### C.3 技术与项目文档

> **赛题原文**：「技术方案设计文档：详述系统架构、技术选型、核心算法、关键模块设计等。」

✅ [`docs/01_TECHNICAL_DESIGN.md`](./docs/01_TECHNICAL_DESIGN.md)（含架构图 / 决策推导 / L1–L4 防御层 / 性能数据 / 已知限制）

> **赛题原文**：「安装与运行指南：一步步指导评委如何部署、配置并运行你的项目。」

✅ [`docs/02_INSTALL_GUIDE.md`](./docs/02_INSTALL_GUIDE.md)（pip / 源码 / Tauri 三条路径 + 客户端配置 + 环境变量表）

> **赛题原文**：「测试报告：包含功能、性能、稳定性及（若适用）安全性的测试用例、过程与结果。」

✅ [`docs/03_TEST_REPORT.md`](./docs/03_TEST_REPORT.md) — 86/86 测试 + 53/53 攻击 + 性能基准

### C.4 演示材料

✅ [`docs/04_DEMO_SCRIPT.md`](./docs/04_DEMO_SCRIPT.md) — 5 分钟现场演示流程
✅ [`examples/red_blue_demo.py`](./examples/red_blue_demo.py) — 可执行红蓝对抗脚本
🟡 演示视频 — **待录制**（按 04 demo script 念稿即可）

---

## D. 演示与验收要求

### D.1 攻击演示（红队）

> **赛题原文**：「在一个未集成你的安全框架的应用中，演示如何通过恶意 Prompt 成功实现一次数据窃取（如读取本地敏感文件并发送到外部服务器）。」

✅ [`examples/red_blue_demo.py`](./examples/red_blue_demo.py) 上半段：5 个工具调用全部直通，包括 `read_file ~/.ssh/id_rsa` + `http_request → attacker.com`。

跑法：

```bash
cd 0427_test01
python examples/red_blue_demo.py
```

### D.2 防御演示（蓝队）

> **赛题原文**：「在已集成你的安全框架的同一个应用中，重复相同的攻击操作，展示你的框架如何成功检测、阻断该攻击，并在可观测性面板上留下清晰的告警记录。」

✅ 同一脚本下半段：每一步 Guard 决策 + 命中规则；同时审计落到 SQLite，dashboard 实时显示。

```bash
SENTINEL_DB=/tmp/sentinel_redblue_demo.db python -m pwa_dashboard.server
# 浏览器打开 http://localhost:8766 看红色 DENY 事件
```

### D.3 数据保护展示

> **赛题原文**：「演示敏感数据（如剪贴板中的手机号）在被 Agent 处理时如何被自动脱敏，以及用户对敏感工具调用的授权流程。」

✅ red_blue_demo.py 第 5 步触发 `clipboard_write({"text": "用户手机号 13800138000"})` → `require_user_authz=true` → ASK_USER → 自动测试场景下用户拒绝 → DENY。

✅ DLP 出向脱敏 demo：可手动构造一个上游响应包含 `sk-...` 等敏感数据，proxy 会自动替换成 `[OPENAI_KEY]` —— `tests/test_dlp_outbound.py` 6/6 单元测试覆盖此路径。

---

## E. 提交时给评委的「一页 quick start」

```bash
# 1. 克隆 + 跑测试 (3 min)
git clone https://github.com/IveGotMagicBean/FeiShuAI_Competition.git
cd FeiShuAI_Competition/0427_test01
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dashboard]"
python tests/test_proxy.py          # 5/5
python -m tests.attack_cases.run_all  # 53/53

# 2. 看红蓝对抗 (1 min)
python examples/red_blue_demo.py

# 3. 启 dashboard 看实时面板 (30s)
python -m pwa_dashboard.server &
xdg-open http://localhost:8766    # mac: open；win: start

# 4. 真接 MCP 客户端（可选，需 npm）
sentinel-mcp wrap -- npx -y @modelcontextprotocol/server-filesystem ~/work
```

---

## 仍待补的（自评）

| 项 | 优先级 | 备注 |
|---|---|---|
| 推到 GitHub + tag v0.2.0 触发 Release 自动出包 | P0 | 用户操作 |
| 录 5 min 演示视频 | P0 | 用户操作（按 04 demo 念稿） |
| Android APK | P1 | 需公网 HTTPS 域名做 TWA assertLinks |
| 模型驱动 detector | P2 | v0.3 范围 |
