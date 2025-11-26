#!/bin/sh

# === НАСТРОЙКИ ===
# Ветка репозитория
BRANCH="master"
# URL до управляющего скрипта
MHSTUDIO_URL="https://raw.githubusercontent.com/l-ptrol/mihomo_studio/${BRANCH}/mhstudio.sh"
# Куда будет установлена команда
INSTALL_PATH="/opt/bin/mhstudio"

echo "=== Установка управляющей команды Mihomo Studio ==="

# 1. Проверка и создание директории
INSTALL_DIR=$(dirname "$INSTALL_PATH")
if [ ! -d "$INSTALL_DIR" ]; then
    echo "Создание директории $INSTALL_DIR..."
    mkdir -p "$INSTALL_DIR"
fi

# 2. Скачивание управляющего скрипта
echo "Скачивание mhstudio в $INSTALL_PATH..."
wget --no-check-certificate -O "$INSTALL_PATH" "$MHSTUDIO_URL"
if [ $? -ne 0 ]; then
    echo "ОШИБКА: Не удалось скачать управляющий скрипт."
    exit 1
fi

# 3. Установка прав на выполнение
chmod +x "$INSTALL_PATH"

echo "=== Установка завершена! ==="
echo "Управляющая команда 'mhstudio' установлена в $INSTALL_PATH."
echo "Для автоматической установки или управления сервисом, просто выполните команду:"
echo "mhstudio"