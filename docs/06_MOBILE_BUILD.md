# Sentinel-MCP · 移动端 .apk 构建指南（Bubblewrap TWA）

> 把 PWA Dashboard 用 Bubblewrap 打包成 Android .apk，挂到 GitHub Releases，
> 满足赛题「移动端可直接安装运行」的硬性要求。

## 0. 整体思路

```
本机 dashboard (FastAPI :8766)
        │
        │ cloudflared tunnel
        ▼
https://<random>.trycloudflare.com   ← 临时 HTTPS 域名
        │
        ▼
Bubblewrap 把这个 URL 包成 TWA → sentinel-mcp.apk
        │
        ▼
用户手机装 .apk → 点开 → 实际加载的就是上面这个 URL
```

**关键点**：TWA 不是把网页打成离线包，它本质是一个「全屏打开指定 URL 的壳子」。
所以必须让 dashboard 跑在公网可达的 HTTPS 上。最快方案 = cloudflared 隧道（免费、零配置、5 分钟出来）。

---

## 1. 用户操作（你需要做的部分）

### 1.1 安装 cloudflared（一次性）

```bash
# Linux / WSL2
curl -fsSL https://pkg.cloudflare.com/cloudflared-linux-amd64.deb -o /tmp/cf.deb
sudo dpkg -i /tmp/cf.deb

# macOS
brew install cloudflared

# Windows: 去 https://github.com/cloudflare/cloudflared/releases 下载 .msi
```

> 提示：执行 curl 前先 `unset http_proxy https_proxy`（不然挂在 7890 代理上）。

### 1.2 启动 dashboard + 隧道（构建 .apk 时跑一次即可）

终端 A：
```bash
cd 0427_test01
source .venv/bin/activate
unset http_proxy https_proxy
python -m pwa_dashboard.server
```

终端 B：
```bash
cloudflared tunnel --url http://localhost:8766
```

输出会有一行：
```
Your quick Tunnel has been created! Visit it at:
https://<random-words>.trycloudflare.com
```

**复制这个 URL**，下一步用。

### 1.3 安装 Bubblewrap CLI（一次性）

```bash
# 需要 Node 18+ 和 JDK 17+
node --version       # 应 ≥ 18
java --version       # 应 ≥ 17（没装的话 sudo apt install openjdk-17-jdk）

npm i -g @bubblewrap/cli
bubblewrap doctor    # 自检 Android SDK 是否齐全；没齐它会自动下载到 ~/.bubblewrap/
```

### 1.4 用模板初始化（用我写好的配置）

```bash
cd 0427_test01/mobile
# 把 twa-manifest.json 里的 host 替换成你的 trycloudflare URL
sed -i "s|HOST_PLACEHOLDER|<random-words>.trycloudflare.com|g" twa-manifest.json

bubblewrap init --manifest=./twa-manifest.json
# 会问几个问题，全按回车用默认值即可
```

### 1.5 构建 .apk

```bash
bubblewrap build
# 第一次会下载 Android SDK + 构建工具，可能要 10-15 分钟
# 完成后产物在当前目录：app-release-signed.apk
```

### 1.6 上传到 Releases

```bash
gh release upload v0.2.0 app-release-signed.apk --repo IveGotMagicBean/FeiShuAI_Competition
# 或：到 GitHub Releases 页手动 attach 文件
```

---

## 2. 我已经准备好的部分

- `mobile/twa-manifest.json` — Bubblewrap 配置模板（颜色/图标/启动 URL 都已填）
- `pwa_dashboard/static/manifest.webmanifest` — PWA manifest 已有（Bubblewrap 直接读）
- 启动图标已就位（`pwa_dashboard/static/icon-512.png`）

---

## 3. 验证清单

装到 Android 手机后：
- [ ] App 抽屉里有 Sentinel-MCP 图标
- [ ] 点击图标，无 URL 栏全屏打开 dashboard（URL 栏可见也算过 — 是「未验证 TWA」状态，不影响功能）
- [ ] 桌面跑 `examples/red_blue_demo.py`，手机端 dashboard 实时刷出事件流
- [ ] 触发一次 ASK_USER，手机端「待审批」tab 出橙色卡片

---

## 4. 已知限制

- **trycloudflare URL 不固定**：每次 `cloudflared tunnel --url` 都生成新随机 URL。
  演示一次性用够；想长期跑要么用 Cloudflare Tunnel 免费版（绑自己域名），要么用 ngrok 的固定子域名（付费）。
- **未验证 TWA 顶部有 URL 栏**：要消掉需要 `assetlinks.json` 部署到固定域名。
  对赛题验收（「能装能跑」）不影响，是纯视觉细节。
- **iOS 没有 TWA**：iOS 上 PWA 只能走 Safari 「添加到主屏幕」，无 .ipa。
  这不是我们能改的——iOS 平台限制。

---

## 5. 故障排查

| 现象 | 原因 | 修法 |
|---|---|---|
| `bubblewrap init` 卡住下载 SDK | 网络墙 | 设 `ANDROID_HOME` 指到本地已有 SDK；或挂梯子 |
| 装到手机点开白屏 | trycloudflare URL 失效或本机 dashboard 没跑 | 重启 `cloudflared tunnel`，重新构建 |
| `bubblewrap build` 报 keystore 不存在 | 第一次构建 | `init` 时会问要不要生成，按 y |
| 实时事件不刷 | SSE 在某些代理下被掐 | 对于演示走 trycloudflare 是 OK 的 |
