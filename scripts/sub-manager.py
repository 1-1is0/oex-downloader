import os
import sys
import json
import base64
import urllib.request
import urllib.parse
import time
import subprocess
import logging
import argparse

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def decode_base64(s):
    s = s.strip()
    s += "=" * ((4 - len(s) % 4) % 4)
    try:
        return base64.b64decode(s).decode("utf-8", errors="ignore")
    except Exception:
        return ""

def fetch_sub(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req) as response:
            content = response.read().decode('utf-8', errors="ignore").strip()
    except Exception as e:
        logging.error(f"Failed to fetch sub: {e}")
        return []

    lines = []
    if "\n" not in content and "://" not in content[:50]:
        decoded = decode_base64(content)
        lines = decoded.splitlines()
    else:
        lines = content.splitlines()

    final_lines = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        
        if "://" in line:
            final_lines.append(line)
        else:
            decoded = decode_base64(line)
            if "://" in decoded:
                for sub_line in decoded.splitlines():
                    if sub_line.strip() and "://" in sub_line:
                        final_lines.append(sub_line.strip())
                        
    return final_lines

def parse_vless(uri):
    try:
        uri = uri.strip()
        if not uri.startswith("vless://"): return None
        uri = uri[8:]
        user_info, rest = uri.split("@", 1)
        host_port, query = rest.split("?", 1)
        host, port = host_port.split(":", 1)
        
        name = ""
        if "#" in query:
            query, name = query.split("#", 1)
        name = urllib.parse.unquote(name)

        q = dict(urllib.parse.parse_qsl(query))
        
        outbound = {
            "tag": name or f"vless-{host}:{port}",
            "protocol": "vless",
            "settings": {
                "vnext": [{
                    "address": host,
                    "port": int(port),
                    "users": [{"id": user_info, "encryption": "none", "flow": q.get("flow", "")}]
                }]
            },
            "streamSettings": {
                "network": q.get("type", "tcp"),
                "security": q.get("security", "none")
            }
        }
        
        if q.get("security") == "tls":
            outbound["streamSettings"]["tlsSettings"] = {
                "serverName": q.get("sni", host),
                "fingerprint": q.get("fp", "chrome")
            }
        elif q.get("security") == "reality":
            outbound["streamSettings"]["realitySettings"] = {
                "serverName": q.get("sni", host),
                "publicKey": q.get("pbk", ""),
                "shortId": q.get("sid", ""),
                "fingerprint": q.get("fp", "chrome"),
                "spiderX": q.get("spx", "")
            }
            
        if q.get("type") == "ws":
            outbound["streamSettings"]["wsSettings"] = {
                "path": q.get("path", "/"),
                "headers": {"Host": q.get("host", host)}
            }
            
        return outbound
    except Exception:
        return None

def parse_vmess(uri):
    try:
        uri = uri.strip()
        if not uri.startswith("vmess://"): return None
        js = json.loads(decode_base64(uri[8:]))
        
        outbound = {
            "tag": js.get("ps", f"vmess-{js.get('add')}:{js.get('port')}"),
            "protocol": "vmess",
            "settings": {
                "vnext": [{
                    "address": js.get("add"),
                    "port": int(js.get("port")),
                    "users": [{"id": js.get("id"), "alterId": int(js.get("aid", 0)), "security": "auto"}]
                }]
            },
            "streamSettings": {
                "network": js.get("net", "tcp"),
                "security": js.get("tls", "none")
            }
        }
        if js.get("tls") == "tls":
            outbound["streamSettings"]["tlsSettings"] = {
                "serverName": js.get("sni") or js.get("host") or js.get("add"),
                "fingerprint": js.get("fp", "chrome")
            }
            
        if js.get("net") == "ws":
            outbound["streamSettings"]["wsSettings"] = {
                "path": js.get("path", "/"),
                "headers": {"Host": js.get("host", js.get("add"))}
            }
            
        return outbound
    except Exception:
        return None

def parse_uri(uri):
    if uri.startswith("vless://"):
        return parse_vless(uri)
    elif uri.startswith("vmess://"):
        return parse_vmess(uri)
    return None

def load_base_config():
    config_path = os.path.join(PROJECT_ROOT, "config", "xray-config.json")
    example_path = os.path.join(PROJECT_ROOT, "config", "xray-config.json.example")
    base_config = None
    
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                base_config = json.load(f)
        except Exception as e:
            logging.warning(f"Failed to load config from {config_path}: {e}")
            
    if not base_config and os.path.exists(example_path):
        try:
            with open(example_path, "r") as f:
                base_config = json.load(f)
        except Exception as e:
            logging.warning(f"Failed to load example config: {e}")
            
    if not base_config:
        base_config = {
            "log": {"loglevel": "warning"},
            "inbounds": [{
                "tag": "socks-in",
                "protocol": "socks",
                "listen": "0.0.0.0",
                "port": 1080,
                "settings": {"auth": "noauth", "udp": True, "ip": "0.0.0.0"},
                "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": False}
            }],
            "dns": {
                "servers": ["https://1.1.1.1/dns-query", "https://8.8.8.8/dns-query"],
                "queryStrategy": "UseIPv4"
            },
            "policy": {
                "levels": {"0": {"handshakeMSeconds": 4000, "connIdle": 300}}
            }
        }
    return base_config

def generate_xray_config(outbounds, dest_path, base_config):
    if not base_config.get("inbounds") or len(base_config["inbounds"]) == 0:
        base_config["inbounds"] = [{
            "tag": "socks-in",
            "protocol": "socks",
            "listen": "0.0.0.0",
            "port": 1080,
            "settings": {"auth": "noauth", "udp": True, "ip": "0.0.0.0"},
            "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": False}
        }]
        
    primary_inbound = base_config["inbounds"][0]
    inbounds = [primary_inbound]
    
    final_outbounds = []
    routing_rules = [
        {"type": "field", "ip": ["geoip:private"], "outboundTag": "direct"}
    ]
    
    for idx, ob in enumerate(outbounds):
        port = 20000 + idx
        tag_name = f"node-{idx}"
        
        ob_copy = json.loads(json.dumps(ob))
        ob_copy["tag"] = tag_name
        final_outbounds.append(ob_copy)
        
        inbounds.append({
            "tag": f"test-inbound-{idx}",
            "port": port,
            "listen": "127.0.0.1",
            "protocol": "socks",
            "settings": {
                "auth": "noauth",
                "udp": True
            }
        })
        
        routing_rules.append({
            "type": "field",
            "inboundTag": [f"test-inbound-{idx}"],
            "outboundTag": tag_name
        })
        
    final_outbounds.append({"tag": "direct", "protocol": "freedom", "settings": {}})
    final_outbounds.append({"tag": "block", "protocol": "blackhole", "settings": {}})
    
    routing_rules.append({
        "type": "field",
        "network": "tcp,udp",
        "balancerTag": "best_nodes"
    })
    
    config = {
        "log": base_config.get("log", {"loglevel": "warning"}),
        "inbounds": inbounds,
        "outbounds": final_outbounds,
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "balancers": [{
                "tag": "best_nodes",
                "selector": ["node-"],
                "strategy": {"type": "leastPing"}
            }],
            "rules": routing_rules
        },
        "dns": base_config.get("dns", {
            "servers": ["https://1.1.1.1/dns-query", "https://8.8.8.8/dns-query"],
            "queryStrategy": "UseIPv4"
        }),
        "policy": base_config.get("policy", {
            "levels": {"0": {"handshakeMSeconds": 4000, "connIdle": 300}}
        }),
        "observatory": {
            "subjectSelector": ["node-"],
            "probeURL": "http://cp.cloudflare.com/generate_204",
            "probeInterval": "1m",
            "enableConcurrency": True
        }
    }
    
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "w") as f:
        json.dump(config, f, indent=2)

def run_tests_in_container(service_name):
    cmd = ["docker", "compose", "exec", "-T", service_name, "/usr/local/bin/test-nodes.sh"]
    try:
        logging.info(f"Running test-nodes.sh inside {service_name} container...")
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, cwd=PROJECT_ROOT)
        return json.loads(res.stdout)
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to run test-nodes.sh in {service_name}: status={e.returncode}, stderr={e.stderr}")
        return []
    except Exception as e:
        logging.error(f"Failed to parse test results from {service_name}: {e}")
        return []

def get_outbound_key(ob):
    try:
        if "settings" in ob and "vnext" in ob["settings"]:
            vnext = ob["settings"]["vnext"][0]
            return (ob.get("protocol"), vnext.get("address"), vnext.get("port"))
    except Exception:
        pass
    return None

def run_update_cycle(args):
    url = "https://raw.githubusercontent.com/barry-far/V2ray-config/main/Sub1.txt"
    logging.info("Starting update cycle...")
    
    # 1. Fetch from sub
    lines = fetch_sub(url)
    outbounds = []
    for line in lines:
        parsed = parse_uri(line)
        if parsed:
            outbounds.append(parsed)
            
    logging.info(f"Fetched and parsed {len(outbounds)} proxy configurations.")
    
    # Limit number of configs to test to keep setup manageable
    test_outbounds = outbounds[:150]
    logging.info(f"Testing top {len(test_outbounds)} configurations in xray-lab...")
    
    # Ensure xray-lab data dir exists
    os.makedirs(os.path.join(PROJECT_ROOT, "data", "xray-lab"), exist_ok=True)
    
    # 2. Generate lab config and restart xray-lab
    base_config = load_base_config()
    lab_config_path = os.path.join(PROJECT_ROOT, "config", "xray-lab-config.json")
    generate_xray_config(test_outbounds, lab_config_path, base_config)
    
    try:
        logging.info("Restarting xray-lab container...")
        subprocess.run(["docker", "compose", "restart", "xray-lab"], check=True, cwd=PROJECT_ROOT)
        time.sleep(3) # allow boot
    except Exception as e:
        logging.error(f"Failed to restart xray-lab container: {e}")
        return
        
    # 3. Test lab container
    lab_results = run_tests_in_container("xray-lab")
    healthy_lab_nodes = []
    for r in lab_results:
        if r.get("healthy"):
            try:
                idx = int(r["tag"].split("-")[1])
                ob = test_outbounds[idx]
                healthy_lab_nodes.append((r.get("speed", 0), ob))
            except Exception:
                pass
                
    logging.info(f"xray-lab test complete: {len(healthy_lab_nodes)}/{len(lab_results)} nodes healthy.")
    
    # 4. Check main container health
    main_config_path = os.path.join(PROJECT_ROOT, "config", "xray-config.json")
    main_exists = os.path.exists(main_config_path)
    
    healthy_main_nodes = []
    health_pct = 0.0
    
    if main_exists:
        main_results = run_tests_in_container("xray")
        # Load current main configuration
        main_config = load_base_config()
        # Find all actual node outbounds in the main config
        main_nodes = [ob for ob in main_config.get("outbounds", []) if ob.get("tag") and ob["tag"].startswith("node-")]
        
        healthy_count = 0
        total_count = len(main_nodes)
        
        for r in main_results:
            if r.get("healthy"):
                healthy_count += 1
                try:
                    idx = int(r["tag"].split("-")[1])
                    ob = main_nodes[idx]
                    healthy_main_nodes.append((r.get("speed", 0), ob))
                except Exception:
                    pass
        
        health_pct = (healthy_count / total_count * 100.0) if total_count > 0 else 0.0
        logging.info(f"Main xray health: {healthy_count}/{total_count} ({health_pct:.2f}%)")
    else:
        logging.info("Main xray configuration does not exist. Health: 0.0%")
        health_pct = 0.0
        
    # 5. Determine if we should update
    should_update = (not main_exists) or (health_pct <= 50.0) or args.force_update
    logging.info(f"Should update main config? {should_update} (health={health_pct:.1f}%, force={args.force_update})")
    
    if should_update:
        # Merge, de-duplicate and sort by speed
        seen_keys = set()
        merged_nodes = []
        
        all_candidates = healthy_main_nodes + healthy_lab_nodes
        all_candidates.sort(key=lambda x: x[0], reverse=True)
        
        for speed, ob in all_candidates:
            key = get_outbound_key(ob)
            if key and key not in seen_keys:
                seen_keys.add(key)
                merged_nodes.append((speed, ob))
                
        final_nodes = [ob for speed, ob in merged_nodes[:100]]
        
        if final_nodes:
            logging.info(f"Updating main config with {len(final_nodes)} healthy/fast nodes...")
            generate_xray_config(final_nodes, main_config_path, base_config)
            try:
                subprocess.run(["docker", "compose", "restart", "xray"], check=True, cwd=PROJECT_ROOT)
                logging.info("Main xray container restarted and config updated.")
            except Exception as e:
                logging.error(f"Failed to restart main xray container: {e}")
        else:
            logging.warning("No healthy nodes found in either main or lab containers. Skipping update to avoid breaking connection.")
    else:
        logging.info("Main container health is above 50% and force-update not requested. No update performed.")

def main():
    parser = argparse.ArgumentParser(description="Xray Subscription Manager")
    parser.add_argument("--force-update", action="store_true", help="Force update the main xray config regardless of health check")
    parser.add_argument("--once", action="store_true", help="Run once and exit instead of running in a loop")
    args = parser.parse_args()
    
    if args.once:
        run_update_cycle(args)
    else:
        while True:
            try:
                run_update_cycle(args)
            except Exception as e:
                logging.error(f"Error during execution: {e}")
            logging.info("Sleeping for 6 hours before next update cycle...")
            time.sleep(6 * 3600)

if __name__ == '__main__':
    main()
