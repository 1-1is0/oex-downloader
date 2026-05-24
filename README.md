# Torrent Stack — xray-core + tun2socks + qBittorrent

A self-contained Docker Compose stack that routes **all** qBittorrent traffic through xray-core's local SOCKS5h listener via a tun2socks sidecar. No host VPN, no host routing changes. SSH stays untouched.

If xray or tun2socks goes down, qBittorrent has no network path — that's the kill switch.

## Prerequisites

1. **Docker Engine ≥ 24.0** with the **Compose v2** plugin (`docker compose ...`, not the legacy `docker-compose`).
2. **`/dev/net/tun` exists** on the host: `ls /dev/net/tun`. If missing, your VPS provider has a TUN/TAP toggle in its panel.
3. **`NET_ADMIN` capability** is allowed (most KVM VPS — fine; some LXC — not). Test:
   ```sh
   docker run --rm --cap-add NET_ADMIN alpine ip link
   ```
4. xray's SOCKS5 inbound (port 1080) lives only inside the Docker bridge network — it is **not** published to the host. Confirm with `ss -tlnp | grep 1080` (should be empty).
5. The qBittorrent WebUI binds to `127.0.0.1:8080` only. Access via SSH local-forward.

## Quick start

```sh
git clone <this-repo> torrent-stack
cd torrent-stack

# 1. Edit config/stack-settings.json — at minimum set vps_public_ip and (if needed) puid/pgid
$EDITOR config/stack-settings.json

# 2. Bootstrap creates xray-config.json from .example and seeds qBittorrent.conf
bash scripts/bootstrap.sh

# 3. Edit the xray config — fill in every PLACEHOLDER (see table below)
$EDITOR config/xray-config.json

# 4. Re-run bootstrap if you changed stack-settings.json
bash scripts/bootstrap.sh

# 5. Bring up the stack
docker compose up -d
docker compose ps                # wait for all three (healthy)

# 6. From your laptop, SSH-tunnel the WebUI
ssh -L 8080:127.0.0.1:8080 user@your-vps
# then open http://localhost:8080

# 7. Verify
bash scripts/verify.sh
```

## Config reference

### `config/stack-settings.json`

| Field | Meaning |
|---|---|
| `webui_port` | Host loopback port for qBittorrent WebUI (`127.0.0.1:<port>`). |
| `puid` / `pgid` | UID/GID owning `./data/` on the host; passed into the qBittorrent container. |
| `socks5_host` | Container hostname of xray on `proxy-net` (leave as `xray`). |
| `socks5_port` | xray inbound port (must match `inbounds[0].port` in `xray-config.json`). |
| `socks5_user` / `socks5_pass` | Set if xray inbound auth is `password`; leave empty for `noauth`. |
| `tun_name` | TUN interface name created inside the tun2socks container (default `tun0`). |
| `downloads_path` | Host path for torrent downloads. Empty → `./data/downloads`. |
| `vps_public_ip` | Your VPS's public IP. Used by the leak-detection healthcheck — leaving it empty disables that check. |
| `log_max_size` / `log_max_file` | Docker json-file log rotation. |

### `config/xray-config.json` PLACEHOLDERs

| Key | What | Where to find |
|---|---|---|
| `outbounds[0].settings.vnext[0].address` | VLESS server hostname/IP | Your VLESS server panel |
| `outbounds[0].settings.vnext[0].port` | VLESS port (usually 443) | Your VLESS server panel |
| `outbounds[0].settings.vnext[0].users[0].id` | Client UUID | VLESS server panel |
| `outbounds[0].settings.vnext[0].users[0].flow` | XTLS flow (e.g. `xtls-rprx-vision`); remove if not Reality | VLESS server panel |
| `streamSettings.realitySettings.serverName` | SNI (e.g. `www.microsoft.com`) | Reality config on server |
| `streamSettings.realitySettings.publicKey` | Reality public key | Server keypair |
| `streamSettings.realitySettings.shortId` | Reality short ID | Server config |

If you're not using VLESS+Reality, replace the entire `vless-out` outbound with whatever protocol your proxy uses.

## Accessing the WebUI

The WebUI is bound to `127.0.0.1:8080` on the VPS — never publicly. Access from your laptop:

```sh
ssh -L 8080:127.0.0.1:8080 user@your-vps
# leave that running, then in a browser:
open http://localhost:8080
```

**Default password:** qBittorrent prints a temporary admin password to its log on first boot:

```sh
docker compose logs qbittorrent | grep -i 'temporary password'
```

Log into the WebUI with `admin` + that password, then immediately set a permanent password under *Tools → Options → Web UI*.

## Updating

```sh
docker compose pull
docker compose up -d --remove-orphans
```

## Verification checklist

Run `bash scripts/verify.sh` for an automated subset, or step through manually:

```
1. docker compose ps                         → all 3 (healthy)
2. docker compose logs xray --tail=20        → "Xray X.Y.Z started", no errors
3. docker compose logs tun2socks --tail=20   → "tun0: up", "SOCKS5 connected"
4. docker compose exec tun2socks wget -qO- https://api.ipify.org
                                             → VLESS exit IP, NOT VPS IP
5. docker compose exec qbittorrent wget -qO- https://api.ipify.org
                                             → same exit IP (shared netns)
6. (same as 4) sanity check
7. docker compose exec tun2socks nslookup example.com 1.1.1.1
                                             → resolves (UDP via TUN works)
8. ipleak.net torrent magnet test            → exit IP shown is VLESS, not VPS
9. Kill switch:
   docker compose stop xray
   docker compose exec tun2socks wget --timeout=10 -qO- https://api.ipify.org
                                             → fails / times out (no leak)
   docker compose start xray
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `tun2socks` unhealthy | xray not reachable at `xray:1080` | Check `docker compose logs xray`; confirm `"listen": "0.0.0.0"` in xray inbound |
| `wget` inside container shows VPS IP | tun0 default route missing | `docker compose exec tun2socks ip route` — should show `default dev tun0` |
| UDP check (nslookup) fails | xray inbound `"udp": false` | Set `"udp": true` in `config/xray-config.json` inbound settings; restart xray |
| qBittorrent shows 0 DHT nodes | `Session\Interface` not `tun0` | Edit `data/qbittorrent/config/qBittorrent/qBittorrent.conf`, set `Session\Interface=tun0`, restart |
| WebUI unreachable from laptop | SSH tunnel not running | Run `ssh -L 8080:127.0.0.1:8080 user@vps` |
| `/dev/net/tun` not found | VPS provider doesn't expose TUN | Enable TUN in VPS panel, or switch to a TCP-only proxy |
| `NET_ADMIN` denied | LXC container without capability | Move to a KVM-based VPS |
| Healthcheck reports leak | `vps_public_ip` set wrong, OR genuine leak | Re-check `vps_public_ip` in `stack-settings.json`; if correct, investigate routes |

## Limitations

- **No inbound peer connections.** SOCKS5 has no listening-port mechanism, so qBittorrent can only make outbound connections to peers. Swarm participation works (DHT, trackers, outbound peers), but seed-only / inbound-only peers won't connect to you.
- **UDP relies on xray's `UDP_ASSOCIATE`.** If the VLESS upstream drops UDP, DHT and µTP go silent. Watch `docker compose logs tun2socks` for `UDP ASSOCIATE` errors.
- **WebUI is loopback-only by design.** Don't change the bind address unless you also add real auth + TLS.
