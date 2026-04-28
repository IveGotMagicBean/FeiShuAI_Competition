# Changelog

## 0.2.0 — 2026-04-28

首个可发布版本。

### Added (later in 2026-04-28)

- **L4 出向 DLP**：proxy `_upstream_to_client` 方向接 `DLPDetector`；
  上游 `tools/call` result 里出现的 OpenAI key / JWT / AWS key / 邮箱 / 手机号
  等命中即原地脱敏，写一条 `output_check` 审计。`MCPProxy(..., dlp_outbound=False)` 可关。
- **Web Push（VAPID 直推）**：dashboard 自动生成 VAPID 密钥对 + SQLite 订阅表 +
  `pywebpush.webpush()` 发推。新 API：`/api/push/vapid-public-key`、
  `POST /api/push/subscribe`、`POST /api/push/unsubscribe`、`POST /api/push/test`。
  Service Worker `push` 事件 + `notificationclick` 跳回 dashboard；前端「订阅锁屏推送」按钮。
  SSE 检测到新 pending 时 `run_in_executor` fan-out 给所有订阅。**不再依赖 Cloudflare relay**。
- **审批历史 tab**：dashboard 待审批面板加 tab 切换「待审批 / 历史」；历史按时间倒序，
  状态色：approved=绿 / denied=红 / expired=灰；SSE `decided` 事件实时进队头。
- **`sentinel-mcp-desktop` 桌面入口**：pip 装包后多一个 CLI；
  自启 dashboard 后端 + 等端口就绪 + 弹窗（pywebview 优先 / 默认浏览器回落）；
  作为不需要 Rust/Tauri 工具链的轻量桌面体验。
- **桌面构建指南**：`docs/05_DESKTOP_BUILD.md` 列三条路径（pywebview / Tauri / PWA install），
  mac / win / linux 系统依赖清单完整。

### Added
- **MCP stdio 代理**（`sentinel-mcp wrap -- <upstream-cmd>`）
  - 拦截 `tools/call`，按 Guard 决策放行 / 拒绝 / 改写 / 请求审批
  - 其它 MCP 方法（`initialize` / `tools/list` / `resources/*` …）原样透传
- **Guard 决策引擎**（L1–L4 四层）
  - L1 输入层：29 条 Prompt Injection 规则
  - L2 工具调用层：策略化 `ALLOW` / `DENY` / `REDACT` / `ASK_USER`
  - L3 沙箱层：文件系统（allow/denylist + glob）/ 网络（域名白名单 + SSRF + 私网阻断）/ Shell（命令白名单 + 危险模式黑名单）
  - L4 输出层：13 个 DLP 模式（dashboard 已使用；proxy 出向脱敏列入 0.3 计划）
- **跨进程审批队列**（`sentinel_mcp.approvals.PendingDecisions`）
  - SQLite WAL 共享，多进程并发安全
  - `make_callback(timeout_seconds)` 给 Guard 用；超时按拒绝处理
- **PWA Dashboard**（`python -m pwa_dashboard.server`）
  - 端口 8766；PWA 三件套（manifest / service worker / 192·512·maskable 图标）
  - SSE 实时事件流（`/api/events/stream`）：`hello` / `event` / `pending` / `decided` 四种事件
  - 待审批面板：手机批准/拒绝高危调用，浏览器原生 Notification
- **示例策略**：`sentinel_mcp/config/policies.yaml` 内置随包发布

### Tests
- `tests/test_proxy.py`：5/5（Proxy 单元测试）
- `tests/test_ask_user_e2e.py`：5/5（ASK_USER 闭环：approve / deny / timeout / proxy 集成 ×2）
- `tests/test_e2e_smoke.py`：4/4（cat 假上游冒烟）
- `tests/test_real_mcp.py`：5/5（@modelcontextprotocol/server-filesystem 真上游，CI 需先 npm install）
- v0.1 攻击集回归 22/22（位于 `0425_test01/tests/attack_cases/run_all.py`）

### Notes
- Python ≥ 3.10
- `pip install sentinel-mcp` 仅装代理；`pip install "sentinel-mcp[dashboard]"` 同时装 PWA dashboard 依赖

## 0.1.0 — 2026-04-25 (legacy v0.1, in `0425_test01/`)

- 初版 Guard SDK + dashboard 演示
- 装饰器接口 `@guard.protected("filesystem")`
- 22 条攻击用例覆盖
