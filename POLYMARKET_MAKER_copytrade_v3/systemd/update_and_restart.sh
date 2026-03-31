#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-/home/trader/polymarket_api/POLYMARKET_MAKER_copytrade}"
RUN_USER="${RUN_USER:-root}"
PYTHON_BIN="${PYTHON_BIN:-/root/.pyenv/versions/poly312/bin/python}"
ENV_FILE="${ENV_FILE:-/root/.polymarket.env}"
REMOTE_NAME="${REMOTE_NAME:-origin}"
BRANCH_NAME="${BRANCH_NAME:-main}"

echo "[INFO] APP_ROOT=$APP_ROOT"
echo "[INFO] REMOTE=$REMOTE_NAME BRANCH=$BRANCH_NAME"

if [[ ! -d "$APP_ROOT/.git" ]]; then
  echo "[ERROR] Git 仓库不存在: $APP_ROOT" >&2
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

echo "[INFO] 当前服务状态："
systemctl --no-pager --full status polymaker-copytrade.service || true
systemctl --no-pager --full status polymaker-autorun.service || true

echo "[OK] 更新并重启完成"
