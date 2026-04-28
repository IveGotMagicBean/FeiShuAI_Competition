# Sentinel-MCP · 发布指南

> 第一次推到 GitHub 怎么搞 / 怎么打 tag 触发出全平台安装包 / 跑挂了怎么修。

---

## A. 第一次推 GitHub（一次性）

### A.1 在 GitHub 网页上创建空仓库

1. 浏览器打开 <https://github.com/new>
2. 填写：
   - **Owner**: `IveGotMagicBean`（你自己的账号）
   - **Repository name**: `FeiShuAI_Competition`
   - **Public**（公开 — 这样 GitHub Actions 才有 unlimited free minutes）
   - **不要勾**「Add a README」「Add .gitignore」「Choose a license」 — 我们本地已经有了，勾了反而冲突
3. 点 **Create repository**

### A.2 本地 git 配一下身份（仅首次）

```bash
git config --global user.name "你的名字"
git config --global user.email "542058929@qq.com"
```

### A.3 配 SSH 密钥（一次性，避免每次输 token）

```bash
# 1) 看是否已有 SSH key
ls ~/.ssh/id_ed25519.pub 2>/dev/null || ls ~/.ssh/id_rsa.pub 2>/dev/null

# 2) 没有就生成一个（直接回车走默认路径，passphrase 留空也可）
ssh-keygen -t ed25519 -C "542058929@qq.com"

# 3) 看公钥内容
cat ~/.ssh/id_ed25519.pub
```

把 `cat` 出来的整行复制，到 GitHub <https://github.com/settings/ssh/new> 粘贴：
- Title 随便写（`WSL2-2026`）
- Key 粘那行公钥
- Add SSH key

测试：
```bash
ssh -T git@github.com
# 出现「Hi IveGotMagicBean! You've successfully authenticated」就成
```

### A.4 在 0427_test01 目录里 init 仓库

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY    # 7890 代理常挂，先 unset
cd /home/linshiyi/Studying/2026.04.25_FeiShuAI/0427_test01

git init -b main
git add .
git status                # 检查一下要提交的文件，看 .gitignore 是否生效
```

> ⚠️ 检查 `git status` 输出里**不该出现**：`.venv/` `__pycache__/` `dist-sidecar/` `desktop/src-tauri/target/` `data/sentinel.db*`。
> 如果有，说明 .gitignore 没生效，先 `git rm -r --cached <文件>` 再 `git add .`。

```bash
git commit -m "init: Sentinel-MCP v0.2.0 · 飞书 AI 黑客松"
```

### A.5 把本地仓库连到 GitHub 远端（SSH）

```bash
git remote add origin git@github.com:IveGotMagicBean/FeiShuAI_Competition.git
git push -u origin main
```

> 用 SSH 链接（A.3 配过 SSH key 之后），不用每次输 token。

### A.6 配仓库 Actions 权限（一次性）

push 完后到 <https://github.com/IveGotMagicBean/FeiShuAI_Competition/settings/actions>：

- **Workflow permissions** 选 **Read and write permissions**
- 勾 **Allow GitHub Actions to create and approve pull requests**
- **Save**

> 不开这个，CI 跑得了但**创建不了 Release**。

---

## B. 打 tag 自动出 .dmg / .msi / .AppImage

### B.1 打 tag 触发

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
cd /home/linshiyi/Studying/2026.04.25_FeiShuAI/0427_test01

# 确认 main 分支干净
git status

# 打 tag
git tag v0.2.0
git push origin v0.2.0
```

### B.2 看 CI 跑

打开 <https://github.com/IveGotMagicBean/FeiShuAI_Competition/actions>。

会看到 **Release** workflow 启动。它分四个 job：

| Job | 在哪个 runner 跑 | 大概要多久 |
|---|---|---|
| `python-and-sidecar` | ubuntu-latest | 3 min |
| `desktop · macos-arm64` | macos-latest | 8 min |
| `desktop · macos-x64` | macos-13 | 8 min |
| `desktop · windows-x64` | windows-latest | 10 min |
| `desktop · linux-x64` | ubuntu-latest | 7 min |
| `publish` | ubuntu-latest（最后跑） | 1 min |

**总共 ~15 分钟**（最慢的 windows runner 决定整体时间）。

### B.3 拿到安装包

跑完后到 <https://github.com/IveGotMagicBean/FeiShuAI_Competition/releases/tag/v0.2.0>：

```
Sentinel-MCP_0.2.0_aarch64.dmg          # macOS Apple Silicon
Sentinel-MCP_0.2.0_x64.dmg              # macOS Intel
Sentinel-MCP_0.2.0_x64_en-US.msi        # Windows
sentinel-mcp_0.2.0_amd64.AppImage       # Linux 通用
sentinel-mcp_0.2.0_amd64.deb            # Debian / Ubuntu
sentinel-mcp-0.2.0-1.x86_64.rpm         # Fedora / RHEL
sentinel_mcp-0.2.0-py3-none-any.whl     # Python wheel
sentinel_mcp-0.2.0.tar.gz               # Python sdist
SHA256SUMS.txt                          # 校验和
```

把这个 Release 链接放进赛事提交即可。

---

## C. 如果跑挂了怎么办

第一次跑大概率会有一两步报错（很正常 — 我没法在本机模拟 GitHub runner 环境）。

### C.1 看哪步挂了

Actions 页面点进失败的那次 Run，每个 job 左侧绿色对勾 / 红色叉号一目了然。展开报错的 step 看错误。

### C.2 常见错误 + 修法

| 错误 | 原因 | 修法 |
|---|---|---|
| `Resource not accessible by integration` | Workflow 没写权限 | 回 A.5 配权限 |
| `pyinstaller: command not found` | requirement 漏装 | 我已经在 release.yml 里 `pip install pyinstaller`；如果还报，可能是 PATH 问题，加 `python -m pyinstaller` |
| Tauri build 报 GTK 缺失 | linux runner 系统包没装齐 | 我已经在 release.yml 里加了 `sudo apt install`；如果新版 ubuntu 改了包名要改 |
| macOS 报 `code object is not signed` | 苹果证书 | 这是**警告不是错误**，dmg 还是出得了；用户右键 → 打开就能装 |
| `Tag v0.2.0 already exists` | 同一个 tag 重推被拒 | 见 C.3 |
| pypi 那个 job skipped / failed | 没配 trusted publisher | 不影响 .dmg/.msi 出包；按 release.yml 注释配；不发 PyPI 也 ok |

### C.3 修了之后重跑

```bash
# 删掉本地 + 远端的旧 tag
git tag -d v0.2.0
git push origin :refs/tags/v0.2.0

# 改完代码重新 commit + tag
git add .
git commit -m "fix: <修了什么>"
git push
git tag v0.2.0
git push origin v0.2.0
```

或者用「version bump」推 v0.2.1 重新发，比删 tag 干净。

---

## D. 发布后怎么演示给评委

把这两条链接贴到提交表里：

1. **仓库**：<https://github.com/IveGotMagicBean/FeiShuAI_Competition>
2. **Release（含全平台安装包）**：<https://github.com/IveGotMagicBean/FeiShuAI_Competition/releases/tag/v0.2.0>

评委可以：
- 直接下 .dmg / .msi 双击装
- 或者按 `SUBMISSION_CHECKLIST.md` 的「评委一页 quick start」跑 `git clone + pip install`
- 看 `examples/red_blue_demo.py` 输出的红蓝对抗
- 看 `data/attack_report.md` 53/53 攻击集结果

---

## E. 后续版本怎么发

只要：

```bash
git tag v0.2.1     # 或 v0.3.0
git push origin v0.2.1
```

整个流程同 B.2/B.3 自动跑。 `pyproject.toml` 里的 version 字段一起改就行。
