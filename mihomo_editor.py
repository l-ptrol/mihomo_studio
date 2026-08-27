# !/opt/bin/python3
# -*- coding: utf-8 -*-
import http.server
import socketserver
import os
import subprocess
import urllib.parse
import urllib.request
import urllib.error
import re
import time
import shutil
import glob
import json
from datetime import datetime

# --- НАСТРОЙКИ ---
PORT = 8888
CONFIG_DIR = "/opt/etc/mihomo"
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.yaml")
PROFILES_DIR = os.path.join(CONFIG_DIR, "profiles")
BACKUP_DIR = os.path.join(CONFIG_DIR, "backup")
LOG_FILE = "/tmp/mihomo_last_restart.log"
RESTART_CMD = "xkeen -restart > " + LOG_FILE + " 2>&1"
UPDATE_CMD = "/opt/bin/mhstudio -update"

# --- ИНИЦИАЛИЗАЦИЯ ---
def init_system():
    if not os.path.exists(BACKUP_DIR):
        try: os.makedirs(BACKUP_DIR)
        except Exception: pass
    if not os.path.exists(PROFILES_DIR):
        try: os.makedirs(PROFILES_DIR)
        except Exception: pass

    if os.path.exists(CONFIG_PATH) and not os.path.islink(CONFIG_PATH):
        try:
            shutil.move(CONFIG_PATH, os.path.join(PROFILES_DIR, "default.yaml"))
            os.symlink(os.path.join(PROFILES_DIR, "default.yaml"), CONFIG_PATH)
        except Exception: pass
    elif not os.path.exists(CONFIG_PATH) and os.path.exists(PROFILES_DIR):
        def_prof = os.path.join(PROFILES_DIR, "default.yaml")
        try:
            with open(def_prof, 'w') as f:
                f.write("proxies: []\n")
            os.symlink(def_prof, CONFIG_PATH)
        except Exception: pass


# --- ПАРСЕРЫ ---
def parse_vless(link, custom_name=None):
    try:
        if not link.startswith("vless://"): return None, "Link error"
        main = link[8:]
        name = "VLESS"
        if custom_name:
            name = custom_name
        elif '#' in main:
            main, n = main.split('#', 1)
            name = urllib.parse.unquote(n).strip()

        name = re.sub(r'[\[\]\{\}\"\']', '', name)
        user_srv = main.split('?')[0]
        params = urllib.parse.parse_qs(main.split('?')[1]) if '?' in main else {}
        if '@' in user_srv:
            uuid, srv_port = user_srv.split('@', 1)
        else:
            return None, "No UUID"
        if ':' in srv_port:
            if ']' in srv_port:
                srv, port = srv_port.rsplit(':', 1)
                srv = srv.replace('[', '').replace(']', '')
            else:
                srv, port = srv_port.split(':')
        else:
            return None, "No Port"

        def get(k):
            return params.get(k, [''])[0]

        y = ['- name: "' + name + '"', '  type: vless', '  server: ' + srv, '  port: ' + port, '  uuid: ' + uuid,
             '  udp: true']
        network = get('type') or 'tcp'
        y.append('  network: ' + network)
        if get('flow'): y.append('  flow: ' + get('flow'))
        sec = get('security')
        if sec:
            y.append('  tls: true')
            if sec == 'reality':
                y.extend(['  servername: ' + get('sni'), '  client-fingerprint: ' + (get('fp') or 'chrome'),
                          '  reality-opts:', '    public-key: ' + get('pbk')])
                if get('sid'): y.append('    short-id: ' + get('sid'))
            else:
                if get('sni'): y.append('  servername: ' + get('sni'))
                if get('fp'): y.append('  client-fingerprint: ' + get('fp'))
                if get('alpn'):
                    av = get("alpn").replace(",", '", "')
                    y.append('  alpn: ["' + av + '"]')
        if network == 'ws':
            y.append('  ws-opts:')
            if get('path'): y.append('    path: ' + get('path'))
            if get('host'): y.extend(['    headers:', '      Host: ' + get('host')])
        elif network == 'grpc' and get('serviceName'):
            y.extend(['  grpc-opts:', '    grpc-service-name: ' + get('serviceName')])

        if sec == 'reality':
            proto = "VLESS Reality"
        elif network == 'ws':
            proto = "VLESS WS"
        elif network == 'grpc':
            proto = "VLESS gRPC"
        else:
            proto = "VLESS"

        return {"yaml": "\n".join(y), "name": name, "protocol": proto}, None
    except Exception as e:
        return None, str(e)


def parse_wireguard(config_text, custom_name=None):
    try:
        conf = {"interface": {}, "peer": {}}
        section = None

        for line in config_text.splitlines():
            line = line.split('#')[0].split(';')[0].strip()
            if not line: continue

            if line.startswith('[') and line.endswith(']'):
                s_name = line[1:-1].lower()
                if s_name in ('interface', 'peer'):
                    section = s_name
                else:
                    section = None
                continue

            if section and '=' in line:
                key, val = line.split('=', 1)
                conf[section][key.strip()] = val.strip()

        iface_raw = conf['interface']
        peer_raw = conf['peer']

        if not iface_raw or not peer_raw:
            return None, "Invalid WireGuard config: missing Interface or Peer"

        iface = {k.lower().replace('_', '').replace('-', ''): (k, v) for k, v in iface_raw.items()}
        peer = {k.lower().replace('_', '').replace('-', ''): (k, v) for k, v in peer_raw.items()}

        def get_val(d, *keys):
            for k in keys:
                norm_k = k.lower().replace('_', '').replace('-', '')
                if norm_k in d:
                    return d[norm_k][1]
            return None

        endpoint = get_val(peer, 'endpoint')
        if not endpoint: return None, "No Endpoint found"

        if ']:' in endpoint:
            server = endpoint.split(']:')[0][1:]
            port = endpoint.split(']:')[1]
        elif ':' in endpoint:
            server, port = endpoint.rsplit(':', 1)
        else:
            return None, "Invalid Endpoint format"

        name = "WireGuard"
        if custom_name:
            name = custom_name
        else:
            first_line = config_text.splitlines()[0].strip()
            if first_line.startswith('#') and len(first_line) > 2:
                name = first_line[1:].strip()
            else:
                name = f"WG_{server}"

        address_raw = get_val(iface, 'address')
        if not address_raw: return None, "No Address found"

        ips = [x.strip() for x in address_raw.split(',')]
        ip_v4 = None
        ip_v6 = None

        for ip in ips:
            clean_ip = ip.split('/')[0].strip()
            if ':' in clean_ip:
                if not ip_v6: ip_v6 = clean_ip
            else:
                if not ip_v4: ip_v4 = clean_ip

        if not ip_v4 and not ip_v6:
            return None, "No valid IP address found"

        y = []
        y.append(f'- name: "{name}"')
        y.append('  type: wireguard')
        y.append(f'  server: {server}')
        y.append(f'  port: {port}')

        if ip_v4: y.append(f'  ip: {ip_v4}')
        if ip_v6: y.append(f'  ipv6: {ip_v6}')

        pk = get_val(iface, 'privatekey', 'private-key')
        if pk: y.append(f'  private-key: {pk}')

        pubk = get_val(peer, 'publickey', 'public-key')
        if pubk: y.append(f'  public-key: {pubk}')

        psk = get_val(peer, 'presharedkey', 'pre-shared-key')
        if psk: y.append(f'  pre-shared-key: {psk}')

        dns_raw = get_val(iface, 'dns')
        if dns_raw:
            dns_list = [d.strip() for d in dns_raw.split(',')]
            y.append(f'  dns: {json.dumps(dns_list)}')

        mtu = get_val(iface, 'mtu')
        if mtu: y.append(f'  mtu: {mtu}')

        y.append('  udp: true')

        AWG_KEY_MAP = {
            'headerprotectionkey': 'header-protection-key',
            'contentpaddingaddition': 'content-padding-addition',
            'rekeyaftertime': 'rekey-after-time',
            'rekeytimeout': 'rekey-timeout',
            'rejectaftertime': 'reject-after-time',
            'keepalivetimeout': 'keepalive-timeout',
            'maxhandshakeattempts': 'max-handshake-attempts',
            'randomtrailers': 'random-trailers',
            'disablecookies': 'disable-cookies',
            'jc': 'jc',
            'jmin': 'jmin',
            'jmax': 'jmax',
            's1': 's1',
            's2': 's2',
            's3': 's3',
            's4': 's4',
            'h1': 'h1',
            'h2': 'h2',
            'h3': 'h3',
            'h4': 'h4',
            'i1': 'i1',
            'i2': 'i2',
            'i3': 'i3',
            'i4': 'i4',
            'i5': 'i5',
            'j1': 'j1',
            'j2': 'j2',
            'j3': 'j3',
            'itime': 'itime',
            'version': 'version'
        }

        std_wg_keys = {
            'privatekey', 'address', 'dns', 'mtu', 'listenport', 'table',
            'preup', 'postup', 'predown', 'postdown', 'saveconfig'
        }

        amn_opts = {}
        for norm_k, (orig_k, v) in iface.items():
            if norm_k in std_wg_keys:
                continue

            target_key = AWG_KEY_MAP.get(norm_k, orig_k.lower())
            val_lower = v.lower()

            if target_key in ('random-trailers', 'disable-cookies'):
                if val_lower in ('on', 'true', 'yes', '1', 'enable', 'enabled'):
                    amn_opts[target_key] = True
                elif val_lower in ('off', 'false', 'no', '0', 'disable', 'disabled'):
                    amn_opts[target_key] = False
                else:
                    amn_opts[target_key] = v
            elif v.isdigit() or (v.startswith('-') and v[1:].isdigit()):
                amn_opts[target_key] = int(v)
            else:
                amn_opts[target_key] = v

        v3_keys = {
            'header-protection-key', 'content-padding-addition', 'rekey-after-time',
            'rekey-timeout', 'reject-after-time', 'keepalive-timeout',
            'max-handshake-attempts', 'random-trailers', 'disable-cookies'
        }
        has_v3 = any(k in amn_opts for k in v3_keys)
        has_v3_1 = 'random-trailers' in amn_opts or 'disable-cookies' in amn_opts

        if has_v3 and 'version' not in amn_opts:
            amn_opts['version'] = 3

        if amn_opts:
            y.append('  amnezia-wg-option:')
            if 'version' in amn_opts:
                y.append(f'    version: {amn_opts["version"]}')

            for k, v in amn_opts.items():
                if k == 'version': continue
                if isinstance(v, bool):
                    y.append(f'    {k}: {"true" if v else "false"}')
                elif isinstance(v, str):
                    if not v:
                        y.append(f'    {k}: ""')
                    else:
                        y.append(f'    {k}: {v}')
                else:
                    y.append(f'    {k}: {v}')

        allowed = get_val(peer, 'allowedips', 'allowed-ips')
        if allowed:
            al_list = [x.strip() for x in allowed.split(',')]
            y.append(f'  allowed-ips: {json.dumps(al_list)}')

        ka = get_val(peer, 'persistentkeepalive', 'persistent-keepalive')
        if ka:
            ka_clean = ka.split('-')[0].strip() if '-' in ka and not ka.strip().startswith('-') else ka.strip()
            try:
                y.append(f'  persistent-keepalive: {int(ka_clean)}')
            except ValueError:
                pass

        if amn_opts:
            if has_v3_1 or amn_opts.get('version') == 3:
                proto = "AmneziaWG v3.1"
            elif any(k in amn_opts for k in ('s1', 's2', 's3', 's4', 'h1', 'h2', 'h3', 'h4', 'i1', 'i2', 'i3', 'i4', 'i5', 'j1', 'j2', 'j3', 'itime')):
                proto = "AmneziaWG v1.5/2.0"
            elif any(k in amn_opts for k in ('jc', 'jmin', 'jmax')):
                proto = "AmneziaWG v1.0"
            else:
                proto = "AmneziaWG"
        else:
            proto = "WireGuard Classic"

        return {"yaml": "\n".join(y), "name": name, "protocol": proto}, None

    except Exception as e:
        return None, str(e)


def insert_proxy_logic(content, proxy_name, target_groups):
    lines = content.splitlines()
    new_lines = []

    def get_indent(s):
        return len(s) - len(s.lstrip())

    in_group_section = False
    current_group_name = None
    in_proxies_list = False
    proxies_list_indent = -1
    inserted_in_group = set()

    for i, line in enumerate(lines):
        stripped = line.strip()
        indent = get_indent(line)
        is_new_group = stripped.startswith('- name:')

        if is_new_group:
            if in_proxies_list and current_group_name in target_groups and current_group_name not in inserted_in_group:
                prefix = " " * (proxies_list_indent + 2)
                new_lines.append(prefix + '- "' + proxy_name + '"')
                inserted_in_group.add(current_group_name)
            in_proxies_list = False

        if stripped.startswith('proxy-groups:'):
            in_group_section = True
        elif in_group_section and indent == 0 and stripped and not stripped.startswith('#'):
            in_group_section = False
            in_proxies_list = False
            current_group_name = None

        if in_group_section:
            if is_new_group:
                raw_name = stripped.split(':', 1)[1].strip()
                current_group_name = raw_name.strip("'").strip('"')

            if current_group_name in target_groups and stripped.startswith('proxies:'):
                if '[' in stripped and stripped.rstrip().endswith(']'):
                    start = line.find('[')
                    end = line.rfind(']')
                    if start != -1 and end != -1:
                        content_inner = line[start + 1:end]
                        if proxy_name not in content_inner:
                            sep = ", " if content_inner.strip() else ""
                            new_content = content_inner + sep + f'"{proxy_name}"'
                            new_line = line[:start + 1] + new_content + line[end:]
                            new_lines.append(new_line)
                            inserted_in_group.add(current_group_name)
                            continue
                        else:
                            new_lines.append(line)
                            inserted_in_group.add(current_group_name)
                            continue

                in_proxies_list = True
                proxies_list_indent = indent
                new_lines.append(line)
                continue

            if in_proxies_list:
                if not stripped or stripped.startswith('#'):
                    new_lines.append(line)
                    continue
                if ('DIRECT' in stripped or 'REJECT' in stripped) and current_group_name not in inserted_in_group:
                    prefix = " " * indent
                    new_lines.append(prefix + '- "' + proxy_name + '"')
                    inserted_in_group.add(current_group_name)

                if indent <= proxies_list_indent:
                    if current_group_name not in inserted_in_group:
                        prefix = " " * (proxies_list_indent + 2)
                        new_lines.append(prefix + '- "' + proxy_name + '"')
                        inserted_in_group.add(current_group_name)
                    in_proxies_list = False

        new_lines.append(line)

    if in_proxies_list and current_group_name in target_groups and current_group_name not in inserted_in_group:
        prefix = " " * (proxies_list_indent + 2)
        new_lines.append(prefix + '- "' + proxy_name + '"')

    return "\n".join(new_lines)


def replace_proxy_block(content, target_name, new_yaml_lines):
    lines = content.splitlines()
    new_content_lines = []

    in_proxies = False
    found_target = False
    replaced = False

    name_pattern = re.compile(r'^\s*-\s+name:\s*(["\'])?' + re.escape(target_name) + r'(\1)?\s*$')

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith('proxies:'):
            in_proxies = True
            new_content_lines.append(line)
            i += 1
            continue

        if in_proxies and line and not line.startswith(' ') and not line.startswith('\t') and not line.startswith('#'):
            in_proxies = False

        if in_proxies and not replaced:
            if name_pattern.match(stripped):
                indent_len = len(line) - len(line.lstrip())
                if new_yaml_lines and "name:" in new_yaml_lines[0]:
                    new_yaml_lines[0] = re.sub(r'name:\s*".*"', f'name: "{target_name}"', new_yaml_lines[0])

                for n_line in new_yaml_lines:
                    new_content_lines.append(" " * indent_len + n_line)

                replaced = True
                found_target = True

                i += 1
                while i < len(lines):
                    next_line = lines[i]
                    next_stripped = next_line.strip()
                    next_indent = len(next_line) - len(next_line.lstrip())
                    if not next_stripped:
                        i += 1
                        continue
                    if next_indent < indent_len: break
                    if next_indent == indent_len and next_stripped.startswith('-'): break
                    i += 1
                continue

        new_content_lines.append(line)
        i += 1

    return "\n".join(new_content_lines)


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<title>Mihomo Studio v1.5</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/ace/1.32.7/ace.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;600&display=swap');
:root {
    --bg-grad: linear-gradient(135deg, #0b0f19 0%, #111827 50%, #1e1b4b 100%);
    --bg-sec: rgba(17, 24, 39, 0.78);
    --bg-ter: rgba(31, 41, 55, 0.65);
    --txt: #f9fafb; --txt-sec: #9ca3af; --bd: rgba(255, 255, 255, 0.12);
    --btn-s: linear-gradient(135deg, #2563eb, #1d4ed8);
    --btn-r: linear-gradient(135deg, #059669, #047857);
    --btn-d: linear-gradient(135deg, #dc2626, #991b1b);
    --btn-u: linear-gradient(135deg, #d97706, #b45309);
    --btn-g: rgba(255, 255, 255, 0.08);
    --btn-g-txt: #f3f4f6;
    --log-bg: rgba(10, 15, 26, 0.92); --log-txt: #e2e8f0;
    --comp-h: 36px; --radius: 10px;
    --shadow: 0 4px 16px rgba(0, 0, 0, 0.2);
    --glass-sh: inset 0 1px 0 rgba(255, 255, 255, 0.12);
    --glass-blur: blur(16px);
}
body.dark {
    --bg-grad: linear-gradient(135deg, #0b0f19 0%, #111827 50%, #1e1b4b 100%);
    --bg-sec: rgba(17, 24, 39, 0.78);
    --bg-ter: rgba(31, 41, 55, 0.65);
    --txt: #f9fafb; --txt-sec: #9ca3af; --bd: rgba(255, 255, 255, 0.12);
    --btn-s: linear-gradient(135deg, #2563eb, #1d4ed8);
    --btn-r: linear-gradient(135deg, #059669, #047857);
    --btn-d: linear-gradient(135deg, #dc2626, #991b1b);
    --btn-u: linear-gradient(135deg, #d97706, #b45309);
    --btn-g: rgba(255, 255, 255, 0.08);
    --btn-g-txt: #f3f4f6;
    --log-bg: rgba(10, 15, 26, 0.92);
    --shadow: 0 4px 16px rgba(0, 0, 0, 0.2);
    --glass-sh: inset 0 1px 0 rgba(255, 255, 255, 0.12);
}
body.light {
    --bg-grad: linear-gradient(135deg, #f0f4f8 0%, #e2e8f0 50%, #cbd5e1 100%);
    --bg-sec: rgba(255, 255, 255, 0.88);
    --bg-ter: rgba(241, 245, 249, 0.92);
    --txt: #0f172a; --txt-sec: #475569; --bd: rgba(15, 23, 42, 0.12);
    --btn-s: linear-gradient(135deg, #2563eb, #1e40af);
    --btn-r: linear-gradient(135deg, #10b981, #059669);
    --btn-d: linear-gradient(135deg, #ef4444, #b91c1c);
    --btn-u: linear-gradient(135deg, #f59e0b, #d97706);
    --btn-g: rgba(15, 23, 42, 0.06);
    --btn-g-txt: #0f172a;
    --log-bg: rgba(15, 23, 42, 0.95); --log-txt: #f8fafc;
    --shadow: 0 4px 16px rgba(15, 23, 42, 0.08);
    --glass-sh: inset 0 1px 0 rgba(255, 255, 255, 0.8);
}
body.midnight {
    --bg-grad: linear-gradient(135deg, #020617 0%, #0b132b 50%, #1c2541 100%);
    --bg-sec: rgba(11, 19, 43, 0.78);
    --bg-ter: rgba(28, 37, 65, 0.65);
    --txt: #f8fafc; --txt-sec: #94a3b8; --bd: rgba(56, 189, 248, 0.18);
    --btn-s: linear-gradient(135deg, #0284c7, #0369a1);
    --btn-r: linear-gradient(135deg, #0d9488, #0f766e);
    --btn-d: linear-gradient(135deg, #e11d48, #be123c);
    --btn-u: linear-gradient(135deg, #ea580c, #c2410c);
    --btn-g: rgba(56, 189, 248, 0.08);
    --btn-g-txt: #e0f2fe;
    --log-bg: rgba(2, 6, 23, 0.92);
}
body.cyber {
    --bg-grad: radial-gradient(circle at center, #021a08 0%, #000000 100%);
    --bg-sec: rgba(0, 26, 8, 0.78);
    --bg-ter: rgba(0, 45, 15, 0.65);
    --txt: #00ff66; --txt-sec: #00bb44; --bd: rgba(0, 255, 102, 0.3);
    --btn-s: linear-gradient(135deg, #008833, #005520);
    --btn-r: linear-gradient(135deg, #00cc44, #008822);
    --btn-d: linear-gradient(135deg, #cc1100, #770000);
    --btn-u: linear-gradient(135deg, #cc9900, #775500);
    --btn-g: rgba(0, 255, 102, 0.12);
    --btn-g-txt: #00ff66;
    --radius: 6px;
    --shadow: 0 0 16px rgba(0, 255, 102, 0.25);
    --glass-sh: inset 0 0 0 1px rgba(0, 255, 102, 0.25);
}

body { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; background: var(--bg-grad); color: var(--txt); margin: 0; display: flex; flex-direction: column; height: 100vh; overflow: hidden; }
* { box-sizing: border-box; }
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: rgba(128,128,128,0.25); border-radius: var(--radius); }
::-webkit-scrollbar-thumb:hover { background: rgba(128,128,128,0.45); }

/* Header */
.hdr {
    background: var(--bg-sec);
    backdrop-filter: var(--glass-blur);
    -webkit-backdrop-filter: var(--glass-blur);
    padding: 0 16px;
    border-bottom: 1px solid var(--bd);
    display: flex;
    justify-content: space-between;
    align-items: center;
    height: 48px;
    min-height: 48px;
    flex-shrink: 0;
    box-shadow: var(--shadow), var(--glass-sh);
    z-index: 10;
}
.hdr-brand {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
}
.logo {
    font-size: 18px;
    font-weight: 800;
    background: linear-gradient(135deg, #3b82f6, #a855f7);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    letter-spacing: -0.4px;
    white-space: nowrap;
}
.ver-badge {
    color: var(--txt-sec);
    font-size: 11px;
    font-weight: 600;
    background: var(--bg-ter);
    padding: 2px 6px;
    border-radius: 6px;
    border: 1px solid var(--bd);
}
.hdr-right {
    display: flex;
    gap: 6px;
    align-items: center;
    flex-shrink: 0;
}
.live-clock {
    font-family: 'JetBrains Mono', 'Consolas', monospace;
    font-size: 12px;
    font-weight: 600;
    color: var(--txt-sec);
    background: var(--bg-ter);
    border: 1px solid var(--bd);
    padding: 2px 8px;
    border-radius: 8px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    height: 28px;
    min-width: 72px;
    letter-spacing: 0.5px;
    box-shadow: inset 0 1px 2px rgba(0,0,0,0.1);
}
.btn-hdr {
    height: 28px !important;
    padding: 0 10px !important;
    font-size: 12px !important;
    gap: 4px !important;
}

/* Action Toolbar */
.bar {
    background: var(--bg-sec);
    backdrop-filter: var(--glass-blur);
    -webkit-backdrop-filter: var(--glass-blur);
    padding: 8px 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    border-bottom: 1px solid var(--bd);
    flex-shrink: 0;
    z-index: 9;
    box-shadow: var(--shadow), var(--glass-sh);
}
.bar-main {
    display: flex;
    gap: 8px;
    align-items: center;
}
.bar-opts {
    display: flex;
    gap: 6px;
    align-items: center;
}
.bar-sel {
    height: 32px !important;
    padding: 0 8px !important;
    font-size: 12px !important;
    width: auto !important;
}

button, input, select, textarea {
    font-family: inherit; font-size: 13px; color: var(--txt);
    border: 1px solid var(--bd); border-radius: var(--radius);
    background: var(--bg-ter);
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1); outline: none;
    box-shadow: var(--shadow);
}
button {
    height: var(--comp-h); padding: 0 14px; cursor: pointer; color: #fff; font-weight: 600;
    display: flex; align-items: center; justify-content: center; gap: 6px; white-space: nowrap; border: 1px solid rgba(255,255,255,0.1);
    box-shadow: 0 2px 6px rgba(0,0,0,0.15), inset 0 1px 0 rgba(255,255,255,0.2);
}
button:hover { transform: translateY(-1px); filter: brightness(1.08); box-shadow: 0 4px 12px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.25); }
button:active { transform: translateY(1px) scale(0.98); box-shadow: inset 0 2px 4px rgba(0,0,0,0.2); }
input, select { height: var(--comp-h); padding: 0 10px; width: 100%; backdrop-filter: blur(5px); }
input:focus, select:focus, textarea:focus { border-color: rgba(37, 99, 235, 0.7); box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.2), inset 0 1px 2px rgba(0,0,0,0.1); background: var(--bg-sec); }

.btn-s { background: var(--btn-s); }
.btn-r { background: var(--btn-r); }
.btn-d { background: var(--btn-d); }
.btn-u { background: var(--btn-u); }
.btn-g { background: var(--btn-g); color: var(--btn-g-txt); border: 1px solid var(--bd); box-shadow: var(--shadow); }

.main { display: flex; flex: 1; overflow: hidden; position: relative; }
#ed { flex: 1; font-size: 14px; }
.sb { width: 330px; background: var(--bg-sec); backdrop-filter: var(--glass-blur); -webkit-backdrop-filter: var(--glass-blur); border-left: 1px solid var(--bd); display: flex; flex-direction: column; overflow-y: auto; flex-shrink: 0; box-shadow: -4px 0 20px rgba(0,0,0,0.1), inset 1px 0 0 rgba(255,255,255,0.08); }
.sec { padding: 14px 16px; border-bottom: 1px solid var(--bd); display: flex; flex-direction: column; gap: 10px; }
.sec h3 { margin: 0 0 4px 0; font-size: 14px; font-weight: 700; color: var(--txt); text-shadow: 0 1px 2px rgba(0,0,0,0.1); }

.ovl { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); z-index: 999; display: none; justify-content: center; align-items: center; padding: 16px; backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px); animation: fadeIn 0.2s ease; }
.mod { background: var(--bg-sec); backdrop-filter: var(--glass-blur); -webkit-backdrop-filter: var(--glass-blur); padding: 22px; border-radius: calc(var(--radius) + 4px); width: 100%; max-width: 580px; border: 1px solid var(--bd); display: flex; flex-direction: column; max-height: 90vh; box-shadow: 0 20px 40px rgba(0,0,0,0.35), inset 0 0 0 1px rgba(255,255,255,0.1); animation: slideUp 0.25s cubic-bezier(0.175, 0.885, 0.32, 1.275); }
.mod h3 { margin-top: 0; color: var(--txt); border-bottom: 1px solid var(--bd); padding-bottom: 12px; margin-bottom: 16px; font-size: 17px; }
@keyframes slideUp { from { opacity:0; transform: translateY(15px) scale(0.97); } to { opacity:1; transform: translateY(0) scale(1); } }

.bk-item { background: var(--bg-ter); padding: 8px 10px; margin-bottom: 6px; border: 1px solid var(--bd); border-radius: var(--radius); display: flex; justify-content: space-between; align-items: center; height: auto; min-height: 38px; transition: all 0.2s; }
.bk-item:hover { background: var(--bg-sec); transform: translateX(2px); box-shadow: var(--shadow); border-color: rgba(255,255,255,0.25); }
.bk-item div:first-child { flex: 1; min-width: 0; padding-right: 8px; display: flex; flex-direction: column; justify-content: center; }
.bk-item b { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: block; font-size: 13px; margin-bottom: 2px; }
.bk-btns { display: flex; gap: 5px; flex-shrink: 0; }
.bk-btns button { width: 30px; padding: 0; height: 30px; font-size: 15px; border-radius: var(--radius); }

#bk-content { background: var(--log-bg); color: var(--log-txt); font-family: 'JetBrains Mono', 'Consolas', monospace; padding: 12px; border-radius: var(--radius); border: 1px solid var(--bd); white-space: pre-wrap; overflow-y: auto; flex-grow: 1; min-height: 200px; max-height: 60vh; font-size: 13px; box-shadow: inset 0 2px 10px rgba(0,0,0,0.2); }
.bk-controls { display: flex; gap: 8px; align-items: center; background: var(--bg-ter); padding: 6px 10px; border-radius: var(--radius); border: 1px solid var(--bd); box-shadow: inset 0 2px 5px rgba(0,0,0,0.05); }
.bk-controls input { width: 55px !important; text-align: center; margin: 0; height: 30px; padding: 0; }
.bk-controls span { font-size: 12px; color: var(--txt-sec); white-space: nowrap; }
.bk-controls button { height: 30px; font-size: 12px; padding: 0 10px; margin-left: auto; }
#bk-list { max-height: 220px; overflow-y: auto; padding-right: 4px; }

.prof-row { display: flex; gap: 8px; align-items: center; }
#prof-sel { flex: 1; font-weight: 500; }
.prof-btns { display: flex; gap: 8px; margin-top: 4px; }
.prof-btns button { flex: 1; height: 34px; font-size: 12px; }
.proxy-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.proxy-grid button { height: 34px; font-size: 12px; }

#cons { background: var(--log-bg); color: var(--log-txt); font-family: 'JetBrains Mono', 'Consolas', monospace; padding: 12px; height: 320px; overflow: auto; white-space: pre-wrap; font-size: 13px; border: 1px solid var(--bd); border-radius: var(--radius); box-shadow: inset 0 2px 10px rgba(0,0,0,0.2); line-height: 1.5; }
.log-line { margin-bottom: 2px; }
.g-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 8px; overflow-y: auto; padding: 4px; margin-top: 5px; max-height: 280px; }
.g-item { position: relative; }
.g-item input { position: absolute; opacity: 0; cursor: pointer; height: 0; width: 0; }
.g-item label { display: flex; align-items: center; justify-content: center; background: var(--bg-ter); border: 1px solid var(--bd); border-radius: var(--radius); padding: 8px 4px; font-size: 12px; color: var(--txt); cursor: pointer; transition: all 0.2s; text-align: center; user-select: none; word-break: break-word; min-height: 38px; font-weight: 500; }
.g-item label:hover { transform: translateY(-1px); background: var(--bg-sec); box-shadow: var(--shadow); }
.g-item input:checked + label { background: linear-gradient(135deg, #2563eb, #1d4ed8); color: white; border-color: transparent; font-weight: bold; box-shadow: 0 4px 12px rgba(37, 99, 235, 0.4); }

.toast { position: fixed; bottom: 30px; left: 50%; transform: translateX(-50%); background: var(--bg-sec); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px); color: var(--txt); padding: 10px 20px; border-radius: 25px; z-index: 3000; display: none; box-shadow: 0 10px 25px rgba(0,0,0,0.25), inset 0 0 0 1px rgba(255,255,255,0.1); border: none; font-weight: 500; align-items: center; gap: 8px; font-size: 13px; animation: slideUpToast 0.25s cubic-bezier(0.175, 0.885, 0.32, 1.275); }
@keyframes slideUpToast { from { opacity:0; transform: translate(-50%, 15px); } to { opacity:1; transform: translate(-50%, 0); } }
.toast-icon { font-size: 16px; }

.modal-tabs { display: flex; border-bottom: 1px solid var(--bd); margin-bottom: 16px; gap: 15px; }
.modal-tabs button { flex: 1; justify-content: center; background: none; border: none; border-bottom: 2px solid transparent; border-radius: 0; padding: 8px; font-size: 14px; color: var(--txt-sec); height: auto; box-shadow: none; font-weight: 600; transition: color 0.2s; }
.modal-tabs button:hover { color: var(--txt); background: none; transform: none; }
.modal-tabs button.active { color: #3b82f6; border-bottom-color: #3b82f6; }
.tab-content { display: none; animation: fadeIn 0.2s ease-out; }
.tab-content.active { display: block; }
@keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }

.file-drop-zone { border: 2px dashed var(--bd); border-radius: var(--radius); padding: 20px; text-align: center; color: var(--txt-sec); cursor: pointer; transition: all 0.2s; margin-bottom: 12px; background: rgba(0,0,0,0.03); }
.file-drop-zone:hover { background: var(--bg-sec); border-color: rgba(37, 99, 235, 0.6); color: #3b82f6; transform: translateY(-1px); }
.file-drop-zone.dragover { background: rgba(37, 99, 235, 0.12); border-color: #3b82f6; color: #3b82f6; }

.proto-badge-row { display: flex; align-items: center; justify-content: space-between; padding: 8px 12px; background: var(--bg-ter); border: 1px solid var(--bd); border-radius: var(--radius); margin-bottom: 12px; animation: fadeIn 0.2s ease-out; }
.proto-lbl { font-size: 12px; color: var(--txt-sec); font-weight: 500; }
.badge-proto { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; font-weight: 700; padding: 3px 10px; border-radius: 16px; letter-spacing: 0.3px; text-shadow: 0 1px 2px rgba(0,0,0,0.2); }
.badge-awg3 { background: linear-gradient(135deg, #8b5cf6, #06b6d4); color: #ffffff; box-shadow: 0 0 10px rgba(139, 92, 246, 0.4); }
.badge-awg2 { background: linear-gradient(135deg, #3b82f6, #6366f1); color: #ffffff; }
.badge-awg1 { background: linear-gradient(135deg, #0ea5e9, #2563eb); color: #ffffff; }
.badge-wg { background: linear-gradient(135deg, #10b981, #059669); color: #ffffff; }
.badge-reality { background: linear-gradient(135deg, #ec4899, #8b5cf6); color: #ffffff; }
.badge-ws { background: linear-gradient(135deg, #06b6d4, #3b82f6); color: #ffffff; }
.badge-grpc { background: linear-gradient(135deg, #f59e0b, #ea580c); color: #ffffff; }
.badge-vless { background: linear-gradient(135deg, #6366f1, #a855f7); color: #ffffff; }

.mobile-nav-bar {
    display: none;
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    height: 52px;
    background: var(--bg-sec);
    backdrop-filter: var(--glass-blur);
    -webkit-backdrop-filter: var(--glass-blur);
    border-top: 1px solid var(--bd);
    z-index: 100;
    justify-content: space-around;
    align-items: center;
    box-shadow: 0 -4px 16px rgba(0,0,0,0.2);
}
.mob-tab {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    background: none;
    border: none;
    border-radius: 0;
    height: 100%;
    padding: 2px 0;
    color: var(--txt-sec);
    gap: 2px;
    box-shadow: none;
    cursor: pointer;
    transition: color 0.15s;
}
.mob-tab:hover { background: none; transform: none; color: var(--txt); }
.mob-tab.active { color: #3b82f6; font-weight: 700; }
.mob-icon { font-size: 16px; line-height: 1; }
.mob-lbl { font-size: 10px; line-height: 1; }

@media (max-width: 768px) {
    .mobile-nav-bar { display: flex !important; }
    .main { flex-direction: column; height: calc(100vh - 148px); }
    .sb { width: 100%; border-left: none; border-top: none; height: 100%; max-height: none; padding-bottom: 60px; box-shadow: none; }
    #ed { width: 100%; height: calc(100vh - 148px); min-height: 250px; }
    
    .hdr {
        padding: 0 10px;
        height: 44px;
        min-height: 44px;
    }
    .logo {
        font-size: 15px;
    }
    .live-clock {
        font-size: 11px;
        padding: 2px 6px;
        min-width: 60px;
        height: 24px;
    }
    .btn-hdr {
        height: 26px !important;
        padding: 0 6px !important;
        font-size: 11px !important;
        gap: 3px !important;
    }

    .bar {
        padding: 6px 10px;
        flex-direction: column;
        gap: 6px;
    }
    .bar-main {
        width: 100%;
        gap: 6px;
    }
    .bar-main button {
        flex: 1;
        height: 32px;
        font-size: 12px;
        padding: 0 4px;
        justify-content: center;
    }
    .bar-opts {
        width: 100%;
        gap: 6px;
    }
    .bar-opts select {
        flex: 1;
        height: 30px;
        font-size: 12px;
        padding: 0 6px;
    }

    .mod { width: 95%; max-height: 90vh; padding: 16px; }
}

@media (max-width: 480px) {
    .hdr-btn-lbl { display: none; }
    .btn-hdr { width: 28px !important; padding: 0 !important; justify-content: center; }
}
</style>
</head>
<body>
<div class="toast" id="toast"><span class="toast-icon"></span> <span id="toast-msg" data-i18n="toast_saved">Saved</span></div>

<div class="hdr">
    <div class="hdr-brand">
        <div class="logo" data-i18n="title">Mihomo Studio</div>
        <span class="ver-badge">v1.5</span>
    </div>
    <div class="hdr-right">
        <div class="live-clock" id="live-clock" title="Время">--:--:--</div>
        <button onclick="restartService()" class="btn-r btn-hdr" title="Перезапустить веб-сервис"><span class="btn-icon">🔄</span><span class="hdr-btn-lbl" data-i18n="restart_service_short">Рестарт</span></button>
        <button onclick="updateStudio()" class="btn-u btn-hdr" title="Проверить обновления"><span class="btn-icon">⚡</span><span class="hdr-btn-lbl" data-i18n="update_btn_short">Обновить</span></button>
    </div>
</div>

<div class="bar">
    <div class="bar-main">
        <button onclick="save('save')" class="btn-s" data-i18n="save">💾 Сохранить</button>
        <button onclick="save('restart')" class="btn-r" data-i18n="restart">🚀 Рестарт</button>
        <button onclick="openPanel()" class="btn-g" title="Открыть панель Mihomo" data-i18n="panel">🌐 Панель</button>
    </div>
    <div class="bar-opts">
        <select id="lang-sel" onchange="setLang(this.value)" class="bar-sel">
            <option value="ru">🇷🇺 RU</option>
            <option value="en">🇺🇸 EN</option>
            <option value="uk">🇺🇦 UA</option>
        </select>
        <select id="theme-sel" onchange="setTheme(this.value)" class="bar-sel">
            <option value="dark" data-i18n="theme_dark">🌑 Тёмная</option>
            <option value="light" data-i18n="theme_light">☀️ Светлая</option>
            <option value="midnight" data-i18n="theme_midnight">🌃 Полночь</option>
            <option value="cyber" data-i18n="theme_cyber">👾 Кибер</option>
        </select>
    </div>
</div>

<div class="main">
    <div id="ed"></div>
    <div class="sb">
        <div class="sec" id="sec-profiles">
            <h3><span data-i18n="profiles">Профили</span></h3>
            <div class="prof-row">
                <select id="prof-sel">__PROFILES__</select>
                <button onclick="switchProf()" class="btn-s" style="padding:0; width:36px; justify-content:center;" title="Выбрать" data-i18n="select">✔</button>
                <button onclick="downloadProf()" class="btn-g" style="padding:0; width:36px; justify-content:center;" title="Скачать" data-i18n="download">💾</button>
            </div>
            <div class="prof-btns">
                 <button onclick="openAddProf()" class="btn-u" data-i18n="create">➕ Создать</button>
                 <button onclick="delProf()" class="btn-d" data-i18n="delete">🗑 Удалить</button>
            </div>
        </div>
        <div class="sec" id="sec-proxy-mgmt">
            <h3><span data-i18n="proxy_mgmt">Управление</span></h3>
            <div class="proxy-grid">
                <button onclick="openAddProxyModal()" class="btn-s" data-i18n="add">➕ Добавить</button>
                <button onclick="openEditProxyModal()" class="btn-u" data-i18n="edit">✏️ Заменить</button>
                <button onclick="showRename()" class="btn-g" data-i18n="rename">Переименовать</button>
                <button onclick="showDel()" class="btn-d" data-i18n="delete">🗑 Удалить</button>
            </div>
        </div>
        <div class="sec" id="sec-backups">
            <h3><span data-i18n="backups">Бэкапы</span></h3>
            <div class="bk-controls">
                <span data-i18n="keep">Оставить:</span>
                <input type="number" id="bk-lim" value="5" min="1" max="50">
                <button onclick="cleanBackups()" class="btn-g" data-i18n="clean">Очистить</button>
            </div>
            <div id="bk-list">__BACKUPS__</div>
        </div>
        <div class="sec" style="text-align: center; font-size: 11px; color: var(--txt-sec); padding: 12px; border-bottom: none; margin-top: auto;">
            Mihomo Studio &copy; 2025 - 2026
        </div>
    </div>
</div>

<div class="mobile-nav-bar" id="mob-nav">
    <button class="mob-tab active" id="mtab-ed" onclick="switchMobileView('ed')">
        <span class="mob-icon">📝</span>
        <span class="mob-lbl" data-i18n="nav_editor">Редактор</span>
    </button>
    <button class="mob-tab" id="mtab-prof" onclick="switchMobileView('prof')">
        <span class="mob-icon">📁</span>
        <span class="mob-lbl" data-i18n="nav_profiles">Профили</span>
    </button>
    <button class="mob-tab" id="mtab-bk" onclick="switchMobileView('bk')">
        <span class="mob-icon">📦</span>
        <span class="mob-lbl" data-i18n="nav_backups">Бэкапы</span>
    </button>
    <button class="mob-tab" id="mtab-add" onclick="openAddProxyModal()">
        <span class="mob-icon">➕</span>
        <span class="mob-lbl" data-i18n="nav_add">Добавить</span>
    </button>
</div>

<div id="m-grp" class="ovl"><div class="mod"><h3 data-i18n="modal_groups">Добавить в группы:</h3>
<div style="display:flex; gap:8px; margin-bottom:12px"><button onclick="tgGrp(true)" class="btn-g" style="flex:1; justify-content:center" data-i18n="btn_sel_all">☑ Выбрать все</button><button onclick="tgGrp(false)" class="btn-g" style="flex:1; justify-content:center" data-i18n="btn_sel_none">☐ Снять все</button></div>
<div id="g-cnt" class="g-list"></div>
<div style="display:flex;justify-content:flex-end;gap:10px;margin-top:16px;padding-top:12px;border-top:1px solid var(--bd)"><button onclick="applyVless()" class="btn-s" style="flex:1;justify-content:center" data-i18n="btn_add">Добавить</button><button onclick="closeM('m-grp')" class="btn-g" style="flex:1;justify-content:center" data-i18n="btn_cancel">Отмена</button></div></div></div>

<div id="m-del" class="ovl"><div class="mod"><h3 data-i18n="modal_del_proxy">Удалить прокси</h3><select id="sel-del"></select><div style="display:flex;justify-content:flex-end;gap:10px;margin-top:16px"><button onclick="doDel()" class="btn-d" data-i18n="delete">Удалить</button><button onclick="closeM('m-del')" class="btn-g" data-i18n="btn_cancel">Отмена</button></div></div></div>

<div id="m-con" class="ovl"><div class="mod"><h3 data-i18n="modal_console">Консоль</h3><div id="cons">...</div><div style="display:flex;justify-content:flex-end;gap:10px;margin-top:16px"><button onclick="location.reload()" class="btn-s" data-i18n="btn_update">Обновить</button><button onclick="closeM('m-con')" class="btn-g" data-i18n="btn_close">Закрыть</button></div></div></div>

<div id="m-ren" class="ovl"><div class="mod">
    <h3 data-i18n="modal_ren_proxy">Переименовать прокси</h3>
    <p style="margin-top:0;font-size:13px;color:var(--txt-sec);margin-bottom:6px;" data-i18n="lbl_sel_ren">Выберите прокси для переименования:</p>
    <select id="sel-ren-proxy"></select>
    <p style="margin-top:12px;font-size:13px;color:var(--txt-sec);margin-bottom:6px;" data-i18n="lbl_new_name">Новое имя:</p>
    <input id="inp-ren-newname" placeholder="Введите новое имя" data-i18n-ph="ph_new_name">
    <div style="display:flex;justify-content:flex-end;gap:10px;margin-top:20px">
        <button onclick="doRename()" class="btn-s" data-i18n="btn_rename">Переименовать</button>
        <button onclick="closeM('m-ren')" class="btn-g" data-i18n="btn_cancel">Отмена</button>
    </div>
</div></div>

<div id="m-add-prof" class="ovl"><div class="mod">
    <h3 data-i18n="modal_new_prof">Новый профиль</h3>
    <label style="font-size:13px; margin-bottom:4px; color:var(--txt-sec); display:block;" data-i18n="lbl_prof_name">Имя (англ, без пробелов):</label>
    <input id="np-name" placeholder="my_config" style="margin-bottom:12px">
    <label style="font-size:13px; margin-bottom:4px; color:var(--txt-sec); display:block;" data-i18n="lbl_content">Содержимое:</label>
    <div style="display:flex; gap:5px; margin-bottom:8px">
        <button onclick="document.getElementById('np-file').click()" class="btn-u" style="flex:1;justify-content:center" data-i18n="btn_load_file">📂 Загрузить файл</button>
    </div>
    <input type="file" id="np-file" style="display:none" onchange="loadProfFile(this)">
    <textarea id="np-content" rows="10" placeholder="Вставьте YAML конфиг сюда..." data-i18n-ph="ph_paste_yaml"></textarea>
    <div style="display:flex;justify-content:flex-end;gap:10px;margin-top:16px">
        <button onclick="saveNewProf()" class="btn-s" data-i18n="btn_save">Сохранить</button>
        <button onclick="closeM('m-add-prof')" class="btn-g" data-i18n="btn_cancel">Отмена</button>
    </div>
</div></div>

<div id="addProxyModal" class="ovl"><div class="mod">
    <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid var(--bd); padding-bottom:12px; margin-bottom:16px;">
       <h3 id="proxyModalTitle" style="margin:0; padding:0; border:0;" data-i18n="modal_add_proxy">Добавить прокси</h3>
       <button onclick="closeM('addProxyModal')" style="width:30px; height:30px; padding:0; background:transparent; color:var(--txt-sec); font-size:18px; box-shadow:none; border:none;">✕</button>
    </div>

    <div id="edit-proxy-container" style="display:none; margin-bottom:12px;">
        <label style="font-size:13px; margin-bottom:4px; color:var(--txt-sec); display:block;" data-i18n="lbl_select_edit">Выберите прокси для изменения:</label>
        <select id="edit-proxy-sel"></select>
        <div style="font-size:11px; color:var(--btn-u); margin-top:6px; background:rgba(217, 119, 6, 0.12); padding:6px 10px; border-radius:6px;" data-i18n="warn_edit">⚠️ Данные этого прокси будут полностью заменены новыми!</div>
    </div>

    <div id="proto-badge-row" class="proto-badge-row" style="display:none;">
        <span class="proto-lbl" data-i18n="proto_detected">Обнаружен протокол:</span>
        <span id="proto-badge" class="badge-proto"></span>
    </div>

    <div class="modal-tabs">
        <button class="active" onclick="switchTab(event, 'vlessTab')" data-i18n="tab_vless">VLESS</button>
        <button onclick="switchTab(event, 'wgTab')" data-i18n="tab_wg">WireGuard|AmneziaWG</button>
    </div>

    <div id="vlessTab" class="tab-content active">
        <label style="font-size:13px; margin-bottom:4px; color:var(--txt-sec); display:block;" data-i18n="lbl_vless_link">Ссылка VLESS:</label>
        <input id="vlessLink" placeholder="vless://..." style="margin-bottom:12px;" oninput="updateProtocolBadge('vless')">

        <div id="vless-name-block">
            <label style="font-size:13px; margin-bottom:4px; color:var(--txt-sec); display:block;" data-i18n="lbl_proxy_name">Имя прокси (необязательно):</label>
            <input id="vlessProxyName" placeholder="Автоматически из ссылки" data-i18n-ph="ph_auto_vless" style="margin-bottom:14px;">
        </div>

        <button onclick="parseVless()" class="btn-s" style="width:100%; justify-content:center;" data-i18n="btn_save">Сохранить</button>
    </div>

    <div id="wgTab" class="tab-content">
        <label style="font-size:13px; margin-bottom:4px; color:var(--txt-sec); display:block;" data-i18n="lbl_wg_conf">Конфигурация WireGuard:</label>
        <div class="file-drop-zone" id="wgDropZone" onclick="document.getElementById('wgFile').click()" ondragover="handleDragOver(event)" ondragleave="handleDragLeave(event)" ondrop="handleFileDrop(event)">
            <span data-i18n="drop_conf_here">📂 Нажмите или перетащите .conf файл сюда</span>
        </div>
        <textarea id="wgConfig" rows="6" placeholder="Вставьте содержимое .conf файла сюда..." data-i18n-ph="ph_paste_conf" style="width:100%; margin-bottom:12px;" oninput="updateProtocolBadge('wg')"></textarea>

        <div id="wg-name-block">
            <label style="font-size:13px; margin-bottom:4px; color:var(--txt-sec); display:block;" data-i18n="lbl_proxy_name">Имя прокси (необязательно):</label>
            <input id="wgProxyName" placeholder="Автоматически из Endpoint" data-i18n-ph="ph_auto_wg" style="margin-bottom:14px;">
        </div>

        <input type="file" id="wgFile" accept=".conf,.txt" style="display:none" onchange="loadWgFile(this)">
        <button onclick="addWireguard()" class="btn-s" style="width:100%; justify-content:center;" data-i18n="btn_save">Сохранить</button>
    </div>
</div></div>

<div id="m-bk-view" class="ovl"><div class="mod">
    <h3 id="bk-view-title" data-i18n="modal_view_bk">Просмотр бэкапа</h3>
    <pre id="bk-content"></pre>
    <div style="display:flex;justify-content:flex-end;gap:10px;margin-top:16px">
        <button id="btn-bk-restore" class="btn-u" data-i18n="btn_restore">Восстановить</button>
        <button onclick="closeM('m-bk-view')" class="btn-g" data-i18n="btn_close">Закрыть</button>
    </div>
</div></div>

<script>
var ed=ace.edit("ed");ed.setTheme("ace/theme/monokai");ed.session.setMode("ace/mode/yaml");ed.setOptions({fontSize:14,tabSize:2,useSoftTabs:true});
var pData=null, GRP_KEY="mihomo_grp_sel", LIM_KEY="mihomo_bk_lim", THM_KEY="mihomo_theme", LANG_KEY="mihomo_lang", MOB_KEY="mihomo_mob_tab";
var initialConfig = __JSON_CONTENT__;
var isEditMode = false;
var currLang = 'ru';
var currentMobileView = localStorage.getItem(MOB_KEY) || 'ed';

const TR = {
    ru: {
        title: "Mihomo Studio",
        save: "💾 Сохранить",
        restart: "🚀 Рестарт",
        panel: "🌐 Панель",
        update_btn: "🔄 Проверить обновления",
        restart_service: "🔄 Рестарт",
        profiles: "Профили",
        create: "➕ Создать",
        delete: "🗑 Удалить",
        select: "✔",
        download: "💾",
        proxy_mgmt: "Управление",
        add: "➕ Добавить",
        edit: "✏️ Заменить",
        rename: "Переименовать",
        backups: "Бэкапы",
        clean: "Очистить",
        keep: "Оставить:",
        theme_dark: "🌑 Тёмная",
        theme_light: "☀️ Светлая",
        theme_midnight: "🌃 Полночь",
        theme_cyber: "👾 Кибер",
        toast_saved: "✅ Успешно сохранено",
        toast_cleaned: "🧹 Очищено",
        toast_deleted: "🗑 Удалено",
        toast_restored: "♻️ Восстановлено",
        toast_added: "✅ Добавлено",
        toast_renamed: "✏️ Прокси переименован",
        toast_updated: "✏️ Данные прокси обновлены",
        toast_checking: "🔍 Проверка обновлений...",
        toast_restarting: "🔄 Перезапуск сервиса...",
        confirm_switch: "Переключиться на профиль {0}?",
        confirm_del_prof: "Удалить профиль {0}? Это действие необратимо.",
        confirm_del_bk: "Удалить бэкап {0}?",
        confirm_clean: "Оставить только {0} последних бэкапов?",
        confirm_restore: "Восстановить {0}? Текущий конфиг будет перезаписан.",
        confirm_del_proxy: "Удалить?",
        confirm_replace: "Заменить данные прокси '{0}'?",
        confirm_update: "Проверить обновления и установить?",
        confirm_restart_service: "Перезапустить веб-сервис Mihomo Studio?",
        prompt_enter_name: "Введите имя!",
        error_invalid_name: "Недопустимое имя!",
        error_exists: "Профиль с таким именем уже существует",
        error_no_proxy_edit: "Выберите прокси для редактирования",
        error_empty_wg: "Конфигурация WireGuard не может быть пустой.",
        alert_updating: "Обновление запущено. Сервис перезапускается...",
        modal_add_proxy: "Добавить прокси",
        modal_edit_proxy: "Изменить прокси",
        lbl_vless_link: "Ссылка VLESS:",
        lbl_proxy_name: "Имя прокси (необязательно):",
        lbl_wg_conf: "Конфигурация WireGuard:",
        btn_add: "Добавить",
        btn_save: "Сохранить",
        btn_cancel: "Отмена",
        btn_restore: "Восстановить",
        btn_close: "Закрыть",
        btn_update: "Обновить",
        tab_vless: "VLESS",
        tab_wg: "WireGuard|AmneziaWG",
        lbl_select_edit: "Выберите прокси для изменения:",
        warn_edit: "⚠️ Данные этого прокси будут полностью заменены новыми!",
        modal_new_prof: "Новый профиль",
        lbl_prof_name: "Имя (англ, без пробелов):",
        lbl_content: "Содержимое:",
        btn_load_file: "📂 Загрузить файл",
        ph_paste_yaml: "Вставьте YAML конфиг сюда...",
        ph_auto_vless: "Автоматически из ссылки",
        ph_auto_wg: "Автоматически из Endpoint",
        ph_paste_conf: "Вставьте содержимое .conf файла сюда...",
        modal_groups: "Добавить в группы:",
        btn_sel_all: "☑ Выбрать все",
        btn_sel_none: "☐ Снять все",
        modal_del_proxy: "Удалить прокси",
        modal_ren_proxy: "Переименовать прокси",
        lbl_sel_ren: "Выберите прокси для переименования:",
        lbl_new_name: "Новое имя:",
        ph_new_name: "Введите новое имя",
        btn_rename: "Переименовать",
        modal_console: "Консоль",
        modal_view_bk: "Просмотр бэкапа",
        log_loading: "⏳ Выполнение xkeen -restart...",
        restart_service_short: "Рестарт",
        update_btn_short: "Обновить",
        nav_editor: "Редактор",
        nav_profiles: "Профили",
        nav_backups: "Бэкапы",
        nav_add: "Добавить",
        proto_detected: "Обнаружен протокол:",
        drop_conf_here: "📂 Нажмите или перетащите .conf файл сюда"
    },
    uk: {
        title: "Mihomo Studio",
        save: "💾 Зберегти",
        restart: "🚀 Рестарт",
        panel: "🌐 Панель",
        update_btn: "🔄 Перевірити оновлення",
        restart_service: "🔄 Рестарт",
        profiles: "Профілі",
        create: "➕ Створити",
        delete: "🗑 Видалити",
        select: "✔",
        download: "💾",
        proxy_mgmt: "Керування",
        add: "➕ Додати",
        edit: "✏️ Замінити",
        rename: "Перейменувати",
        backups: "Бекапи",
        clean: "Очистити",
        keep: "Залишити:",
        theme_dark: "🌑 Темна",
        theme_light: "☀️ Світла",
        theme_midnight: "🌃 Північ",
        theme_cyber: "👾 Кібер",
        toast_saved: "✅ Успішно збережено",
        toast_cleaned: "🧹 Очищено",
        toast_deleted: "🗑 Видалено",
        toast_restored: "♻️ Відновлено",
        toast_added: "✅ Додано",
        toast_renamed: "✏️ Проксі перейменовано",
        toast_updated: "✏️ Дані проксі оновлено",
        toast_checking: "🔍 Перевірка оновлень...",
        toast_restarting: "🔄 Перезапуск сервісу...",
        confirm_switch: "Переключитися на профіль {0}?",
        confirm_del_prof: "Видалити профіль {0}? Ця дія незворотна.",
        confirm_del_bk: "Видалити бекап {0}?",
        confirm_clean: "Залишити тільки {0} останніх бекапів?",
        confirm_restore: "Відновити {0}? Поточний конфіг буде перезаписано.",
        confirm_del_proxy: "Видалити?",
        confirm_replace: "Замінити дані проксі '{0}'?",
        confirm_update: "Перевірити оновлення та встановити?",
        confirm_restart_service: "Перезапустити веб-сервіс Mihomo Studio?",
        prompt_enter_name: "Введіть ім'я!",
        error_invalid_name: "Неприпустиме ім'я!",
        error_exists: "Профіль з таким ім'ям вже існує",
        error_no_proxy_edit: "Виберіть проксі для редагування",
        error_empty_wg: "Конфігурація WireGuard не може бути порожньою.",
        alert_updating: "Оновлення запущено. Сервіс перезапускається...",
        modal_add_proxy: "Додати проксі",
        modal_edit_proxy: "Змінити проксі",
        lbl_vless_link: "Посилання VLESS:",
        lbl_proxy_name: "Ім'я проксі (необов'язково):",
        lbl_wg_conf: "Конфігурація WireGuard:",
        btn_add: "Додати",
        btn_save: "Зберегти",
        btn_cancel: "Скасувати",
        btn_restore: "Відновити",
        btn_close: "Закрити",
        btn_update: "Оновити",
        tab_vless: "VLESS",
        tab_wg: "WireGuard|AmneziaWG",
        lbl_select_edit: "Виберіть проксі для зміни:",
        warn_edit: "⚠️ Дані цього проксі будуть повністю замінені новими!",
        modal_new_prof: "Новий профіль",
        lbl_prof_name: "Ім'я (англ, без пробілів):",
        lbl_content: "Вміст:",
        btn_load_file: "📂 Завантажити файл",
        ph_paste_yaml: "Вставте YAML конфіг сюди...",
        ph_auto_vless: "Автоматично з посилання",
        ph_auto_wg: "Автоматично з Endpoint",
        ph_paste_conf: "Вставте вміст .conf файлу сюди...",
        modal_groups: "Додати в групи:",
        btn_sel_all: "☑ Обрати всі",
        btn_sel_none: "☐ Зняти всі",
        modal_del_proxy: "Видалити проксі",
        modal_ren_proxy: "Перейменувати проксі",
        lbl_sel_ren: "Виберіть проксі для перейменування:",
        lbl_new_name: "Нове ім'я:",
        ph_new_name: "Введіть нове ім'я",
        btn_rename: "Перейменувати",
        modal_console: "Консоль",
        modal_view_bk: "Перегляд бекапу",
        log_loading: "⏳ Виконання xkeen -restart...",
        restart_service_short: "Рестарт",
        update_btn_short: "Оновити",
        nav_editor: "Редактор",
        nav_profiles: "Профілі",
        nav_backups: "Бекапи",
        nav_add: "Додати",
        proto_detected: "Виявлено протокол:",
        drop_conf_here: "📂 Натисніть або перетягніть .conf файл сюди"
    },
    en: {
        title: "Mihomo Studio",
        save: "💾 Save",
        restart: "🚀 Restart",
        panel: "🌐 Panel",
        update_btn: "🔄 Check for updates",
        restart_service: "🔄 Restart",
        profiles: "Profiles",
        create: "➕ Create",
        delete: "🗑 Delete",
        select: "✔",
        download: "💾",
        proxy_mgmt: "Management",
        add: "➕ Add",
        edit: "✏️ Replace",
        rename: "Rename",
        backups: "Backups",
        clean: "Clean",
        keep: "Keep:",
        theme_dark: "🌑 Dark",
        theme_light: "☀️ Light",
        theme_midnight: "🌃 Midnight",
        theme_cyber: "👾 Cyber",
        toast_saved: "✅ Saved successfully",
        toast_cleaned: "🧹 Cleaned",
        toast_deleted: "🗑 Deleted",
        toast_restored: "♻️ Restored",
        toast_added: "✅ Added",
        toast_renamed: "✏️ Proxy renamed",
        toast_updated: "✏️ Proxy data updated",
        toast_checking: "🔍 Checking for updates...",
        toast_restarting: "🔄 Restarting service...",
        confirm_switch: "Switch to profile {0}?",
        confirm_del_prof: "Delete profile {0}? This action is irreversible.",
        confirm_del_bk: "Delete backup {0}?",
        confirm_clean: "Keep only the last {0} backups?",
        confirm_restore: "Restore {0}? Current config will be overwritten.",
        confirm_del_proxy: "Delete?",
        confirm_replace: "Replace data for proxy '{0}'?",
        confirm_update: "Check for updates and install?",
        confirm_restart_service: "Restart Mihomo Studio web service?",
        prompt_enter_name: "Enter name!",
        error_invalid_name: "Invalid name!",
        error_exists: "Profile with this name already exists",
        error_no_proxy_edit: "Select a proxy to edit",
        error_empty_wg: "WireGuard configuration cannot be empty.",
        alert_updating: "Update started. Service is restarting...",
        modal_add_proxy: "Add Proxy",
        modal_edit_proxy: "Edit Proxy",
        lbl_vless_link: "VLESS Link:",
        lbl_proxy_name: "Proxy Name (optional):",
        lbl_wg_conf: "WireGuard Config:",
        btn_add: "Add",
        btn_save: "Save",
        btn_cancel: "Cancel",
        btn_restore: "Restore",
        btn_close: "Close",
        btn_update: "Update",
        tab_vless: "VLESS",
        tab_wg: "WireGuard|AmneziaWG",
        lbl_select_edit: "Select proxy to replace:",
        warn_edit: "⚠️ This proxy's data will be fully replaced!",
        modal_new_prof: "New Profile",
        lbl_prof_name: "Name (English, no spaces):",
        lbl_content: "Content:",
        btn_load_file: "📂 Upload File",
        ph_paste_yaml: "Paste YAML config here...",
        ph_auto_vless: "Automatically from link",
        ph_auto_wg: "Automatically from Endpoint",
        ph_paste_conf: "Paste .conf file content here...",
        modal_groups: "Add to groups:",
        btn_sel_all: "☑ Select All",
        btn_sel_none: "☐ Select None",
        modal_del_proxy: "Delete Proxy",
        modal_ren_proxy: "Rename Proxy",
        lbl_sel_ren: "Select proxy to rename:",
        lbl_new_name: "New Name:",
        ph_new_name: "Enter new name",
        btn_rename: "Rename",
        modal_console: "Console",
        modal_view_bk: "View Backup",
        log_loading: "⏳ Running xkeen -restart...",
        restart_service_short: "Restart",
        update_btn_short: "Update",
        nav_editor: "Editor",
        nav_profiles: "Profiles",
        nav_backups: "Backups",
        nav_add: "Add",
        proto_detected: "Detected Protocol:",
        drop_conf_here: "📂 Click or drop .conf file here"
    }
};

function t(k, ...args) {
    let s = TR[currLang][k] || k;
    args.forEach((a, i) => s = s.replace('{'+i+'}', a));
    return s;
}

function updateClock() {
    var now = new Date();
    var h = String(now.getHours()).padStart(2, '0');
    var m = String(now.getMinutes()).padStart(2, '0');
    var s = String(now.getSeconds()).padStart(2, '0');
    var el = document.getElementById('live-clock');
    if (el) el.innerText = h + ':' + m + ':' + s;
}
updateClock();
setInterval(updateClock, 1000);

function setLang(l) {
    currLang = l;
    localStorage.setItem(LANG_KEY, l);
    document.getElementById('lang-sel').value = l;

    document.querySelectorAll('[data-i18n]').forEach(e => {
        let k = e.getAttribute('data-i18n');
        if(TR[l][k]) e.innerText = TR[l][k];
    });
    document.querySelectorAll('[data-i18n-ph]').forEach(e => {
        let k = e.getAttribute('data-i18n-ph');
        if(TR[l][k]) e.placeholder = TR[l][k];
    });

    if(isEditMode) document.getElementById('proxyModalTitle').innerText = TR[l].modal_edit_proxy;
    else document.getElementById('proxyModalTitle').innerText = TR[l].modal_add_proxy;
}

function openPanel() {
    var url = window.location.protocol + "//" + window.location.host + "/mihomo_panel/ui/";
    window.open(url, '_blank');
}
ed.setValue(initialConfig); ed.clearSelection();

function fmtLog(raw) {
    if(!raw) return '<div class="log-line" style="color:var(--txt-sec)">Log empty</div>';
    return raw.split('\n').map(l => {
        if(!l.trim()) return "";
        l = l.replace(/\x1b\[32m/g, '<span style="color:#10b981">')
             .replace(/\x1b\[33m/g, '<span style="color:#f59e0b">')
             .replace(/\x1b\[31m/g, '<span style="color:#ef4444">')
             .replace(/\x1b\[0m/g, '</span>');
        var m = l.match(/time="(.*?)"\s+level=(\w+)\s+msg="(.*)"/);
        if(m) {
            var ts = new Date(m[1]).toLocaleTimeString();
            var lvl = m[2].toUpperCase();
            var txt = m[3];
            var cls = 'color:#38bdf8';
            if(lvl==='WARN'||lvl==='WARNING') cls='color:#f59e0b';
            if(lvl==='ERROR'||lvl==='FATAL') cls='color:#ef4444';
            return `<div class="log-line"><span style="color:var(--txt-sec)">[${ts}]</span> <span style="${cls}">[${lvl}]</span> ${txt}</div>`;
        }
        return `<div class="log-line">${l}</div>`;
    }).join('');
}

function detectProtocolFromWg(conf) {
    if (!conf || !conf.trim()) return null;
    var low = conf.toLowerCase();
    if (low.includes('headerprotectionkey') || low.includes('header-protection-key') ||
        low.includes('randomtrailers') || low.includes('random-trailers') ||
        low.includes('disablecookies') || low.includes('disable-cookies') ||
        low.includes('contentpaddingaddition') || low.includes('content-padding-addition') ||
        low.includes('rekeyaftertime') || low.includes('rekey-after-time')) {
        return { name: 'AmneziaWG v3.1', cls: 'badge-awg3' };
    }
    if (low.includes('s1') || low.includes('s2') || low.includes('s3') || low.includes('s4') ||
        low.includes('h1') || low.includes('h2') || low.includes('h3') || low.includes('h4') ||
        low.includes('i1') || low.includes('i2') || low.includes('j1') || low.includes('itime')) {
        return { name: 'AmneziaWG v1.5/2.0', cls: 'badge-awg2' };
    }
    if (low.includes('jc') || low.includes('jmin') || low.includes('jmax')) {
        return { name: 'AmneziaWG v1.0', cls: 'badge-awg1' };
    }
    if (low.includes('[interface]') || low.includes('privatekey') || low.includes('endpoint')) {
        return { name: 'WireGuard Classic', cls: 'badge-wg' };
    }
    return null;
}

function detectProtocolFromVless(link) {
    if (!link || !link.trim().startsWith('vless://')) return null;
    var low = link.toLowerCase();
    if (low.includes('security=reality') || low.includes('pbk=')) {
        return { name: 'VLESS Reality', cls: 'badge-reality' };
    }
    if (low.includes('type=ws') || low.includes('security=tls')) {
        return { name: 'VLESS WebSocket', cls: 'badge-ws' };
    }
    if (low.includes('type=grpc')) {
        return { name: 'VLESS gRPC', cls: 'badge-grpc' };
    }
    return { name: 'VLESS', cls: 'badge-vless' };
}

function updateProtocolBadge(type) {
    var badgeRow = document.getElementById('proto-badge-row');
    var badge = document.getElementById('proto-badge');
    var info = null;

    if (type === 'vless') {
        var link = document.getElementById('vlessLink').value;
        info = detectProtocolFromVless(link);
    } else if (type === 'wg') {
        var conf = document.getElementById('wgConfig').value;
        info = detectProtocolFromWg(conf);
    }

    if (info) {
        badge.className = 'badge-proto ' + info.cls;
        badge.innerText = info.name;
        badgeRow.style.display = 'flex';
    } else {
        badgeRow.style.display = 'none';
    }
}

function extractWgName(conf) {
    if (!conf || !conf.trim()) return '';
    var lines = conf.split(/\r?\n/);
    var firstLineComment = null;
    for (var i = 0; i < lines.length; i++) {
        var l = lines[i].trim();
        if (i === 0 && l.startsWith('#') && l.length > 2) {
            firstLineComment = l.substring(1).trim();
        }
        var m = l.match(/^endpoint\s*=\s*(.+)$/i);
        if (m && m[1]) {
            var ep = m[1].trim();
            if (firstLineComment) return firstLineComment;
            if (ep.startsWith('[') && ep.includes(']:')) {
                return 'WG_' + ep.split(']:')[0].substring(1);
            } else if (ep.includes(':')) {
                return 'WG_' + ep.split(':')[0].trim();
            } else {
                return 'WG_' + ep;
            }
        }
    }
    return firstLineComment || '';
}

document.getElementById('vlessLink').addEventListener('input', function() {
    updateProtocolBadge('vless');
    if(isEditMode) return;
    var link = this.value.trim();
    if (link.startsWith("vless://") && link.includes("#")) {
        var name = link.split('#')[1];
        if(name) {
            document.getElementById('vlessProxyName').value = decodeURIComponent(name).trim();
        }
    }
});

document.getElementById('wgConfig').addEventListener('input', function() {
    updateProtocolBadge('wg');
    if(isEditMode) return;
    var nameField = document.getElementById('wgProxyName');
    var autoName = extractWgName(this.value);
    if (autoName && (!nameField.value || nameField.value.startsWith('WG_'))) {
        nameField.value = autoName;
    }
});

function handleDragOver(e) {
    e.preventDefault();
    e.stopPropagation();
    document.getElementById('wgDropZone').classList.add('dragover');
}

function handleDragLeave(e) {
    e.preventDefault();
    e.stopPropagation();
    document.getElementById('wgDropZone').classList.remove('dragover');
}

function handleFileDrop(e) {
    e.preventDefault();
    e.stopPropagation();
    document.getElementById('wgDropZone').classList.remove('dragover');
    var files = e.dataTransfer.files;
    if (files.length > 0) {
        var f = files[0];
        var r = new FileReader();
        r.onload = function(evt) {
            var content = evt.target.result;
            document.getElementById('wgConfig').value = content;
            if (!isEditMode) {
                var extracted = extractWgName(content);
                if (!extracted) {
                    var fname = f.name.replace(/\.[^/.]+$/, "").replace(/[^a-zA-Z0-9_-]/g, "_");
                    if (fname) extracted = fname;
                }
                if (extracted) document.getElementById('wgProxyName').value = extracted;
            }
            updateProtocolBadge('wg');
        };
        r.readAsText(f);
    }
}

function switchMobileView(view) {
    currentMobileView = view;
    localStorage.setItem(MOB_KEY, view);
    document.querySelectorAll('.mob-tab').forEach(b => b.classList.remove('active'));
    var tabBtn = document.getElementById('mtab-' + view);
    if (tabBtn) tabBtn.classList.add('active');

    var edElem = document.getElementById('ed');
    var sbElem = document.querySelector('.sb');
    var secProf = document.getElementById('sec-profiles');
    var secMgmt = document.getElementById('sec-proxy-mgmt');
    var secBk = document.getElementById('sec-backups');

    if (window.innerWidth <= 768) {
        if (view === 'ed') {
            edElem.style.display = 'block';
            sbElem.style.display = 'none';
            ed.resize();
        } else if (view === 'prof') {
            edElem.style.display = 'none';
            sbElem.style.display = 'flex';
            if (secProf) secProf.style.display = 'flex';
            if (secMgmt) secMgmt.style.display = 'flex';
            if (secBk) secBk.style.display = 'none';
        } else if (view === 'bk') {
            edElem.style.display = 'none';
            sbElem.style.display = 'flex';
            if (secProf) secProf.style.display = 'none';
            if (secMgmt) secMgmt.style.display = 'none';
            if (secBk) secBk.style.display = 'flex';
        }
    } else {
        edElem.style.display = 'block';
        sbElem.style.display = 'flex';
        if (secProf) secProf.style.display = 'flex';
        if (secMgmt) secMgmt.style.display = 'flex';
        if (secBk) secBk.style.display = 'flex';
        ed.resize();
    }
}

// Initialize mobile view immediately on load
switchMobileView(currentMobileView);
window.addEventListener('resize', function() {
    switchMobileView(currentMobileView);
});

function closeM(i){document.getElementById(i).style.display='none'}
function showToast(msg){ 
    var tBox=document.getElementById('toast'); 
    var tMsg=document.getElementById('toast-msg');
    tMsg.innerText=msg; 
    tBox.style.display='flex'; 
    setTimeout(function(){tBox.style.display='none'}, 3000);
}

function setTheme(t) {
    document.body.className = t;
    localStorage.setItem(THM_KEY, t);
    document.getElementById('theme-sel').value = t;
    var aceT = 'ace/theme/monokai';
    var edBg = '#0b0f19';
    if(t === 'light') { aceT = 'ace/theme/chrome'; edBg = '#f8fafc'; }
    if(t === 'midnight') { aceT = 'ace/theme/tomorrow_night_blue'; edBg = '#020617'; }
    if(t === 'cyber') { aceT = 'ace/theme/terminal'; edBg = '#000000'; }
    ed.setTheme(aceT);
    document.getElementById('ed').style.background = edBg;
    document.getElementById('ed').style.backdropFilter = 'none';
}
var savedTheme = localStorage.getItem(THM_KEY) || 'dark';
setTheme(savedTheme);

var savedLang = localStorage.getItem(LANG_KEY) || 'ru';
setLang(savedLang);

function getProxiesList() {
    var c = ed.getValue();
    var prs = [];
    var lines = c.split('\n');
    var inP = false;
    for(var i=0; i<lines.length; i++) {
        var l = lines[i];
        if(l.trim().startsWith('proxies:')) { inP = true; continue; }
        if(inP && l.length > 0 && !l.startsWith(' ') && !l.startsWith('\t') && !l.startsWith('#')) { inP = false; break; }
        if(inP && l.trim().startsWith('- name:')) {
            var m = l.match(/- name:\s*["']?([^"']+)["']?/);
            if(m && m[1]) prs.push(m[1].trim());
        }
    }
    return prs;
}

function openAddProxyModal() {
    isEditMode = false;
    document.getElementById('proxyModalTitle').innerText = t('modal_add_proxy');
    document.querySelector('[data-i18n="tab_vless"]').innerText = t('tab_vless');
    document.querySelector('[data-i18n="tab_wg"]').innerText = t('tab_wg');
    document.getElementById('edit-proxy-container').style.display = 'none';
    document.getElementById('vless-name-block').style.display = 'block';
    document.getElementById('wg-name-block').style.display = 'block';
    document.getElementById('proto-badge-row').style.display = 'none';

    document.getElementById('vlessLink').value = '';
    document.getElementById('vlessProxyName').value = '';
    document.getElementById('wgConfig').value = '';
    document.getElementById('wgProxyName').value = '';

    document.getElementById('addProxyModal').style.display = 'flex';
}

function openEditProxyModal() {
    isEditMode = true;
    document.getElementById('proxyModalTitle').innerText = t('modal_edit_proxy');
    document.querySelector('[data-i18n="tab_vless"]').innerText = t('tab_vless');
    document.querySelector('[data-i18n="tab_wg"]').innerText = t('tab_wg');
    document.getElementById('edit-proxy-container').style.display = 'block';
    document.getElementById('vless-name-block').style.display = 'none';
    document.getElementById('wg-name-block').style.display = 'none';
    document.getElementById('proto-badge-row').style.display = 'none';

    var prs = getProxiesList();
    var sel = document.getElementById('edit-proxy-sel');
    sel.innerHTML = '';
    if(prs.length === 0) {
        var o = document.createElement('option');
        o.text = "---";
        sel.add(o);
        sel.disabled = true;
    } else {
        sel.disabled = false;
        prs.forEach(p => {
            var o = document.createElement('option');
            o.text = p;
            sel.add(o);
        });
    }

    document.getElementById('vlessLink').value = '';
    document.getElementById('wgConfig').value = '';

    document.getElementById('addProxyModal').style.display = 'flex';
}

function switchTab(evt, tabName) {
    var i, tabcontent, tablinks;
    tabcontent = document.getElementsByClassName("tab-content");
    for (i = 0; i < tabcontent.length; i++) {
        tabcontent[i].classList.remove("active");
    }
    tablinks = document.getElementsByClassName("modal-tabs")[0].getElementsByTagName("button");
    for (i = 0; i < tablinks.length; i++) {
        tablinks[i].classList.remove("active");
    }
    document.getElementById(tabName).classList.add("active");
    evt.currentTarget.classList.add("active");
    if (tabName === 'vlessTab') updateProtocolBadge('vless');
    else if (tabName === 'wgTab') updateProtocolBadge('wg');
}

function loadWgFile(input) {
    var f = input.files[0];
    if (!f) return;
    var r = new FileReader();
    r.onload = function(e) {
        var content = e.target.result;
        document.getElementById('wgConfig').value = content;
        if (!isEditMode) {
            var extracted = extractWgName(content);
            if (!extracted) {
                var fname = f.name.replace(/\.[^/.]+$/, "").replace(/[^a-zA-Z0-9_-]/g, "_");
                if (fname) extracted = fname;
            }
            if (extracted) document.getElementById('wgProxyName').value = extracted;
        }
        updateProtocolBadge('wg');
    };
    r.readAsText(f);
    input.value = '';
}

function addWireguard() {
    var conf = document.getElementById('wgConfig').value;
    var name = ''; 

    if(isEditMode) {
        name = document.getElementById('edit-proxy-sel').value;
        if(!name || document.getElementById('edit-proxy-sel').disabled) return alert(t('error_no_proxy_edit'));
    } else {
        name = document.getElementById('wgProxyName').value.trim();
    }

    if (!conf) return alert(t('error_empty_wg'));

    var p = new URLSearchParams();
    p.append('act', 'add_wireguard');
    p.append('config_text', conf);
    if (name) p.append('proxy_name', name);

    fetch('/', { method: 'POST', body: p })
        .then(r => r.json())
        .then(d => {
            if (d.error) {
                alert(d.error);
            } else {
                if(isEditMode) {
                   replaceProxyData(name, d.yaml);
                } else {
                   pData = d;
                   closeM('addProxyModal');
                   showG();
                }
            }
        })
        .catch(e => alert("Error: " + e));
}

function parseVless(){
    var link = document.getElementById('vlessLink').value;
    var name = '';
    if(isEditMode) {
        name = document.getElementById('edit-proxy-sel').value;
        if(!name || document.getElementById('edit-proxy-sel').disabled) return alert(t('error_no_proxy_edit'));
    } else {
        name = document.getElementById('vlessProxyName').value.trim();
    }

    if(!link.startsWith("vless://")) return alert("Only vless:// supported");
    var p = new URLSearchParams();
    p.append('act', 'parse');
    p.append('link', link);
    if (name) p.append('proxy_name', name);

    fetch('/', {method:'POST', body:p})
        .then(r=>r.json())
        .then(d=>{
            if(d.error) alert(d.error);
            else {
                if(isEditMode) {
                    replaceProxyData(name, d.yaml);
                } else {
                    pData = d;
                    closeM('addProxyModal');
                    showG();
                }
            }
        })
        .catch(e => alert("Error: " + e));
}

function showG(){
    var val = ed.getValue(), grps = [], inG = false, lines = val.split('\n');
    for(var i = 0; i < lines.length; i++){
        var l = lines[i];
        if(l.trim().startsWith('proxy-groups:')) { inG = true; continue; }
        if(inG && l.length > 0 && !l.startsWith(' ') && !l.startsWith('\t') && !l.startsWith('#')) { inG = false; break; }
        if(inG && l.trim().startsWith('- name:')){
            var m = l.match(/- name:\s*["']?([^"']+)["']?/);
            if(m && m[1]) grps.push(m[1].trim());
        }
    }
    var h = '';
    grps.forEach((g, idx) => {
        var id = 'grp_' + idx;
        h += `<div class="g-item"><input type="checkbox" id="${id}" value="${g}"><label for="${id}">${g}</label></div>`;
    });
    var container = document.getElementById('g-cnt');
    container.innerHTML = h;
    var sv = JSON.parse(localStorage.getItem(GRP_KEY));
    if(sv && Array.isArray(sv)){
        var cbs = container.querySelectorAll('input[type=checkbox]');
        cbs.forEach(cb => { if(sv.includes(cb.value)) cb.checked = true; });
    }
    document.getElementById('m-grp').style.display = 'flex';
}

function tgGrp(v){
    document.getElementById('g-cnt').querySelectorAll('input[type=checkbox]').forEach(c=>c.checked=v);
}

function applyVless(){
    if(!pData) return;
    var cbs=document.getElementById('g-cnt').querySelectorAll('input[type=checkbox]:checked'), sel=[];
    cbs.forEach(c=>sel.push(c.value));
    localStorage.setItem(GRP_KEY, JSON.stringify(sel));

    var p = new URLSearchParams();
    p.append('act', 'apply_insert');
    p.append('content', ed.getValue());
    p.append('proxy_name', pData.name);
    p.append('proxy_yaml', pData.yaml);
    p.append('targets', JSON.stringify(sel));

    fetch('/', {method:'POST', body:p})
        .then(r=>r.json())
        .then(d=>{
            ed.setValue(d.new_content);
            ed.clearSelection();
            closeM('m-grp');
            pData = null;
            showToast(t('toast_added'));
        });
}

function replaceProxyData(targetName, newYaml) {
    var p = new URLSearchParams();
    p.append('act', 'replace_proxy');
    p.append('content', ed.getValue());
    p.append('target_name', targetName);
    p.append('new_yaml', newYaml);

    fetch('/', {method:'POST', body:p})
        .then(r=>r.json())
        .then(d=>{
            if(d.error) {
                alert('Ошибка: ' + d.error);
            } else {
                ed.setValue(d.new_content);
                ed.clearSelection();
                closeM('addProxyModal');
                showToast(t('toast_updated'));
            }
        });
}

function save(mode){
    var c = ed.getValue();
    var p = new URLSearchParams(); p.append('act', mode); p.append('content', c);
    if(mode === 'restart') {
        document.getElementById('cons').innerHTML = '<div style="padding:20px;text-align:center;color:var(--txt-sec)">' + t('log_loading') + '</div>';
        document.getElementById('m-con').style.display = 'flex'; 
    }
    fetch('/', { method: 'POST', body: p })
        .then(r => r.json())
        .then(d => {
            if(mode === 'save'){
                showToast(t('toast_saved'));
                if(d.backups) document.getElementById('bk-list').innerHTML = d.backups;
            } else {
                document.getElementById('cons').innerHTML = fmtLog(d.log);
                document.getElementById('cons').scrollTop = document.getElementById('cons').scrollHeight;
                document.getElementById('m-con').style.display = 'flex';
            }
        })
        .catch(e => {
            if(mode === 'restart') {
                document.getElementById('cons').innerHTML += `<div style="color:#ef4444;padding:10px">Error: ${e}</div>`;
            } else {
                alert("Error: " + e);
            }
        });
}

function updateStudio() {
    if (!confirm(t('confirm_update'))) return;
    showToast(t('toast_checking'));
    document.getElementById('cons').innerHTML = '<div style="padding:20px;text-align:center;color:var(--txt-sec)">🔍 Проверка обновлений...</div>';
    document.getElementById('m-con').style.display = 'flex';
    var p = new URLSearchParams();
    p.append('act', 'update_service');
    fetch('/', { method: 'POST', body: p })
        .then(r => r.json())
        .then(d => {
            document.getElementById('cons').innerHTML = fmtLog(d.log);
            document.getElementById('cons').scrollTop = document.getElementById('cons').scrollHeight;
        })
        .catch(e => {
            document.getElementById('cons').innerHTML = `<div style="color:#ef4444;padding:10px">Error: ${e}</div>`;
        });
}

function restartService() {
    if (!confirm(t('confirm_restart_service'))) return;
    showToast(t('toast_restarting'));
    var p = new URLSearchParams();
    p.append('act', 'restart_service');
    fetch('/', { method: 'POST', body: p })
        .then(r => r.json())
        .then(d => {
            setTimeout(function() {
                location.reload();
            }, 3000);
        })
        .catch(e => {
            setTimeout(function() {
                location.reload();
            }, 3000);
        });
}

function switchProf(){
    var v=document.getElementById('prof-sel').value;
    if(confirm(t('confirm_switch', v))) {
        var p=new URLSearchParams(); p.append('act', 'switch_prof'); p.append('name', v);
        fetch('/', {method:'POST', body:p}).then(r=>r.json()).then(d=>{
            if(d.error) alert(d.error);
            else location.reload();
        });
    }
}

function downloadProf(){
    var v=document.getElementById('prof-sel').value;
    window.location.href = '/?download_profile=' + encodeURIComponent(v);
}

function openAddProf(){
    document.getElementById('np-name').value='';
    document.getElementById('np-content').value='';
    document.getElementById('m-add-prof').style.display='flex';
}

function loadProfFile(inp){
    var f = inp.files[0];
    if(!f) return;
    var r = new FileReader();
    r.onload = function(e){
        document.getElementById('np-content').value = e.target.result;
        var name = f.name.replace(/\.[^/.]+$/, "");
        name = name.replace(/[^a-zA-Z0-9_-]/g, "_");
        document.getElementById('np-name').value = name;
    };
    r.readAsText(f);
}

function saveNewProf(){
    var n=document.getElementById('np-name').value.trim();
    var c=document.getElementById('np-content').value;
    if(!n) return alert(t('prompt_enter_name'));
    if(!/^[a-zA-Z0-9_-]+$/.test(n)) return alert(t('error_invalid_name'));

    var sel = document.getElementById('prof-sel');
    for(var i=0; i<sel.options.length; i++){
        if(sel.options[i].value === n) return alert(t('error_exists'));
    }

    var p=new URLSearchParams();
    p.append('act', 'add_prof');
    p.append('name', n);
    p.append('content', c);
    fetch('/', {method:'POST', body:p}).then(r=>r.json()).then(d=>{
        if(d.error) alert(d.error);
        else location.reload();
    });
}

function delProf(){
    var v=document.getElementById('prof-sel').value;
    var sel = document.getElementById('prof-sel');
    if(sel.options.length <= 1) return alert("Cannot delete the only profile!");

    if(confirm(t('confirm_del_prof', v))){
        var p=new URLSearchParams(); p.append('act', 'del_prof'); p.append('name', v);
        fetch('/', {method:'POST', body:p}).then(r=>r.json()).then(d=>{
            if(d.error) alert(d.error);
            else location.reload();
        });
    }
}

function cleanBackups(){
    var lim=document.getElementById('bk-lim').value;
    localStorage.setItem(LIM_KEY, lim);
    if(confirm(t('confirm_clean', lim))){
        var p=new URLSearchParams(); p.append('act', 'clean_backups'); p.append('limit', lim);
        fetch('/', {method:'POST', body:p}).then(r=>r.json()).then(d=>{
            document.getElementById('bk-list').innerHTML=d.backups;
            showToast(t('toast_cleaned'));
        });
    }
}
var svLim = localStorage.getItem(LIM_KEY);
if(svLim) document.getElementById('bk-lim').value = svLim;

function delBackup(f){
    if(confirm(t('confirm_del_bk', f))){
        var p=new URLSearchParams(); p.append('act', 'del_backup'); p.append('f', f);
        fetch('/', {method:'POST', body:p}).then(r=>r.json()).then(d=>{
            document.getElementById('bk-list').innerHTML=d.backups;
            showToast(t('toast_deleted'));
        });
    }
}

function viewBackup(f) {
    var p = new URLSearchParams();
    p.append('act', 'view_backup');
    p.append('f', f);
    fetch('/', { method: 'POST', body: p })
        .then(r => r.json())
        .then(d => {
            if (d.error) {
                alert(d.error);
            } else {
                document.getElementById('bk-content').innerText = d.content;
                document.getElementById('m-bk-view').style.display = 'flex';
                document.getElementById('bk-view-title').innerText = t('modal_view_bk') + ': ' + f;
                document.getElementById('btn-bk-restore').onclick = function() {
                    closeM('m-bk-view');
                    restoreBackup(f);
                };
            }
        });
}

function restoreBackup(f){
    if(confirm(t('confirm_restore', f))){
        var p=new URLSearchParams(); p.append('act', 'rest'); p.append('f', f);
        fetch('/', {method:'POST', body:p}).then(()=>location.reload());
    }
}

function showDel(){
    var prs = getProxiesList();
    var s = document.getElementById('sel-del');
    s.innerHTML = '';
    prs.forEach(p => { var o = document.createElement('option'); o.text = p; s.add(o); });
    document.getElementById('m-del').style.display = 'flex';
}

function doDel(){
    var nm=document.getElementById('sel-del').value;
    if(!nm) return;
    if(!confirm(t('confirm_del_proxy') + " " + nm + "?")) return;
    closeM('m-del');
    var ls=ed.getValue().split(/\r?\n/);
    var nls=[], inP=false, delB=false, bInd=-1;
    for(var l of ls){
        if(l.match(/^proxies:/)){inP=true;nls.push(l);continue}
        if(inP && l.match(/^[a-zA-Z]/) && !l.match(/^proxies:/)){inP=false;delB=false}
        if(inP){
            var df=l.match(/^(\s+)-\s+name:\s+(.*)/);
            if(df){
                var ind=df[1].length, pn=df[2].trim().replace(/^['"]|['"]$/g,'');
                if(pn===nm){delB=true;bInd=ind;continue}else if(delB)delB=false;
            } else if(delB){
                var ci=l.search(/\S/); if(l.trim()===''||ci>bInd)continue; else delB=false;
            }
        }
        if(delB)continue;

        if(l.match(/^\s*proxies:\s*\[.*\]/)){
             var st = l.indexOf('['); var en = l.lastIndexOf(']');
             var pre = l.substring(0, st+1); var suf = l.substring(en);
             var mid = l.substring(st+1, en);
             var parts = mid.split(',');
             var res = []; var changed = false;
             for(var p of parts){
                 var clean = p.trim().replace(/^['"]|['"]$/g, '');
                 if(clean === nm){ changed = true; } else { res.push(p); }
             }
             if(changed){ nls.push(pre + res.join(',') + suf); continue; }
        }

        var rm=l.match(/^\s+-\s+(?:"([^"]+)"|'([^']+)'|([^"':]+))\s*$/);
        if(rm){var rn=rm[1]||rm[2]||rm[3];if(rn&&rn.trim()===nm)continue}
        nls.push(l);
    }
    ed.setValue(nls.join('\n'));
    ed.clearSelection();
    showToast(t('toast_deleted'));
}

function showRename() {
    var proxies = getProxiesList();
    var sel = document.getElementById('sel-ren-proxy');
    sel.innerHTML = '';
    proxies.forEach(p => {
        var o = document.createElement('option');
        o.text = p;
        sel.add(o);
    });

    if (proxies.length > 0) {
        document.getElementById('inp-ren-newname').value = proxies[0];
    } else {
        document.getElementById('inp-ren-newname').value = '';
    }

    sel.onchange = function() {
        document.getElementById('inp-ren-newname').value = this.value;
    };

    document.getElementById('m-ren').style.display = 'flex';
}

function doRename() {
    var oldName = document.getElementById('sel-ren-proxy').value;
    var newName = document.getElementById('inp-ren-newname').value.trim();

    if (!oldName) return alert('Выберите прокси для переименования');
    if (!newName) return alert('Введите новое имя');
    if (oldName === newName) {
        closeM('m-ren');
        return;
    }

    var content = ed.getValue();
    var params = new URLSearchParams();
    params.append('act', 'rename_proxy');
    params.append('old_name', oldName);
    params.append('new_name', newName);
    params.append('content', content);

    fetch('/', { method: 'POST', body: params })
        .then(r => r.json())
        .then(d => {
            if (d.error) {
                alert('Ошибка: ' + d.error);
            } else {
                ed.setValue(d.new_content);
                ed.clearSelection();
                closeM('m-ren');
                showToast(t('toast_renamed'));
            }
        })
        .catch(e => alert("Network error: " + e));
}
</script></body></html>"""


class H(http.server.SimpleHTTPRequestHandler):
    def end_headers(s):
        s.send_header('Cache-Control', 'no-store, no-cache, must-revalidate');
        s.send_header('Pragma',
                      'no-cache');
        s.send_header(
            'Expires', '0');
        super().end_headers()

    def get_bks(s):
        b = ""
        # Теперь берем ВСЕ бэкапы, а не только первые 10
        for f in sorted(glob.glob(BACKUP_DIR + "/*.yaml"), key=os.path.getmtime, reverse=True):
            n = os.path.basename(f);
            t = datetime.fromtimestamp(os.path.getmtime(f)).strftime("%d.%m %H:%M")
            b += f'''<div class="bk-item">
                    <div><b>{n}</b><span style="font-size:11px;color:var(--txt-sec)">{t}</span></div>
                    <div class="bk-btns">
                        <button onclick="viewBackup('{n}')" class="btn-u" title="Просмотр">👁️</button>
                        <button onclick="restoreBackup('{n}')" class="btn-g" title="Восстановить">↺</button>
                        <button onclick="delBackup('{n}')" class="btn-d" title="Удалить">✕</button>
                    </div>
                   </div>'''
        if not b: b = '<div style="color:var(--txt-sec);font-size:13px;text-align:center;padding:15px">Нет бэкапов</div>'
        return b

    def get_prof_opts(s):
        curr = ""
        if os.path.exists(CONFIG_PATH):
            real = os.path.realpath(CONFIG_PATH)
            curr = os.path.splitext(os.path.basename(real))[0]

        opts = ""
        files = sorted(glob.glob(PROFILES_DIR + "/*.yaml"))
        for f in files:
            n = os.path.splitext(os.path.basename(f))[0]
            sel = "selected" if n == curr else ""
            opts += f'<option value="{n}" {sel}>{n}</option>'
        return opts

    def get_panel_port(self):
        panel_port = ''
        try:
            with open(CONFIG_PATH, 'r') as f:
                config_content = f.read()
                # Улучшенный regex для поиска порта (учитывает кавычки и IP)
                # Ищет external-controller: "0.0.0.0:9090" или '127.0.0.1:9090' или просто :9090
                match = re.search(r"external-controller:\s*(?:['\"]?)(?:[^:]*):(\d+)(?:['\"]?)", config_content)
                if match:
                    panel_port = match.group(1)
        except (IOError, FileNotFoundError):
            pass
        return panel_port

    # --- PROXY LOGIC ---
    def proxy_pass(self, method):
        panel_port = self.get_panel_port()
        if not panel_port:
            self.send_error(500, "Panel port not found in config")
            return

        # Strip prefix
        rel_path = self.path.replace('/mihomo_panel/', '', 1)
        target_url = f"http://127.0.0.1:{panel_port}/{rel_path}"

        # Read Body
        content_len = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_len) if content_len > 0 else None

        # Create Request
        try:
            req = urllib.request.Request(target_url, data=body, method=method)
            for k, v in self.headers.items():
                if k.lower() not in ['host', 'origin', 'referer']:
                    req.add_header(k, v)

            # Важно: подменяем Host для корректной работы backend
            req.add_header('Host', f'127.0.0.1:{panel_port}')

            with urllib.request.urlopen(req) as resp:
                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    # Фильтруем CORS заголовки от backend, т.к. мы их сами выставим если надо,
                    # но здесь мы действуем как same-origin
                    if k.lower() not in ['access-control-allow-origin', 'server', 'date']:
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(resp.read())

        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            for k, v in e.headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(e.read())
        except Exception as e:
            # self.send_error(500, str(e))
            pass  # Silent fail to avoid crashing

    def do_GET(s):
        if s.path.startswith('/mihomo_panel/'):
            s.proxy_pass('GET')
            return

        if s.path != '/': return s.send_error(404)
        c = open(CONFIG_PATH).read() if os.path.exists(CONFIG_PATH) else "proxies:\n"

        s.send_response(200);
        s.send_header('Content-type', 'text/html;charset=utf-8');
        s.end_headers()
        out = HTML_TEMPLATE.replace('__JSON_CONTENT__', json.dumps(c)) \
            .replace('__BACKUPS__', s.get_bks()) \
            .replace('__PROFILES__', s.get_prof_opts()) \
            
        s.wfile.write(out.encode('utf-8'))

    def do_POST(s):
        if s.path.startswith('/mihomo_panel/'):
            s.proxy_pass('POST')
            return

        l = int(s.headers['Content-Length']);
        d = s.rfile.read(l).decode('utf-8', 'ignore')
        p = {k: v[0] for k, v in urllib.parse.parse_qs(d).items()};
        a = p.get('act')
        s.send_response(200);
        s.send_header('Content-Type', 'application/json');
        s.end_headers()

        # --- PROFILE ACTIONS ---
        if a == 'switch_prof':
            n = p.get('name')
            target = os.path.join(PROFILES_DIR, n + ".yaml")
            if os.path.exists(target):
                if os.path.exists(CONFIG_PATH) or os.path.islink(CONFIG_PATH):
                    os.unlink(CONFIG_PATH)
                os.symlink(target, CONFIG_PATH)
                s.wfile.write(json.dumps({'status': 'ok'}).encode('utf-8'))
            else:
                s.wfile.write(json.dumps({'error': 'Profile not found'}).encode('utf-8'))
            return

        if a == 'add_prof':
            n = p.get('name')
            c = p.get('content', '')
            target = os.path.join(PROFILES_DIR, n + ".yaml")
            if os.path.exists(target):
                s.wfile.write(json.dumps({'error': 'Профиль с таким именем уже существует'}).encode('utf-8'))
            else:
                with open(target, 'w') as f:
                    f.write(c)
                if not os.path.exists(CONFIG_PATH): os.symlink(target, CONFIG_PATH)
                s.wfile.write(json.dumps({'status': 'ok'}).encode('utf-8'))
            return

        if a == 'del_prof':
            n = p.get('name')
            target = os.path.join(PROFILES_DIR, n + ".yaml")
            real_curr = os.path.realpath(CONFIG_PATH)
            if os.path.realpath(target) == real_curr:
                s.wfile.write(
                    json.dumps({'error': 'Нельзя удалить активный профиль. Сначала переключитесь на другой.'}).encode(
                        'utf-8'))
            elif os.path.exists(target):
                os.remove(target)
                s.wfile.write(json.dumps({'status': 'ok'}).encode('utf-8'))
            else:
                s.wfile.write(json.dumps({'error': 'File not found'}).encode('utf-8'))
            return

        if a == 'get_prof_content':
            n = p.get('name')
            target = os.path.join(PROFILES_DIR, n + ".yaml")
            if os.path.exists(target):
                with open(target, 'r', encoding='utf-8') as f:
                    content = f.read()
                s.wfile.write(json.dumps({'status': 'ok', 'content': content}).encode('utf-8'))
            else:
                s.wfile.write(json.dumps({'error': 'Profile not found'}).encode('utf-8'))
            return

        if a == 'rename_proxy':
            old_name = p.get('old_name')
            new_name = p.get('new_name')
            content = p.get('content', '')
            if not all([old_name, new_name, content]):
                s.wfile.write(json.dumps({'error': 'Missing parameters'}).encode('utf-8'))
                return

            # 1. Замена в определении прокси: - name: "old_name"
            # Regex для поиска `name: 'old_name'`, `name: "old_name"` или `name: old_name`
            # Используем `re.escape` для безопасности
            escaped_old = re.escape(old_name)
            # (?P<quote>['"]?) - захватывает кавычку (если она есть) в группу 'quote'
            # \\1 - ссылается на захваченную кавычку, чтобы заменить на такую же
            pattern_def = r"(name\s*:\s*)(?P<quote>['\"]?)" + escaped_old + r"(?P=quote)"
            # Заменяем, сохраняя оригинальные кавычки
            content = re.sub(pattern_def, r'\g<1>"' + new_name + '"', content, count=1)

            # 2. Замена в списках proxy-groups: - "old_name"
            # Regex для поиска `- 'old_name'`, `- "old_name"` или `- old_name`
            pattern_list = r"(-\s+)(?P<quote>['\"]?)" + escaped_old + r"(?P=quote)"
            content = re.sub(pattern_list, r'\g<1>"' + new_name + '"', content)

            # 3. Замена в Inline Lists: [ ..., "old_name", ... ]
            # Ищем old_name внутри delimiters [ или , с последующим , или ]
            pattern_inline = r"([\[,]\s*)(?P<q>['\"]?)" + escaped_old + r"(?P=q)(\s*[,\]])"
            content = re.sub(pattern_inline, r'\1\g<q>' + new_name + r'\g<q>\3', content)

            s.wfile.write(json.dumps({'status': 'ok', 'new_content': content}).encode('utf-8'))
            return

        # --- EXISTING ACTIONS ---

        if a == 'parse':
            link = p.get('link', '')
            custom_name = p.get('proxy_name')
            d, e = parse_vless(link, custom_name)
            s.wfile.write(json.dumps(d if d else {'error': e}).encode('utf-8'));
            return

        if a == 'add_wireguard':
            config_text = p.get('config_text', '')
            custom_name = p.get('proxy_name')
            if not config_text:
                s.wfile.write(json.dumps({'error': 'Empty config'}).encode('utf-8'))
                return

            proxy_data, err = parse_wireguard(config_text, custom_name)
            if err:
                s.wfile.write(json.dumps({'error': err}).encode('utf-8'))
                return

            s.wfile.write(json.dumps(proxy_data).encode('utf-8'))
            return

        if a == 'apply_insert':
            content = p.get('content', '');
            p_name = p.get('proxy_name', '');
            p_yaml = p.get('proxy_yaml', '');
            targets = json.loads(p.get('targets', '[]'))
            lines = content.splitlines();
            inserted = False
            for i, line in enumerate(lines):
                if line.strip().startswith('proxies:'):
                    blk = p_yaml.splitlines();
                    for bi, bl in enumerate(blk): lines.insert(i + 1 + bi, "  " + bl)
                    inserted = True;
                    break
            if not inserted: lines.append("proxies:"); lines.extend(["  " + l for l in p_yaml.splitlines()])
            uc = insert_proxy_logic("\n".join(lines), p_name, targets)
            s.wfile.write(json.dumps({'new_content': uc}).encode('utf-8'));
            return

        if a == 'replace_proxy':
            target_name = p.get('target_name', '')
            new_yaml = p.get('new_yaml', '')
            content = p.get('content', '')

            new_yaml_lines = new_yaml.splitlines()
            uc = replace_proxy_block(content, target_name, new_yaml_lines)
            s.wfile.write(json.dumps({'new_content': uc}).encode('utf-8'))
            return

        if a == 'clean_backups':
            limit = int(p.get('limit', 5))
            files = sorted(glob.glob(BACKUP_DIR + "/*.yaml"), key=os.path.getmtime, reverse=True)
            if len(files) > limit:
                for f in files[limit:]:
                    try:
                        os.remove(f)
                    except:
                        pass
            s.wfile.write(json.dumps({'backups': s.get_bks()}).encode('utf-8'));
            return

        if a == 'del_backup':
            fname = p.get('f')
            path = os.path.join(BACKUP_DIR, os.path.basename(fname))
            if os.path.exists(path): os.remove(path)
            s.wfile.write(json.dumps({'backups': s.get_bks()}).encode('utf-8'));
            return

        if a == 'rest':
            shutil.copy(os.path.join(BACKUP_DIR, os.path.basename(p.get('f'))), CONFIG_PATH)
            s.wfile.write(json.dumps({'status': 'ok'}).encode('utf-8'));
            return

        if a == 'view_backup':
            fname = p.get('f')
            path = os.path.join(BACKUP_DIR, os.path.basename(fname))
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
                s.wfile.write(json.dumps({'content': content}).encode('utf-8'))
            else:
                s.wfile.write(json.dumps({'error': 'File not found'}).encode('utf-8'))
            return

        if a == 'update_service':
            try:
                output = subprocess.check_output(UPDATE_CMD, shell=True, stderr=subprocess.STDOUT)
                log = output.decode('utf-8', 'ignore')
            except subprocess.CalledProcessError as e:
                log = e.output.decode('utf-8', 'ignore')
            except Exception as e:
                log = str(e)
            s.wfile.write(json.dumps({'log': log}).encode('utf-8'))
            return

        if a == 'restart_service':
            # Отправляем ответ клиенту сразу, так как после перезапуска сервер умрет
            s.wfile.write(json.dumps({'status': 'restarting'}).encode('utf-8'))
            # Запускаем перезапуск в фоне с небольшой задержкой, чтобы успеть отправить ответ
            subprocess.Popen("/opt/etc/init.d/S95mihomo-web restart", shell=True)
            return

        new_c = p.get('content', '').replace('\r\n', '\n')
        if a in ['save', 'restart']:
            if os.path.exists(CONFIG_PATH):
                real_p = os.path.basename(os.path.realpath(CONFIG_PATH))
                prof_n = os.path.splitext(real_p)[0]
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                shutil.copy(CONFIG_PATH, f"{BACKUP_DIR}/{prof_n}_{ts}.yaml")

            with open(CONFIG_PATH, 'w') as f:
                f.write(new_c);
                f.flush();
                os.fsync(f.fileno())

        if a == 'restart':
            my_env = os.environ.copy();
            my_env["TERM"] = "xterm-256color"
            subprocess.run(RESTART_CMD, shell=True, env=my_env)
            log = open(LOG_FILE).read() if os.path.exists(LOG_FILE) else "Log empty"
            s.wfile.write(json.dumps({'log': log}).encode('utf-8'))
        elif a == 'save':
            s.wfile.write(json.dumps(
                {'status': 'ok', 'time': datetime.now().strftime("%H:%M:%S"), 'backups': s.get_bks()}).encode('utf-8'))

    def do_PUT(s):
        if s.path.startswith('/mihomo_panel/'):
            s.proxy_pass('PUT')
            return
        s.send_error(405, "Method Not Allowed")

    def do_DELETE(s):
        if s.path.startswith('/mihomo_panel/'):
            s.proxy_pass('DELETE')
            return
        s.send_error(405, "Method Not Allowed")


if __name__ == '__main__':
    init_system()
    try:
        socketserver.TCPServer.allow_reuse_address = True
        print(f"Mihomo Studio starting on port {PORT}...")
        socketserver.TCPServer(("", PORT), H).serve_forever()
    except Exception as e:
        print(e)

