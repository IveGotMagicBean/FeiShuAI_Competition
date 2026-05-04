  例 1：ALLOW（绿色，立刻通过）

  cat examples/quick/01_allow.json | SENTINEL_DB=$(pwd)/data/sentinel.db /tmp/sm-venv/bin/python -m sentinel_mcp.cli wrap -- cat

  ---
  例 2：ASK_USER（橙色，等待审批）

  cat examples/quick/02_ask_user.json | SENTINEL_DB=$(pwd)/data/sentinel.db /tmp/sm-venv/bin/python -m sentinel_mcp.cli wrap -- cat

  ---
  例 3：DENY · SSH 私钥（红色）

  cat examples/quick/03_deny_ssh.json | SENTINEL_DB=$(pwd)/data/sentinel.db /tmp/sm-venv/bin/python -m sentinel_mcp.cli wrap -- cat

  ---
  例 4：DENY · 危险 shell 命令（红色）

  cat examples/quick/04_deny_shell.json | SENTINEL_DB=$(pwd)/data/sentinel.db /tmp/sm-venv/bin/python -m sentinel_mcp.cli wrap -- cat

  ---
  例 5：DENY · 黑名单域名（红色）

  cat examples/quick/05_deny_attacker.json | SENTINEL_DB=$(pwd)/data/sentinel.db /tmp/sm-venv/bin/python -m sentinel_mcp.cli wrap -- cat



   cd /home/linshiyi/Studying/2026.04.25_FeiShuAI/0427_test01
  cat examples/quick/02_ask_user.json | SENTINEL_DB=$(pwd)/data/sentinel.db /tmp/sm-venv/bin/python -m sentinel_mcp.cli wrap -- cat
