#!/bin/sh

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

# Копируем локальные PWA файлы
echo "Копирование PWA файлов..."
cp -f public/manifest.json /opt/etc/mihomo/public/manifest.json
cp -f public/icons/*.png /opt/etc/mihomo/public/icons/

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