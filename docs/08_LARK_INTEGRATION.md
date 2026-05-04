# Sentinel-MCP · 飞书集成完整指南（实战踩坑版）

> 这一篇覆盖两条飞书集成路径：
> 1. **审批通道**：把 ASK_USER 推到飞书消息卡片，远程一键批准
> 2. **保护飞书 API**：用 `sentinel-mcp wrap` 包飞书官方 MCP server
>
> 跟着这一篇 30 分钟跑通端到端。每一步都标了「**会卡哪儿**」，按今天我们踩过的坑顺序写。

---

## 0. 注册自建应用（5 分钟）

1. 打开 https://open.feishu.cn/ → 右上角「**开发者后台**」（用飞书账号登录）
2. 「应用管理 → 自建应用」→ **「创建企业自建应用」**
3. 填：
   - **应用名称**：「Sentinel-MCP 安全审批助手」（**别用「中间人代理」「拦截器」之类的词**，企业管理员审核会皱眉）
   - **应用描述**：`Sentinel-MCP 安全代理的飞书审批通道。AI Agent 发起敏感工具调用时推送审批卡片到指定群，用户在飞书里点「批准 / 拒绝」即可远程决断。仅推送，不读取也不存储任何业务数据。`
   - **图标**：随便上传一张 PNG
4. 创建完进应用主页 → 抄两个东西保留好：
   - **App ID**：`cli_xxxxxxxxxxxx`
   - **App Secret**：一长串

---

## 1. 添加机器人能力（1 分钟）

「应用能力 → 添加应用能力」→ 只勾 **「机器人」**（其它能力一个都不要）。

启用机器人时配：
- **机器人名称**：「Sentinel-MCP 审批助手」
- **图标**：复用应用图标
- **使用范围**：「**仅自己可见**」最快批

---

## 2. 申请权限（2 分钟）

左侧「**权限管理**」→ 搜并勾上这 4 条：

| 权限 | 用途 |
|---|---|
| `im:message` | 读消息 |
| `im:message:send_as_bot` | 以机器人身份发消息（发卡片必备） |
| `im:resource` | 卡片资源 / 回传 |
| `im:chat` | 拿群信息（后面要查 chat_id） |

> ⚠ **不要全勾** — 只要这 4 条，权限范围最小化让审核更快过。

---

## 3. 配 ⚠ **两个独立的回调 URL** ⚠（最大坑！）

> **这是今天踩的第一大坑**：飞书把「事件回调」和「卡片回调」放在**两个不同的 tab**，两个都要配。

### 3.1 事件配置（普通事件回调）

左侧「**事件与回调**」→ tab「**事件配置**」

- **订阅方式**：选「**将事件发送至 开发者服务器**」（不是「长连接」）
- **请求地址**：填你的 callback URL（dashboard 启动后给你；trycloudflare 隧道的 URL 像下面这样）：
  ```
  https://normal-engineers-citations-registration.trycloudflare.com/api/lark/callback
  ```
- **加密策略**：点「随机生成」拿到：
  - **Encrypt Key**（保留好，等会儿要写到 dashboard config）
  - **Verification Token**（同上）

### 3.2 ⚠ **回调配置**（卡片按钮点击专用，**别忘这个**）

同一页另一个 tab：「**回调配置**」

- 跟事件配置**完全独立**的另一套订阅方式 + 请求地址
- 同样选「**将回调发送至 开发者服务器**」
- 同样填刚才那个 URL（**两个 URL 一样可以，dashboard 同一个 endpoint 处理两种**）
- 「**已订阅的回调**」点「**添加回调**」→ 搜 **「卡片回传交互」/「`card.action.trigger`」** → 勾上

> 💡 **没配第二个 → 飞书卡片按钮点击你收不到 → 飞书界面弹「出错了稍后重试 code 200340」**
> 这个错误的本质就是「卡片回调路径未配」，但飞书提示信息特别误导人。

### 3.3 加密 callback 需要 dashboard 支持 AES 解密

如果你设了 Encrypt Key，**飞书所有 callback（含 url_verification 握手）都被 AES-256-CBC 加密包成 `{"encrypt": "..."}`**。Sentinel-MCP 已经在 `sentinel_mcp/lark_notifier.py` 加了 `decrypt_payload()` 处理（v0.2.1+ 版本），不用你管。

---

## 4. 发布应用（**机器人才能用，必须做**）

> **这是今天踩的第二大坑**：没发布版本前，机器人**找不到、加不了群**。

左侧「**版本管理与发布**」→「**创建版本**」

- 版本号：`0.0.1`
- 更新说明：「初始版本」
- 可见范围：选「**仅自己可见**」最快批
- 点「**提交申请**」→ 弹审核页 → 你是企业管理员 → 自己秒批

> 不是企业管理员的话，找你公司管理员临时帮你过一下。

---

## 5. 把机器人加到测试群

飞书 app 里：
1. 创建一个新群（名字任意，比如「Sentinel 测试」）
2. 群设置 → 群机器人 → **添加机器人** → 搜你刚发布的应用名 → 添加

> ⚠ 上一步**应用没发布**的话，这里搜不到机器人。

---

## 6. 拿 chat_id（API 一行命令）

机器人在群里之后，跑这个脚本拿群的 `chat_id`：

```bash
# 改成你的 App ID/Secret，然后跑
APP_ID="cli_xxxxxxxxxxxx"
APP_SECRET="<YOUR_APP_SECRET>"

TOKEN=$(curl -s -X POST 'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal' \
  -H 'Content-Type: application/json' \
  -d "{\"app_id\":\"$APP_ID\",\"app_secret\":\"$APP_SECRET\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['tenant_access_token'])")

curl -s 'https://open.feishu.cn/open-apis/im/v1/chats?page_size=20' \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

输出会有：

```json
{
  "data": {
    "items": [
      {"chat_id": "oc_xxxxxxxxxxxx", "name": "Sentinel 测试", ...}
    ]
  }
}
```

复制 `chat_id`（`oc_xxx` 那串）。

---

## 7. 把 4 个值写到 dashboard config

启 dashboard 后跑：

```bash
curl -s -X POST http://localhost:8766/api/lark/config \
  -H 'Content-Type: application/json' \
  -d '{
    "app_id": "cli_xxxxxxxxxxxx",
    "app_secret": "<YOUR_APP_SECRET>",
    "encrypt_key": "你刚才生成的 Encrypt Key",
    "verification_token": "你刚才生成的 Verification Token",
    "target_chat_id": "oc_xxxxxxxxxxxx"
  }'
```

返回 `{"ok": true, "saved_to": "/home/.../lark_config.json"}` → 配好了。

---

## 8. 验证全链路（4 步连环测）

### 8.1 测试发消息

```bash
curl -s -X POST http://localhost:8766/api/lark/test \
  -H 'Content-Type: application/json' \
  -d '{"text":"🛡 Sentinel-MCP 测试 ✓"}'
```

→ 飞书群应该收到机器人发的纯文本消息。

### 8.2 触发一次 ASK_USER

```bash
cd /path/to/0427_test01
cat examples/quick/02_ask_user.json | SENTINEL_DB=$(pwd)/data/sentinel.db \
  /tmp/sm-venv/bin/python -m sentinel_mcp.cli wrap -- cat
```

→ 终端**挂住**等审批 → 飞书群弹**橙色卡片**「🛡 Sentinel-MCP · 待您授权」+ [✅ 批准] [🚫 拒绝] 按钮。

### 8.3 在飞书群里点 [✅ 批准]

→ **卡片立刻变绿色**「✅ 已批准 by xxx」 + **WSL 终端立刻解锁** + dashboard 待审批数 -1 + 历史里多一条 `decided_by: lark:ou_xxx`。

> 此时如果飞书弹「出错了稍后重试 code 200340」 → 99% 是第 3.2 步「回调配置」没配对。

### 8.4 看 callback 真的来了

```bash
grep "POST /api/lark/callback" /tmp/dev-dashboard.log | tail -5
```

→ 应该有几条 `INFO: 123.58.x.x:0 - "POST /api/lark/callback HTTP/1.1" 200 OK`（123.58.x 和 101.126.x 是飞书 IP）。

---

## 路径 2：包飞书 OpenAPI MCP server（演示「保护飞书 API」）

dashboard → 集成面板 → 点 Cursor / Claude Desktop 卡片的「+ 添加 wrapped MCP server」→ 预设选「**飞书 OpenAPI · @larksuiteoapi/lark-mcp**」→ 填 App ID + Secret → 写入。

写入后 client 的 mcpServers 多一条：

```json
"lark-mcp-guarded": {
  "command": "sentinel-mcp",
  "args": ["wrap", "--", "npx", "-y", "@larksuiteoapi/lark-mcp", "mcp", "-a", "cli_xxx", "-s", "sec_xxx"]
}
```

重启 Cursor → 它现在调飞书 OpenAPI 全部经过 Sentinel 代理。

**演示场景**：「Cursor 列出全公司 Bitable → Sentinel 拦下 → 飞书弹审批 → 你点拒绝 → AI 收到 deny error，不能滥用」。

---

## 故障排查（按出现频率排）

| 现象 | 原因 | 修法 |
|---|---|---|
| 飞书弹「出错了稍后重试 code 200340」 | **「回调配置」没配 callback URL 或没订阅 `card.action.trigger`** | 第 3.2 步重新配 |
| 加群时搜不到机器人 | 应用没发布 | 第 4 步发布 v0.0.1 |
| 测试消息没收到 | 1) 机器人没在群里 / 2) chat_id 错 / 3) App Secret 错 | 第 5/6/7 步重做 |
| dashboard 飞书面板状态不是绿色 | App ID/Secret 没保存 / lark-oapi 未装 | `pip install lark-oapi` + 重新调 /api/lark/config |
| 回调 url_verification 失败 | 1) trycloudflare 隧道断了 / 2) Encrypt Key 没配但飞书加密了 | 1) 重启 cloudflared 重配 URL / 2) 第 3.1 重设 Encrypt Key |
| 终端 ASK_USER 卡住但飞书没卡片 | LarkNotifier 未启用 / `target_chat_id` 没配 | curl /api/lark/status 看 notifier_active |
| 点按钮卡片不变色但终端解锁了 | callback 收到 + 决策成功，但 patch_to_decided 失败 | 看 dashboard 日志 grep 'patch failed'（一般是 message_id 缺失） |

---

## 配置文件 / 端口

| 路径 | 说明 |
|---|---|
| `~/.sentinel-mcp/lark_config.json` | 飞书 App 凭证（chmod 0o600） |
| `~/.sentinel-mcp/sentinel.db` | AppImage 自带的审计 DB（默认） |
| `0427_test01/data/sentinel.db` | dev 模式 dashboard 用的审计 DB |
| `localhost:8766` | dashboard HTTP |

> 删 `lark_config.json` = 重置飞书集成；删 `sentinel.db` = 清空所有事件 + 审批历史。

---

## 一句话总结

**做对了 6 件事飞书就通**：注册应用 → 加机器人 → 申请 4 项权限 → **配 2 个回调 URL（事件 + 回调）** → 发布版本 → 拿 chat_id 写 config。最容易漏的是**第二个回调 URL（卡片专用）**。
