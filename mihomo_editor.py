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
def parse_vless(link):
    try:
        if not link.startswith("vless://"): return None, "Link error"
        main = link[8:]
        name = "VLESS"
        if '#' in main: main, n = main.split('#', 1); name = urllib.parse.unquote(n).strip()
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


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<title>Mihomo Editor v18.4</title>
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

.bk-controls {display:flex; gap:8px; align-items:center; background:var(--bg-ter); padding:5px 10px; border-radius:4px; border: 1px solid var(--bd);}
.bk-controls input {width: 50px !important; text-align: center; margin:0; height: 28px; padding: 0;}
.bk-controls span {font-size:12px; color:var(--txt-sec); white-space: nowrap;}
.bk-controls button { height: 28px; font-size: 11px; padding: 0 10px; margin-left: auto; }

.prof-row {display:flex; gap:8px; align-items:center;}
#prof-sel { flex: 1; }
.prof-btns { display: flex; gap: 8px; margin-top: 5px; }
.prof-btns button { flex: 1; }

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
<div class="toast" id="toast">✅ Успешно сохранено</div>
<div class="hdr">
    <div style="display:flex;align-items:center;gap:10px">
        <h2 style="margin:0;color:#4caf50">Mihomo Studio</h2>
        <span style="color:var(--txt-sec);font-size:12px">v18.4 Auto-Panel</span>
    </div>
    <div id="last-load">Loaded: __TIME__</div>
</div>
<div class="bar">
    <button onclick="save('save')" class="btn-s">💾 Сохранить</button>
    <button onclick="save('restart')" class="btn-r">🚀 Рестарт</button>
    <button onclick="openPanel()" class="btn-g" title="Открыть встроенную панель Mihomo">🌐 Панель</button>
    <select id="theme-sel" onchange="setTheme(this.value)" style="max-width:120px; padding:0 10px; margin:0;">
        <option value="dark">🌑 Dark</option>
        <option value="light">☀️ Light</option>
        <option value="midnight">🌃 Midnight</option>
        <option value="cyber">👾 Cyber</option>
    </select>
</div>
<div class="main">
    <div id="ed"></div>
    <div class="sb">
        <div class="sec">
            <h3>Профили</h3>
            <div class="prof-row">
                <select id="prof-sel">__PROFILES__</select>
                <button onclick="switchProf()" class="btn-s" style="padding:0; width:36px; justify-content:center;" title="Выбрать">✔</button>
            </div>
            <div class="prof-btns">
                 <button onclick="openAddProf()" class="btn-u">➕ Создать</button>
                 <button onclick="delProf()" class="btn-d">🗑 Удалить</button>
            </div>
        </div>
        <div class="sec">
            <h3>Быстрый VLESS</h3>
            <input id="vl" placeholder="vless://..." style="margin:0">
            <button onclick="parseVless()" class="btn-s" style="width:100%">➕ Добавить прокси</button>
        </div>
        <div class="sec">
            <button onclick="showDel()" class="btn-d" style="width:100%">🗑 Удалить прокси</button>
        </div>
        <div class="sec">
            <h3>Бэкапы</h3>
            <div class="bk-controls">
                <span>Оставить:</span>
                <input type="number" id="bk-lim" value="5" min="1" max="50">
                <button onclick="cleanBackups()" class="btn-g">Очистить</button>
            </div>
            <div id="bk-list">__BACKUPS__</div>
        </div>
    </div>
</div>

<div id="m-grp" class="ovl"><div class="mod"><h3>Добавить в группы:</h3>
<div style="display:flex; gap:10px; margin-bottom:10px"><button onclick="tgGrp(true)" class="btn-g" style="flex:1; justify-content:center">☑ Выбрать все</button><button onclick="tgGrp(false)" class="btn-g" style="flex:1; justify-content:center">☐ Снять все</button></div>
<div id="g-cnt" class="g-list"></div>
<div style="display:flex;justify-content:flex-end;gap:10px;margin-top:15px;padding-top:10px;border-top:1px solid var(--bd)"><button onclick="applyVless()" class="btn-s" style="flex:1;justify-content:center">Добавить</button><button onclick="closeM('m-grp')" class="btn-g" style="flex:1;justify-content:center">Отмена</button></div></div></div>

<div id="m-del" class="ovl"><div class="mod"><h3>Удалить прокси</h3><select id="sel-del"></select><div style="display:flex;justify-content:flex-end;gap:10px;margin-top:15px"><button onclick="doDel()" class="btn-d">Удалить</button><button onclick="closeM('m-del')" class="btn-g">Отмена</button></div></div></div>
<div id="m-con" class="ovl"><div class="mod"><h3>Консоль</h3><div id="cons">...</div><div style="display:flex;justify-content:flex-end;gap:10px;margin-top:15px"><button onclick="location.reload()" class="btn-s">Обновить</button><button onclick="closeM('m-con')" class="btn-g">Закрыть</button></div></div></div>

<div id="m-add-prof" class="ovl"><div class="mod">
    <h3>Новый профиль</h3>
    <label style="font-size:12px; margin-bottom:5px; color:var(--txt-sec)">Имя (англ, без пробелов):</label>
    <input id="np-name" placeholder="my_config" style="margin-bottom:10px">
    <label style="font-size:12px; margin-bottom:5px; color:var(--txt-sec)">Содержимое:</label>
    <div style="display:flex; gap:5px; margin-bottom:5px">
        <button onclick="document.getElementById('np-file').click()" class="btn-u" style="flex:1;justify-content:center">📂 Загрузить файл</button>
    </div>
    <input type="file" id="np-file" style="display:none" onchange="loadProfFile(this)">
    <textarea id="np-content" rows="10" placeholder="Вставьте YAML конфиг сюда..."></textarea>
    <div style="display:flex;justify-content:flex-end;gap:10px;margin-top:15px">
        <button onclick="saveNewProf()" class="btn-s">Сохранить</button>
        <button onclick="closeM('m-add-prof')" class="btn-g">Отмена</button>
    </div>
</div></div>

<script>
var ed=ace.edit("ed");ed.setTheme("ace/theme/monokai");ed.session.setMode("ace/mode/yaml");ed.setOptions({fontSize:14,tabSize:2,useSoftTabs:true});
var pData=null, GRP_KEY="mihomo_grp_sel", LIM_KEY="mihomo_bk_lim", THM_KEY="mihomo_theme";
var initialConfig = __JSON_CONTENT__;

// Открываем панель через наш локальный прокси (безопасно для PNA/CORS)
function openPanel() {
    var url = window.location.protocol + "//" + window.location.host + "/mihomo_panel/ui/";
    window.open(url, '_blank');
}
ed.setValue(initialConfig); ed.clearSelection();

function closeM(i){document.getElementById(i).style.display='none'}
function showToast(msg){ var t=document.getElementById('toast'); t.innerText=msg||'✅ Выполнено'; t.style.display='block'; setTimeout(()=>{t.style.display='none'}, 2000); }

function switchProf() {
    var p = document.getElementById('prof-sel').value;
    if(!confirm("Переключиться на профиль " + p + "?")) return;
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
    if(!n) return alert("Введите имя!");
    if(!n.match(/^[a-zA-Z0-9_-]+$/)) return alert("Недопустимое имя!");
    var params = new URLSearchParams(); params.append('act', 'add_prof'); params.append('name', n); params.append('content', c);
    fetch('/',{method:'POST',body:params}).then(r=>r.json()).then(d=>{
        if(d.error) alert(d.error);
        else { showToast("✅ Профиль создан"); setTimeout(()=>{window.location.reload()}, 500); }
    });
}
function delProf() {
    var p = document.getElementById('prof-sel').value;
    if(!p) return;
    if(!confirm("Удалить профиль " + p + "? Это действие необратимо.")) return;
    var params = new URLSearchParams(); params.append('act', 'del_prof'); params.append('name', p);
    fetch('/',{method:'POST',body:params}).then(r=>r.json()).then(d=>{
        if(d.error) alert(d.error);
        else { showToast("🗑 Удалено"); setTimeout(()=>{window.location.reload()}, 500); }
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

var bkInp = document.getElementById('bk-lim');
if(localStorage.getItem(LIM_KEY)) bkInp.value = localStorage.getItem(LIM_KEY);
bkInp.addEventListener('change', function(){ localStorage.setItem(LIM_KEY, this.value); });

function save(mode){
    var c=ed.getValue();
    var p=new URLSearchParams(); p.append('act', mode); p.append('content', c);
    if(mode==='restart') {
        document.getElementById('m-con').style.display='flex'; 
        document.getElementById('cons').innerHTML='<div style="padding:20px;text-align:center">⏳ Выполнение xkeen -restart...</div>';
    }
    fetch('/',{method:'POST',body:p}).then(r=>r.json()).then(d=>{
        if(mode==='save'){
            showToast("✅ Сохранено");
            document.getElementById('last-load').innerText = "Saved: " + d.time;
            if(d.backups) document.getElementById('bk-list').innerHTML = d.backups;
        } else {
            document.getElementById('cons').innerHTML = fmtLog(d.log);
        }
    }).catch(e=>alert("Error: "+e));
}

function cleanBackups(){
    var lim = document.getElementById('bk-lim').value;
    if(!confirm('Оставить только ' + lim + ' последних бэкапов?')) return;
    var p=new URLSearchParams(); p.append('act', 'clean_backups'); p.append('limit', lim);
    fetch('/',{method:'POST',body:p}).then(r=>r.json()).then(d=>{
        showToast("🧹 Очищено");
        if(d.backups) document.getElementById('bk-list').innerHTML = d.backups;
    });
}

function delBackup(fname){
    if(!confirm('Удалить бэкап ' + fname + '?')) return;
    var p=new URLSearchParams(); p.append('act', 'del_backup'); p.append('f', fname);
    fetch('/',{method:'POST',body:p}).then(r=>r.json()).then(d=>{
        showToast("🗑 Удалено");
        if(d.backups) document.getElementById('bk-list').innerHTML = d.backups;
    });
}

function restoreBackup(fname){
    if(!confirm('Восстановить ' + fname + '? Текущий конфиг будет перезаписан.')) return;
    var p=new URLSearchParams(); p.append('act', 'rest'); p.append('f', fname);
    fetch('/',{method:'POST',body:p}).then(r=>r.text()).then(()=>{
        window.location.reload();
    });
}

function parseVless(){
    var l=document.getElementById('vl').value;if(!l)return;
    var p=new URLSearchParams();p.append('act','parse');p.append('link',l);
    fetch('/',{method:'POST',body:p}).then(r=>r.json()).then(d=>{if(d.error)alert(d.error);else{pData=d;showG();document.getElementById('vl').value=''}})
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
    fetch('/',{method:'POST',body:p}).then(r=>r.json()).then(d=>{if(d.error)alert(d.error);else{ed.setValue(d.new_content);ed.clearSelection();showToast("✅ Добавлено")}});
}
function showDel(){
    var ls=ed.getValue().split(/\\r?\\n/); var prs=[], inP=0;
    for(var l of ls){ if(l.match(/^proxies:/))inP=1; if(inP && l.match(/^[a-zA-Z]/) && !l.match(/^proxies:/))inP=0; if(inP){var m=l.match(/^\s+-\s+name:\s+(.*)/);if(m)prs.push(m[1].trim().replace(/^['"]|['"]$/g,''))}}
    var s=document.getElementById('sel-del');s.innerHTML='';
    prs.forEach(p=>{var o=document.createElement('option');o.text=p;s.add(o)});
    document.getElementById('m-del').style.display='flex';
}
function doDel(){
    var nm=document.getElementById('sel-del').value;if(!nm)return;if(!confirm('Удалить?'))return;closeM('m-del');
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
        var rm=l.match(/^\s+-\s+(?:"([^"]+)"|'([^']+)'|([^"':]+))\s*$/);
        if(rm){var rn=rm[1]||rm[2]||rm[3];if(rn&&rn.trim()===nm)continue}
        nls.push(l);
    }
    ed.setValue(nls.join('\\n'));
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

        # --- EXISTING ACTIONS ---

        if a == 'parse':
            d, e = parse_vless(p.get('link', ''))
            s.wfile.write(json.dumps(d if d else {'error': e}).encode('utf-8'));
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