#!/usr/bin/env bash
#
# 在 Ubuntu 上一键部署 TSCP 剧情工坊（gunicorn + systemd，监听 8888）。
#
#   sudo ./deploy/install-ubuntu.sh
#
# 可用环境变量覆盖：APP_DIR / DATA_DIR / SERVICE_USER / PORT
#
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/tscp}"
DATA_DIR="${DATA_DIR:-/var/lib/tscp-web}"
SERVICE_USER="${SERVICE_USER:-tscp}"
PORT="${PORT:-8888}"
ENV_FILE="/etc/tscp-web.env"
UNIT="/etc/systemd/system/tscp-web.service"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m错误:\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "请用 sudo 运行（需要创建用户并写 systemd 单元）"
command -v python3 >/dev/null 2>&1 || die "找不到 python3，先 apt install python3 python3-venv"

log "检查 Python 版本（需要 3.9+）"
python3 - <<'PY' || die "Python 版本过低"
import sys
raise SystemExit(0 if sys.version_info >= (3, 9) else 1)
PY
python3 -c 'import venv' 2>/dev/null || die "缺少 venv 模块：apt install python3-venv"

log "创建系统用户 $SERVICE_USER"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi

log "同步代码到 $APP_DIR"
mkdir -p "$APP_DIR"
if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
        --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
        --exclude '.pytest_cache' --exclude 'webapp/data' --exclude 'source/dist' \
        "$SOURCE_DIR"/ "$APP_DIR"/
else
    cp -a "$SOURCE_DIR"/. "$APP_DIR"/
    rm -rf "$APP_DIR/.git" "$APP_DIR/.venv" "$APP_DIR/webapp/data" \
           "$APP_DIR/.pytest_cache" "$APP_DIR/source/dist"
fi

log "创建虚拟环境并安装网页版依赖（不含 PySide6 / pygame）"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip >/dev/null
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements-web.txt"

log "准备数据目录 $DATA_DIR"
mkdir -p "$DATA_DIR"

log "写入 $ENV_FILE"
if [[ -f "$ENV_FILE" ]]; then
    log "$ENV_FILE 已存在，保留原有内容（密钥不会被覆盖）"
else
    SECRET="$("$APP_DIR/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))')"
    sed -e "s|^TSCP_SECRET_KEY=.*|TSCP_SECRET_KEY=$SECRET|" \
        -e "s|^TSCP_DATA_DIR=.*|TSCP_DATA_DIR=$DATA_DIR|" \
        -e "s|^TSCP_PORT=.*|TSCP_PORT=$PORT|" \
        "$SOURCE_DIR/deploy/tscp-web.env.example" > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
fi

log "安装 systemd 单元"
sed -e "s|^ReadWritePaths=.*|ReadWritePaths=$DATA_DIR|" \
    -e "s|--bind 0\.0\.0\.0:8888|--bind 0.0.0.0:$PORT|" \
    "$SOURCE_DIR/deploy/tscp-web.service" > "$UNIT"
chmod 644 "$UNIT"

log "设置权限"
chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR" "$APP_DIR"

# 端口冲突留到启动时才失败很难查，所以在动手之前先说清楚。
# 更新已有部署时跳过：那时占用端口的正是我们自己。
if ! systemctl is-active --quiet tscp-web 2>/dev/null; then
    log "检查端口 $PORT 是否空闲"
    if command -v ss >/dev/null 2>&1; then
        if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$PORT\$"; then
            printf '\n端口 %s 已经被占用了：\n\n' "$PORT" >&2
            ss -ltnp 2>/dev/null | grep -E "[:.]$PORT\$" >&2 || true
            die "换一个端口：sudo PORT=9999 ./deploy/install-ubuntu.sh"
        fi
    fi
fi

log "启动服务"
systemctl daemon-reload
systemctl enable tscp-web >/dev/null
systemctl restart tscp-web
sleep 2

if systemctl is-active --quiet tscp-web; then
    log "服务已启动"
else
    printf '\n服务没起来，看日志：\n\n  journalctl -u tscp-web -n 50 --no-pager\n\n'
    systemctl --no-pager --lines=20 status tscp-web || true
    exit 1
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
cat <<EOF

部署完成。

  访问      http://${IP:-<服务器地址>}:$PORT
  第一个注册的账号会自动成为管理员。

  代码      $APP_DIR
  数据      $DATA_DIR
  配置      $ENV_FILE
  单元      $UNIT

常用命令：
  systemctl status tscp-web
  systemctl restart tscp-web
  journalctl -u tscp-web -f

如果 8888 打不开，检查防火墙：
  sudo ufw allow $PORT/tcp
EOF
