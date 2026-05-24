#!/bin/sh
# Healthcheck for tun2socks: verify egress IP differs from VPS_PUBLIC_IP.
# VPS_PUBLIC_IP is provided as an env var by docker-compose.
EGRESS=$(wget -qO- --timeout=8 https://api.ipify.org 2>/dev/null)
[ -z "$EGRESS" ] && exit 1
[ -n "$VPS_PUBLIC_IP" ] && [ "$EGRESS" = "$VPS_PUBLIC_IP" ] && exit 1
exit 0
