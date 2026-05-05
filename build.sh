#!/usr/bin/env bash
# CachyOS / Arch build script
set -e

echo "Building ClubGG Bot (Arch/CachyOS)..."

pip install --break-system-packages -r requirements.txt 2>/dev/null || \
    pip install -r requirements.txt

pyinstaller --onefile \
    --name clubgg-bot \
    --add-data 'assets:assets' \
    --add-data 'gui/styles.qss:gui' \
    --hidden-import cv2 \
    --hidden-import PyQt6.QtCore \
    --hidden-import PyQt6.QtWidgets \
    --hidden-import PyQt6.QtGui \
    --hidden-import pytesseract \
    --hidden-import treys \
    --hidden-import distro \
    --collect-all PyQt6 \
    main.py

mv dist/clubgg-bot dist/clubgg-bot.x86_64
chmod +x dist/clubgg-bot.x86_64

echo "Build complete: dist/clubgg-bot.x86_64"
