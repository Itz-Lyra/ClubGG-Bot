#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# ClubGG Bot — Setup Script
# Supports: CachyOS / Arch  +  Fedora 39/40/41
# Run as your normal user (sudo will be called when needed)
# Usage: bash install.sh
# ─────────────────────────────────────────────────────────────────────────────
set -e

BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✓ $*${NC}"; }
warn() { echo -e "${YELLOW}  ⚠ $*${NC}"; }
info() { echo -e "${CYAN}${BOLD}→ $*${NC}"; }
die()  { echo -e "${RED}  ✗ $*${NC}"; exit 1; }
ask()  { echo -e "${BOLD}  $*${NC}"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║        ClubGG Bot — Setup Script         ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════╝${NC}"
echo ""

# ── Detect distro ─────────────────────────────────────────────────────────────
if [ -f /etc/os-release ]; then
    source /etc/os-release
    DISTRO="${ID,,}"
else
    DISTRO="unknown"
fi

case "$DISTRO" in
    cachyos|arch|manjaro|endeavouros) DISTRO_FAMILY="arch" ;;
    fedora)                           DISTRO_FAMILY="fedora" ;;
    *) warn "Unrecognised distro '$DISTRO' — attempting Arch-style install"; DISTRO_FAMILY="arch" ;;
esac

ok "Detected: $PRETTY_NAME ($DISTRO_FAMILY)"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — System packages
# ─────────────────────────────────────────────────────────────────────────────
info "Step 1/5 — Installing system packages"

if [ "$DISTRO_FAMILY" = "arch" ]; then
    sudo pacman -Sy --needed --noconfirm \
        android-tools \
        tesseract \
        tesseract-data-eng \
        python \
        python-pip \
        waydroid \
        2>/dev/null || {
            # waydroid may be AUR-only on some Arch setups
            sudo pacman -Sy --needed --noconfirm \
                android-tools tesseract tesseract-data-eng python python-pip
            warn "waydroid not in official repos — will install from AUR"
            if command -v paru &>/dev/null; then
                paru -S --needed --noconfirm waydroid
            elif command -v yay &>/dev/null; then
                yay -S --needed --noconfirm waydroid
            else
                die "AUR helper not found. Install paru: https://github.com/Morganamilo/paru\nthen re-run this script."
            fi
        }

elif [ "$DISTRO_FAMILY" = "fedora" ]; then
    sudo dnf install -y \
        android-tools \
        tesseract \
        tesseract-langpack-eng \
        python3 \
        python3-pip \
        libGL \
        libxkbcommon \
        2>/dev/null || true

    # Waydroid on Fedora needs the COPR repo
    if ! command -v waydroid &>/dev/null; then
        info "Adding Waydroid COPR repo..."
        sudo dnf copr enable -y aleasto/waydroid
        sudo dnf install -y waydroid
    fi

    # Fedora: binder kernel module required
    info "Loading binder kernel module..."
    sudo modprobe binder_linux devices="binder,hwbinder,vndbinder" 2>/dev/null || \
        warn "binder_linux module failed — may need: sudo dnf install kernel-modules-extra"

    # Make binder persistent across reboots
    echo "binder_linux" | sudo tee /etc/modules-load.d/binder.conf >/dev/null

    # Fedora: SELinux must be permissive for Waydroid
    SELINUX_STATUS=$(getenforce 2>/dev/null || echo "Disabled")
    if [ "$SELINUX_STATUS" = "Enforcing" ]; then
        warn "SELinux is Enforcing — setting to Permissive (required for Waydroid)"
        sudo setenforce 0
        sudo sed -i 's/SELINUX=enforcing/SELINUX=permissive/' /etc/selinux/config
        ok "SELinux set to permissive"
    else
        ok "SELinux: $SELINUX_STATUS"
    fi

    # Fedora: firewall — allow Waydroid subnet
    if command -v firewall-cmd &>/dev/null; then
        sudo firewall-cmd --zone=trusted --add-source=192.168.240.0/24 --permanent 2>/dev/null || true
        sudo firewall-cmd --reload 2>/dev/null || true
        ok "Firewall: Waydroid subnet allowed"
    fi
fi

ok "System packages installed"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — Waydroid init
# ─────────────────────────────────────────────────────────────────────────────
info "Step 2/5 — Waydroid initialisation"

if waydroid status 2>/dev/null | grep -q "running\|not running"; then
    ok "Waydroid already initialised"
else
    info "Initialising Waydroid (downloads ~400MB Android image — no GAPPS needed)..."
    sudo waydroid init
    ok "Waydroid initialised"
fi
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — Python environment + pip packages
# ─────────────────────────────────────────────────────────────────────────────
info "Step 3/5 — Python environment"

cd "$SCRIPT_DIR"

if [ ! -d venv ]; then
    python3 -m venv venv
    ok "Virtual environment created"
fi

venv/bin/pip install --upgrade pip -q
venv/bin/pip install -r requirements.txt -q
ok "Python packages installed (venv/)"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — Build binary
# ─────────────────────────────────────────────────────────────────────────────
info "Step 4/5 — Building binary"

source venv/bin/activate

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
    main.py -y --log-level WARN 2>/dev/null

mv dist/clubgg-bot dist/clubgg-bot.x86_64
chmod +x dist/clubgg-bot.x86_64
deactivate

ok "Binary: dist/clubgg-bot.x86_64"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — Summary
# ─────────────────────────────────────────────────────────────────────────────
info "Step 5/5 — Done"
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║  Setup complete!  Read README.md for the next steps.         ║${NC}"
echo -e "${BOLD}║                                                              ║${NC}"
echo -e "${BOLD}║  Quick summary:                                              ║${NC}"
echo -e "${BOLD}║  1. Start Waydroid:   waydroid session start                 ║${NC}"
echo -e "${BOLD}║  2. Connect ADB:      adb connect 192.168.240.112:5555       ║${NC}"
echo -e "${BOLD}║  3. Install ClubGG:   see README.md → ClubGG Setup           ║${NC}"
echo -e "${BOLD}║  4. Run the bot:      ./dist/clubgg-bot.x86_64               ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
