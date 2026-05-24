#!/bin/sh
# Read config/stack-settings.json and write .env at repo root.
set -eu

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
CFG="$ROOT_DIR/config/stack-settings.json"
ENV_FILE="$ROOT_DIR/.env"

if [ ! -f "$CFG" ]; then
  echo "error: $CFG not found" >&2
  exit 1
fi

if command -v jq >/dev/null 2>&1; then
  get() { jq -r "$1 // \"\"" "$CFG"; }
elif command -v python3 >/dev/null 2>&1; then
  get() {
    python3 -c "
import json, sys
d = json.load(open('$CFG'))
key = '$1'.lstrip('.')
v = d.get(key, '')
print('' if v is None else v)
"
  }
else
  echo "error: need jq or python3 to parse JSON" >&2
  exit 1
fi

WEBUI_PORT=$(get '.webui_port')
PUID=$(get '.puid')
PGID=$(get '.pgid')
TUN_NAME=$(get '.tun_name')
HOST=$(get '.socks5_host')
PORT=$(get '.socks5_port')
SUSER=$(get '.socks5_user')
SPASS=$(get '.socks5_pass')
DL=$(get '.downloads_path')
VPS_IP=$(get '.vps_public_ip')
LOG_SIZE=$(get '.log_max_size')
LOG_FILE=$(get '.log_max_file')

if [ -n "$SUSER" ] && [ -n "$SPASS" ]; then
  URL="socks5://${SUSER}:${SPASS}@${HOST}:${PORT}"
else
  URL="socks5://${HOST}:${PORT}"
fi

[ -z "$DL" ] && DL="./data/downloads"

cat > "$ENV_FILE" <<EOF
WEBUI_PORT=${WEBUI_PORT}
PUID=${PUID}
PGID=${PGID}
TUN_NAME=${TUN_NAME}
SOCKS5_PROXY_URL=${URL}
SOCKS5_USER=${SUSER}
SOCKS5_PASS=${SPASS}
DOWNLOADS_PATH=${DL}
VPS_PUBLIC_IP=${VPS_IP}
LOG_MAX_SIZE=${LOG_SIZE}
LOG_MAX_FILE=${LOG_FILE}
EOF

echo "Wrote $ENV_FILE"
if [ -z "$VPS_IP" ]; then
  echo "warning: vps_public_ip is empty in stack-settings.json — proxy healthcheck will not detect leaks." >&2
fi
