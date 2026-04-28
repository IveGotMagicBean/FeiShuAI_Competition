# Sentinel-MCP Desktop Shell（v0.2 W2 雏形）

> **重要**：这台 HPC 服务器（CentOS 7 / glibc 2.17 / 没装 webkit2gtk）**不能构建也不能运行 Tauri**。
> 这里只是脚手架代码；要看到桌面窗口，必须把这个目录拉到你本地的 **Mac / Windows / 现代 Linux** 上构建。

## 这个壳现在做了什么

- 主窗口加载 `http://localhost:8766`（PWA dashboard）
- 系统托盘图标 + 菜单：`打开 Dashboard / 最小化 / 关于 / 退出`
- 关闭窗口缩到托盘，不退出进程（桌面 app 标准行为）
- 默认 capability 只放权 tray + opener，禁用危险 fs/shell

## 还差什么（W2 / W3 排期里的事）

- [ ] **Sidecar**：让 Tauri 自动启动 Python `pwa_dashboard.server`，不需要用户手动开后端
- [ ] **原生通知**：高风险事件直接弹系统 toast
- [ ] **开机自启**：tauri-plugin-autostart
- [ ] **Mac 公证 / Win 代码签名**（W2 末，要钱）
- [ ] **Web Push 通道接入**（W3，配合 PWA 移动端）

## 在你本地 Mac 怎么跑起来

### 1. 一次性装好工具链

```bash
# Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source ~/.cargo/env

# Tauri CLI（cargo 子命令版，最稳）
cargo install tauri-cli --version "^2.0" --locked

# Mac 上额外要 Xcode CLI tools（应该已经有）
xcode-select --install
```

### 2. 重新生成 macOS 专用图标（.icns）

服务器侧没法生成 .icns，到你 Mac 上执行：

```bash
cd desktop
cargo tauri icon ./src-tauri/icons/icon.png
```

这会自动产出 `icon.icns` 和补全所有缺失的尺寸。

### 3. 启动 Python 后端（另开一个终端）

```bash
# 在仓库根
source <conda env>
cd 0427_test01
python -m pwa_dashboard.server
# 监听 :8766
```

### 4. 跑 Tauri（开发模式，热重载）

```bash
cd desktop
cargo tauri dev
```

会弹一个原生窗口，里面就是 dashboard。托盘图标在 macOS 顶栏 / Windows 任务栏。

### 5. 出 release 安装包

```bash
cargo tauri build
```

产出位置：

| 平台 | 输出 |
|------|------|
| macOS | `src-tauri/target/release/bundle/dmg/Sentinel-MCP_0.2.0_aarch64.dmg`（M 系列）<br>`src-tauri/target/release/bundle/dmg/Sentinel-MCP_0.2.0_x64.dmg`（Intel） |
| Windows | `src-tauri/target/release/bundle/msi/Sentinel-MCP_0.2.0_x64_en-US.msi` |
| Linux  | `src-tauri/target/release/bundle/appimage/sentinel-mcp_0.2.0_amd64.AppImage` |

## 目录结构

```
desktop/
├── README.md           ← 本文件
├── .gitignore          ← 忽略 target/ gen/ Cargo.lock
├── dist/
│   └── index.html      ← 静态前端兜底页（主窗口直接走 devUrl 不会用到）
└── src-tauri/
    ├── Cargo.toml      ← Rust 依赖（tauri 2 + tray-icon 特性）
    ├── tauri.conf.json ← Tauri 配置（窗口 / bundle / icon 路径）
    ├── build.rs        ← tauri-build 钩子
    ├── capabilities/
    │   └── default.json ← 权限白名单（最小授权原则）
    ├── icons/          ← PWA 同款 S 盾，14 个尺寸 + .ico
    │   ├── 32x32.png
    │   ├── 128x128.png
    │   ├── icon.png
    │   ├── icon.ico
    │   └── ...（在 Mac 上 `cargo tauri icon` 补 .icns）
    └── src/
        ├── main.rs     ← 入口（windows_subsystem 控制 + 调 lib::run）
        └── lib.rs      ← 实际应用逻辑：托盘菜单 + 窗口生命周期
```

## 已知问题 & W2 修复

- 第一次 `cargo tauri build` 会从 crates.io 下载 ~80 个 crate，慢但只一次（缓存到 `~/.cargo/`）
- 没有 .icns，macOS 包打不出标准图标 → `cargo tauri icon` 自动补
- Sidecar 没接，意味着用户必须手动开 Python 后端 → W2 day 5/8 修
- Windows 下需要 WebView2 Runtime（Win11 自带，Win10 装包时拉）
