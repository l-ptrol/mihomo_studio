#!/bin/bash
set -e

VERSION="2.1.7"
OUT_DIR="dist"

mkdir -p "$OUT_DIR"

echo "=== Сборка Mihomo Studio v${VERSION} ==="

# mips (big-endian)
echo "[1/3] Компиляция linux/mips..."
GOOS=linux GOARCH=mips go build -ldflags="-s -w -X main.Version=${VERSION}" -o "${OUT_DIR}/mhstudio-mips" ./cmd/server

# mipsle (little-endian)
echo "[2/3] Компиляция linux/mipsel..."
GOOS=linux GOARCH=mipsle go build -ldflags="-s -w -X main.Version=${VERSION}" -o "${OUT_DIR}/mhstudio-mipsel" ./cmd/server

# aarch64 (arm64)
echo "[3/3] Компиляция linux/aarch64..."
GOOS=linux GOARCH=arm64 go build -ldflags="-s -w -X main.Version=${VERSION}" -o "${OUT_DIR}/mhstudio-aarch64" ./cmd/server

echo "=== Сборка завершена! ==="
echo "Бинарники в директории ${OUT_DIR}/:"
ls -lh "${OUT_DIR}/"