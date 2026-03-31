#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-/home/trader/polymarket_api/POLYMARKET_MAKER_copytrade}"
RUN_USER="${RUN_USER:-root}"
PYTHON_BIN="${PYTHON_BIN:-/root/.pyenv/versions/poly312/bin/python}"
ENV_FILE="${ENV_FILE:-/root/.polymarket.env}"
ARCHIVE_URL="${ARCHIVE_URL:-https://github.com/meta-xucong/POLYMARKET_MAKER_copytrade/archive/refs/heads/main.tar.gz}"
KEEP_RUNTIME_STATE="${KEEP_RUNTIME_STATE:-1}"
SERVICES=(
  "polymaker-copytrade.service"
  "polymaker-autorun.service"
)
RUNTIME_EXCLUDES=(
  "POLYMARKET_MAKER_copytrade_v3/copytrade/tokens_from_copytrade.json"
  "POLYMARKET_MAKER_copytrade_v3/copytrade/copytrade_state.json"
  "POLYMARKET_MAKER_copytrade_v3/copytrade/copytrade_sell_signals.json"
  "POLYMARKET_MAKER_copytrade_v3/copytrade/copytrade_blacklist.json"
  "POLYMARKET_MAKER_copytrade_v3/copytrade/*.log"
  "POLYMARKET_MAKER_copytrade_v3/POLYMARKET_MAKER_AUTO/*.log"
  "POLYMARKET_MAKER_copytrade_v3/POLYMARKET_MAKER_AUTO/data/"
)

print_recent_logs() {
  local service="$1"
  echo "[INFO] Recent logs: $service"
  journalctl -u "$service" -n 60 --no-pager || true
}

check_service_active() {
  local service="$1"
  if systemctl is-active --quiet "$service"; then
    echo "[OK] Service active: $service"
    return 0
  fi

  echo "[ERROR] Service is not active: $service" >&2
  systemctl --no-pager --full status "$service" || true
  print_recent_logs "$service"
  return 1
}

require_cmd() {
  local cmd="$1"
  if command -v "$cmd" >/dev/null 2>&1; then
    return 0
  fi
  echo "[ERROR] Missing command: $cmd" >&2
  exit 1
}

has_cmd() {
  local cmd="$1"
  command -v "$cmd" >/dev/null 2>&1
}

backup_runtime_state() {
  local app_root="$1"
  local backup_root="$2"

  mkdir -p "$backup_root"
  for rel_path in "${RUNTIME_EXCLUDES[@]}"; do
    local abs_path="$app_root/$rel_path"
    if compgen -G "$abs_path" >/dev/null 2>&1; then
      while IFS= read -r matched; do
        [[ -e "$matched" ]] || continue
        local rel_matched="${matched#"$app_root"/}"
        local target_dir
        target_dir="$(dirname "$backup_root/$rel_matched")"
        mkdir -p "$target_dir"
        cp -a "$matched" "$target_dir/"
      done < <(compgen -G "$abs_path")
    fi
  done
}

restore_runtime_state() {
  local backup_root="$1"
  local app_root="$2"

  if [[ -d "$backup_root" ]]; then
    cp -a "$backup_root"/. "$app_root"/
  fi
}

sync_with_tar_fallback() {
  local src_dir="$1"
  local app_root="$2"
  local runtime_backup=""

  if [[ "$KEEP_RUNTIME_STATE" == "1" ]]; then
    runtime_backup="$TMP_DIR/runtime_backup"
    backup_runtime_state "$app_root" "$runtime_backup"
  fi

  find "$app_root" -mindepth 1 -maxdepth 1 \
    ! -name '.git' \
    ! -name 'case' \
    -exec rm -rf {} +

  cp -a "$src_dir"/. "$app_root"/

  if [[ "$KEEP_RUNTIME_STATE" == "1" ]]; then
    restore_runtime_state "$runtime_backup" "$app_root"
  fi
}

echo "[INFO] APP_ROOT=$APP_ROOT"
echo "[INFO] ARCHIVE_URL=$ARCHIVE_URL"
if [[ "$KEEP_RUNTIME_STATE" == "1" ]]; then
  echo "[INFO] UPDATE_MODE=preserve_runtime_state"
else
  echo "[INFO] UPDATE_MODE=reset_runtime_state"
fi

require_cmd curl
require_cmd tar
require_cmd systemctl

if [[ ! -d "$APP_ROOT" ]]; then
  echo "[ERROR] Install directory does not exist: $APP_ROOT" >&2
  exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

ARCHIVE_PATH="$TMP_DIR/app.tar.gz"
EXTRACT_DIR="$TMP_DIR/extract"

echo "[INFO] Downloading latest archive..."
curl -L --fail "$ARCHIVE_URL" -o "$ARCHIVE_PATH"

mkdir -p "$EXTRACT_DIR"
echo "[INFO] Extracting archive..."
tar -xzf "$ARCHIVE_PATH" -C "$EXTRACT_DIR"

SRC_DIR="$(find "$EXTRACT_DIR" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
if [[ -z "${SRC_DIR:-}" || ! -d "$SRC_DIR" ]]; then
  echo "[ERROR] Failed to find extracted source directory" >&2
  exit 1
fi

echo "[INFO] Syncing files into install directory..."
if has_cmd rsync; then
  RSYNC_ARGS=(
    -a
    --delete
    --exclude ".git/"
    --exclude "case/"
  )

  if [[ "$KEEP_RUNTIME_STATE" == "1" ]]; then
    for pattern in "${RUNTIME_EXCLUDES[@]}"; do
      RSYNC_ARGS+=(--exclude "$pattern")
    done
  fi

  rsync "${RSYNC_ARGS[@]}" \
    "$SRC_DIR"/ "$APP_ROOT"/
else
  echo "[WARN] rsync not found, using fallback sync mode"
  sync_with_tar_fallback "$SRC_DIR" "$APP_ROOT"
fi

echo "[INFO] Reinstalling and refreshing systemd services..."
sudo bash "$APP_ROOT/POLYMARKET_MAKER_copytrade_v3/systemd/install_services.sh" \
  "$APP_ROOT" \
  "$RUN_USER" \
  "$PYTHON_BIN" \
  "$ENV_FILE"

echo "[INFO] Restarting target services..."
sudo systemctl restart "${SERVICES[@]}"

echo "[INFO] Waiting for services to stabilize..."
sleep 5

for service in "${SERVICES[@]}"; do
  check_service_active "$service"
done

echo "[INFO] Current service status:"
for service in "${SERVICES[@]}"; do
  systemctl --no-pager --full status "$service" || true
done

echo "[OK] Update and restart completed"
