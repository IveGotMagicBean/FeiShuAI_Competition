# Contributing to Sentinel-MCP

谢谢你对 Sentinel-MCP 的关注！本项目目前在飞书 AI 校园挑战赛冲刺期，主线开发节奏较快，
但所有代码都欢迎提 issue / PR。

## 开发环境

```bash
git clone https://github.com/IveGotMagicBean/FeiShuAI_Competition.git
cd FeiShuAI_Competition/0427_test01
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dashboard,dev]"
```

## 跑测试

```bash
python tests/test_proxy.py            # 5/5  Proxy 单元
python tests/test_ask_user_e2e.py     # 5/5  ASK_USER 异步审批闭环
python tests/test_e2e_smoke.py        # 4/4  cat 假上游冒烟
python tests/test_real_mcp.py         # 5/5  真 MCP 上游（需 npm install）
```

提 PR 之前请确保以上四个套件全部通过。

## 提 PR 须知

1. 一个 PR 只做一件事 — 重构 / 修 bug / 加功能不要混
2. 增加任何对外 API（CLI 参数、Python 函数、HTTP 端点）都要带至少一个测试
3. 提交信息用中文或英文都行，但请用动词起头：`fix:` / `feat:` / `docs:` / `test:` / `refactor:`
4. 涉及安全策略变动（新增/修改 detector、sandbox 规则）的 PR 必须同时附攻击用例

## 报告漏洞

发现安全漏洞**不要开 public issue**，按 [`SECURITY.md`](./SECURITY.md) 流程私下沟通。

## 行为准则

详见 [`CODE_OF_CONDUCT.md`](./CODE_OF_CONDUCT.md)（Contributor Covenant 2.1）。
