#!/bin/sh

# Проверка наличия openssl
if ! command -v openssl &> /dev/null; then
    echo "openssl not found. Please install it first."
    # opkg update && opkg install openssl-utils (example for OpenWrt)
    exit 1
fi

# Определяем директорию, в которой находится скрипт
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)

# === НАСТРОЙКИ РЕПОЗИТОРИЯ ===
# Укажи здесь имя ветки (master или main)
BRANCH="master"
BASE_URL="http://freedom.l-ptr.ru:3000/Petro1990/mihomo_studio/raw/branch/${BRANCH}"

# === ПУТИ НА РОУТЕРЕ ===
INSTALL_DIR="/opt/scripts"
INIT_DIR="/opt/etc/init.d"
PY_SCRIPT="mihomo_editor.py"
INIT_SCRIPT="S95mihomo-web"

echo "=== Установка Mihomo Studio из репозитория (v18.3 + Codecs Fix) ==="

# 1. Проверка и установка зависимостей
echo "[1/4] Проверка Python и модулей..."
opkg update
# python3-codecs ОБЯЗАТЕЛЕН для работы urllib и кодировки idna
PACKAGES="python3-base python3-light python3-email python3-urllib python3-codecs"

for pkg in $PACKAGES; do
    if ! opkg list-installed | grep -q "^$pkg"; then
        echo "Устанавливаем $pkg..."
        opkg install "$pkg"
    else
        echo "$pkg уже установлен."
    fi
done

# 2. Создание директорий
echo "[2/4] Проверка директорий..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$INIT_DIR"
# Создаем папки для работы скрипта, если их нет
mkdir -p "/opt/etc/mihomo/profiles"
mkdir -p "/opt/etc/mihomo/backup"

# Создаем директорию для PWA
mkdir -p "/opt/etc/mihomo/public/icons"

# 3. Скачивание и копирование файлов
echo "[3/4] Скачивание и копирование файлов..."

# Загружаем PWA файлы
echo "Загрузка PWA файлов..."

# Директории на роутере для PWA
PWA_DIR="/opt/etc/mihomo/public"
ICONS_DIR="$PWA_DIR/icons"

# Убедимся, что директории существуют
mkdir -p "$ICONS_DIR"

# URL-ы для сырых файлов PWA
MANIFEST_URL="$BASE_URL/public/manifest.json"
ICON192_URL="$BASE_URL/public/icons/icon-192x192.png"
ICON512_URL="$BASE_URL/public/icons/icon-512x512.png"

# Загрузка файлов
echo "Загрузка manifest.json..."
wget --no-check-certificate -O "$PWA_DIR/manifest.json" "$MANIFEST_URL"

echo "Загрузка icon-192x192.png..."
wget --no-check-certificate -O "$ICONS_DIR/icon-192x192.png" "$ICON192_URL"

echo "Загрузка icon-512x512.png..."
wget --no-check-certificate -O "$ICONS_DIR/icon-512x512.png" "$ICON512_URL"

# Загружаем service-worker.js
echo "Загрузка service-worker.js..."
wget -O "$PWA_DIR/service-worker.js" "$BASE_URL/public/service-worker.js"

# Скачиваем основной скрипт
echo "Загрузка $PY_SCRIPT..."
wget --no-check-certificate -O "$INSTALL_DIR/$PY_SCRIPT" "$BASE_URL/$PY_SCRIPT"
if [ $? -ne 0 ]; then
    echo "ОШИБКА: Не удалось скачать $PY_SCRIPT. Проверь URL или имя ветки."
    exit 1
fi

# Скачиваем скрипт автозапуска
echo "Загрузка $INIT_SCRIPT..."
wget --no-check-certificate -O "$INIT_DIR/$INIT_SCRIPT" "$BASE_URL/$INIT_SCRIPT"
if [ $? -ne 0 ]; then
    echo "ОШИБКА: Не удалось скачать $INIT_SCRIPT."
    exit 1
fi

# 4. Права доступа и перезапуск
echo "[4/4] Настройка прав и запуск..."
chmod +x "$INSTALL_DIR/$PY_SCRIPT"
chmod +x "$INIT_DIR/$INIT_SCRIPT"

# Перезапуск службы
"$INIT_DIR/$INIT_SCRIPT" restart

echo "=== Установка завершена! ==="
echo "Веб-интерфейс доступен по адресу: http://$(uname -n):8888"