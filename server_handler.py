# /opt/scripts/mihomo-studio/server_handler.py
import http.server
import os
import subprocess
import urllib.parse
import urllib.request
import urllib.error
import re
import shutil
import glob
import json
from datetime import datetime
import yaml

import config
from parsers import parse_vless, parse_wireguard
from yaml_units import insert_proxy_logic, replace_proxy_block


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def get_bks(self):
        b = ""
        for f in sorted(glob.glob(config.BACKUP_DIR + "/*.yaml"), key=os.path.getmtime, reverse=True)[:10]:
            n = os.path.basename(f)
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

    def get_prof_opts(self):
        curr = ""
        if os.path.exists(config.CONFIG_PATH):
            real = os.path.realpath(config.CONFIG_PATH)
            curr = os.path.splitext(os.path.basename(real))[0]

        opts = ""
        files = sorted(glob.glob(config.PROFILES_DIR + "/*.yaml"))
        for f in files:
            n = os.path.splitext(os.path.basename(f))[0]
            sel = "selected" if n == curr else ""
            opts += f'<option value="{n}" {sel}>{n}</option>'
        return opts

    def get_panel_port(self):
        panel_port = ''
        try:
            with open(config.CONFIG_PATH, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
                if data and 'external-controller' in data:
                    panel_port = str(data['external-controller']).split(':')[-1]
        except (IOError, FileNotFoundError, yaml.YAMLError):
            pass
        return panel_port

    def proxy_pass(self, method):
        panel_port = self.get_panel_port()
        if not panel_port:
            self.send_error(500, "Panel port not found in config")
            return

        rel_path = self.path.replace('/mihomo_panel/', '', 1)
        target_url = f"http://127.0.0.1:{panel_port}/{rel_path}"

        content_len = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_len) if content_len > 0 else None

        try:
            req = urllib.request.Request(target_url, data=body, method=method)
            for k, v in self.headers.items():
                if k.lower() not in ['host', 'origin', 'referer']:
                    req.add_header(k, v)

            req.add_header('Host', f'127.0.0.1:{panel_port}')

            with urllib.request.urlopen(req) as resp:
                self.send_response(resp.status)
                for k, v in resp.getheaders():
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
        except Exception:
            pass

    def do_GET(self):
        if self.path.startswith('/mihomo_panel/'):
            self.proxy_pass('GET')
            return

        if self.path != '/': return self.send_error(404)

        try:
            with open(config.CONFIG_PATH, 'r', encoding='utf-8') as f:
                c = f.read()
        except (FileNotFoundError, IOError):
            c = "proxies:\n"

        # Читаем шаблон из файла
        template_path = os.path.join(config.TEMPLATE_DIR, "index.html")
        try:
            with open(template_path, "r", encoding="utf-8") as f:
                html_template = f.read()
        except FileNotFoundError:
            self.send_error(500, "Template not found")
            return

        self.send_response(200)
        self.send_header('Content-type', 'text/html;charset=utf-8')
        self.end_headers()

        try:
            with open(config.VERSION_FILE, "r") as f:
                version = f.read().strip()
        except FileNotFoundError:
            version = ""

        out = html_template.replace('__JSON_CONTENT__', json.dumps(c)) \
            .replace('__BACKUPS__', self.get_bks()) \
            .replace('__PROFILES__', self.get_prof_opts()) \
            .replace('__TIME__', datetime.now().strftime("%H:%M:%S")) \
            .replace('__VERSION__', version)
        self.wfile.write(out.encode('utf-8'))

    def do_POST(self):
        if self.path.startswith('/mihomo_panel/'):
            self.proxy_pass('POST')
            return

        l = int(self.headers['Content-Length'])
        d = self.rfile.read(l).decode('utf-8', 'ignore')
        p = {k: v[0] for k, v in urllib.parse.parse_qs(d).items()}
        a = p.get('act')

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()

        # PROFILE ACTIONS
        if a == 'switch_prof':
            n = p.get('name')
            target = os.path.join(config.PROFILES_DIR, n + ".yaml")
            if os.path.exists(target):
                if os.path.exists(config.CONFIG_PATH) or os.path.islink(config.CONFIG_PATH):
                    os.unlink(config.CONFIG_PATH)
                os.symlink(target, config.CONFIG_PATH)
                self.wfile.write(json.dumps({'status': 'ok'}).encode('utf-8'))
            else:
                self.wfile.write(json.dumps({'error': 'Profile not found'}).encode('utf-8'))
            return

        if a == 'add_prof':
            n = p.get('name')
            c = p.get('content', '')
            target = os.path.join(config.PROFILES_DIR, n + ".yaml")
            if os.path.exists(target):
                self.wfile.write(json.dumps({'error': 'Профиль с таким именем уже существует'}).encode('utf-8'))
            else:
                with open(target, 'w', encoding='utf-8') as f:
                    f.write(c)
                if not os.path.exists(config.CONFIG_PATH): os.symlink(target, config.CONFIG_PATH)
                self.wfile.write(json.dumps({'status': 'ok'}).encode('utf-8'))
            return

        if a == 'del_prof':
            n = p.get('name')
            target = os.path.join(config.PROFILES_DIR, n + ".yaml")
            real_curr = os.path.realpath(config.CONFIG_PATH)
            if os.path.realpath(target) == real_curr:
                self.wfile.write(json.dumps({'error': 'Нельзя удалить активный профиль.'}).encode('utf-8'))
            elif os.path.exists(target):
                os.remove(target)
                self.wfile.write(json.dumps({'status': 'ok'}).encode('utf-8'))
            else:
                self.wfile.write(json.dumps({'error': 'File not found'}).encode('utf-8'))
            return

        if a == 'get_prof_content':
            n = p.get('name')
            target = os.path.join(config.PROFILES_DIR, n + ".yaml")
            if os.path.exists(target):
                with open(target, 'r', encoding='utf-8') as f:
                    content = f.read()
                self.wfile.write(json.dumps({'status': 'ok', 'content': content}).encode('utf-8'))
            else:
                self.wfile.write(json.dumps({'error': 'Profile not found'}).encode('utf-8'))
            return

        if a == 'rename_proxy':
            old_name = p.get('old_name')
            new_name = p.get('new_name')
            content = p.get('content', '')
            if not all([old_name, new_name, content]):
                self.wfile.write(json.dumps({'error': 'Missing parameters'}).encode('utf-8'))
                return

            try:
                data = yaml.safe_load(content)

                if 'proxies' in data:
                    for proxy in data['proxies']:
                        if proxy.get('name') == old_name:
                            proxy['name'] = new_name
                            break

                if 'proxy-groups' in data:
                    for group in data['proxy-groups']:
                        if 'proxies' in group:
                            group['proxies'] = [new_name if p == old_name else p for p in group['proxies']]
                
                new_content = yaml.dump(data, allow_unicode=True, sort_keys=False)
                self.wfile.write(json.dumps({'status': 'ok', 'new_content': new_content}).encode('utf-8'))
            except yaml.YAMLError as e:
                self.wfile.write(json.dumps({'error': f"YAML Error: {e}"}).encode('utf-8'))
            return

        # PARSING & PROXY ACTIONS
        if a == 'parse':
            link = p.get('link', '')
            custom_name = p.get('proxy_name')
            d, e = parse_vless(link, custom_name)
            self.wfile.write(json.dumps(d if d else {'error': e}).encode('utf-8'))
            return

        if a == 'add_wireguard':
            config_text = p.get('config_text', '')
            custom_name = p.get('proxy_name')
            if not config_text:
                self.wfile.write(json.dumps({'error': 'Empty config'}).encode('utf-8'))
                return
            proxy_data, err = parse_wireguard(config_text, custom_name)
            if err:
                self.wfile.write(json.dumps({'error': err}).encode('utf-8'))
                return
            self.wfile.write(json.dumps(proxy_data).encode('utf-8'))
            return

        if a == 'apply_insert':
            content = p.get('content', '')
            p_name = p.get('proxy_name', '')
            p_yaml_str = p.get('proxy_yaml', '')
            targets = json.loads(p.get('targets', '[]'))

            try:
                data = yaml.safe_load(content) or {}
                proxy_to_add = next(yaml.safe_load_all(p_yaml_str))

                if 'proxies' not in data or data['proxies'] is None:
                    data['proxies'] = []
                data['proxies'].append(proxy_to_add)

                updated_data = insert_proxy_logic(data, p_name, targets)
                
                new_content = yaml.dump(updated_data, allow_unicode=True, sort_keys=False)
                self.wfile.write(json.dumps({'new_content': new_content}).encode('utf-8'))
            except yaml.YAMLError as e:
                self.wfile.write(json.dumps({'error': f"YAML Error: {e}"}).encode('utf-8'))
            return

        if a == 'replace_proxy':
            target_name = p.get('target_name', '')
            new_yaml_str = p.get('new_yaml', '')
            content = p.get('content', '')
            
            try:
                data = yaml.safe_load(content)
                new_proxy_data = next(yaml.safe_load_all(new_yaml_str))
                
                updated_data = replace_proxy_block(data, target_name, new_proxy_data)

                new_content = yaml.dump(updated_data, allow_unicode=True, sort_keys=False)
                self.wfile.write(json.dumps({'new_content': new_content}).encode('utf-8'))
            except yaml.YAMLError as e:
                self.wfile.write(json.dumps({'error': f"YAML Error: {e}"}).encode('utf-8'))
            return

        # BACKUPS
        if a == 'clean_backups':
            limit = int(p.get('limit', 5))
            files = sorted(glob.glob(config.BACKUP_DIR + "/*.yaml"), key=os.path.getmtime, reverse=True)
            if len(files) > limit:
                for f in files[limit:]:
                    try:
                        os.remove(f)
                    except:
                        pass
            self.wfile.write(json.dumps({'backups': self.get_bks()}).encode('utf-8'))
            return

        if a == 'del_backup':
            fname = p.get('f')
            path = os.path.join(config.BACKUP_DIR, os.path.basename(fname))
            if os.path.exists(path): os.remove(path)
            self.wfile.write(json.dumps({'backups': self.get_bks()}).encode('utf-8'))
            return

        if a == 'rest':
            shutil.copy(os.path.join(config.BACKUP_DIR, os.path.basename(p.get('f'))), config.CONFIG_PATH)
            self.wfile.write(json.dumps({'status': 'ok'}).encode('utf-8'))
            return

        if a == 'view_backup':
            fname = p.get('f')
            path = os.path.join(config.BACKUP_DIR, os.path.basename(fname))
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
                self.wfile.write(json.dumps({'content': content}).encode('utf-8'))
            else:
                self.wfile.write(json.dumps({'error': 'File not found'}).encode('utf-8'))
            return

        # SAVE & RESTART
        new_c = p.get('content', '').replace('\r\n', '\n')
        if a in ['save', 'restart']:
            if os.path.exists(config.CONFIG_PATH):
                real_p = os.path.basename(os.path.realpath(config.CONFIG_PATH))
                prof_n = os.path.splitext(real_p)[0]
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                shutil.copy(config.CONFIG_PATH, f"{config.BACKUP_DIR}/{prof_n}_{ts}.yaml")

            with open(config.CONFIG_PATH, 'w', encoding='utf-8') as f:
                try:
                    # Проверяем, что YAML валидный, и пересохраняем его для форматирования
                    data = yaml.safe_load(new_c)
                    yaml.dump(data, f, allow_unicode=True, sort_keys=False)
                except yaml.YAMLError:
                    # Если YAML невалидный, сохраняем как есть
                    f.write(new_c)
                f.flush()
                os.fsync(f.fileno())

        if a == 'restart':
            my_env = os.environ.copy()
            my_env["TERM"] = "xterm-256color"
            subprocess.run(config.RESTART_CMD, shell=True, env=my_env)
            log = open(config.LOG_FILE).read() if os.path.exists(config.LOG_FILE) else "Log empty"
            self.wfile.write(json.dumps({'log': log}).encode('utf-8'))
        elif a == 'save':
            self.wfile.write(json.dumps(
                {'status': 'ok', 'time': datetime.now().strftime("%H:%M:%S"), 'backups': self.get_bks()}).encode(
                'utf-8'))

    def do_PUT(self):
        if self.path.startswith('/mihomo_panel/'):
            self.proxy_pass('PUT')
            return
        self.send_error(405, "Method Not Allowed")

    def do_DELETE(self):
        if self.path.startswith('/mihomo_panel/'):
            self.proxy_pass('DELETE')
            return
        self.send_error(405, "Method Not Allowed")