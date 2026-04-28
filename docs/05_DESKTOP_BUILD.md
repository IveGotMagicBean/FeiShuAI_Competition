# Sentinel-MCP · 桌面端构建指南

桌面分发分两条路径，按对原生体验的需求选：

| 路径 | 安装难度 | 体验 | 适合 |
|---|---|---|---|
| **A · `sentinel-mcp-desktop`**（pip 装） | ⭐ | 一键起后端 + 默认浏览器 / pywebview 窗口 | 90% 用户、demo、CI、Docker |
| **B · Tauri 原生包**（dmg / msi / AppImage） | ⭐⭐⭐ | 系统托盘、原生窗口、可双击安装 | 终端用户分发 / 上架 |

## 路径 A — `sentinel-mcp-desktop`

```bash
pip install "sentinel-mcp[dashboard]"
sentinel-mcp-desktop
```

它会：

1. 启动 PWA dashboard 子进程（uvicorn 在 8766）
2. 等端口就绪
3. 优先用 [`pywebview`](https://pywebview.flowrl.com/) 弹原生窗口；未装则回落到默认浏览器
4. 你 Ctrl+C 时优雅停掉子进程

可选装 pywebview（Linux 还需要 `python3-gi` + `webkit2gtk`，macOS / Windows 自动就绪）：

```bash
pip install pywebview
```

参数：

```text
sentinel-mcp-desktop [--port 8766] [--host 127.0.0.1] [--no-window] [--no-server]
```

## 路径 B — Tauri 原生包

> 适合分发给非技术用户。出 `.dmg` / `.msi` / `.AppImage`。

### B.1 一次性装好工具链

```bash
# Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source ~/.cargo/env

# Tauri CLI (cargo 子命令版，最稳)
cargo install tauri-cli --version "^2.0" --locked
```

### B.2 平台原生依赖

#### macOS

```bash
xcode-select --install
```

补 `.icns`（git 仓库里没存）：

```bash
cd 0427_test01/desktop
cargo tauri icon ./src-tauri/icons/icon.png
```

#### Linux（Ubuntu 22.04 / Debian 12）

```bash
sudo apt update && sudo apt install -y \
    libwebkit2gtk-4.1-dev \
    build-essential \
    curl \
    wget \
    file \
    libxdo-dev \
    libssl-dev \
    libayatana-appindicator3-dev \
    librsvg2-dev \
    pkg-config
```

> Fedora / RHEL：换成 `webkit2gtk4.1-devel`、`gtk3-devel`、`libappindicator-gtk3-devel`、`librsvg2-devel`。
>
> WSL2 也是这一套，只是 GUI 输出走 WSLg。

#### Windows

WebView2 Runtime — Win11 自带；Win10 让 `cargo tauri build` 自动拉，或手动下：
<https://developer.microsoft.com/microsoft-edge/webview2/>

### B.3 构建

```bash
cd 0427_test01/desktop

# 开发：热重载
cargo tauri dev

# 出 release 包
cargo tauri build
```

产物位置：

| 平台 | 输出 |
|---|---|
| macOS | `desktop/src-tauri/target/release/bundle/dmg/Sentinel-MCP_0.2.0_aarch64.dmg`（M 系列）<br>`.../Sentinel-MCP_0.2.0_x64.dmg`（Intel） |
| Windows | `desktop/src-tauri/target/release/bundle/msi/Sentinel-MCP_0.2.0_x64_en-US.msi` |
| Linux | `desktop/src-tauri/target/release/bundle/appimage/sentinel-mcp_0.2.0_amd64.AppImage` |

### B.4 已知 caveat

- 第一次 `cargo tauri build` 会从 crates.io 拉 ~80 个 crate，5–10 分钟（缓存到 `~/.cargo/`，之后就快）
- macOS App Store 上架还要 Apple Developer ID 公证；Windows 商店要 EV 代码签名 — 这两步**要钱**，赛事场景可跳
- Tauri 不会自动起 Python 后端 — 装好 `sentinel-mcp[dashboard]` 后，桌面 app 通过 `devUrl` 指向 `localhost:8766`；后端要用户手动启 `python -m pwa_dashboard.server`
- W2 计划接入 [tauri-plugin-shell sidecar](https://v2.tauri.app/plugin/sidecar/)，让 Tauri 包内自动拉起 Python 后端，用户体验降到「双击 dmg → 自动一切就绪」

## 路径 C — 让浏览器变 PWA（零代码）

任何 Chromium 系浏览器（Chrome / Edge / Arc）打开 `http://localhost:8766` 后：

- 桌面：地址栏右边 ⊕ 图标 → 「安装为应用」
- 移动：菜单 → 「添加到主屏幕」

效果：独立窗口、应用启动器图标、SW 离线兜底。**对绝大多数用户而言这就够了**，不需要 Tauri。
