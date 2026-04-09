#!/bin/bash
set -e

VERSION="2.2.57"
OUT_DIR="dist"

mkdir -p "$OUT_DIR"

echo "=== Сборка# === Mihomo Studio v2.2.57 (Go) — Установщик ==="

# mips (big-endian)
echo "[1/3] Компиляция linux/mips..."
GOOS=linux GOARCH=mips GOMIPS=softfloat go build -ldflags="-s -w -X main.Version=${VERSION}" -o "${OUT_DIR}/mhstudio-mips" ./cmd/server

# mipsle (little-endian)
echo "[2/3] Компиляция linux/mipsel..."
GOOS=linux GOARCH=mipsle GOMIPS=softfloat go build -ldflags="-s -w -X main.Version=${VERSION}" -o "${OUT_DIR}/mhstudio-mipsel" ./cmd/server

# aarch64 (arm64)
echo "[3/3] Компиляция linux/aarch64..."
GOOS=linux GOARCH=arm64 go build -ldflags="-s -w -X main.Version=${VERSION}" -o "${OUT_DIR}/mhstudio-aarch64" ./cmd/server

# mips64 (big-endian 64-bit)
echo "[4/5] Компиляция linux/mips64..."
GOOS=linux GOARCH=mips64 go build -ldflags="-s -w -X main.Version=${VERSION}" -o "${OUT_DIR}/mhstudio-mips64" ./cmd/server

# mips64le (little-endian 64-bit)
echo "[5/5] Компиляция linux/mips64le..."
GOOS=linux GOARCH=mips64le go build -ldflags="-s -w -X main.Version=${VERSION}" -o "${OUT_DIR}/mhstudio-mips64le" ./cmd/server


echo "=== Сборка завершена! ==="
echo "Бинарники в директории ${OUT_DIR}/:"
ls -lh "${OUT_DIR}/"