#!/bin/bash
# Скачивает ffmpeg и ffprobe для macOS без Homebrew.
# Источник: https://evermeet.cx/ffmpeg/

set -e
cd "$(dirname "$0")/.."
BIN_DIR="$(pwd)/bin"
mkdir -p "$BIN_DIR"

echo "Скачиваю ffmpeg и ffprobe в $BIN_DIR ..."

curl -L -o "$BIN_DIR/ffmpeg.zip" "https://evermeet.cx/ffmpeg/ffmpeg-7.1.zip"
curl -L -o "$BIN_DIR/ffprobe.zip" "https://evermeet.cx/ffmpeg/ffprobe-7.1.zip"

unzip -o -j "$BIN_DIR/ffmpeg.zip" -d "$BIN_DIR"
unzip -o -j "$BIN_DIR/ffprobe.zip" -d "$BIN_DIR"
rm -f "$BIN_DIR/ffmpeg.zip" "$BIN_DIR/ffprobe.zip"

chmod +x "$BIN_DIR/ffmpeg" "$BIN_DIR/ffprobe"

echo "Готово:"
"$BIN_DIR/ffmpeg" -version | head -1
"$BIN_DIR/ffprobe" -version | head -1
