#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-/home/trader/polymarket_api/POLYMARKET_MAKER_copytrade}"
RUN_USER="${RUN_USER:-root}"
PYTHON_BIN="${PYTHON_BIN:-/root/.pyenv/versions/poly312/bin/python}"
ENV_FILE="${ENV_FILE:-/root/.polymarket.env}"
ARCHIVE_URL="${ARCHIVE_URL:-https://github.com/meta-xucong/POLYMARKET_MAKER_copytrade/archive/refs/heads/main.tar.gz}"
SERVICES=(
  "polymaker-copytrade.service"
  "polymaker-autorun.service"
)

print_recent_logs() {
  local service="$1"
  echo "[INFO] 最近日志: $service"
  journalctl -u "$service" -n 60 --no-pager || true
}

check_service_active() {
  local service="$1"
  if systemctl is-active --quiet "$service"; then
    echo "[OK] 服务运行中: $service"
    return 0
  fi

  echo "[ERROR] 服务未处于 active 状态: $service" >&2
  systemctl --no-pager --full status "$service" || true
  print_recent_logs "$service"
  return 1
}

require_cmd() {
  local cmd="$1"
  if command -v "$cmd" >/dev/null 2>&1; then
    return 0
  fi
  echo "[ERROR] 缺少命令: $cmd" >&2
  exit 1
}

echo "[INFO] APP_ROOT=$APP_ROOT"
echo "[INFO] ARCHIVE_URL=$ARCHIVE_URL"

require_cmd curl
require_cmd tar
require_cmd rsync
require_cmd systemctl

if [[ ! -d "$APP_ROOT" ]]; then
  echo "[ERROR] 安装目录不存在: $APP_ROOT" >&2
  exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

ARCHIVE_PATH="$TMP_DIR/app.tar.gz"
EXTRACT_DIR="$TMP_DIR/extract"

echo "[INFO] 下载最新代码压缩包..."
curl -L --fail "$ARCHIVE_URL" -o "$ARCHIVE_PATH"

mkdir -p "$EXTRACT_DIR"
echo "[INFO] 解压代码..."
tar -xzf "$ARCHIVE_PATH" -C "$EXTRACT_DIR"

SRC_DIR="$(find "$EXTRACT_DIR" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
if [[ -z "${SRC_DIR:-}" || ! -d "$SRC_DIR" ]]; then
  echo "[ERROR] 解压后未找到源码目录" >&2
  exit 1
fi

echo "[INFO] 同步最新文件到安装目录..."
rsync -a --delete \
  --exclude ".git/" \
  --exclude "case/" \
  "$SRC_DIR"/ "$APP_ROOT"/

echo "[INFO] 重新安装并重启 systemd 服务..."
sudo bash "$APP_ROOT/POLYMARKET_MAKER_copytrade_v3/systemd/install_services.sh" \
  "$APP_ROOT" \
  "$RUN_USER" \
  "$PYTHON_BIN" \
  "$ENV_FILE"

echo "[INFO] 显式重启目标服务..."
sudo systemctl restart "${SERVICES[@]}"

echo "[INFO] 等待服务稳定..."
sleep 5

for service in "${SERVICES[@]}"; do
  check_service_active "$service"
done

echo "[INFO] 当前服务状态："
for service in "${SERVICES[@]}"; do
  systemctl --no-pager --full status "$service" || true
done

echo "[OK] 更新并重启完成"
