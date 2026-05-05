# ClubGG Bot

Automated Stage 1 tournament registration and play for ClubGG on Linux via Waydroid.

---

## Setup Guides

- **[CachyOS / Arch → README-cachyos.md](README-cachyos.md)**
- **[Fedora → README-fedora.md](README-fedora.md)**

---

## Quick Summary

1. Install Tesseract + ADB
2. Initialise Waydroid with GApps: `sudo waydroid init -s GAPPS`
3. Install libhoudini for ARM translation + Play Store access
4. Install ClubGG from the Play Store inside Waydroid
5. Set Card Set 3 in ClubGG settings
6. Install Python packages: `pip install -r requirements.txt`
7. Run: `python3 main.py`

See the distro-specific guide above for exact commands.

---

## Running the Bot

```bash
waydroid session start
adb connect 192.168.240.112:5555
cd clubgg-bot
source venv/bin/activate
python3 main.py
```

Make sure ClubGG is open on the Stage 1 lobby before clicking START BOT.
