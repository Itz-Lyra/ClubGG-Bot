# ClubGG Bot — Fedora Setup

Assumes Waydroid and Python are already installed.

---

## Step 1 — Install dependencies

```bash
sudo dnf install tesseract tesseract-langpack-eng android-tools python3 python3-pip
```

---

## Step 2 — Fedora-specific Waydroid requirements

```bash
# Load binder kernel module
sudo modprobe binder_linux devices="binder,hwbinder,vndbinder"

# Make it load on every boot
echo "binder_linux" | sudo tee /etc/modules-load.d/binder.conf

# SELinux must be permissive
sudo setenforce 0
sudo sed -i 's/SELINUX=enforcing/SELINUX=permissive/' /etc/selinux/config
```

---

## Step 3 — Install Python packages

```bash
cd clubgg-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## Step 4 — Initialise Waydroid with GApps

```bash
sudo waydroid init -s GAPPS
```

If already initialised without GApps, reset it first:

```bash
sudo waydroid container stop
sudo waydroid init -f -s GAPPS
```

---

## Step 5 — Install libhoudini (ARM translation + Play Store support)

ClubGG is ARM-only and is only available on the Play Store. You need libhoudini to run ARM apps on your x86_64 PC, and GApps to access the Play Store.

```bash
git clone https://github.com/casualsnek/waydroid_script
cd waydroid_script
sudo pip3 install -r requirements.txt
sudo python3 main.py install libhoudini
```

Restart Waydroid after:

```bash
sudo waydroid container stop
waydroid session start
```

---

## Step 6 — Connect ADB

```bash
waydroid session start
adb connect 192.168.240.112:5555
adb devices
# Should show: 192.168.240.112:5555   device
```

If ADB times out, the firewall is blocking it:

```bash
sudo firewall-cmd --zone=trusted --add-source=192.168.240.0/24 --permanent
sudo firewall-cmd --reload
adb connect 192.168.240.112:5555
```

---

## Step 7 — Install ClubGG from Play Store

Open the Waydroid window, sign into the Play Store with a Google account, search for **ClubGG** and install it. libhoudini handles the ARM translation automatically.

Sign into ClubGG with your GGPoker account.

---

## Step 8 — Set Card Set 3 (required)

In ClubGG: **Settings → Game Settings → Cards → Card Set 3**

The bot's suit detection only works with Card Set 3.

---

## Step 9 — Run the bot

```bash
cd clubgg-bot
source venv/bin/activate
python3 main.py
```

Make sure you're on the Stage 1 lobby in ClubGG before clicking START BOT.

---

## Every session

```bash
waydroid session start
adb connect 192.168.240.112:5555
cd clubgg-bot
source venv/bin/activate
python3 main.py
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `adb connect` times out | Add firewall rule (Step 6), then `adb kill-server && adb start-server` |
| Waydroid won't start | `sudo modprobe binder_linux devices="binder,hwbinder,vndbinder"` then retry |
| ClubGG not on Play Store / crashes | libhoudini not installed — redo Step 5 |
| Play Store won't open | GApps not installed — redo Step 4 with `-s GAPPS` |
| "Card Set 3 not confirmed" | ClubGG → Game Settings → Cards → Set 3 |
| All-in swipe lands wrong | `python3 tools/calibrate.py` → click ^ button → update `shove_x1/y1/x2/y2` in config.json |
| binder_linux module not found | `sudo dnf install kernel-modules-extra` then reboot |
