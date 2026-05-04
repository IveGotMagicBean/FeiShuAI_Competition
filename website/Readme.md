# Sentinel-MCP 官网

`sentinel-mcp.dev` 的源码（暂用免费子域）。单页静态站点，纯 HTML/CSS/JS，**零构建**。

## 文件

| 文件 | 用途 |
|---|---|
| `index.html` | 入口 — 当前默认重定向到 `zh.html`，后续加 `en.html` 后按浏览器语言分流 |
| `zh.html` | 中文主版本（校园挑战赛 评委用） |
| `assets/screenshots/` | dashboard 截图占位 — 录完截图丢这里 |
| `assets/icons/` | 客户端 logo / favicon 等 |
| `CNAME` | GitHub Pages 自定义域用（暂无） |

## 本地预览

```bash
# 任意一种都行
python3 -m http.server 8000 --directory website
# → 浏览器开 http://localhost:8000/zh.html

# 或者
cd website && npx -y serve .
```

## 部署

### 选项 A · GitHub Pages（最简，免费）

1. 把 `website/` 目录 push 到 sentinel-mcp 仓库
2. 仓库 Settings → Pages → Source 选 `main` 分支 / `/website` 子目录
3. 等 30 秒，访问 `https://<username>.github.io/sentinel-mcp/`

### 选项 B · Cloudflare Pages（推荐，国内能访问、自动 HTTPS）

1. 登 dash.cloudflare.com → Workers & Pages → Create
2. Pages → Connect to Git → 选 sentinel-mcp 仓库
3. 构建设置：
   - Build command: `（留空）`
   - Build output directory: `website`
4. Deploy。1 分钟出一个 `<project>.pages.dev` 域名

### 选项 C · 自定义域 `sentinel-mcp.dev`

1. namecheap / cloudflare 注册 `sentinel-mcp.dev`（~$12/year，.dev 强制 HTTPS）
2. 上面任一部署完成后 → 加自定义域：
   - GitHub Pages：`website/CNAME` 写 `sentinel-mcp.dev` + 域名 DNS 加 CNAME 指向 `<username>.github.io`
   - Cloudflare Pages：项目设置 → Custom domains → Add → 自动帮你配 DNS
3. 等 5-30 分钟生效

## 还需要做的

- [ ] 截 6 张 dashboard 截图（Onboarding / 保护状态 / 强度面板 / Hook 矩阵 / 审批卡 / 实时事件流）丢 `assets/screenshots/`
- [ ] 加 `<img>` 引用截图到 zh.html 第 4 节后（建议 carousel 形态）
- [ ] 录 5 分钟红蓝对抗演示视频，传 B 站，iframe 嵌入第 5 节
- [ ] 出英文版 `en.html`（v0.4，先服务国内评委）
- [ ] 替换 GitHub URL `IveGotMagicBean/FeiShuAI_Competition` 为真实仓库
