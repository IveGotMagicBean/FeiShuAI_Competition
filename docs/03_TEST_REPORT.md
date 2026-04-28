# Sentinel-MCP · 测试报告

> 版本：0.2.0 · 测试日期：2026-04-28 · 平台：Ubuntu 22.04 (WSL2) / Python 3.12

## 总览

| 套件 | 通过 / 总数 | 备注 |
|---|---|---|
| Proxy 单元测试 | **5 / 5** | `_handle_client_msg` 四种路径 |
| ASK_USER 异步审批闭环 | **5 / 5** | approve / deny / timeout / proxy×2 |
| cat 假上游冒烟 | **4 / 4** | DENY/ALLOW/PASSTHROUGH 全链路 |
| 真 MCP 上游 e2e | **5 / 5** | `@modelcontextprotocol/server-filesystem` |
| L4 出向 DLP | **6 / 6** | OpenAI key / JWT / 多类型 / 透传分支 |
| Web Push 管理 | **4 / 4** | VAPID 持久化 / 订阅 CRUD / 非法拒绝 |
| 桌面入口 | **4 / 4** | wait_for_port / argparse |
| 攻击用例回归 | **53 / 53** | 100% 拦截率 |
| **合计** | **86 / 86** | |

平均决策耗时 **4.09 ms**，P99 **7.86 ms**。

## 攻击用例覆盖（53 条）

详见 [`data/attack_report.md`](../data/attack_report.md)（每跑一次自动重写）。

| 前缀 | 类别 | 数量 | 通过 |
|---|---|---|---|
| A | Prompt 注入 / 越狱 | 12 | 12 |
| B | 文件系统 / 路径穿越 | 13 | 13 |
| C | 网络外发 / SSRF | 11 | 11 |
| D | 命令注入 / 危险 shell | 11 | 11 |
| E | 敏感工具 / 兜底匹配 | 6 | 6 |

### A. Prompt 注入 / 越狱（12 条）

涵盖：英文 ignore-previous、中文「忽略前面」、DAN 越狱、开发者模式、伪造 `[SYSTEM]:`、
伪造 `</user><system>` 边界、RAG 间接注入、Pretend roleplay、自称管理员、奶奶模式、
对话续写攻击、正常输入对照。

### B. 文件系统 / 路径穿越（13 条）

涵盖：`../` 穿越、SSH 私钥、AWS 凭据、`.env`、/etc/shadow、/proc/self/environ、
kubeconfig、GPG 私钥、Docker config、ED25519 私钥、写入授权、正常 list_dir、正常文件读取对照。

### C. 网络外发 / SSRF（11 条）

涵盖：attacker.com、pastebin、云元数据、内网 IP、127.0.0.1、10.x、ngrok、webhook.site、
transfer.sh、0x0.st、正常 anthropic（authz 拒绝）。

### D. 命令注入 / 危险 shell（11 条）

涵盖：`;` 拼接、rm -rf、curl|sh、fork bomb、git status（authz）、mkfs、dd 写盘、
chmod 777、chown root、wget|bash、rm -fr 短选项变体。

### E. 敏感工具 / 兜底匹配（6 条）

涵盖：clipboard_write 授权、未声明工具放行、credentials 文件兜底、`.env.production`
兜底、SSH 公钥也禁、正常 list 子目录。

## 单元 / 集成测试

### Proxy 单元测试（`tests/test_proxy.py`）

```
✓ non-tool-call passthrough     # initialize / tools/list 等原样转
✓ ALLOW safe /tmp read          # 沙箱白名单内文件
✓ DENY ~/.ssh/id_rsa read       # 命中 denylist
✓ DENY rm -rf shell             # 命中 blocked_patterns
✓ DENY /etc/passwd read         # 命中 denylist
```

### ASK_USER 异步审批闭环（`tests/test_ask_user_e2e.py`）

```
✓ approve flow                  # decide(approved=True) → ALLOW
✓ deny flow                     # decide(approved=False) → DENY + user_denied
✓ timeout flow                  # 不 decide → expired + DENY
✓ proxy approve handoff         # event loop 不挂死，msg 正确转发
✓ proxy deny handoff            # 拒绝时回 -32000 error
```

### 真 MCP 上游 e2e（`tests/test_real_mcp.py`）

需要先 `npm install --prefix /tmp/sentinel_npm @modelcontextprotocol/server-filesystem`：

```
✓ initialize 与真 MCP server 握手成功
✓ tools/list 透传成功
✓ Proxy 直接拦截 /etc/passwd 读取
✓ Proxy 直接拦截 ~/.ssh/id_rsa 读取
✓ ALLOW 合法读取：透传到上游并返回 'hello world'
```

## 性能基准

53 条攻击集决策延迟分布：

- min: 2.47 ms
- avg: 4.09 ms
- P50: 3.65 ms
- P95: 5.75 ms
- P99: 7.86 ms

代理引入的开销主要来自：JSON 解析（~0.5 ms）、规则正则匹配（~2 ms）、
SQLite 审计写入（~1 ms）。**绝对值远小于 Agent 调用 LLM 的端到端延迟**（数百 ms）。

## 漏检 / 已知缺口

- Prompt Injection 是规则 + 启发式，对**精心构造的对抗性 prompt**仍可能漏检；
  W2 计划接小模型分类器作为补强（参考 PromptGuard / Granite-Guardian）。
- L4 出向 DLP 当前只在 dashboard 层使用，proxy `_upstream_to_client` 方向尚未接；
  W2 接入。

## 复现步骤

```bash
git clone https://github.com/IveGotMagicBean/FeiShuAI_Competition.git
cd FeiShuAI_Competition/0427_test01
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dashboard,dev]"

python tests/test_proxy.py
python tests/test_ask_user_e2e.py
python tests/test_e2e_smoke.py
python -m tests.attack_cases.run_all
```

CI 在每个 PR 上跑同样的套件，矩阵覆盖 Python 3.10 / 3.11 / 3.12。
