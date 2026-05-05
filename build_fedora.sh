#!/usr/bin/env bash
# Fedora build script (requires venv — see Addendum 5)
set -e

echo "Building ClubGG Bot (Fedora)..."

# Ensure venv exists
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "Virtual environment created."
fi

source venv/bin/activate
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
    --collect-all cv2 \
    --collect-all PyQt6 \
    main.py

mv dist/clubgg-bot dist/clubgg-bot.x86_64
chmod +x dist/clubgg-bot.x86_64

echo "Build complete: dist/clubgg-bot.x86_64"
