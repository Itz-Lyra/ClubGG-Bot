#!/usr/bin/env python3
"""
Template capture script.

Run this on the machine where Waydroid is running to capture all missing
template images from live device screenshots. Takes ADB screenshots of
specific screen states and crops each template precisely.

Usage:
    python3 capture_templates.py

The script guides you through each required screen state and captures
the template automatically.
"""
import os, sys, subprocess, time, cv2, numpy as np

ADB_DEVICE = "192.168.240.112:5555"
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "assets", "templates")
os.makedirs(TEMPLATES_DIR, exist_ok=True)

# TITLE_BAR offset for scrcpy window screenshots (0 for direct ADB screenshots)
# When capturing via `adb exec-out screencap -p`, there is NO title bar offset.
TITLE = 0


def adb_screenshot() -> np.ndarray:
    """Take ADB screenshot directly — no scrcpy window, no title bar."""
    cmd = ["adb", "-s", ADB_DEVICE, "exec-out", "screencap", "-p"]
    result = subprocess.run(cmd, capture_output=True, timeout=15)
    data = np.frombuffer(result.stdout, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("Screenshot failed — is Waydroid connected?")
    return img


def save_crop(name: str, img: np.ndarray, y1_pct: float, x1_pct: float,
              y2_pct: float, x2_pct: float) -> None:
    h, w = img.shape[:2]
    y1 = int(y1_pct / 100 * h)
    x1 = int(x1_pct / 100 * w)
    y2 = int(y2_pct / 100 * h)
    x2 = int(x2_pct / 100 * w)
    crop = img[y1:y2, x1:x2]
    path = os.path.join(TEMPLATES_DIR, name)
    cv2.imwrite(path, crop)
    print(f"  Saved: {name} ({crop.shape[1]}x{crop.shape[0]})")


def wait_and_shoot(prompt: str) -> np.ndarray:
    input(f"\n>>> {prompt}\n    Press ENTER when ready...")
    print("  Capturing...", end=" ", flush=True)
    img = adb_screenshot()
    print(f"OK ({img.shape[1]}x{img.shape[0]})")
    return img


# ────────────────────────────────────────────────────────────────────────────
# Capture steps — each waits for you to put the app in the right state
# These coordinates are percentages for native ADB screenshots (no title bar)
# ────────────────────────────────────────────────────────────────────────────

def capture_all():
    print("=== ClubGG Bot Template Capture ===")
    print(f"ADB device: {ADB_DEVICE}")
    print(f"Output dir: {TEMPLATES_DIR}")
    print()
    print("This script will guide you through each screen state.")
    print("Navigate ClubGG to the described state before pressing ENTER.")
    print()

    # ── Stage 1 lobby ────────────────────────────────────────────────────
    img = wait_and_shoot(
        "Stage 1 lobby visible with Registering, Late Reg., Running, and Announced badges"
    )
    save_crop("badge_registering.png", img, 38.0, 1.4, 48.2, 29.0)
    save_crop("badge_late_reg.png",    img, 17.1, 1.4, 27.3, 29.0)
    save_crop("badge_running.png",     img, 69.7, 1.4, 79.9, 29.0)
    save_crop("tab_stage1.png",        img, 93.5, 32.0, 100.0, 44.8)

    # ── Lobby with Me badge ───────────────────────────────────────────────
    img = wait_and_shoot(
        "Stage 1 lobby with at least one tournament showing the red 'Me' badge (top-right of card)"
    )
    # Me badge is at top-right of a Registering card — find first Me card
    # Use region: right 15% of screen, upper lobby area
    save_crop("badge_me.png", img, 38.0, 88.2, 42.2, 97.6)

    # ── Announced badge ───────────────────────────────────────────────────
    img = wait_and_shoot(
        "Stage 1 lobby scrolled to show grey 'Announced' badges"
    )
    save_crop("badge_announced.png", img, 58.5, 1.4, 68.7, 29.0)

    # ── Tournament detail — Register ─────────────────────────────────────
    img = wait_and_shoot(
        "Tournament detail page with green REGISTER button at bottom (not yet registered)"
    )
    save_crop("btn_register.png", img, 93.5, 3.7, 99.1, 96.3)

    # ── Tournament detail — Unregister ───────────────────────────────────
    img = wait_and_shoot(
        "Tournament detail page with red UNREGISTER button at bottom (already registered)"
    )
    save_crop("btn_unregister.png", img, 93.5, 3.7, 99.1, 96.3)

    # ── Registration modal ────────────────────────────────────────────────
    img = wait_and_shoot(
        "Registration modal open ('Tournament Registration' header visible, Register button)"
    )
    save_crop("text_tournament_reg.png", img, 44.3, 3.2, 48.4, 96.8)
    save_crop("btn_register_modal.png",  img, 93.5, 3.7, 99.1, 96.3)

    # ── Success modal ─────────────────────────────────────────────────────
    img = wait_and_shoot(
        "Registration success modal ('Good Luck!' visible, Confirm button)"
    )
    save_crop("text_good_luck.png", img, 71.5, 22.5, 76.8, 79.0)
    save_crop("btn_confirm.png",    img, 93.5, 3.7, 99.1, 96.3)

    # ── Go to Table popup ─────────────────────────────────────────────────
    img = wait_and_shoot(
        "Go to Table popup visible ('Would you like to go to the table?' with Cancel + Go to Table buttons)"
    )
    save_crop("text_go_to_table_prompt.png", img,  8.9, 4.8, 31.3, 95.8)
    save_crop("btn_go_to_table.png",          img, 26.1, 49.1, 32.1, 95.8)
    save_crop("btn_cancel.png",               img, 26.1,  4.8, 32.1, 48.5)

    # ── Pre-game countdown ────────────────────────────────────────────────
    img = wait_and_shoot(
        "Tournament table open with countdown circle ('Tournament Starts in N seconds')"
    )
    save_crop("text_tournament_starts.png", img, 40.2, 28.2, 44.7, 73.3)

    # ── In-hand action (your turn) ────────────────────────────────────────
    img = wait_and_shoot(
        "At a table, YOUR TURN (Fold + Call/Check + ^ caret all visible at bottom)"
    )
    save_crop("btn_fold.png",  img, 92.0,  2.1, 99.1, 34.6)
    save_crop("btn_caret.png", img, 91.5, 49.9, 98.0, 63.6)
    save_crop("btn_call.png",  img, 92.0, 49.9, 99.1, 98.2)
    save_crop("tab_active_nlh.png", img, 5.1, 0.0, 11.8, 20.9)

    # ── Bust-out screen ───────────────────────────────────────────────────
    img = wait_and_shoot(
        "Bust-out screen visible ('SEE YOU NEXT TIME' in large white text)"
    )
    save_crop("text_see_you.png", img, 5.0, 5.0, 20.0, 95.0)
    save_crop("btn_close.png",    img, 93.5, 3.7, 99.1, 46.4)

    # ── Win screen ────────────────────────────────────────────────────────
    img = wait_and_shoot(
        "Win screen visible ('THANK YOU FOR PLAYING!' in large white text)"
    )
    save_crop("text_thank_you.png", img, 5.0, 5.0, 22.0, 95.0)

    # ── Break screen ──────────────────────────────────────────────────────
    img = wait_and_shoot(
        "Tournament break screen ('Players are now on break.' text visible)"
    )
    save_crop("text_on_break.png", img, 35.0, 10.0, 50.0, 90.0)

    # ── Me page ───────────────────────────────────────────────────────────
    img = wait_and_shoot(
        "Me tab open showing Mercy09 profile and Tournament Tickets section"
    )
    save_crop("tab_me.png",       img, 93.5, 86.2, 100.0, 100.0)
    # Ticket count rows — capture for OCR calibration reference
    save_crop("me_tickets_area.png", img, 45.0, 0.0, 75.0, 100.0)

    # ── Login screen ─────────────────────────────────────────────────────
    img = wait_and_shoot(
        "ClubGG login screen (email + password fields visible)"
    )
    save_crop("screen_login.png",   img, 25.0, 5.0, 75.0, 95.0)
    save_crop("email_field.png",    img, 33.0, 10.0, 41.0, 90.0)
    save_crop("password_field.png", img, 46.0, 10.0, 54.0, 90.0)
    save_crop("btn_login.png",      img, 60.0, 10.0, 68.0, 90.0)

    print("\n=== Capture complete! ===")
    print(f"Templates saved to: {TEMPLATES_DIR}")
    missing = []
    for f in ["text_see_you.png", "text_thank_you.png", "text_on_break.png", "screen_login.png"]:
        p = os.path.join(TEMPLATES_DIR, f)
        if not os.path.exists(p) or os.path.getsize(p) < 1000:
            missing.append(f)
    if missing:
        print(f"\nSTILL MISSING (stubs active — bot will use OCR fallback):")
        for f in missing:
            print(f"  {f}")


if __name__ == "__main__":
    capture_all()
