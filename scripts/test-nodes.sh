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
    
    # Let jq construct the JSON object and handle types/comparisons
    jq -n \
       --arg tag "$TAG" \
       --arg port "$PORT" \
       --arg speed "$SPEED" \
       '{tag: $tag, port: ($port | tonumber), speed: ($speed | tonumber), healthy: (($speed | tonumber) > 0)}'
    exit 0
fi

# Main flow
if [ ! -f "$CONFIG_FILE" ]; then
    echo "[]"
    exit 0
fi

# Extract port and base64-encoded tags
# Format: <port> <base64_tag>
PORTS_TAGS=$(jq -r '.inbounds[] | select(.tag != null and (.tag | startswith("test-inbound-"))) as $in | .routing.rules[] | select(.inboundTag != null and (.inboundTag[] == $in.tag)) | "\($in.port) \(.outboundTag | @base64)"' "$CONFIG_FILE" 2>/dev/null || true)

if [ -z "$PORTS_TAGS" ]; then
    echo "[]"
    exit 0
fi

# Run tests in parallel using xargs -P
# We will use 15 parallel workers.
RESULTS=$(echo "$PORTS_TAGS" | xargs -P 15 -n 2 "$0" --single 2>/dev/null || true)

# Format the results into a single JSON array
if [ -z "$RESULTS" ]; then
    echo "[]"
else
    # Combine JSON lines into a JSON array using jq
    echo "$RESULTS" | jq -s .
fi
