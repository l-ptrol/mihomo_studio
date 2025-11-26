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

# --- ИНИЦИАЛИЗАЦИЯ ---
if not os.path.exists(BACKUP_DIR): os.makedirs(BACKUP_DIR)
if not os.path.exists(PROFILES_DIR): os.makedirs(PROFILES_DIR)

if os.path.exists(CONFIG_PATH) and not os.path.islink(CONFIG_PATH):
    shutil.move(CONFIG_PATH, os.path.join(PROFILES_DIR, "default.yaml"))
    os.symlink(os.path.join(PROFILES_DIR, "default.yaml"), CONFIG_PATH)
elif not os.path.exists(CONFIG_PATH):
    def_prof = os.path.join(PROFILES_DIR, "default.yaml")
    with open(def_prof, 'w') as f:
        f.write("proxies: []\n")
    os.symlink(def_prof, CONFIG_PATH)


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
                srv, port = srv_port.rsplit(':', 1);
                srv = srv.replace('[', '').replace(']', '')
            else:
                srv, port = srv_port.split(':')
        else:
            return None, "No Port"

        def get(k):
            return params.get(k, [''])[0]

        y = ['- name: "' + name + '"', '  type: vless', '  server: ' + srv, '  port: ' + port, '  uuid: ' + uuid,
             '  udp: true']
        y.append('  network: ' + (get('type') or 'tcp'))
        if get('flow'): y.append('  flow: ' + get('flow'))
        if get('security'):
            y.append('  tls: true')
            if get('security') == 'reality':
                y.extend(['  servername: ' + get('sni'), '  client-fingerprint: ' + (get('fp') or 'chrome'),
                          '  reality-opts:', '    public-key: ' + get('pbk')])
                if get('sid'): y.append('    short-id: ' + get('sid'))
            else:
                if get('sni'): y.append('  servername: ' + get('sni'))
                if get('fp'): y.append('  client-fingerprint: ' + get('fp'))
                if get('alpn'):
                    av = get("alpn").replace(",", '", "')
                    y.append('  alpn: ["' + av + '"]')
        if get('type') == 'ws':
            y.append('  ws-opts:')
            if get('path'): y.append('    path: ' + get('path'))
            if get('host'): y.extend(['    headers:', '      Host: ' + get('host')])
        elif get('type') == 'grpc' and get('serviceName'):
            y.extend(['  grpc-opts:', '    grpc-service-name: ' + get('serviceName')])
        return {"yaml": "\n".join(y), "name": name}, None
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
                if s_name == 'interface' or s_name == 'peer':
                    section = s_name
                else:
                    section = None
                continue

            if section and '=' in line:
                key, val = line.split('=', 1)
                conf[section][key.strip().lower()] = val.strip()

        iface = conf['interface']
        peer = conf['peer']

        if not iface or not peer:
            return None, "Invalid WireGuard config: missing Interface or Peer"

        endpoint = peer.get('endpoint', '')
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

        address_raw = iface.get('address', '')
        if not address_raw: return None, "No Address found"

        ips = [x.strip() for x in address_raw.split(',')]
        ip_v4 = None
        ip_v6 = None

        for ip in ips:
            clean_ip = ip.split('/')[0]
            if ':' in clean_ip:
                if not ip_v6: ip_v6 = clean_ip
            else:
                if not ip_v4: ip_v4 = clean_ip

        if not ip_v4 and not ip_v6:
            return None, "No valid IP address found"

        y = []
        y.append(f'- name: "{name}"')
        y.append(f'  type: wireguard')
        y.append(f'  server: {server}')
        y.append(f'  port: {port}')

        if ip_v4: y.append(f'  ip: {ip_v4}')
        if ip_v6: y.append(f'  ipv6: {ip_v6}')

        pk = iface.get('privatekey')
        if pk: y.append(f'  private-key: {pk}')

        pubk = peer.get('publickey')
        if pubk: y.append(f'  public-key: {pubk}')

        psk = peer.get('presharedkey')
        if psk: y.append(f'  pre-shared-key: {psk}')

        dns_raw = iface.get('dns')
        if dns_raw:
            dns_list = [d.strip() for d in dns_raw.split(',')]
            y.append(f'  dns: {json.dumps(dns_list)}')

        mtu = iface.get('mtu')
        if mtu: y.append(f'  mtu: {mtu}')

        y.append('  udp: true')

        amnezia_keys = ['jc', 'jmin', 'jmax', 's1', 's2', 'h1', 'h2', 'h3', 'h4']
        amn_opts = {}
        for k in amnezia_keys:
            if k in iface:
                val = iface[k]
                if val.isdigit():
                    amn_opts[k] = int(val)

        if amn_opts:
            y.append('  amnezia-wg-option:')
            for k, v in amn_opts.items():
                y.append(f'    {k}: {v}')

        allowed = peer.get('allowedips')
        if allowed:
            al_list = [x.strip() for x in allowed.split(',')]
            y.append(f'  allowed-ips: {json.dumps(al_list)}')

        ka = peer.get('persistentkeepalive')
        if ka:
            y.append(f'  persistent-keepalive: {ka}')

        return {"yaml": "\n".join(y), "name": name}, None

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


HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<title>Mihomo Editor v18.10</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/ace/1.32.7/ace.js"></script>
<style>
:root {
    --bg: #1a1a1a; --bg-sec: #252526; --bg-ter: #2d2d2d;
    --txt: #e0e0e0; --txt-sec: #aaa; --bd: #333;
    --btn-s: #007acc; --btn-r: #2e7d32; --btn-d: #d32f2f; --btn-u: #e6a23c; --btn-g: #555;
    --log-bg: #111; --log-txt: #ccc;
    --comp-h: 36px;
}
body.light {
    --bg: #f5f5f5; --bg-sec: #ffffff; --bg-ter: #e0e0e0;
    --txt: #333; --txt-sec: #666; --bd: #ccc;
    --btn-s: #0078d4; --btn-g: #777; --log-bg: #fff; --log-txt: #222;
}
body.midnight {
    --bg: #0f172a; --bg-sec: #1e293b; --bg-ter: #334155;
    --txt: #f1f5f9; --txt-sec: #94a3b8; --bd: #475569;
    --btn-s: #3b82f6; --btn-u: #f59e0b;
}
body.cyber {
    --bg: #000000; --bg-sec: #111111; --bg-ter: #222222;
    --txt: #00ff00; --txt-sec: #00cc00; --bd: #004400;
    --btn-s: #007700; --btn-r: #00aa00; --btn-d: #aa0000; --btn-u: #aaaa00; --btn-g: #333;
}

body{font-family:'Segoe UI',sans-serif;background:var(--bg);color:var(--txt);margin:0;display:flex;flex-direction:column;height:100vh;overflow:hidden;}
* { box-sizing: border-box; }

.hdr{background:var(--bg-sec);padding:10px 15px;border-bottom:1px solid var(--bd);display:flex;justify-content:space-between;align-items:center;height:45px;flex-shrink:0}
.bar{background:var(--bg-ter);padding:8px 15px;display:flex;gap:10px;border-bottom:1px solid var(--bd);flex-wrap:wrap;flex-shrink:0}

button, input, select, textarea {
    font-family: inherit; font-size: 13px; color: var(--txt);
    border: 1px solid var(--bd); border-radius: 4px;
    background: var(--bg-ter);
    transition: 0.2s; outline: none;
}
button { 
    height: var(--comp-h); padding: 0 15px; cursor: pointer; color: #fff; font-weight: 600; 
    display: flex; align-items: center; justify-content: center; gap: 5px; white-space: nowrap; border: none;
}
input, select {
    height: var(--comp-h); padding: 0 10px; width: 100%;
}
button:hover{filter:brightness(1.1)}
.btn-s{background:var(--btn-s)}.btn-r{background:var(--btn-r)}.btn-d{background:var(--btn-d)}.btn-u{background:var(--btn-u)}.btn-g{background:var(--btn-g)}

.main{display:flex;flex:1;overflow:hidden}
#ed{flex:1;font-size:14px}
.sb{width:320px;background:var(--bg-sec);border-left:1px solid var(--bd);display:flex;flex-direction:column;overflow-y:auto;flex-shrink:0}
.sec{padding:15px;border-bottom:1px solid var(--bd); display:flex; flex-direction:column; gap:8px;}
.sec h3{margin:0 0 5px 0;font-size:14px;color:var(--txt-sec)}

.ovl{position:fixed;top:0;left:0;width:100%;height:100%;background:#000000b3;z-index:999;display:none;justify-content:center;align-items:center;padding:10px}
.mod{background:var(--bg-sec);padding:20px;border-radius:8px;width:100%;max-width:600px;border:1px solid var(--bd);display:flex;flex-direction:column;max-height:90vh}
.mod h3{margin-top:0;color:var(--txt);border-bottom:1px solid var(--bd);padding-bottom:10px}

.bk-item{background:var(--bg-ter);padding:5px 8px;margin-bottom:5px;border:1px solid var(--bd);border-radius:4px;display:flex;justify-content:space-between;align-items:center;height: auto; min-height: 38px;}
.bk-item div:first-child { flex: 1; min-width: 0; padding-right: 5px; display: flex; flex-direction: column; justify-content: center; }
.bk-item b { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: block; }
.bk-btns { display: flex; gap: 4px; flex-shrink: 0; }
.bk-btns button { width: 28px; padding: 0; height: 28px; font-size: 14px; }

#bk-content {
    background: var(--log-bg);
    color: var(--log-txt);
    font-family: 'Consolas', monospace;
    padding: 10px;
    border-radius: 4px;
    border: 1px solid var(--bd);
    white-space: pre-wrap;
    overflow-y: auto;
    flex-grow: 1;
    min-height: 200px;
    max-height: 60vh;
}

.bk-controls {display:flex; gap:8px; align-items:center; background:var(--bg-ter); padding:5px 10px; border-radius:4px; border: 1px solid var(--bd);}
.bk-controls input {width: 50px !important; text-align: center; margin:0; height: 28px; padding: 0;}
.bk-controls span {font-size:12px; color:var(--txt-sec); white-space: nowrap;}
.bk-controls button { height: 28px; font-size: 11px; padding: 0 10px; margin-left: auto; }

.prof-row {display:flex; gap:8px; align-items:center;}
#prof-sel { flex: 1; }
.prof-btns { display: flex; gap: 8px; margin-top: 5px; }
.prof-btns button { flex: 1; }

.proxy-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }

#last-load {
    font-size: 11px;
    color: var(--txt);
    background: var(--bg-ter);
    border: 1px solid var(--bd);
    padding: 2px 10px;
    border-radius: 12px;
    display: inline-flex;
    align-items: center;
    height: 24px;
    font-weight: 600;
}

#cons{background:var(--log-bg);color:var(--log-txt);font-family:'Consolas',monospace;padding:10px;height:350px;overflow:auto;white-space:pre-wrap;font-size:12px;border:1px solid var(--bd);border-radius:4px}
.g-list {display: grid;grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));gap: 8px;overflow-y: auto;padding: 5px;margin-top: 5px;}
.g-item { position: relative; }
.g-item input { position: absolute; opacity: 0; cursor: pointer; height: 0; width: 0; }
.g-item label {display: flex;align-items: center;justify-content: center;background: var(--bg-ter);border: 1px solid var(--bd);border-radius: 4px;padding: 10px 5px;font-size: 12px;color: var(--txt-sec);cursor: pointer;transition: all 0.2s;text-align: center;user-select: none;word-break: break-word;min-height: 35px;}
.g-item input:checked + label {background: var(--btn-s);color: white;border-color: var(--btn-s);font-weight: bold;}
.toast {position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); background: var(--btn-r); color: white; padding: 10px 20px; border-radius: 5px; z-index: 3000; display: none; box-shadow: 0 2px 10px rgba(0,0,0,0.5);}

.modal-tabs { display: flex; border-bottom: 1px solid var(--bd); margin-bottom: 15px; }
.modal-tabs button {
   flex: 1; justify-content: center; background: none; border: none; border-bottom: 2px solid transparent;
   border-radius: 0; padding: 10px; font-size: 14px; color: var(--txt-sec); height: auto;
}
.modal-tabs button.active { color: var(--txt); border-bottom-color: var(--btn-s); font-weight: bold; }
.tab-content { display: none; }
.tab-content.active { display: block; }
.file-drop-zone {
   border: 2px dashed var(--bd); border-radius: 4px; padding: 20px; text-align: center;
   color: var(--txt-sec); cursor: pointer; transition: 0.2s; margin-bottom: 10px;
}
.file-drop-zone:hover { background: var(--bg-ter); border-color: var(--btn-s); }
.file-drop-zone.dragover { background: var(--bg-ter); border-color: var(--btn-s); }

.log-time { color: #888; margin-right: 8px; }
.log-info { color: #2196f3; font-weight: bold; }
.log-warn { color: #ff9800; font-weight: bold; }
.log-err { color: #f44336; font-weight: bold; }
.log-green { color: #4caf50; }
.log-yellow { color: #ffc107; }

@media (max-width: 768px) {
    .main { flex-direction: column; }
    .sb { width: 100%; border-left: none; border-top: 1px solid var(--bd); height: auto; max-height: 45vh; }
    #ed { flex: 1; min-height: 40vh; }
    .bar button, .bar select { flex: 1; justify-content: center; }
    .mod { width: 95%; max-height: 95vh; padding: 15px; }
}
</style>
</head>
<body>
<div class="toast" id="toast" data-i18n="toast_saved">✅ Успешно сохранено</div>
<div class="hdr">
    <div style="display:flex;align-items:center;gap:10px">
        <h2 style="margin:0;color:#4caf50" data-i18n="title">Mihomo Studio</h2>
        <span style="color:var(--txt-sec);font-size:12px">v18.10 Auto-Panel</span>
    </div>
    <div id="last-load">Loaded: __TIME__</div>
</div>
<div class="bar">
    <button onclick="save('save')" class="btn-s" data-i18n="save">💾 Сохранить</button>
    <button onclick="save('restart')" class="btn-r" data-i18n="restart">🚀 Рестарт</button>
    <button onclick="openPanel()" class="btn-g" title="Открыть встроенную панель Mihomo" data-i18n="panel">🌐 Панель</button>

    <div style="display:flex; gap:5px; margin-left:auto;">
        <select id="lang-sel" onchange="setLang(this.value)" style="width:100px; padding:0 5px;">
            <option value="ru">🇷🇺 RU</option>
            <option value="en">🇺🇸 EN</option>
            <option value="uk">🇺🇦 UA</option>
        </select>
        <select id="theme-sel" onchange="setTheme(this.value)" style="width:120px; padding:0 5px;">
            <option value="dark" data-i18n="theme_dark">🌑 Dark</option>
            <option value="light" data-i18n="theme_light">☀️ Light</option>
            <option value="midnight" data-i18n="theme_midnight">🌃 Midnight</option>
            <option value="cyber" data-i18n="theme_cyber">👾 Cyber</option>
        </select>
    </div>
</div>
<div class="main">
    <div id="ed"></div>
    <div class="sb">
        <div class="sec">
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
        <div class="sec">
            <h3><span data-i18n="proxy_mgmt">Управление</span></h3>
            <div class="proxy-grid">
                <button onclick="openAddProxyModal()" class="btn-s" data-i18n="add">➕ Добавить</button>
                <button onclick="openEditProxyModal()" class="btn-u" data-i18n="edit">✏️ Заменить</button>
                <button onclick="showRename()" class="btn-g" data-i18n="rename">Переименовать</button>
                <button onclick="showDel()" class="btn-d" data-i18n="delete">🗑 Удалить</button>
            </div>
        </div>
        <div class="sec">
            <h3><span data-i18n="backups">Бэкапы</span></h3>
            <div class="bk-controls">
                <span data-i18n="keep">Оставить:</span>
                <input type="number" id="bk-lim" value="5" min="1" max="50">
                <button onclick="cleanBackups()" class="btn-g" data-i18n="clean">Очистить</button>
            </div>
            <div id="bk-list">__BACKUPS__</div>
        </div>
    </div>
</div>

<div id="m-grp" class="ovl"><div class="mod"><h3 data-i18n="modal_groups">Добавить в группы:</h3>
<div style="display:flex; gap:10px; margin-bottom:10px"><button onclick="tgGrp(true)" class="btn-g" style="flex:1; justify-content:center" data-i18n="btn_sel_all">☑ Выбрать все</button><button onclick="tgGrp(false)" class="btn-g" style="flex:1; justify-content:center" data-i18n="btn_sel_none">☐ Снять все</button></div>
<div id="g-cnt" class="g-list"></div>
<div style="display:flex;justify-content:flex-end;gap:10px;margin-top:15px;padding-top:10px;border-top:1px solid var(--bd)"><button onclick="applyVless()" class="btn-s" style="flex:1;justify-content:center" data-i18n="btn_add">Добавить</button><button onclick="closeM('m-grp')" class="btn-g" style="flex:1;justify-content:center" data-i18n="btn_cancel">Отмена</button></div></div></div>

<div id="m-del" class="ovl"><div class="mod"><h3 data-i18n="modal_del_proxy">Удалить прокси</h3><select id="sel-del"></select><div style="display:flex;justify-content:flex-end;gap:10px;margin-top:15px"><button onclick="doDel()" class="btn-d" data-i18n="delete">Удалить</button><button onclick="closeM('m-del')" class="btn-g" data-i18n="btn_cancel">Отмена</button></div></div></div>
<div id="m-con" class="ovl"><div class="mod"><h3 data-i18n="modal_console">Консоль</h3><div id="cons">...</div><div style="display:flex;justify-content:flex-end;gap:10px;margin-top:15px"><button onclick="location.reload()" class="btn-s" data-i18n="btn_update">Обновить</button><button onclick="closeM('m-con')" class="btn-g" data-i18n="btn_close">Закрыть</button></div></div></div>

<div id="m-ren" class="ovl"><div class="mod">
    <h3 data-i18n="modal_ren_proxy">Переименовать прокси</h3>
    <p style="margin-top:0;font-size:13px;color:var(--txt-sec)" data-i18n="lbl_sel_ren">Выберите прокси для переименования:</p>
    <select id="sel-ren-proxy"></select>
    <p style="margin-top:15px;font-size:13px;color:var(--txt-sec)" data-i18n="lbl_new_name">Новое имя:</p>
    <input id="inp-ren-newname" placeholder="Введите новое имя" data-i18n-ph="ph_new_name">
    <div style="display:flex;justify-content:flex-end;gap:10px;margin-top:20px">
        <button onclick="doRename()" class="btn-s" data-i18n="btn_rename">Переименовать</button>
        <button onclick="closeM('m-ren')" class="btn-g" data-i18n="btn_cancel">Отмена</button>
    </div>
</div></div>

<div id="m-add-prof" class="ovl"><div class="mod">
    <h3 data-i18n="modal_new_prof">Новый профиль</h3>
    <label style="font-size:12px; margin-bottom:5px; color:var(--txt-sec)" data-i18n="lbl_prof_name">Имя (англ, без пробелов):</label>
    <input id="np-name" placeholder="my_config" style="margin-bottom:10px">
    <label style="font-size:12px; margin-bottom:5px; color:var(--txt-sec)" data-i18n="lbl_content">Содержимое:</label>
    <div style="display:flex; gap:5px; margin-bottom:5px">
        <button onclick="document.getElementById('np-file').click()" class="btn-u" style="flex:1;justify-content:center" data-i18n="btn_load_file">📂 Загрузить файл</button>
    </div>
    <input type="file" id="np-file" style="display:none" onchange="loadProfFile(this)">
    <textarea id="np-content" rows="10" placeholder="Вставьте YAML конфиг сюда..." data-i18n-ph="ph_paste_yaml"></textarea>
    <div style="display:flex;justify-content:flex-end;gap:10px;margin-top:15px">
        <button onclick="saveNewProf()" class="btn-s" data-i18n="btn_save">Сохранить</button>
        <button onclick="closeM('m-add-prof')" class="btn-g" data-i18n="btn_cancel">Отмена</button>
    </div>
</div></div>

<div id="addProxyModal" class="ovl"><div class="mod">
    <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid var(--bd); padding-bottom:10px; margin-bottom:0;">
       <h3 id="proxyModalTitle" style="margin:0; padding:0; border:0;" data-i18n="modal_add_proxy">Добавить прокси</h3>
       <button onclick="closeM('addProxyModal')" style="width:32px; height:32px; padding:0; background:var(--bg-ter); color:var(--txt); font-size:18px;">✕</button>
    </div>

    <div id="edit-proxy-container" style="display:none; margin-bottom:10px;">
        <label style="font-size:12px; margin-bottom:5px; color:var(--txt-sec)" data-i18n="lbl_select_edit">Выберите прокси для изменения:</label>
        <select id="edit-proxy-sel"></select>
        <div style="font-size:11px; color:var(--btn-u); margin-top:5px;" data-i18n="warn_edit">⚠️ Данные этого прокси будут полностью заменены новыми!</div>
    </div>

    <div class="modal-tabs">
        <button class="active" onclick="switchTab(event, 'vlessTab')" data-i18n="tab_vless">VLESS</button>
        <button onclick="switchTab(event, 'wgTab')" data-i18n="tab_wg">WireGuard</button>
    </div>

    <div id="vlessTab" class="tab-content active">
        <label style="font-size:12px; margin-bottom:5px; color:var(--txt-sec)" data-i18n="lbl_vless_link">Ссылка VLESS:</label>
        <input id="vlessLink" placeholder="vless://..." style="margin-bottom:10px;">

        <div id="vless-name-block">
            <label style="font-size:12px; margin-bottom:5px; color:var(--txt-sec)" data-i18n="lbl_proxy_name">Имя прокси (необязательно):</label>
            <input id="vlessProxyName" placeholder="Автоматически из ссылки" data-i18n-ph="ph_auto_vless" style="margin-bottom:10px;">
        </div>

        <button onclick="parseVless()" class="btn-s" style="width:100%; justify-content:center;" data-i18n="btn_save">Сохранить</button>
    </div>

    <div id="wgTab" class="tab-content">
        <label style="font-size:12px; margin-bottom:5px; color:var(--txt-sec)" data-i18n="lbl_wg_conf">Конфигурация WireGuard:</label>
        <textarea id="wgConfig" rows="8" placeholder="Вставьте содержимое .conf файла сюда..." data-i18n-ph="ph_paste_conf" style="width:100%; margin-bottom:10px;"></textarea>

        <div id="wg-name-block">
            <label style="font-size:12px; margin-bottom:5px; color:var(--txt-sec)" data-i18n="lbl_proxy_name">Имя прокси (необязательно):</label>
            <input id="wgProxyName" placeholder="Автоматически из Endpoint" data-i18n-ph="ph_auto_wg" style="margin-bottom:10px;">
        </div>

        <input type="file" id="wgFile" accept=".conf" style="display:none" onchange="loadWgFile(this)">
        <button onclick="document.getElementById('wgFile').click()" class="btn-u" style="width:100%; justify-content:center; margin-bottom:10px;" data-i18n="btn_load_file">📂 Или загрузить .conf файл</button>
        <button onclick="addWireguard()" class="btn-s" style="width:100%; justify-content:center;" data-i18n="btn_save">Сохранить</button>
    </div>
</div></div>

<div id="m-view-bk" class="ovl"><div class="mod">
    <h3 data-i18n="modal_view_bk">Просмотр бэкапа</h3>
    <pre id="bk-content" style="flex-grow:1; overflow-y:auto; min-height: 200px; max-height:60vh;"></pre>
    <div style="display:flex;justify-content:flex-end;gap:10px;margin-top:15px;padding-top:10px;border-top:1px solid var(--bd)">
        <button id="bk-restore-btn" class="btn-r" data-i18n="btn_restore">Восстановить</button>
        <button onclick="closeM('m-view-bk')" class="btn-g" data-i18n="btn_close">Закрыть</button>
    </div>
</div></div>

<script>
var ed=ace.edit("ed");ed.setTheme("ace/theme/monokai");ed.session.setMode("ace/mode/yaml");ed.setOptions({fontSize:14,tabSize:2,useSoftTabs:true});
var pData=null, GRP_KEY="mihomo_grp_sel", LIM_KEY="mihomo_bk_lim", THM_KEY="mihomo_theme", LANG_KEY="mihomo_lang";
var initialConfig = __JSON_CONTENT__;
var isEditMode = false;
var currLang = 'ru';

const TR = {
    ru: {
        title: "Mihomo Studio",
        save: "💾 Сохранить",
        restart: "🚀 Рестарт",
        panel: "🌐 Панель",
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
        confirm_switch: "Переключиться на профиль {0}?",
        confirm_del_prof: "Удалить профиль {0}? Это действие необратимо.",
        confirm_del_bk: "Удалить бэкап {0}?",
        confirm_clean: "Оставить только {0} последних бэкапов?",
        confirm_restore: "Восстановить {0}? Текущий конфиг будет перезаписан.",
        confirm_del_proxy: "Удалить?",
        confirm_replace: "Заменить данные прокси '{0}'?",
        prompt_enter_name: "Введите имя!",
        error_invalid_name: "Недопустимое имя!",
        error_exists: "Профиль с таким именем уже существует",
        error_no_proxy_edit: "Выберите прокси для редактирования",
        error_empty_wg: "Конфигурація WireGuard не може бути порожньою.",
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
        tab_wg: "WireGuard",
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
        last_load: "Загружено:",
        last_saved: "Сохранено:"
    },
    uk: {
        title: "Mihomo Studio",
        save: "💾 Зберегти",
        restart: "🚀 Рестарт",
        panel: "🌐 Панель",
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
        confirm_switch: "Переключитися на профіль {0}?",
        confirm_del_prof: "Видалити профіль {0}? Ця дія незворотна.",
        confirm_del_bk: "Видалити бекап {0}?",
        confirm_clean: "Залишити тільки {0} останніх бекапів?",
        confirm_restore: "Відновити {0}? Поточний конфіг буде перезаписано.",
        confirm_del_proxy: "Видалити?",
        confirm_replace: "Замінити дані проксі '{0}'?",
        prompt_enter_name: "Введіть ім'я!",
        error_invalid_name: "Неприпустиме ім'я!",
        error_exists: "Профіль з таким ім'ям вже існує",
        error_no_proxy_edit: "Виберіть проксі для редагування",
        error_empty_wg: "Конфігурація WireGuard не може бути порожньою.",
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
        tab_wg: "WireGuard",
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
        last_load: "Завантажено:",
        last_saved: "Збережено:"
    },
    en: {
        title: "Mihomo Studio",
        save: "💾 Save",
        restart: "🚀 Restart",
        panel: "🌐 Panel",
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
        confirm_switch: "Switch to profile {0}?",
        confirm_del_prof: "Delete profile {0}? This action is irreversible.",
        confirm_del_bk: "Delete backup {0}?",
        confirm_clean: "Keep only the last {0} backups?",
        confirm_restore: "Restore {0}? Current config will be overwritten.",
        confirm_del_proxy: "Delete?",
        confirm_replace: "Replace data for proxy '{0}'?",
        prompt_enter_name: "Enter name!",
        error_invalid_name: "Invalid name!",
        error_exists: "Profile with this name already exists",
        error_no_proxy_edit: "Select a proxy to edit",
        error_empty_wg: "WireGuard configuration cannot be empty.",
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
        tab_wg: "WireGuard",
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
        last_load: "Loaded:",
        last_saved: "Saved:"
    }
};

function t(k, ...args) {
    let s = TR[currLang][k] || k;
    args.forEach((a, i) => s = s.replace('{'+i+'}', a));
    return s;
}

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

    // Update dynamic parts
    if(isEditMode) document.getElementById('proxyModalTitle').innerText = TR[l].modal_edit_proxy;
    else document.getElementById('proxyModalTitle').innerText = TR[l].modal_add_proxy;
}

// Открываем панель через наш локальный прокси (безопасно для PNA/CORS)
function openPanel() {
    var url = window.location.protocol + "//" + window.location.host + "/mihomo_panel/ui/";
    window.open(url, '_blank');
}
ed.setValue(initialConfig); ed.clearSelection();

document.getElementById('vlessLink').addEventListener('input', function() {
    if(isEditMode) return; 
    var link = this.value;
    if (link.startsWith("vless://") && link.includes("#")) {
        var name = link.split('#')[1];
        document.getElementById('vlessProxyName').value = decodeURIComponent(name).trim();
    }
});

document.getElementById('wgConfig').addEventListener('input', function() {
    if(isEditMode) return;
    var conf = this.value;
    var nameField = document.getElementById('wgProxyName');
    var endpointMatch = conf.match(/Endpoint\s*=\s*(.+)/);
    if (endpointMatch && endpointMatch[1]) {
        var server = endpointMatch[1].split(':')[0].trim();
        if (server) nameField.value = 'WG_' + server;
    }
});

function closeM(i){document.getElementById(i).style.display='none'}
function showToast(msg){ var tBox=document.getElementById('toast'); tBox.innerText=msg||t('toast_saved'); tBox.style.display='block'; setTimeout(()=>{tBox.style.display='none'}, 2000); }

function switchProf() {
    var p = document.getElementById('prof-sel').value;
    if(!confirm(t('confirm_switch', p))) return;
    var params = new URLSearchParams(); params.append('act', 'switch_prof'); params.append('name', p);
    fetch('/',{method:'POST',body:params}).then(r=>r.json()).then(d=>{
        if(d.error) alert(d.error);
        else window.location.reload();
    });
}
function openAddProf() {
    document.getElementById('np-name').value='';
    document.getElementById('np-content').value='';
    document.getElementById('m-add-prof').style.display='flex';
}
function loadProfFile(input) {
    var f=input.files[0]; var r=new FileReader();
    r.onload=function(e){document.getElementById('np-content').value = e.target.result};
    r.readAsText(f); input.value='';
}
function saveNewProf() {
    var n = document.getElementById('np-name').value.trim();
    var c = document.getElementById('np-content').value;
    if(!n) return alert(t('prompt_enter_name'));
    if(!n.match(/^[a-zA-Z0-9_-]+$/)) return alert(t('error_invalid_name'));
    var params = new URLSearchParams(); params.append('act', 'add_prof'); params.append('name', n); params.append('content', c);
    fetch('/',{method:'POST',body:params}).then(r=>r.json()).then(d=>{
        if(d.error) alert(d.error);
        else { showToast(t('toast_saved')); setTimeout(()=>{window.location.reload()}, 500); }
    });
}
function delProf() {
    var p = document.getElementById('prof-sel').value;
    if(!p) return;
    if(!confirm(t('confirm_del_prof', p))) return;
    var params = new URLSearchParams(); params.append('act', 'del_prof'); params.append('name', p);
    fetch('/',{method:'POST',body:params}).then(r=>r.json()).then(d=>{
        if(d.error) alert(d.error);
        else { showToast(t('toast_deleted')); setTimeout(()=>{window.location.reload()}, 500); }
    });
}

function downloadProf() {
    var sel = document.getElementById('prof-sel');
    if (!sel.value) return;
    var name = sel.value;
    var params = new URLSearchParams();
    params.append('act', 'get_prof_content');
    params.append('name', name);
    fetch('/', { method: 'POST', body: params })
        .then(r => r.json())
        .then(d => {
            if (d.error) {
                alert(d.error);
            } else {
                var a = document.createElement('a');
                a.href = 'data:text/yaml;charset=utf-8,' + encodeURIComponent(d.content);
                a.download = name + '.yaml';
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                showToast('💾');
            }
        });
}

function fmtLog(raw) {
    if(!raw) return "Log empty";
    return raw.split('\\n').map(l => {
        if(!l.trim()) return "";
        l = l.replace(/\\x1b\\[32m/g, '<span class="log-green">')
             .replace(/\\x1b\\[33m/g, '<span class="log-yellow">')
             .replace(/\\x1b\\[0m/g, '</span>');
        var m = l.match(/time="(.*?)"\s+level=(\w+)\s+msg="(.*)"/);
        if(m) {
            var ts = new Date(m[1]).toLocaleTimeString();
            var lvl = m[2].toUpperCase();
            var txt = m[3];
            var cls = 'log-info';
            if(lvl==='WARN'||lvl==='WARNING') cls='log-warn';
            if(lvl==='ERROR'||lvl==='FATAL') cls='log-err';
            return `<div class="log-line"><span class="log-time">[${ts}]</span><span class="${cls}">[${lvl}]</span> ${txt}</div>`;
        }
        return `<div class="log-line">${l}</div>`;
    }).join('');
}

function setTheme(t) {
    document.body.className = t;
    localStorage.setItem(THM_KEY, t);
    document.getElementById('theme-sel').value = t;
    var aceT = 'ace/theme/monokai';
    if(t === 'light') aceT = 'ace/theme/chrome';
    if(t === 'midnight') aceT = 'ace/theme/tomorrow_night_blue';
    if(t === 'cyber') aceT = 'ace/theme/terminal';
    ed.setTheme(aceT);
}
var savedTheme = localStorage.getItem(THM_KEY) || 'dark';
setTheme(savedTheme);

var savedLang = localStorage.getItem(LANG_KEY) || 'ru';
setLang(savedLang);

var bkInp = document.getElementById('bk-lim');
if(localStorage.getItem(LIM_KEY)) bkInp.value = localStorage.getItem(LIM_KEY);
bkInp.addEventListener('change', function(){ localStorage.setItem(LIM_KEY, this.value); });

function save(mode){
    var c=ed.getValue();
    var p=new URLSearchParams(); p.append('act', mode); p.append('content', c);
    if(mode==='restart') {
        document.getElementById('m-con').style.display='flex'; 
        document.getElementById('cons').innerHTML='<div style="padding:20px;text-align:center">' + t('log_loading') + '</div>';
    }
    fetch('/',{method:'POST',body:p}).then(r=>r.json()).then(d=>{
        if(mode==='save'){
            showToast(t('toast_saved'));
            document.getElementById('last-load').innerText = t('last_saved') + " " + d.time;
            if(d.backups) document.getElementById('bk-list').innerHTML = d.backups;
        } else {
            var consoleDiv = document.getElementById('cons');
            consoleDiv.innerHTML = fmtLog(d.log);
            consoleDiv.scrollTop = consoleDiv.scrollHeight;
        }
    }).catch(e=>alert("Error: "+e));
}

function cleanBackups(){
    var lim = document.getElementById('bk-lim').value;
    if(!confirm(t('confirm_clean', lim))) return;
    var p=new URLSearchParams(); p.append('act', 'clean_backups'); p.append('limit', lim);
    fetch('/',{method:'POST',body:p}).then(r=>r.json()).then(d=>{
        showToast(t('toast_cleaned'));
        if(d.backups) document.getElementById('bk-list').innerHTML = d.backups;
    });
}

function delBackup(fname){
    if(!confirm(t('confirm_del_bk', fname))) return;
    var p=new URLSearchParams(); p.append('act', 'del_backup'); p.append('f', fname);
    fetch('/',{method:'POST',body:p}).then(r=>r.json()).then(d=>{
        showToast(t('toast_deleted'));
        if(d.backups) document.getElementById('bk-list').innerHTML = d.backups;
    });
}

function viewBackup(fname) {
    var p = new URLSearchParams();
    p.append('act', 'view_backup');
    p.append('f', fname);
    fetch('/', { method: 'POST', body: p }).then(r => r.json()).then(d => {
        if (d.error) {
            alert(d.error);
        } else {
            document.getElementById('bk-content').textContent = d.content;
            document.getElementById('bk-restore-btn').onclick = function() { restoreBackup(fname) };
            document.getElementById('m-view-bk').style.display = 'flex';
        }
    });
}

function restoreBackup(fname){
    if(!confirm(t('confirm_restore', fname))) return;
    var p=new URLSearchParams(); p.append('act', 'rest'); p.append('f', fname);
    fetch('/',{method:'POST',body:p}).then(r=>r.text()).then(()=>{
        window.location.reload();
    });
}

function getProxiesList() {
    var ls = ed.getValue().split(/\\r?\\n/);
    var prs = [], inP = 0;
    for (var l of ls) {
        if (l.match(/^proxies:/)) inP = 1;
        if (inP && l.match(/^[a-zA-Z]/) && !l.match(/^proxies:/)) inP = 0;
        if (inP) {
            var m = l.match(/^\s+-\s+name:\s+(.*)/);
            if (m) prs.push(m[1].trim().replace(/^['"]|['"]$/g, ''))
        }
    }
    return prs;
}

function openAddProxyModal() {
    isEditMode = false;
    document.getElementById('proxyModalTitle').innerText = t('modal_add_proxy');
    document.getElementById('edit-proxy-container').style.display = 'none';
    document.getElementById('vless-name-block').style.display = 'block';
    document.getElementById('wg-name-block').style.display = 'block';

    // Clear inputs
    document.getElementById('vlessLink').value = '';
    document.getElementById('vlessProxyName').value = '';
    document.getElementById('wgConfig').value = '';
    document.getElementById('wgProxyName').value = '';

    document.getElementById('addProxyModal').style.display = 'flex';
}

function openEditProxyModal() {
    isEditMode = true;
    document.getElementById('proxyModalTitle').innerText = t('modal_edit_proxy');
    document.getElementById('edit-proxy-container').style.display = 'block';
    document.getElementById('vless-name-block').style.display = 'none';
    document.getElementById('wg-name-block').style.display = 'none';

    // Populate select
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

    // Clear inputs
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
}

function loadWgFile(input) {
    var f = input.files[0];
    if (!f) return;
    var r = new FileReader();
    r.onload = function(e) {
        var content = e.target.result;
        document.getElementById('wgConfig').value = content;
        document.getElementById('wgConfig').dispatchEvent(new Event('input'));
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
        });
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

    if (!link) return;

    var p = new URLSearchParams();
    p.append('act', 'parse');
    p.append('link', link);
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
        });
}

function replaceProxyData(targetName, newYaml) {
    if(!confirm(t('confirm_replace', targetName))) return;
    var content = ed.getValue();
    var p = new URLSearchParams();
    p.append('act', 'replace_proxy');
    p.append('target_name', targetName);
    p.append('new_yaml', newYaml);
    p.append('content', content);

    fetch('/', { method: 'POST', body: p })
        .then(r => r.json())
        .then(d => {
            if (d.error) {
                alert(d.error);
            } else {
                ed.setValue(d.new_content);
                ed.clearSelection();
                closeM('addProxyModal');
                showToast(t('toast_updated'));
            }
        });
}

function showG(){
    var txt=ed.getValue(); var ls=txt.split(/\\r?\\n/); var grps=[], inG=false;
    for(var l of ls){ if(l.match(/^proxy-groups:/))inG=true; if(inG && l.match(/^[a-zA-Z]/) && !l.match(/^proxy-groups:/))inG=false; if(inG){var m=l.match(/^\s*-\s+name:\s+(.*)/);if(m)grps.push(m[1].trim().replace(/^['"]|['"]$/g,''))}}
    var c=document.getElementById('g-cnt'); c.innerHTML=''; var sv=JSON.parse(localStorage.getItem(GRP_KEY));
    if(grps.length===0) c.innerHTML='<div style="color:orange">Группы не найдены</div>';
    else grps.forEach(g=>{
        var ch=(sv===null||sv.includes(g))?'checked':'';
        c.innerHTML+=`<div class="g-item"><input type="checkbox" id="c_${g}" value="${g}" ${ch}><label for="c_${g}">${g}</label></div>`;
    });
    document.getElementById('m-grp').style.display='flex';
}
function tgGrp(s){document.querySelectorAll('#g-cnt input').forEach(c=>c.checked=s)}
function applyVless(){
    closeM('m-grp'); var txt=ed.getValue(); var sels=[];
    document.querySelectorAll('#g-cnt input:checked').forEach(c=>sels.push(c.value));
    localStorage.setItem(GRP_KEY, JSON.stringify(sels));
    var p=new URLSearchParams(); p.append('act','apply_insert'); p.append('content',txt); p.append('proxy_name',pData.name); p.append('proxy_yaml',pData.yaml); p.append('targets',JSON.stringify(sels));
    fetch('/',{method:'POST',body:p}).then(r=>r.json()).then(d=>{if(d.error)alert(d.error);else{ed.setValue(d.new_content);ed.clearSelection();showToast(t('toast_added'))}});
}
function showDel(){
    var prs = getProxiesList();
    var s=document.getElementById('sel-del');s.innerHTML='';
    prs.forEach(p=>{var o=document.createElement('option');o.text=p;s.add(o)});
    document.getElementById('m-del').style.display='flex';
}
function doDel(){
    var nm=document.getElementById('sel-del').value;if(!nm)return;if(!confirm(t('confirm_del_proxy')))return;closeM('m-del');
    var ls=ed.getValue().split(/\\r?\\n/); var nls=[], inP=false, delB=false, bInd=-1;
    for(var l of ls){
        if(l.match(/^proxies:/)){inP=true;nls.push(l);continue} if(inP && l.match(/^[a-zA-Z]/) && !l.match(/^proxies:/)){inP=false;delB=false}
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
    ed.setValue(nls.join('\\n'));
}

function showRename() {
    var prs = getProxiesList();
    var s = document.getElementById('sel-ren-proxy');
    s.innerHTML = '';
    prs.forEach(p => {
        var o = document.createElement('option');
        o.text = p;
        s.add(o)
    });
    document.getElementById('inp-ren-newname').value = '';
    document.getElementById('m-ren').style.display = 'flex';
}

function doRename() {
    var oldName = document.getElementById('sel-ren-proxy').value;
    var newName = document.getElementById('inp-ren-newname').value.trim();
    if (!newName) {
        alert("Новое имя не может быть пустым.");
        return;
    }
    if (!oldName) {
        alert("Прокси не выбран.");
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
        .catch(e => alert("Сетевая ошибка: " + e));
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
        for f in sorted(glob.glob(BACKUP_DIR + "/*.yaml"), key=os.path.getmtime, reverse=True)[:10]:
            n = os.path.basename(f);
            t = datetime.fromtimestamp(os.path.getmtime(f)).strftime("%d.%m %H:%M")
            b += f'''<div class="bk-item">
                    <div><b>{n}</b><span style="font-size:10px;color:var(--txt-sec)">{t}</span></div>
                    <div class="bk-btns">
                        <button onclick="viewBackup('{n}')" class="btn-u" title="Просмотр">👁️</button>
                        <button onclick="restoreBackup('{n}')" class="btn-g" title="Восстановить">↺</button>
                        <button onclick="delBackup('{n}')" class="btn-d" title="Удалить">✕</button>
                    </div>
                   </div>'''
        if not b: b = '<div style="color:var(--txt-sec);font-size:12px;text-align:center;padding:10px">Нет бэкапов</div>'
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
            .replace('__TIME__', datetime.now().strftime("%H:%M:%S"))
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


try:
    socketserver.TCPServer.allow_reuse_address = True;
    socketserver.TCPServer(("", PORT), H).serve_forever()
except Exception as e:
    print(e)