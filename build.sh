#!/bin/bash
set -e

VERSION="2.2.71"
OUT_DIR="dist"

mkdir -p "$OUT_DIR"

echo "=== Mihomo Studio Build v${VERSION} ==="

# 1. mips (big-endian)
echo "[1/3] Компиляция linux/mips..."
GOOS=linux GOARCH=mips GOMIPS=softfloat go build -ldflags="-s -w -X main.Version=${VERSION}" -o "${OUT_DIR}/mhstudio-mips" ./cmd/server

# 2. mipsle (little-endian)
echo "[2/3] Компиляция linux/mipsel..."
GOOS=linux GOARCH=mipsle GOMIPS=softfloat go build -ldflags="-s -w -X main.Version=${VERSION}" -o "${OUT_DIR}/mhstudio-mipsel" ./cmd/server

# 3. aarch64 (arm64)
echo "[3/3] Компиляция linux/aarch64..."
GOOS=linux GOARCH=arm64 go build -ldflags="-s -w -X main.Version=${VERSION}" -o "${OUT_DIR}/mhstudio-aarch64" ./cmd/server

echo "=== Сборка завершена! ==="
echo "Бинарники в директории ${OUT_DIR}/:"
ls -lh "${OUT_DIR}/"