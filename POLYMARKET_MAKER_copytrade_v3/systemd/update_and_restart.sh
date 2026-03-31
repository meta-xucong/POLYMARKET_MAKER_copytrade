#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-/home/trader/polymarket_api/POLYMARKET_MAKER_copytrade}"
RUN_USER="${RUN_USER:-root}"
PYTHON_BIN="${PYTHON_BIN:-/root/.pyenv/versions/poly312/bin/python}"
ENV_FILE="${ENV_FILE:-/root/.polymarket.env}"
REMOTE_NAME="${REMOTE_NAME:-origin}"
BRANCH_NAME="${BRANCH_NAME:-main}"
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

echo "[INFO] APP_ROOT=$APP_ROOT"
echo "[INFO] REMOTE=$REMOTE_NAME BRANCH=$BRANCH_NAME"

if ! git -C "$APP_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "[ERROR] Git 仓库不存在或目录不可用: $APP_ROOT" >&2
  exit 1
fi

cd "$APP_ROOT"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "[ERROR] 检测到未提交的已跟踪改动，已停止自动更新。" >&2
  echo "[HINT] 先提交/暂存/还原改动后再执行本脚本。" >&2
  git status --short
  exit 1
fi

echo "[INFO] 拉取远程最新代码..."
git fetch "$REMOTE_NAME" "$BRANCH_NAME"

LOCAL_SHA="$(git rev-parse HEAD)"
REMOTE_SHA="$(git rev-parse "$REMOTE_NAME/$BRANCH_NAME")"

if [[ "$LOCAL_SHA" != "$REMOTE_SHA" ]]; then
  echo "[INFO] 更新本地代码到 $REMOTE_NAME/$BRANCH_NAME"
  git pull --ff-only "$REMOTE_NAME" "$BRANCH_NAME"
else
  echo "[INFO] 本地已是最新代码"
fi

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
