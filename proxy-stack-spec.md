# Implementation Specification — Dockerized Torrent Stack with xray-core SOCKS5h Transparent Proxy

**Document type:** Implementation specification for an autonomous agent  
**Scope:** Full Docker Compose stack — xray-core, tun2socks, qbittorrent-nox — with all-container traffic routed through xray's local SOCKS5h listener. No VPN. No changes to host routing.

---

## 1. Overview & Goals

Build a self-contained Docker Compose stack that:

1. Runs **xray-core** as a container, loading its config from a mounted JSON file on the host (`xray-config.json`). xray creates an inbound SOCKS5h listener (with UDP enabled) and an outbound VLESS/Reality (or any protocol) tunnel to a remote proxy server.
2. Runs **tun2socks** as a network sidecar. It connects to xray's SOCKS5h listener and creates a TUN device that tunnels all TCP and UDP traffic from within the shared network namespace.
3. Runs **qbittorrent-nox** with its network namespace set to the tun2socks container (`network_mode: service:tun2socks`). All of qBittorrent's traffic — peers, trackers, DHT, WebUI responses — leaves through the TUN interface and into xray.
4. Exposes the **qBittorrent WebUI** port only on `127.0.0.1` of the Docker host (never on the public NIC), so the operator accesses it via SSH local port-forward.
5. Survives container restarts in the correct dependency order. If tun2socks or xray goes down, qBittorrent has no network path — this is the kill switch.
6. Is fully configurable from the host via two JSON files: one for xray, one for the application stack settings.

---

## 2. Repository / Directory Layout

The agent must produce exactly this layout. Nothing else goes in the root:

```
torrent-stack/
├── docker-compose.yml          # the only compose file
├── config/
│   ├── xray-config.json        # xray-core full config (user-edited)
│   └── stack-settings.json     # port numbers, UIDs, volume paths, proxy address
├── data/
│   ├── xray/                   # xray state (auto-created by container, empty initially)
│   ├── qbittorrent/            # qBittorrent config persistence
│   │   └── config/             # mounted as /config inside qbt container
│   └── downloads/              # torrent download destination
├── scripts/
│   └── healthcheck-proxy.sh    # verifies egress IP != VPS IP (used in compose healthcheck)
└── README.md                   # operator instructions (see §10)
```

All paths in `docker-compose.yml` must be relative (using `./`). The stack must work when the entire `torrent-stack/` directory is cloned to any location on the VPS.

---

## 3. Configuration Files

### 3.1 `config/stack-settings.json`

This file is the single source of truth for the compose stack's runtime parameters. The agent must read it and template `docker-compose.yml` accordingly, OR document clearly that the operator must propagate values manually. Prefer a `docker-compose.yml` that uses **environment variable substitution** with a companion `.env` file generated from `stack-settings.json` by a small bootstrap script (`scripts/generate-env.sh`).

```jsonc
{
  // Host port for qBittorrent WebUI. Bind to 127.0.0.1 only.
  "webui_port": 8080,

  // PUID/PGID for the qbittorrent-nox process inside the container.
  // Set these to the UID/GID of the user on the VPS who owns ./data/
  "puid": 1000,
  "pgid": 1000,

  // Address of xray's SOCKS5h inbound reachable from sibling containers.
  // xray runs in its own container on a shared Docker bridge network.
  // Use the service name "xray" and the port defined in xray-config.json inbound.
  "socks5_host": "xray",
  "socks5_port": 1080,

  // tun2socks TUN interface name inside the sidecar container.
  "tun_name": "tun0",

  // Optional: if xray's socks inbound has auth enabled, set here.
  // Leave empty strings if noauth.
  "socks5_user": "",
  "socks5_pass": "",

  // Absolute path on the VPS host for download storage.
  // Defaults to ./data/downloads relative to docker-compose.yml if empty.
  "downloads_path": "",

  // Docker log options
  "log_max_size": "10m",
  "log_max_file": "3"
}
```

### 3.2 `config/xray-config.json`

Full xray-core config JSON. The agent provides a working template with placeholders. The operator replaces placeholders before running the stack.

```jsonc
{
  "log": {
    "loglevel": "warning",
    // xray writes logs to stdout/stderr; Docker captures them.
    "access": "",
    "error": ""
  },

  "inbounds": [
    {
      "tag": "socks-in",
      "protocol": "socks",
      // Listen on all interfaces within the container's network namespace
      // so tun2socks (in a sibling container on the same bridge) can reach it.
      "listen": "0.0.0.0",
      "port": 1080,
      "settings": {
        "auth": "noauth",    // change to "password" if socks5_user/pass set
        "udp": true,         // REQUIRED: enables UDP_ASSOCIATE for DHT/µTP
        "ip": "0.0.0.0"     // the UDP reply source address inside xray container
      },
      "sniffing": {
        "enabled": true,
        "destOverride": ["http", "tls", "quic"],
        "routeOnly": false
      }
    }
  ],

  "outbounds": [
    {
      "tag": "vless-out",
      "protocol": "vless",
      "settings": {
        "vnext": [
          {
            // PLACEHOLDER: replace with your VLESS server address
            "address": "YOUR_VLESS_SERVER_IP_OR_DOMAIN",
            "port": 443,
            "users": [
              {
                // PLACEHOLDER: replace with your UUID
                "id": "YOUR_UUID_HERE",
                "encryption": "none",
                // PLACEHOLDER: flow for XTLS-Reality; remove if not using Reality
                "flow": "xtls-rprx-vision"
              }
            ]
          }
        ]
      },
      "streamSettings": {
        "network": "tcp",
        "security": "reality",
        "realitySettings": {
          "serverName": "YOUR_SNI_DOMAIN",   // PLACEHOLDER e.g. "www.microsoft.com"
          "fingerprint": "chrome",
          // PLACEHOLDER: your Reality public key
          "publicKey": "YOUR_REALITY_PUBLIC_KEY",
          // PLACEHOLDER: your Reality short ID
          "shortId": "YOUR_SHORT_ID",
          "spiderX": ""
        }
      },
      "mux": {
        "enabled": false    // keep off for XTLS-Vision; enable for non-XTLS flows
      }
    },
    {
      // Fallback: direct for local/LAN traffic (Docker internal DNS etc.)
      "tag": "direct",
      "protocol": "freedom",
      "settings": {}
    },
    {
      "tag": "block",
      "protocol": "blackhole",
      "settings": {}
    }
  ],

  "routing": {
    "domainStrategy": "IPIfNonMatch",
    "rules": [
      {
        // Keep Docker-internal DNS and RFC1918 traffic direct
        "type": "field",
        "ip": ["geoip:private"],
        "outboundTag": "direct"
      },
      {
        // Everything else through VLESS
        "type": "field",
        "network": "tcp,udp",
        "outboundTag": "vless-out"
      }
    ]
  },

  "dns": {
    // xray resolves hostnames via the VLESS outbound's remote DNS.
    // Use a reputable upstream.
    "servers": ["1.1.1.1", "8.8.8.8"],
    "queryStrategy": "UseIPv4"
  },

  "policy": {
    "levels": {
      "0": { "handshakeMSeconds": 4000, "connIdle": 300 }
    },
    "system": { "statsInboundUplink": false, "statsInboundDownlink": false }
  }
}
```

**Agent note:** do not hardcode VLESS settings. The file above is a template; the agent should document all `PLACEHOLDER` fields in the README so the operator knows exactly what to fill in.

---

## 4. `docker-compose.yml` — Full Specification

### 4.1 Services

#### Service: `xray`

| Field | Value |
|---|---|
| Image | `ghcr.io/xtls/xray-core:latest` |
| Restart | `unless-stopped` |
| Networks | `proxy-net` (the shared bridge, see §4.2) |
| Volumes | `./config/xray-config.json:/etc/xray/config.json:ro` |
| Command | `["xray", "run", "-c", "/etc/xray/config.json"]` |
| Healthcheck | `CMD wget -qO- --proxy=socks5h://127.0.0.1:1080 https://api.ipify.org \|\| exit 1`; interval 30s, timeout 10s, retries 3, start_period 15s |
| Logging | json-file driver; max-size from settings; max-file from settings |
| Cap/Privileges | None needed |
| Read-only FS | Recommended: `read_only: true` with a tmpfs on `/tmp` |

> The xray container does NOT need `NET_ADMIN`; it is a SOCKS5 server, not a TUN manipulator.

#### Service: `tun2socks`

| Field | Value |
|---|---|
| Image | `ghcr.io/xjasonlyu/tun2socks:latest` |
| Restart | `unless-stopped` |
| Depends on | `xray` with condition `service_healthy` |
| Networks | `proxy-net` |
| Cap add | `NET_ADMIN` |
| Devices | `/dev/net/tun:/dev/net/tun` |
| Sysctls | `net.ipv6.conf.all.disable_ipv6=1`, `net.ipv6.conf.default.disable_ipv6=1` |
| Ports | `127.0.0.1:${WEBUI_PORT}:8080` (WebUI forwarded here because qbt shares netns) |
| Environment | See below |
| Healthcheck | `CMD wget -qO- https://api.ipify.org \|\| exit 1`; interval 30s, timeout 15s, retries 5, start_period 20s |
| Logging | json-file; same limits as xray |

tun2socks environment variables:

```yaml
environment:
  LOGLEVEL: "warning"
  TUN: "${TUN_NAME}"              # from stack-settings.json, e.g. tun0
  # Build the proxy URL from settings; if no auth: socks5://xray:1080
  # If auth: socks5://user:pass@xray:1080
  PROXY: "${SOCKS5_PROXY_URL}"   # agent computes this in generate-env.sh
  # After tun0 is up, add a default route through it and keep Docker's
  # DNS (169.254.x.x or 127.0.0.11) reachable via the bridge.
  EXTRA_COMMANDS: >-
    ip route del default &&
    ip route add default dev ${TUN_NAME} &&
    ip route add 172.16.0.0/12 via $(ip route | awk '/^default/ {print $3; exit}') &&
    ip route add 10.0.0.0/8    via $(ip route | awk '/^default/ {print $3; exit}') &&
    ip route add 192.168.0.0/16 via $(ip route | awk '/^default/ {print $3; exit}')
```

> **Why route RFC1918 back via bridge?** Docker's internal DNS resolver (`127.0.0.11`) and inter-container traffic live in RFC1918 space. Routing them back through the bridge gateway prevents tun2socks from trying to tunnel Docker's own control traffic through SOCKS5, which would break DNS resolution inside the container. The xray container already handles the rest via its routing rules.

> **`EXTRA_COMMANDS` implementation detail:** tun2socks supports an `EXTRA_COMMANDS` env var that is executed as a shell command after the TUN device is up. The inline `$(ip route …)` subshell runs at container start. The agent must ensure the command is passed as a single string; YAML block scalars (`>-`) are recommended.

#### Service: `qbittorrent`

| Field | Value |
|---|---|
| Image | `qbittorrentofficial/qbittorrent-nox:latest` |
| Restart | `unless-stopped` |
| Depends on | `tun2socks` with condition `service_healthy` |
| `network_mode` | `service:tun2socks` ← **critical; do NOT add a `networks:` key here** |
| Volumes | `./data/qbittorrent/config:/config`, `${DOWNLOADS_PATH}:/downloads` |
| Environment | `QBT_LEGAL_NOTICE=confirm`, `QBT_WEBUI_PORT=8080`, `PUID=${PUID}`, `PGID=${PGID}` |
| Ports | **None.** Ports are published on the `tun2socks` service. |
| Logging | json-file; same limits |

> Docker forbids `ports:` on a service that uses `network_mode: service:`. The WebUI port published on `tun2socks` (`127.0.0.1:${WEBUI_PORT}:8080`) is what reaches the operator. This is correct behavior.

### 4.2 Networks

```yaml
networks:
  proxy-net:
    driver: bridge
    # Optional: pin the subnet to avoid collisions with VPS LAN
    ipam:
      config:
        - subnet: 172.30.0.0/24
```

Only `xray` and `tun2socks` are on `proxy-net`. `qbittorrent` is not on any named network — it uses tun2socks's netns entirely.

### 4.3 Volumes

Use **bind mounts** only (no named Docker volumes). This makes backup and inspection trivial on a VPS:

```yaml
# Under each service — shown as pseudo-YAML for clarity
xray:
  volumes:
    - ./config/xray-config.json:/etc/xray/config.json:ro
    - ./data/xray:/var/run/xray           # state dir; usually empty

tun2socks:
  volumes: []                              # no persistent state needed

qbittorrent:
  volumes:
    - ./data/qbittorrent/config:/config
    - ./data/downloads:/downloads          # or ${DOWNLOADS_PATH}:/downloads
```

### 4.4 `.env` file (generated by `scripts/generate-env.sh`)

`docker-compose.yml` references these variables via `${VAR}` substitution. The script reads `config/stack-settings.json` and writes `.env`:

| Variable | Source field | Example |
|---|---|---|
| `WEBUI_PORT` | `webui_port` | `8080` |
| `PUID` | `puid` | `1000` |
| `PGID` | `pgid` | `1000` |
| `TUN_NAME` | `tun_name` | `tun0` |
| `SOCKS5_PROXY_URL` | computed from host/port/user/pass | `socks5://xray:1080` or `socks5://user:pass@xray:1080` |
| `DOWNLOADS_PATH` | `downloads_path` (fallback `./data/downloads`) | `/mnt/data/torrents` |
| `LOG_MAX_SIZE` | `log_max_size` | `10m` |
| `LOG_MAX_FILE` | `log_max_file` | `3` |

`generate-env.sh` must be POSIX sh (no bash-isms) and use `python3 -c` or `jq` to parse JSON:

```sh
#!/bin/sh
# scripts/generate-env.sh
set -e
CFG="$(dirname "$0")/../config/stack-settings.json"
if command -v jq >/dev/null 2>&1; then
  get() { jq -r "$1" "$CFG"; }
else
  get() { python3 -c "import sys,json; d=json.load(open('$CFG')); print(d$1)"; }
  # simplified; agent should implement properly
fi
WEBUI_PORT=$(get '.webui_port')
PUID=$(get '.puid')
PGID=$(get '.pgid')
TUN_NAME=$(get '.tun_name')
HOST=$(get '.socks5_host')
PORT=$(get '.socks5_port')
USER=$(get '.socks5_user')
PASS=$(get '.socks5_pass')
if [ -n "$USER" ] && [ -n "$PASS" ]; then
  URL="socks5://${USER}:${PASS}@${HOST}:${PORT}"
else
  URL="socks5://${HOST}:${PORT}"
fi
DL=$(get '.downloads_path')
[ -z "$DL" ] && DL="./data/downloads"
LOG_SIZE=$(get '.log_max_size')
LOG_FILE=$(get '.log_max_file')
cat > "$(dirname "$0")/../.env" <<EOF
WEBUI_PORT=${WEBUI_PORT}
PUID=${PUID}
PGID=${PGID}
TUN_NAME=${TUN_NAME}
SOCKS5_PROXY_URL=${URL}
DOWNLOADS_PATH=${DL}
LOG_MAX_SIZE=${LOG_SIZE}
LOG_MAX_FILE=${LOG_FILE}
EOF
echo ".env written."
```

---

## 5. qBittorrent Initial Configuration

qBittorrent persists its config in `./data/qbittorrent/config/qBittorrent/qBittorrent.conf`. The agent must provide a **pre-seeded `qBittorrent.conf`** placed at `./data/qbittorrent/config/qBittorrent/qBittorrent.conf` so the operator doesn't have to configure via WebUI for the security-critical settings.

```ini
[BitTorrent]
Session\AnonymousModeEnabled=true
Session\DHTEnabled=true           ; true because tun2socks handles UDP end-to-end
Session\PeXEnabled=false          ; PeX leaks peer IPs; disable for privacy
Session\LSDEnabled=false          ; LSD is LAN-only, useless and noisy on a VPS
Session\DefaultSavePath=/downloads
Session\Interface=tun0            ; bind to the TUN interface only — critical
Session\InterfaceName=tun0
Session\MaxConnections=200
Session\MaxConnectionsPerTorrent=50
Session\BTProtocol=TCP            ; optionally add uTP once you verify µTP works

[Network]
; No proxy settings here — tun2socks handles all routing transparently.
; Setting a proxy inside qBittorrent on top of tun2socks would double-proxy
; and likely break UDP. Leave all Proxy/* keys absent or at defaults.
Proxy\Type=0

[Preferences]
WebUI\Port=8080
WebUI\LocalHostAuth=true
WebUI\AuthSubnetWhitelistEnabled=false
WebUI\HostHeaderValidation=false  ; set true if you know your access hostname
WebUI\HTTPS\Enabled=false
Downloads\SavePath=/downloads
Downloads\TempPath=/downloads/incomplete
Downloads\TempPathEnabled=true
General\Locale=en
```

> **`Session\Interface=tun0`** is the most important setting here. It tells libtorrent to bind the listening socket and all outbound peer connections to `tun0`. Without it, libtorrent will try to bind to `eth0` (the Docker bridge NIC) as well and may send data unproxied if tun2socks has a hiccup. Setting this means libtorrent will fail loudly rather than leak.

> **No proxy settings inside qBittorrent.** Because tun2socks already intercepts all traffic at the kernel level (via TUN), adding a SOCKS5 proxy config inside qBittorrent would create a double-proxy path and break UDP. The agent must not add `Proxy\Type=SOCKS5` in the config.

---

## 6. `scripts/healthcheck-proxy.sh`

Used as a compose healthcheck for the tun2socks service. Returns 0 only if the egress IP differs from the known VPS public IP.

```sh
#!/bin/sh
# Fetch current egress IP through the TUN interface.
# Fail if it equals VPS_PUBLIC_IP (passed as env var) or if fetch fails.
EGRESS=$(wget -qO- --timeout=8 https://api.ipify.org 2>/dev/null)
[ -z "$EGRESS" ] && exit 1
[ "$EGRESS" = "$VPS_PUBLIC_IP" ] && exit 1
exit 0
```

In `docker-compose.yml`, the tun2socks healthcheck uses this script:

```yaml
healthcheck:
  test: ["CMD", "sh", "/scripts/healthcheck-proxy.sh"]
  interval: 30s
  timeout: 15s
  retries: 5
  start_period: 25s
environment:
  VPS_PUBLIC_IP: "${VPS_PUBLIC_IP}"  # added to .env by generate-env.sh or set manually
```

The script is mounted read-only into tun2socks:

```yaml
volumes:
  - ./scripts/healthcheck-proxy.sh:/scripts/healthcheck-proxy.sh:ro
```

---

## 7. Startup Order & Dependency Chain

```
xray (healthy)
  └─→ tun2socks (depends_on: xray healthy → then healthy itself)
        └─→ qbittorrent (depends_on: tun2socks healthy)
```

Docker Compose enforces this via `condition: service_healthy` on each `depends_on`. If xray goes down, tun2socks loses its upstream but does not immediately die — the tun2socks healthcheck will fail after retries, which marks it unhealthy, which causes qBittorrent to have a broken network (no route through TUN) but does NOT automatically stop qBittorrent. The agent must document this clearly: qBittorrent's traffic will black-hole (no VPS IP leak, because its only route is tun0) until xray recovers.

---

## 8. Host Prerequisites & Firewall

The agent must document these requirements in the README but does not need to automate them (they are one-time VPS setup):

1. **Docker Engine ≥ 24.0** and **Docker Compose v2** (`docker compose` plugin, not `docker-compose` standalone).
2. **`/dev/net/tun` exists** on the VPS host. Most VPS providers enable TUN by default. Check with `ls /dev/net/tun`. If missing, the user's VPS control panel usually has a "TUN/TAP" toggle in networking settings.
3. **`NET_ADMIN` capability** is not blocked by the VPS provider. Most KVM/dedicated VPS allow it; some LXC-based VPS do not. Check with `docker run --rm --cap-add NET_ADMIN alpine ip link` — if it doesn't error, you're fine.
4. **Firewall rules:** Port 1080 (xray SOCKS5 inbound) must NOT be reachable from the public internet. Since xray listens inside the Docker bridge network (not on the host), this is automatically true — no iptables action needed. Confirm with `ss -tlnp | grep 1080` on the host — it should show nothing.
5. **WebUI port** (default 8080) must also not be exposed publicly. Since `docker-compose.yml` binds it to `127.0.0.1:8080`, it is automatically loopback-only. Operator accesses it via `ssh -L 8080:127.0.0.1:8080 user@vps`.

---

## 9. Verification Procedure

The agent must include these as a numbered checklist in the README, and optionally as a script `scripts/verify.sh`:

```
1. docker compose ps
   → All three containers: status "Up", health "(healthy)"

2. docker compose logs xray --tail=20
   → No error lines. Expect "Xray X.Y.Z started"

3. docker compose logs tun2socks --tail=20
   → "tun0: up", "SOCKS5 connected", no "connection refused"

4. docker exec -it tun2socks wget -qO- https://api.ipify.org
   → Prints an IP that is NOT your VPS's public IP.
   → Prints the exit IP of your VLESS server.

5. docker exec -it qbittorrent wget -qO- https://api.ipify.org
   → Same non-VPS IP as above (shares netns with tun2socks).

6. docker exec -it tun2socks sh -c 'wget -qO- --timeout=5 https://api.ipify.org'
   → Same result.

7. UDP check (requires bind-tools in image, may need apk add):
   docker exec -it tun2socks nslookup example.com 1.1.1.1
   → Resolves successfully (UDP DNS via TUN → xray → VLESS server).

8. Torrent IP leak test:
   - Go to https://ipleak.net → click "Activate" under "Torrent Address detection"
   - Copy the magnet link.
   - Add it in qBittorrent WebUI.
   - Wait ~60s. The page should show the VLESS exit IP, not the VPS IP.

9. Kill switch test:
   docker compose stop xray
   docker exec -it tun2socks wget -qO- --timeout=10 https://api.ipify.org
   → Times out / fails. No traffic leaks via VPS IP.
   docker compose start xray  # bring it back up
```

---

## 10. README Requirements

The agent must produce a `README.md` covering:

1. **Prerequisites** (§8 above, in plain language)
2. **Quick start:**
   ```sh
   git clone ...
   cd torrent-stack
   # 1. Edit config/xray-config.json — fill in all PLACEHOLDER values
   # 2. Edit config/stack-settings.json — set your UID/GID and desired ports
   sh scripts/generate-env.sh
   docker compose up -d
   docker compose ps   # wait for all (healthy)
   # 3. SSH tunnel: ssh -L 8080:127.0.0.1:8080 user@your-vps
   # 4. Open http://localhost:8080 in browser
   # 5. Run verification checklist (see Verification section)
   ```
3. **Config reference** — every field in `xray-config.json` and `stack-settings.json` explained in one sentence each.
4. **xray-config.json PLACEHOLDER guide** — a table of each `PLACEHOLDER` key, what it means, where to find it (e.g. "get from your VLESS server panel").
5. **Accessing the WebUI** — SSH tunnel command, default password note (printed in `docker logs qbittorrent` on first boot), how to change it.
6. **Updating** — `docker compose pull && docker compose up -d --remove-orphans`.
7. **Verification checklist** (§9 above, copy verbatim).
8. **Troubleshooting table:**

   | Symptom | Likely cause | Fix |
   |---|---|---|
   | `tun2socks` unhealthy | xray not reachable at `xray:1080` | Check xray logs; verify `"listen": "0.0.0.0"` in xray inbound |
   | `wget` inside container shows VPS IP | tun0 default route missing | Check `EXTRA_COMMANDS`; run `docker exec tun2socks ip route` |
   | UDP check (nslookup) fails | xray inbound has `"udp": false` | Set `"udp": true` in xray-config.json inbound settings |
   | qBittorrent shows 0 DHT nodes | `Session\Interface` not set to `tun0` | Edit `qBittorrent.conf`, set `Session\Interface=tun0` |
   | WebUI unreachable from laptop | SSH tunnel not running | Run `ssh -L 8080:127.0.0.1:8080 user@vps` in a terminal |
   | `/dev/net/tun` not found | VPS provider doesn't expose TUN | Enable TUN in VPS control panel; or switch to redsocks (TCP-only) |
   | `NET_ADMIN` denied | LXC container without capability | Contact VPS provider; or use a KVM-based VPS |

9. **Limitations** (copy from §4 of the parent report):
   - No inbound peer connections (no listening port through SOCKS5).
   - If xray's VLESS upstream drops UDP, DHT/µTP silently stops working; check `docker logs tun2socks` for `UDP ASSOCIATE` errors.
   - WebUI is loopback-only by design; do not change the bind address without also adding authentication hardening.

---

## 11. What the Agent Must NOT Do

- Do not generate a `Dockerfile` for xray or tun2socks — use the official upstream images as specified.
- Do not generate a custom `Dockerfile` for qbittorrent — use `qbittorrentofficial/qbittorrent-nox:latest` or `lscr.io/linuxserver/qbittorrent:latest`.
- Do not use named Docker volumes — only bind mounts (§4.3).
- Do not publish any port on `0.0.0.0` — only `127.0.0.1:PORT:CONTAINER_PORT`.
- Do not add `network_mode: host` to any service.
- Do not set `privileged: true` on any container — only specific capabilities (`NET_ADMIN`) where required.
- Do not add a `proxy:` block inside `qBittorrent.conf` — tun2socks handles routing transparently.
- Do not hardcode VLESS credentials anywhere in `docker-compose.yml` or `.env` — they belong only in `config/xray-config.json`.
- Do not expose port 1080 on the host — it lives only inside the Docker bridge network.

---

## 12. Exact File List to Produce

```
torrent-stack/
├── docker-compose.yml
├── .env.example              # copy of .env with placeholder values; .env is gitignored
├── .gitignore                # ignores: .env, data/, config/xray-config.json
├── config/
│   ├── xray-config.json      # full template with PLACEHOLDER markers
│   ├── stack-settings.json   # with default values filled in
│   └── qbittorrent.conf      # pre-seeded qBittorrent config (copied to data/ on first run)
├── data/
│   ├── xray/                 # empty dir (add .gitkeep)
│   ├── qbittorrent/
│   │   └── config/
│   │       └── qBittorrent/
│   │           └── qBittorrent.conf   # pre-seeded from config/qbittorrent.conf
│   └── downloads/            # empty dir (add .gitkeep)
├── scripts/
│   ├── generate-env.sh       # reads stack-settings.json → writes .env
│   ├── healthcheck-proxy.sh  # used by tun2socks healthcheck
│   └── verify.sh             # runs all 9 verification steps
└── README.md
```

Total: 11 files + 2 empty dirs with `.gitkeep`.

---

*End of specification.*
