#!/bin/sh
# === Mihomo Studio v2.2.46 (Go) — Установщик ===
# Автоопределение архитектуры и установка бинарника

set -e

BRANCH="test-go"
REPO="l-ptrol/mihomo_studio"
BASE_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}"
RAW_BASE="${BASE_URL}/dist"
T_STAMP=$(date +%s)

INSTALL_DIR="/opt/scripts"
INIT_DIR="/opt/etc/init.d"
BIN_DIR="/opt/bin"
MIHOMO_ETC_DIR="/opt/etc/mihomo"

# === Автоопределение архитектуры ===
detect_arch() {
    ARCH=$(uname -m 2>/dev/null || echo "unknown")
    
    # HACK: Проверка MT7621/Ralink Little Endian для роутеров Keenetic/Netis
    if [ "$ARCH" = "mips" ]; then
        if grep -qiE "MediaTek|Ralink|MT76|RT3|RT5|Little" /proc/cpuinfo 2>/dev/null; then
            ARCH="mipsel"
        fi
    fi

    case "$ARCH" in
        aarch64|arm64|armv8*|armv7l|armv6l|arm*)
            echo "aarch64"
            ;;
        mips|mips64)
            echo "mips"
            ;;
        mipsel|mips32el|mips64el)
            echo "mipsel"
            ;;
        *)
            echo "unknown"
            ;;
    esac
}

# === Скачивание с автовыбором curl/wget ===
download_file() {
    url="$1"
    dest="$2"
    
    # Try CURL first
    if command -v curl >/dev/null 2>&1; then
        if curl -sL -A "Mozilla/5.0" --connect-timeout 10 "$url" -o "$dest"; then
            return 0
        fi
    fi

    # Try WGET fallback
    if command -v wget >/dev/null 2>&1; then
        if wget -q --no-check-certificate --timeout=15 "$url" -O "$dest"; then
            return 0
        fi
    fi
    return 1
}

echo "========================================"
echo "# Mihomo Studio v2.2.46 - Installer"
echo "========================================"

# Определяем архитектуру
TARGET_ARCH=$(detect_arch)
echo ">>> Обнаружена архитектура: ${TARGET_ARCH}"

if [ "$TARGET_ARCH" = "unknown" ]; then
    echo "ОШИБКА: Не удалось определить архитектуру."
    echo "Поддерживаемые: aarch64, mips, mipsel"
    exit 1
fi

# Создаём директории
echo ">>> Создание директорий..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$INIT_DIR"
mkdir -p "${MIHOMO_ETC_DIR}/profiles"
mkdir -p "${MIHOMO_ETC_DIR}/backup"

# Остановка старой версии для предотвращения ошибки "Text file busy"
echo ">>> Остановка старой версии (если запущена)..."
[ -f "${INIT_DIR}/S95mihomo-web" ] && "${INIT_DIR}/S95mihomo-web" stop 2>/dev/null || true
pkill -9 -f mhstudio 2>/dev/null || true

# Скачиваем бинарник
BINARY_NAME="mhstudio-${TARGET_ARCH}"
echo ">>> Скачивание бинарника ${BINARY_NAME}..."

if ! download_file "${RAW_BASE}/${BINARY_NAME}?t=${T_STAMP}" "/opt/bin/mhstudio"; then
    echo "Не удалось скачать бинарник из папки dist/."
    echo "Попробуйте собрать вручную: go build ./cmd/server"
    exit 1
fi

chmod +x /opt/bin/mhstudio

# Скачиваем init-скрипт
echo ">>> Скачивание init-скрипта..."
download_file "${BASE_URL}/S95mihomo-web" "${INIT_DIR}/S95mihomo-web"
chmod +x "${INIT_DIR}/S95mihomo-web"

echo "=== Установка завершена! ==="
echo "Веб-интерфейс доступен по адресу: http://$(hostname -i 2>/dev/null || echo '<IP-роутера>'):8888"
echo ""
echo "Управление:"
echo "  mhstudio -start     — Запустить"
echo "  mhstudio -stop      — Остановить"
echo "  mhstudio -restart   — Перезапустить"

# Запускаем сервис
"${INIT_DIR}/S95mihomo-web" restart