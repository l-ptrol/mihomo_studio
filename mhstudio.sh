#!/bin/sh

#
# Copyright (C) 2024-2025 by l-ptrol
#

# === КОНСТАНТЫ И ПЕРЕМЕННЫЕ ===
SERVICE_NAME="Mihomo Studio"
BRANCH="master"
BASE_URL="https://raw.githubusercontent.com/l-ptrol/mihomo_studio/${BRANCH}"
INSTALL_DIR="/opt/scripts"
INIT_DIR="/opt/etc/init.d"
MIHOMO_ETC_DIR="/opt/etc/mihomo" # Добавлено для совместимости и управления конфигами
PY_SCRIPT="mihomo_editor.py"
INIT_SCRIPT="S95mihomo-web"
PACKAGES="python3-base python3-light python3-email python3-urllib python3-codecs"

# === ФУНКЦИИ ===

# --- Получение локальной версии ---
get_local_version() {
    local_py_path="$INSTALL_DIR/$PY_SCRIPT"
    if [ -f "$local_py_path" ]; then
        # Ищем строку с <title>, извлекаем 'v' и номер версии, затем удаляем 'v'
        grep '<title>Mihomo Editor v' "$local_py_path" | sed -n 's/.*Mihomo Editor v\([^<]*\)<.*/\1/p'
    else
        echo "0"
    fi
}

# --- Получение удаленной версии ---
get_remote_version() {
    local remote_py_url="$BASE_URL/$PY_SCRIPT"
    local temp_py_path="/tmp/$PY_SCRIPT.tmp"
    
    # Скачиваем удаленный скрипт во временный файл
    wget --no-check-certificate -O "$temp_py_path" "$remote_py_url" >/dev/null 2>&1
    
    if [ $? -eq 0 ] && [ -s "$temp_py_path" ]; then
        # Извлекаем версию из временного файла
        grep '<title>Mihomo Editor v' "$temp_py_path" | sed -n 's/.*Mihomo Editor v\([^<]*\)<.*/\1/p'
        rm "$temp_py_path" # Удаляем временный файл
    else
        # Если скачать не удалось, возвращаем 0 и удаляем пустой файл (если создался)
        [ -f "$temp_py_path" ] && rm "$temp_py_path"
        echo "0"
    fi
}

# --- Отображение заголовка ---
display_header() {
    # Переменные local_version и remote_version должны быть определены
    # перед вызовом этой функции в глобальной области видимости.

    # Коррекция отображения для пользователя
    if [ "$local_version" = "0" ]; then
        display_local="не установлено"
    else
        display_local="$local_version"
    fi

    if [ "$remote_version" = "0" ]; then
        display_remote="не удалось получить"
    else
        display_remote="$remote_version"
    fi

    echo "========================================"
    echo " ${SERVICE_NAME} Installer"
    echo "----------------------------------------"
    echo " Установленная версия:  ${display_local}"
    echo " Доступная версия:      ${display_remote}"
    echo "========================================"
}

# --- Справка ---
usage() {
    echo "Использование: mhstudio {update|reinstall|uninstall|uninstall-full}"
    echo "  mhstudio -update          - Обновить сервис (если есть новая версия)"
    echo "  mhstudio -reinstall       - Принудительно переустановить/обновить сервис"
    echo "  mhstudio -uninstall       - Удалить сервис (сохранив зависимости)"
    echo "  mhstudio -uninstall-full  - Удалить сервис и все его зависимости"
}

# --- Установка зависимостей ---
install_dependencies() {
    echo ">>> Проверка и установка зависимостей..."
    opkg update
    for pkg in $PACKAGES; do
        if ! opkg list-installed | grep -q "^$pkg"; then
            echo "Устанавливаем $pkg..."
            opkg install "$pkg"
        else
            echo "$pkg уже установлен."
        fi
    done
}

# --- Создание директорий ---
create_dirs() {
    echo ">>> Создание директорий..."
    mkdir -p "$INSTALL_DIR"
    mkdir -p "$INIT_DIR"
    mkdir -p "${MIHOMO_ETC_DIR}/profiles"
    mkdir -p "${MIHOMO_ETC_DIR}/backup"
}

# --- Скачивание файлов ---
download_files() {
    echo ">>> Скачивание файлов..."
    
    wget --no-check-certificate -O "$INSTALL_DIR/$PY_SCRIPT" "${BASE_URL}/${PY_SCRIPT}"
    if [ $? -ne 0 ]; then echo "ОШИБКА: Не удалось скачать $PY_SCRIPT."; exit 1; fi

    wget --no-check-certificate -O "$INIT_DIR/$INIT_SCRIPT" "${BASE_URL}/${INIT_SCRIPT}"
    if [ $? -ne 0 ]; then echo "ОШИБКА: Не удалось скачать $INIT_SCRIPT."; exit 1; fi
}

# --- Установка прав доступа ---
set_permissions() {
    echo ">>> Установка прав доступа..."
    chmod +x "$INSTALL_DIR/$PY_SCRIPT"
    chmod +x "$INIT_DIR/$INIT_SCRIPT"
}

# --- Перезапуск сервиса ---
restart_service() {
    if [ -f "$INIT_DIR/$INIT_SCRIPT" ]; then
        echo ">>> Перезапуск сервиса..."
        "$INIT_DIR/$INIT_SCRIPT" restart
    else
        echo "ПРЕДУПРЕЖДЕНИЕ: Скрипт инициализации не найден. Не удалось перезапустить сервис."
    fi
}

# --- Полный цикл установки ---
install_service() {
    echo ">>> Начинаем установку/обновление..."
    
    # Остановка сервиса (если запущен)
    if [ -f "$INIT_DIR/$INIT_SCRIPT" ]; then
        "$INIT_DIR/$INIT_SCRIPT" stop
    fi

    install_dependencies
    create_dirs
    download_files
    set_permissions
    restart_service

    echo "=== Установка/обновление завершено! ==="
    echo "Веб-интерфейс (если запущен) доступен по адресу: http://$(uname -n):8888"
}

# --- Удаление сервиса ---
uninstall_service() {
    local mode=$1
    echo "[1/2] Остановка и удаление службы..."
    if [ -f "$INIT_DIR/$INIT_SCRIPT" ]; then
        "$INIT_DIR/$INIT_SCRIPT" stop
    fi
    rm -f "$INSTALL_DIR/$PY_SCRIPT"
    rm -f "$INIT_DIR/$INIT_SCRIPT"

    if [ "$mode" = "full" ]; then
        echo "[2/2] Удаление зависимостей..."
        echo "ВНИМАНИЕ: Следующие пакеты будут удалены: $PACKAGES"
        echo "Это может повлиять на работу других приложений."
        opkg remove $PACKAGES
    else
        echo "[2/2] Зависимости не были удалены."
    fi

    echo "=== Удаление завершено! ==="
}


# --- ТОЧКА ВХОДА ---

# Проверяем, установлен ли сервис
if [ ! -f "$INSTALL_DIR/$PY_SCRIPT" ]; then
    echo "Сервис Mihomo Studio не найден. Запускаю первичную установку..."
    install_service
    echo "Установка завершена. Для дальнейшего управления используйте команды:"
    usage
    exit 0
fi

# Если сервис уже установлен, обрабатываем аргументы
local_version=$(get_local_version)
remote_version=$(get_remote_version)
display_header

# Если аргументов нет, показать справку
if [ -z "$1" ]; then
    usage
    exit 0
fi

case "$1" in
    -update)
        if [ "$local_version" = "0" ]; then
            echo "Сервис не установлен. Используйте 'install'."
            exit 1
        fi
        # Сравнение версий. `sort -V` корректно сравнивает номера версий.
        latest=$(printf "%s\n%s" "$local_version" "$remote_version" | sort -V | tail -n1)
        if [ "$local_version" = "$remote_version" ] || [ "$local_version" = "$latest" ]; then
            echo "У вас уже установлена последняя версия."
            exit 0
        fi
        echo "Доступно обновление. Установка..."
        install_service
        ;;
    -reinstall)
        echo "Принудительная переустановка..."
        install_service
        ;;
    -uninstall)
        uninstall_service
        ;;
    -uninstall-full)
        uninstall_service "full"
        ;;
    *)
        echo "Неизвестная команда: $1"
        usage
        exit 1
        ;;
esac