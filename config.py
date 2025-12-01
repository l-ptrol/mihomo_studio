# /opt/scripts/mihomo-studio/config.py
import os

PORT = 8888
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")

# Пути Keenetic / Mihomo
CONFIG_DIR = "/opt/etc/mihomo"
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.yaml")
PROFILES_DIR = os.path.join(CONFIG_DIR, "profiles")
BACKUP_DIR = os.path.join(CONFIG_DIR, "backup")

VERSION_FILE = os.path.join(BASE_DIR, "version.txt")
 
LOG_FILE = "/tmp/mihomo_last_restart.log"
# Команда рестарта (важно для Keenetic)
RESTART_CMD = f"xkeen -restart > {LOG_FILE} 2>&1"