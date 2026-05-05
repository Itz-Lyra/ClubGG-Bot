#!/usr/bin/env bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# Create venv if missing
if [ ! -f venv/bin/activate ]; then
    echo "Setting up virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate

# Install/update packages if needed
if ! python3 -c "import cv2, pytesseract, PyQt6, treys" &>/dev/null; then
    echo "Installing requirements..."
    pip install -r requirements.txt -q
fi

python3 main.py
