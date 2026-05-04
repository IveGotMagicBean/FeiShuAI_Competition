# Sentinel-MCP

> **客户端侧 AI Agent 安全框架** · MCP 工具调用代理 + 策略沙箱 + DLP
>
> 飞书 AI 校园挑战赛参赛项目

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-brightgreen.svg)](#)
[![Tests](https://img.shields.io/badge/tests-86%2F86%20passing-success.svg)](#测试与基准)
[![Attacks](https://img.shields.io/badge/attacks-53%2F53%20blocked-success.svg)](./tests/attack_cases/cases.py)

<!-- ═════════════════════════════════════════════════════════════════
     截图区（5 张，按下面顺序截好后替换 docs/screenshots/ 里的同名文件）

     1. dashboard-main.png       — 桌面浏览器看 dashboard 主面板（有几条 audit 事件）
     2. ask-user-card.png        — dashboard 「待审批」tab 一张橙色卡片展开状态
     3. phone-pwa-locked.png     — 手机锁屏弹「Sentinel-MCP - write_file 请求授权」推送
     4. red-blue-terminal.png    — 终端跑 examples/red_blue_demo.py 红蓝对照彩色输出
     5. github-releases.png      — Releases 页 v0.2.0 的全套 .dmg/.msi/.AppImage/.apk 列表

     截好后取消下面注释；图存到 docs/screenshots/
     ═════════════════════════════════════════════════════════════════ -->

<!--
<p align="center">
  <img src="docs/screenshots/dashboard-main.png" width="48%" alt="Dashboard 主面板"/>
  &nbsp;
  <img src="docs/screenshots/ask-user-card.png" width="48%" alt="ASK_USER 待审批卡片"/>
</p>
<p align="center">
  <img src="docs/screenshots/phone-pwa-locked.png" width="30%" alt="手机锁屏推送"/>
  &nbsp;
  <img src="docs/screenshots/red-blue-terminal.png" width="60%" alt="红蓝对照终端输出"/>
</p>
-->

---

## 项目介绍

`Sentinel-MCP` 在 AI Agent（Cursor / Claude Desktop / 自研框架等）和它要调用的本地工具
（文件系统 / Shell / HTTP / 剪贴板…）之间插一层安全代理。每一次 `tools/call`
都会经过 Guard 决策引擎，按可配置策略**放行 / 拒绝 / 改写参数 / 索取人工审批**。
所有事件实时推送到一个本地 PWA dashboard。

```
  Cursor / Claude Desktop                       上游 MCP Server
  (MCP 客户端)                                  (filesystem / shell / http / …)
        │                                              ▲
        │ stdin / stdout (JSON-RPC 2.0)                │
        ▼                                              │
  ┌─────────────────────────────────────────────────────┐
  │  sentinel-mcp wrap                                  │
  │  ┌──────────┐  ┌────────────┐  ┌────────────────┐  │
  │  │ L1 输入  │→ │ L2 工具调用 │→ │ L3 沙箱        │  │
  │  │ 注入检测 │  │ 策略 + 审批 │  │ FS / Net / Shell│  │
  │  └──────────┘  └────────────┘  └────────────────┘  │
  │                                       ↓             │
  │                          ┌──────────────────────┐   │
  │                          │ L4 出向 DLP（脱敏）  │   │
  │                          └──────────────────────┘   │
  │                                       ↓             │
  │                              audit · SQLite WAL     │
  │                                       ↓             │
  │                       ┌────────────────────────┐    │
  │                       │ PWA Dashboard          │←──┼─ 手机 / 桌面
  │                       │ SSE + Web Push 通知    │    │
  │                       └────────────────────────┘    │
  └─────────────────────────────────────────────────────┘
```

---

## 核心特性

| 层 | 能力 |
|---|---|
| **L1 输入** | 33 条 Prompt 注入规则 + 对话边界识别（伪造 `</user><system>`、`[SYSTEM]:`、奶奶模式、续写攻击等全覆盖） |
| **L2 工具调用** | YAML 策略 → `ALLOW` / `DENY` / `REDACT` / `ASK_USER` 四态决策；首次调用敏感工具必须用户授权 |
| **L3 沙箱** | 文件系统（allow/denylist + glob）/ 网络（域名白名单 + SSRF 阻断 + 私网阻断）/ Shell（命令白名单 + 危险模式黑名单） |
| **L4 输出** | 13 个 DLP 模式（OpenAI Key / JWT / AWS / 私钥 / 邮箱 / 手机号 / 身份证 / 银行卡 / SSN …）原地脱敏 |
| **观测面板** | PWA Dashboard：SSE 实时推流 + 待审批 / 历史 双 tab + 浏览器原生 Notification + 手机锁屏 Web Push |
| **跨进程审批** | SQLite WAL 共享审批队列，多个代理实例并发安全；超时按拒绝处理 |
| **可观测性** | 全调用审计落盘；CLI / Dashboard / Tauri 桌面包共用同一份 DB |

---

## 快速试用

```bash
git clone https://github.com/IveGotMagicBean/FeiShuAI_Competition.git
cd FeiShuAI_Competition
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dashboard]"

# 1) 看红蓝对抗 — 同一串恶意 tool calls，未防护 5/5 直通 vs 防护 5/5 全拦
python examples/red_blue_demo.py

# 2) 跑全套攻击集 — 53 条用例 100% 拦截
python -m tests.attack_cases.run_all

# 3) 启 PWA Dashboard
python -m pwa_dashboard.server   # http://localhost:8766
```

---

## 安装

### 方式 A · 桌面安装包

到 [GitHub Releases](https://github.com/IveGotMagicBean/FeiShuAI_Competition/releases) 下载对应平台：

| 平台 | 文件 |
|---|---|
| macOS (Apple Silicon) | `Sentinel-MCP_x.y.z_aarch64.dmg` |
| macOS (Intel) | `Sentinel-MCP_x.y.z_x64.dmg` |
| Windows | `Sentinel-MCP_x.y.z_x64_en-US.msi` |
| Linux | `sentinel-mcp_x.y.z_amd64.AppImage` / `.deb` / `.rpm` |

双击安装，启动后自动起后端，主窗口直接是 Dashboard。

### 方式 B · pip 安装

```bash
pip install "sentinel-mcp[dashboard]"   # 含 Dashboard
pip install sentinel-mcp                # 仅代理
```

### 方式 C · 移动端 PWA

启动 Dashboard 后，手机浏览器打开 `http://<你的电脑 IP>:8766` →「添加到主屏」→ 像 app 一样使用。

---

## 用法

### 1. 包装一个上游 MCP server

```bash
sentinel-mcp wrap -- npx -y @modelcontextprotocol/server-filesystem ~/work
```

把 Cursor / Claude Desktop 配置里指向 `npx …` 的命令换成上面这行即可。

### 2. 启动 Dashboard 看实时事件

```bash
python -m pwa_dashboard.server
# 浏览器打开 http://localhost:8766
```

### 3. 在 Python Agent 里用 SDK（非 MCP 场景）

```python
from guard import Guard
guard = Guard.from_yaml("config/policies.yaml")

@guard.protected("filesystem")
def read_file(path: str) -> str:
    return Path(path).read_text()
```

### 4. 自定义策略

复制 [`config/policies.yaml`](./config/policies.yaml) 改一份，然后 `--config /path/to/your.yaml`。

---

## 测试与基准

```bash
python tests/test_proxy.py             # Proxy 单元 (5/5)
python tests/test_ask_user_e2e.py      # ASK_USER 异步审批闭环 (5/5)
python tests/test_e2e_smoke.py         # cat 假上游冒烟 (4/4)
python tests/test_dlp_outbound.py      # L4 出向 DLP (6/6)
python tests/test_push.py              # Web Push 管理 (4/4)
python tests/test_desktop.py           # 桌面入口 (4/4)
python -m tests.attack_cases.run_all   # 53 条红队攻击集 (53/53)
```

| 指标 | 数值 |
|---|---|
| 单元 + 集成测试 | **86 / 86 通过** |
| 攻击用例拦截率 | **53 / 53 = 100%** |
| 决策延迟（avg / P99） | **4.09 ms / 7.86 ms** |

---

## 文档

| 文档 | 用途 |
|---|---|
| [`SUBMISSION_CHECKLIST.md`](./SUBMISSION_CHECKLIST.md) | **赛题对照清单** — 每条要求映射到具体文件 |
| [`docs/01_TECHNICAL_DESIGN.md`](./docs/01_TECHNICAL_DESIGN.md) | 技术方案设计（架构 / 决策推导 / L1–L4 细节 / 性能） |
| [`docs/02_INSTALL_GUIDE.md`](./docs/02_INSTALL_GUIDE.md) | 安装与运行指南 |
| [`docs/03_TEST_REPORT.md`](./docs/03_TEST_REPORT.md) | 测试报告（含性能基准） |
| [`docs/04_DEMO_SCRIPT.md`](./docs/04_DEMO_SCRIPT.md) | 5 分钟现场演示脚本 |
| [`docs/05_DESKTOP_BUILD.md`](./docs/05_DESKTOP_BUILD.md) | 桌面端构建指南（PyInstaller / Tauri / PWA 三条路径） |
| [`RELEASE_GUIDE.md`](./RELEASE_GUIDE.md) | 怎么发布新版本（git tag → 自动出全平台安装包） |
| [`ROADMAP_v0.2.md`](./ROADMAP_v0.2.md) | 30 天路线图 |
| [`CHANGELOG.md`](./CHANGELOG.md) | 版本日志 |

---

## 项目结构

```
.
├── sentinel_mcp/        # 主包：MCP 代理 + 审批 + CLI 入口
│   ├── proxy.py         #   stdio JSON-RPC 拦截 + L4 出向 DLP
│   ├── approvals.py     #   跨进程审批队列（SQLite WAL）
│   ├── cli.py           #   sentinel-mcp wrap CLI
│   ├── desktop.py       #   sentinel-mcp-desktop（pywebview/浏览器壳）
│   └── config/          #   默认策略 policies.yaml
├── guard/               # 决策引擎（v0.1 演进）
│   ├── core.py          #   Decision / Guard / GuardResult
│   ├── sandbox.py       #   FS / Network / Shell 三沙箱
│   ├── policies.py
│   ├── audit.py         #   SQLite 审计日志
│   └── detectors/
│       ├── prompt_injection.py    # 33 条注入规则 + 边界识别
│       └── dlp.py                  # 13 个敏感数据模式
├── pwa_dashboard/       # PWA 实时观测面板
│   ├── server.py        #   FastAPI + SSE + Web Push API
│   ├── push.py          #   VAPID + pywebpush 封装
│   ├── templates/       #   Alpine.js + Tailwind 单页
│   └── static/          #   sw.js / manifest / 图标
├── desktop/             # Tauri 2.0 桌面壳
│   ├── src-tauri/       #   Rust 原生外壳
│   ├── sidecar/         #   PyInstaller spec（冻结 dashboard server）
│   └── dist/
├── examples/
│   ├── red_blue_demo.py # 红蓝对抗演示
│   └── _fake_tools.py
├── tests/               # 单元 + e2e + 攻击套件
├── docs/                # 5 篇交付文档
├── config/              # 默认策略 YAML
├── .github/workflows/
│   ├── ci.yml           #   lint + test + 攻击回归 + wheel build
│   └── release.yml      #   tag 触发：mac/win/linux 三平台桌面包 + PyPI
└── pyproject.toml
```

---

## 贡献

见 [`CONTRIBUTING.md`](./CONTRIBUTING.md)。
报告安全漏洞请遵循 [`SECURITY.md`](./SECURITY.md)。

## 许可证

Apache-2.0。详见 [`LICENSE`](./LICENSE)。
