
## Docker patterns: redirecting all container traffic through host SOCKS5h (no VPN)
You said you specifically don't want a VPN on the VPS because it would disrupt SSH. Both patterns below leave the host's network untouched — only the torrent container's outbound traffic is proxied — so SSH is never affected.
Pattern A — tun2socks sidecar (recommended; handles TCP + UDP)
xjasonlyu/tun2socks builds a TUN inside the container and forwards everything (TCP and UDP via gVisor's user-space stack) into your SOCKS5h endpoint. The torrent container shares the network namespace via network_mode: "service:tun2socks", so it has no other network path; if tun2socks is down, the torrent container is offline (effectively a built-in kill switch). Medeveltun2socks
```yaml
# docker-compose.yml
services:
  tun2socks:
    image: ghcr.io/xjasonlyu/tun2socks:latest
    container_name: tun2socks
    restart: unless-stopped
    cap_add:
      - NET_ADMIN
    devices:
      - /dev/net/tun:/dev/net/tun
    environment:
      LOGLEVEL: info
      TUN: tun0
      # host.docker.internal works on Docker 20.10+ with extra_hosts mapping below.
      # Replace with the LAN/Docker-bridge IP of your xray SOCKS5h listener if needed.
      PROXY: socks5://host.docker.internal:1080
      # If your xray inbound has username/password auth:
      # PROXY: socks5://user:pass@host.docker.internal:1080
      EXTRA_COMMANDS: "ip rule add iif lo ipproto udp dport 53 lookup main"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    sysctls:
      - net.ipv6.conf.default.disable_ipv6=1
      - net.ipv6.conf.all.disable_ipv6=1
    # Expose the qBittorrent WebUI port HERE because qbittorrent shares this netns
    ports:
      - "127.0.0.1:8080:8080"

  qbittorrent:
    image: qbittorrentofficial/qbittorrent-nox:latest
    container_name: qbittorrent
    depends_on:
      - tun2socks
    network_mode: "service:tun2socks"
    environment:
      QBT_LEGAL_NOTICE: confirm
      QBT_WEBUI_PORT: 8080
      PUID: "1000"
      PGID: "1000"
    volumes:
      - ./qbt/config:/config
      - ./downloads:/downloads
    restart: unless-stopped
```
Important caveats:

The torrent container shares tun2socks's netns, so its WebUI port must be published on the tun2socks service, not on the qbittorrent service (Docker rejects ports: on containers using network_mode: service:).
xray-core's SOCKS5 inbound must have "udp": true to forward UDP. If you launched the proxy as a VLESS client locally with a socks inbound, edit the inbound:

json  { "tag": "socks-in", "protocol": "socks", "listen": "0.0.0.0", "port": 1080,
    "settings": { "auth": "noauth", "udp": true } }
Listen on 0.0.0.0 (or 172.17.0.1) so containers can reach it; firewall it to localhost/Docker bridge with iptables/ufw if exposed.

If your xray SOCKS5 inbound only speaks TCP, tun2socks will log UDP ASSOCIATE: command not supported and DHT/UDP trackers will fail — same as qBittorrent's native SOCKS5 mode. GitHub
If the host already has port 1080 occupied locally, just point PROXY at the host gateway IP that you see inside the container (ip route | grep default).