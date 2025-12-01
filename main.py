# /opt/scripts/mihomo-studio/main.py
#!/opt/bin/python3
# -*- coding: utf-8 -*-
import http.server
import socketserver
import os
import shutil
import config
from server_handler import Handler

# --- ИНИЦИАЛИЗАЦИЯ ---
def init_filesystem():
    if not os.path.exists(config.BACKUP_DIR):
        os.makedirs(config.BACKUP_DIR)
    if not os.path.exists(config.PROFILES_DIR):
        os.makedirs(config.PROFILES_DIR)

    if os.path.exists(config.CONFIG_PATH) and not os.path.islink(config.CONFIG_PATH):
        # Если конфиг есть, но не симлинк - переносим в профили
        shutil.move(config.CONFIG_PATH, os.path.join(config.PROFILES_DIR, "default.yaml"))
        os.symlink(os.path.join(config.PROFILES_DIR, "default.yaml"), config.CONFIG_PATH)
    elif not os.path.exists(config.CONFIG_PATH):
        # Создаем дефолтный
        def_prof = os.path.join(config.PROFILES_DIR, "default.yaml")
        with open(def_prof, 'w') as f:
            f.write("proxies: []\n")
        os.symlink(def_prof, config.CONFIG_PATH)

def main():
    init_filesystem()
    try:
        socketserver.TCPServer.allow_reuse_address = True
        print(f"Starting server on port {config.PORT}...")
        httpd = socketserver.TCPServer(("", config.PORT), Handler)
        httpd.serve_forever()
    except Exception as e:
        print(f"Server error: {e}")

if __name__ == "__main__":
    main()