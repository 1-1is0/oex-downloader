#!/bin/sh
# Verification checklist for the torrent stack (spec §9, automated subset).
# Skips step 8 (ipleak.net magnet test) — that's manual.
set -u

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
cd "$ROOT_DIR"

PASS=0
FAIL=0

ok()   { echo "PASS  $1"; PASS=$((PASS+1)); }
fail() { echo "FAIL  $1"; FAIL=$((FAIL+1)); }

VPS_IP=""
if [ -f .env ]; then
  VPS_IP=$(grep '^VPS_PUBLIC_IP=' .env | cut -d= -f2-)
fi

echo "--- 1. docker compose ps ---"
docker compose ps
for svc in xray tun2socks qbittorrent; do
  state=$(docker inspect --format='{{.State.Health.Status}}' "$(docker compose ps -q "$svc" 2>/dev/null)" 2>/dev/null || echo "missing")
  case "$state" in
    healthy) ok "$svc healthy" ;;
    *)       fail "$svc state=$state" ;;
  esac
done

echo
echo "--- 2/3. recent logs (xray, tun2socks) ---"
docker compose logs --tail=10 xray 2>&1 | sed 's/^/  xray: /'
docker compose logs --tail=10 tun2socks 2>&1 | sed 's/^/  tun2socks: /'

echo
echo "--- 4. egress IP from tun2socks ---"
EGRESS=$(docker compose exec -T tun2socks wget -qO- --timeout=10 https://api.ipify.org 2>/dev/null || true)
if [ -z "$EGRESS" ]; then
  fail "tun2socks egress fetch failed"
elif [ -n "$VPS_IP" ] && [ "$EGRESS" = "$VPS_IP" ]; then
  fail "tun2socks egress = VPS_PUBLIC_IP ($EGRESS) — LEAK"
else
  ok "tun2socks egress = $EGRESS"
fi

echo
echo "--- 5. egress IP from qbittorrent (shared netns) ---"
QEGRESS=$(docker compose exec -T qbittorrent wget -qO- --timeout=10 https://api.ipify.org 2>/dev/null || true)
if [ -z "$QEGRESS" ]; then
  fail "qbittorrent egress fetch failed"
elif [ -n "$VPS_IP" ] && [ "$QEGRESS" = "$VPS_IP" ]; then
  fail "qbittorrent egress = VPS_PUBLIC_IP ($QEGRESS) — LEAK"
else
  ok "qbittorrent egress = $QEGRESS"
fi

echo
echo "--- 7. UDP DNS via TUN ---"
if docker compose exec -T tun2socks nslookup example.com 1.1.1.1 >/dev/null 2>&1; then
  ok "UDP DNS resolves through TUN"
else
  fail "UDP DNS through TUN failed (xray inbound udp:true?)"
fi

echo
echo "--- 8. DNS-over-HTTPS configured in xray ---"
XRAY_CFG=""
for c in config/xray-config.json config/xray-config.socks5-outbound.json; do
  [ -f "$c" ] && XRAY_CFG="$c" && break
done
if [ -z "$XRAY_CFG" ]; then
  fail "no xray config file found to inspect"
else
  if grep -Eq '"https(\+local)?://[^"]+/dns-query"' "$XRAY_CFG"; then
    ok "xray dns.servers uses https:// (DoH) in $XRAY_CFG"
  else
    fail "xray dns.servers in $XRAY_CFG is NOT DoH — expected entries like \"https://1.1.1.1/dns-query\""
  fi
  if grep -Eq '"servers"\s*:\s*\[[^]]*"[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+"' "$XRAY_CFG"; then
    fail "xray dns.servers still contains a bare IP (plain UDP DNS) in $XRAY_CFG"
  fi
fi

echo
echo "--- 8b. DoH endpoint reachable through the proxy chain ---"
DOH_BODY=$(docker compose exec -T tun2socks wget -qO- --timeout=10 \
  --header='accept: application/dns-json' \
  'https://1.1.1.1/dns-query?name=example.com&type=A' 2>/dev/null || true)
if echo "$DOH_BODY" | grep -q '"Status":0'; then
  ok "DoH GET to 1.1.1.1 returned a valid answer through tun2socks→xray"
else
  fail "DoH GET to 1.1.1.1 failed (body: $(echo "$DOH_BODY" | head -c 120))"
fi

echo
echo "--- 8c. xray logs show DoH activity, no plaintext :53 errors ---"
DNS_LOG=$(docker compose logs --tail=200 xray 2>&1 || true)
if echo "$DNS_LOG" | grep -Eiq 'failed to (open|dial) connection.* :53'; then
  fail "xray logs show plaintext :53 failures — check dns config"
else
  ok "no plaintext :53 errors in last 200 xray log lines"
fi

echo
echo "--- 9. kill switch (skipped: would stop xray) ---"
echo "  to test manually: docker compose stop xray && docker compose exec tun2socks wget --timeout=10 -qO- https://api.ipify.org"
echo "  expect: failure / timeout. Then: docker compose start xray"

echo
echo "Summary: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]


# Verify after docker compose restart xray:

# docker compose exec tun2socks nslookup example.com 1.1.1.1
# # should still resolve — xray intercepts, does DoH on its own, returns answer
# docker compose logs xray | grep -i dns
# # should show https outbound activity, no plaintext :53 traffic
