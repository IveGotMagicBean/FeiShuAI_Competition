# Sentinel-MCP · 自部署到 VPS（阿里云北京 / 腾讯云 / 任何 Linux 服务器）

把 Cloudflare Worker 上的后端**完整复刻**到你自己的服务器，30 分钟跑通。

---

## 整套架构

```
评委手机 / 你电脑
         ↓
http://你服务器IP:8080
         ↓
你的 VPS（FastAPI + SQLite + 静态文件）
         ↓ ↑
跟原来 Cloudflare 那套行为完全一样
```

---

## 部署前你要准备

| 项 | 说明 |
|---|---|
| Linux 服务器 | Ubuntu / CentOS / Alibaba Cloud Linux 都可以，1 核 1G 就够 |
| SSH 能登 | 在你电脑能 `ssh root@服务器IP` |
| 端口 8080 开 | 阿里云用户：去 ECS 控制台开 8080 入方向 |

---

## 第 1 步：阿里云开 8080 端口

> 自购 VPS 跳过这一步

1. 登录 **阿里云控制台** → 云服务器 ECS → 实例
2. 点你的实例 → **安全组**
3. **配置规则** → **入方向** → **手动添加**
4. 填：
   - 类型：**自定义 TCP**
   - 端口范围：**8080/8080**
   - 授权对象：**0.0.0.0/0**
   - 描述：sentinel-mcp
5. **确定**

---

## 第 2 步：SSH 登录服务器，跑一行命令

```bash
ssh root@你的IP
```

进入服务器后：

```bash
bash <(curl -sL https://raw.githubusercontent.com/IveGotMagicBean/FeiShuAI_Competition/main/server/deploy.sh)
```

**就这一行**。脚本会自动：
1. 装 python3 / pip / git / sqlite3
2. git clone 仓库到 /opt/sentinel-mcp
3. 装 FastAPI + uvicorn
4. 写 systemd service 自动开机启动
5. 启动并打印访问地址

跑完看到 `✅ Sentinel-MCP 启动成功` 就 OK。

---

## 第 3 步：测试

在你电脑上：

```bash
curl http://你的IP:8080/api/health
# 应该返回: {"ok":true,"ts":...}

curl -X POST http://你的IP:8080/api/pair/register \
  -H "Content-Type: application/json" -d '{}'
# 应该返回 instance_id + admin_token + 6 位配对码
```

浏览器打开 `http://你的IP:8080/zh.html` 看到官网首页 = 大功告成。

---

## 第 4 步：把客户端代码指向你的新服务器

我会帮你改这 3 处（push 一个 commit 即可）：

| 文件 | 改什么 |
|---|---|
| `website/zh.html` | 二维码 URL → `http://你的IP:8080/mobile.html` |
| `sentinel_mcp/cloud_relay.py` | DEFAULT_CLOUD_BASE → `http://你的IP:8080` |
| `mobile/twa-manifest.json.template` | host → 你的 IP |

mobile.html 用的是相对路径 `/api/...`，**不用改**。

---

## 第 5 步（可选）：以后绑域名

买个域名后：

1. 域名厂商 DNS 加 **A 记录**指向你的服务器 IP
2. 服务器装 nginx + certbot 申请 Let's Encrypt 证书：
   ```bash
   sudo apt install -y nginx certbot python3-certbot-nginx
   sudo certbot --nginx -d 你的域名.top
   ```
3. 把客户端代码再指向 `https://你的域名.top`

⚠️ 国内服务器 80/443 端口需要 ICP 备案；想跳过备案就一直用 8080 端口。

---

## 常见问题

### Q: 跑完脚本但 curl 失败？

```bash
# 看服务状态
sudo systemctl status sentinel-mcp

# 看日志
sudo journalctl -u sentinel-mcp -f

# 看本地能不能访问
curl http://127.0.0.1:8080/api/health
```

如果 127.0.0.1 能访问但公网 IP 不行 → **是阿里云安全组 8080 没开**。

### Q: 想换端口？

```bash
sudo systemctl edit sentinel-mcp
# 加: Environment=SENTINEL_PORT=9090
sudo systemctl restart sentinel-mcp
```

### Q: 升级版本？

```bash
cd /opt/sentinel-mcp
sudo git pull
sudo systemctl restart sentinel-mcp
```

### Q: 想看 KV 存了什么？

```bash
sudo sqlite3 /opt/sentinel-mcp/server/sentinel.db
> SELECT key, expires_at FROM kv;
```

### Q: 数据想清掉重来？

```bash
sudo systemctl stop sentinel-mcp
sudo rm /opt/sentinel-mcp/server/sentinel.db
sudo systemctl start sentinel-mcp
```

---

## API 兼容性

8 个 endpoint **完全等价于** Cloudflare Worker 版本，URL / Header / Body / Response 一字不差：

```
POST /api/pair/register       桌面拿配对码
POST /api/pair/redeem         手机兑 mobile_token
POST /api/events/push         桌面 push 事件
GET  /api/events/list         手机拉事件
POST /api/approvals/push      桌面 push 审批
GET  /api/approvals/list      手机拉审批
POST /api/approvals/decide    手机决策
GET  /api/decisions/poll      桌面拉决策
```

切换部署不需要改任何业务代码逻辑，只改 `cloud_base` URL。
