#!/usr/bin/env python3
"""
Run the bot in debug mode.

Usage:
    python3 debug_mode.py

What it does:
- Connects to Waydroid via ADB
- Walks through every navigation tab
- Screenshots before and after every action
- Reads ticket counts and logs what OCR sees
- Checks Game Settings card set detection
- Does NOT register for any tournaments
- Does NOT play any hands

Output: debug/run_YYYYMMDD_HHMMSS/
  - Numbered PNG screenshots of every step
  - log.txt with what the bot detected at each step
  - Individual OCR crop images for ticket/membership regions

Send the whole debug/run_*/ folder and I can fix whatever is wrong.
"""
import sys
import os

# Fix import paths for running directly
sys.path.insert(0, os.path.dirname(__file__))

from bot.debug_runner import run_debug

if __name__ == "__main__":
    run_debug()
