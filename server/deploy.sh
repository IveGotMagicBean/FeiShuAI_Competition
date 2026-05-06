#!/bin/bash
# Sentinel-MCP · 一键部署到 Linux VPS
# ============================================================
# 适用：Ubuntu / Debian / CentOS / Alibaba Cloud Linux
# 用法（在你的服务器上跑）：
#     bash <(curl -sL https://raw.githubusercontent.com/IveGotMagicBean/FeiShuAI_Competition/main/server/deploy.sh)
# 或者手工：
#     git clone https://github.com/IveGotMagicBean/FeiShuAI_Competition.git
#     cd FeiShuAI_Competition/server
#     bash deploy.sh

set -e

INSTALL_DIR="${INSTALL_DIR:-/opt/sentinel-mcp}"
SENTINEL_PORT="${SENTINEL_PORT:-8080}"
REPO_URL="${REPO_URL:-https://github.com/IveGotMagicBean/FeiShuAI_Competition.git}"
SERVICE_NAME="sentinel-mcp"

echo "================================================"
echo "  Sentinel-MCP 自部署脚本"
echo "================================================"
echo "  INSTALL_DIR : $INSTALL_DIR"
echo "  SENTINEL_PORT: $SENTINEL_PORT"
echo "  REPO_URL    : $REPO_URL"
echo "================================================"

# ---- 1. 装系统依赖 ----
if command -v apt-get >/dev/null 2>&1; then
    echo "[1/5] 装 python3 + git + sqlite (apt) ..."
    sudo apt-get update -y
    sudo apt-get install -y python3 python3-pip python3-venv git sqlite3 curl
elif command -v yum >/dev/null 2>&1; then
    echo "[1/5] 装 python3 + git + sqlite (yum) ..."
    sudo yum install -y python3 python3-pip git sqlite curl
elif command -v dnf >/dev/null 2>&1; then
    echo "[1/5] 装 python3 + git + sqlite (dnf) ..."
    sudo dnf install -y python3 python3-pip git sqlite curl
else
    echo "无法识别包管理器，请手动安装 python3 / pip / git / sqlite3 后重试"
    exit 1
fi

# ---- 2. 拉代码 ----
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "[2/5] 已存在 $INSTALL_DIR, 拉最新代码 ..."
    cd "$INSTALL_DIR"
    sudo git pull --rebase
else
    echo "[2/5] git clone 到 $INSTALL_DIR ..."
    sudo mkdir -p "$INSTALL_DIR"
    sudo chown -R "$USER:$USER" "$INSTALL_DIR"
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# ---- 3. 装 Python 依赖 ----
echo "[3/5] 装 Python 依赖（FastAPI + uvicorn）..."
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install fastapi 'uvicorn[standard]'

# ---- 4. 装 systemd service ----
echo "[4/5] 写 systemd service ..."

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Sentinel-MCP Self-hosted Backend
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$INSTALL_DIR/server
Environment=SENTINEL_PORT=$SENTINEL_PORT
Environment=SENTINEL_HOST=0.0.0.0
Environment=SENTINEL_DB_PATH=$INSTALL_DIR/server/sentinel.db
Environment=SENTINEL_WEBSITE_DIR=$INSTALL_DIR/website
ExecStart=$INSTALL_DIR/.venv/bin/python $INSTALL_DIR/server/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

# ---- 5. 报状态 ----
echo "[5/5] 完成! 等 3 秒检查状态 ..."
sleep 3

if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo ""
    echo "================================================"
    echo "  ✅ Sentinel-MCP 启动成功"
    echo "================================================"
    PUBLIC_IP=$(curl -s --max-time 5 https://api.ipify.org || echo "<your-server-ip>")
    echo ""
    echo "  本地访问:     http://127.0.0.1:$SENTINEL_PORT/"
    echo "  公网访问:     http://$PUBLIC_IP:$SENTINEL_PORT/"
    echo "  手机版:       http://$PUBLIC_IP:$SENTINEL_PORT/mobile.html"
    echo "  健康检查:     http://$PUBLIC_IP:$SENTINEL_PORT/api/health"
    echo ""
    echo "  ⚠️  阿里云用户:"
    echo "    去 ECS 控制台 → 安全组 → 入方向规则"
    echo "    添加规则: TCP 端口 $SENTINEL_PORT, 源 0.0.0.0/0"
    echo ""
    echo "  常用命令:"
    echo "    查看日志: sudo journalctl -u $SERVICE_NAME -f"
    echo "    重启:    sudo systemctl restart $SERVICE_NAME"
    echo "    停止:    sudo systemctl stop $SERVICE_NAME"
    echo ""
else
    echo ""
    echo "❌ Sentinel-MCP 启动失败! 看日志诊断:"
    echo "    sudo journalctl -u $SERVICE_NAME -n 50"
    exit 1
fi
