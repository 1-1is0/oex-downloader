#!/bin/sh
# One-time setup: ensure xray-config.json and qBittorrent.conf exist, then write .env.
set -eu

ROOT_DIR=$(cd "$(dirname "$0")" && pwd)

XRAY_TPL="$ROOT_DIR/xray/xray-config.json.example"
XRAY_CFG="$ROOT_DIR/xray/xray-config.json"
QBT_TPL="$ROOT_DIR/qbittorrent/qbittorrent.conf"
QBT_DST_DIR="$ROOT_DIR/data/qbittorrent/config/qBittorrent"
QBT_DST="$QBT_DST_DIR/qBittorrent.conf"

if [ ! -f "$XRAY_CFG" ]; then
  cp "$XRAY_TPL" "$XRAY_CFG"
  echo "Created $XRAY_CFG from template — EDIT IT to fill PLACEHOLDER values before starting the stack."
fi

mkdir -p "$QBT_DST_DIR"
if [ ! -f "$QBT_DST" ]; then
  cp "$QBT_TPL" "$QBT_DST"
  echo "Seeded $QBT_DST"
fi

mkdir -p "$ROOT_DIR/data/xray" "$ROOT_DIR/data/downloads"

sh "$ROOT_DIR/generate-env.sh"
echo "Bootstrap complete. Next: docker compose up -d"
