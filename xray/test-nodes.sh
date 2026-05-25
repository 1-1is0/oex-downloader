#!/bin/bash
set -eo pipefail

CONFIG_FILE="/etc/xray/config.json"

if [ "$1" = "--single" ]; then
    PORT=$2
    BASE64_TAG=$3
    TAG=$(echo "$BASE64_TAG" | base64 -d)
    TEST_URL="http://speedtest.tele2.net/1MB.zip"
    
    # Perform curl speed test through proxy
    SPEED=$(curl -x "socks5h://127.0.0.1:${PORT}" \
                 -s -w "%{speed_download}" -o /dev/null \
                 --connect-timeout 5 \
                 --max-time 10 \
                 "$TEST_URL" || echo "0")
                 
    # Normalize speed if empty
    if [ -z "$SPEED" ]; then
        SPEED="0"
    fi
    
    # Write JSON object to a temporary file unique to this port
    jq -n \
       --arg tag "$TAG" \
       --arg port "$PORT" \
       --arg speed "$SPEED" \
       '{tag: $tag, port: ($port | tonumber), speed: ($speed | tonumber), healthy: (($speed | tonumber) > 0)}' > "/tmp/test-node-${PORT}.json"
    exit 0
fi

# Main flow
if [ ! -f "$CONFIG_FILE" ]; then
    echo "[]"
    exit 0
fi

# Clean up any old temporary test files
rm -f /tmp/test-node-*.json

# Extract port and base64-encoded tags
# Format: <port> <base64_tag>
PORTS_TAGS=$(jq -r '. as $root | .inbounds[] | select(.tag != null and (.tag | startswith("test-inbound-"))) as $in | $root.routing.rules[] | select(.inboundTag != null) | select(.inboundTag[] == $in.tag) | "\($in.port) \(.outboundTag | @base64)"' "$CONFIG_FILE" 2>/dev/null || true)

if [ -z "$PORTS_TAGS" ]; then
    echo "[]"
    exit 0
fi

# Run tests in parallel using xargs -P
# Workers will write their outputs directly to /tmp/test-node-*.json files.
echo "$PORTS_TAGS" | xargs -P 15 -n 2 "$0" --single >/dev/null 2>&1 || true

# Consolidate results from all temporary files
RESULTS=$(cat /tmp/test-node-*.json 2>/dev/null || true)

if [ -z "$RESULTS" ]; then
    echo "[]"
else
    # Combine JSON lines into a JSON array using jq
    echo "$RESULTS" | jq -s .
fi

# Clean up temporary test files
rm -f /tmp/test-node-*.json
