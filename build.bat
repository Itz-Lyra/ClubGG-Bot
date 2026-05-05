@echo off
echo Building ClubGG Bot (Windows)...

pip install -r requirements.txt

pyinstaller --onefile --windowed ^
    --name clubgg-bot ^
    --add-data "assets;assets" ^
    --add-data "gui/styles.qss;gui" ^
    --hidden-import cv2 ^
    --hidden-import PyQt6.QtCore ^
    --hidden-import PyQt6.QtWidgets ^
    --hidden-import PyQt6.QtGui ^
    --hidden-import pytesseract ^
    --hidden-import treys ^
    --hidden-import distro ^
    --collect-all PyQt6 ^
    main.py

echo Build complete: dist\clubgg-bot.exe
