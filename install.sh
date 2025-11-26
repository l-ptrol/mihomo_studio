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
    LOCAL_VERSION=$(get_local_version)
    REMOTE_VERSION=$(get_remote_version)

    # Коррекция отображения для пользователя
    if [ "$LOCAL_VERSION" = "0" ]; then
        DISPLAY_LOCAL="не установлено"
    else
        DISPLAY_LOCAL="$LOCAL_VERSION"
    fi

    if [ "$REMOTE_VERSION" = "0" ]; then
        DISPLAY_REMOTE="не удалось получить"
    else
        DISPLAY_REMOTE="$REMOTE_VERSION"
    fi

    echo "========================================"
    echo " ${SERVICE_NAME} Installer"
    echo "----------------------------------------"
    echo " Установленная версия:  ${DISPLAY_LOCAL}"
    echo " Доступная версия:      ${DISPLAY_REMOTE}"
    echo "========================================"
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

# --- Скачивание файлов ---
download_files() {
    echo ">>> Скачивание файлов..."
    # Создание директорий
    mkdir -p "$INSTALL_DIR"
    mkdir -p "$INIT_DIR"
    mkdir -p "${MIHOMO_ETC_DIR}/profiles"
    mkdir -p "${MIHOMO_ETC_DIR}/backup"

    # Скачивание
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
    download_files
    set_permissions
    restart_service

    echo "=== Установка/обновление завершено! ==="
    echo "Веб-интерфейс (если запущен) доступен по адресу: http://$(uname -n):8888"
}

# --- Синоним для обновления ---
update_service() {
    install_service
}

# --- Удаление сервиса ---
uninstall_service() {
    echo ">>> Начинаем удаление..."

    # 1. Остановка сервиса
    if [ -f "$INIT_DIR/$INIT_SCRIPT" ]; then
        echo "[1/3] Остановка сервиса..."
        "$INIT_DIR/$INIT_SCRIPT" stop
    fi

    # 2. Удаление файлов
    echo "[2/3] Удаление файлов..."
    rm -f "$INSTALL_DIR/$PY_SCRIPT"
    rm -f "$INIT_DIR/$INIT_SCRIPT"
    # Файл версии больше не используется, строка удалена.
    # Опционально: можно добавить удаление всей директории /opt/etc/mihomo, но это может удалить пользовательские конфиги
    # rm -rf "$MIHOMO_ETC_DIR" 

    # 3. Запрос на удаление зависимостей
    echo "[3/3] Удаление зависимостей..."
    echo "ВНИМАНИЕ: Следующие пакеты были установлены как зависимости:"
    echo "$PACKAGES"
    echo "Эти пакеты могут использоваться другими приложениями. Их удаление может нарушить их работу."
    
    read -p "Вы хотите удалить эти зависимости? (y/N): " answer
    if [ "$answer" = "y" ] || [ "$answer" = "Y" ]; then
        echo "Удаляем зависимости..."
        opkg remove $PACKAGES
    else
        echo "Зависимости не будут удалены."
    fi

    echo "=== ${SERVICE_NAME} успешно удален. ==="
}

# --- Главное меню ---
main_menu() {
    # Сценарий 1: Не установлено
    if [ "$LOCAL_VERSION" = "0" ]; then
        echo "Сервис не установлен. Выберите действие:"
        echo " 1. Установить"
        echo " q. Выход"
        read -p "Ваш выбор: " choice
        case "$choice" in
            1) install_service ;;
            q|Q) exit 0 ;;
            *) echo "Неверный выбор." ;;
        esac
    # Сценарий 2: Есть обновление
    elif [ "$LOCAL_VERSION" != "$REMOTE_VERSION" ] && [ "$REMOTE_VERSION" != "0" ]; then
        echo "Доступна новая версия! Выберите действие:"
        echo " 1. Обновить"
        echo " 2. Удалить"
        echo " q. Выход"
        read -p "Ваш выбор: " choice
        case "$choice" in
            1) update_service ;;
            2) uninstall_service ;;
            q|Q) exit 0 ;;
            *) echo "Неверный выбор." ;;
        esac
    # Сценарий 3: Последняя версия установлена
    else
        echo "Установлена последняя версия. Выберите действие:"
        echo " 1. Переустановить"
        echo " 2. Удалить"
        echo " q. Выход"
        read -p "Ваш выбор: " choice
        case "$choice" in
            1) install_service ;;
            2) uninstall_service ;;
            q|Q) exit 0 ;;
            *) echo "Неверный выбор." ;;
        esac
    fi
}

# === ТОЧКА ВХОДА ===
display_header
main_menu