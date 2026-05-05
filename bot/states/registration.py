"""
4-step tournament registration flow (Section 04).
Also handles Me page ticket reading and startup sequence.
"""
from __future__ import annotations
import cv2

import logging
import time
from typing import Optional
from ..ocr import ocr_text  # noqa: used in verify_card_set and modal detection
from .lobby import navigate_to_tab

log = logging.getLogger(__name__)


def read_ticket_inventory(adb, vision_detector, screen_w: int, screen_h: int) -> dict:
    """
    Navigate to Me tab, read ticket counts and membership tier.
    Returns dict with final_tickets, stage2_tickets, stage1_tickets, membership.
    """
    from ..vision import State, REGIONS
    from ..ocr import ocr_ticket_count, ocr_membership_tier

    # Navigate to Me tab using verified coordinates
    log.info("Navigating to Me tab for ticket inventory")
    from ..vision import NAV_TABS
    adb.tap(*NAV_TABS["me"])
    time.sleep(2.0)

    # Verify we're on Me page
    screen = adb.screenshot()
    state = vision_detector.detect(screen)
    if state != State.ME_PAGE:
        log.warning("Could not navigate to Me page — using defaults")
        navigate_to_tab(adb, "stage1")
        return {"final_tickets": 0, "stage2_tickets": 0, "stage1_tickets": 0, "membership": "Free"}

    # Read ticket counts — regions verified from SM-S948U Me page screenshots
    # Me page structure: Tournament Tickets section with rows for each ticket type
    # Numbers appear on the RIGHT side of each row
    from ..adb import Region
    h, w = screen.shape[:2]

    # Verified pixel positions from actual screenshot (621x1342 content):
    # Stage 3 (Final): count at Region(77.0%, 28.5%, 11.9%, 2.8%)  → "11"
    # Stage 2:         count at Region(77.3%, 31.4%, 11.6%, 2.8%)  → "3"
    # Stage 1:         count at Region(77.3%, 34.5%, 11.6%, 2.6%)  → "0"
    # Get app content area
    from ..vision import _APP_LEFT, _APP_RIGHT
    import re as re_mod
    import numpy as np_local
    app_left  = _APP_LEFT
    app_right = _APP_RIGHT if _APP_RIGHT else screen_w
    app = screen[:, app_left:app_right]
    h_app, w_app = app.shape[:2]

    def _read_count(y1p, y2p, ticket_type):
        # Positions verified on 709px app / 2560x1380 display
        # Final:  purple digit,  y=26.1-29.7%, x=83.2-88.9%
        # Stage2: dark-grey digit, y=29.7-32.7%, x=85.3-89.6%
        # Stage1: dark-grey digit, y=32.7-35.7%, x=85.3-89.6%
        y1 = int(y1p / 100 * h_app)
        y2 = int(y2p / 100 * h_app)
        if ticket_type == "final":
            strip = app[y1:y2, int(0.832 * w_app):int(0.889 * w_app)]
            hsv   = cv2.cvtColor(strip, cv2.COLOR_BGR2HSV)
            mask  = cv2.inRange(hsv, np_local.array([120, 30, 30]),
                                     np_local.array([170, 255, 255]))
        else:
            strip = app[y1:y2, int(0.853 * w_app):int(0.896 * w_app)]
            gray  = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
            _, mask = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY_INV)
        result = np_local.full_like(strip, 255)
        result[mask > 0] = [0, 0, 0]
        scale_factor = max(1, 6)
        big = cv2.resize(result,
                         (max(1, result.shape[1] * scale_factor),
                          max(1, result.shape[0] * scale_factor)),
                         interpolation=cv2.INTER_NEAREST)
        text = ocr_text(big, scale=1.0).strip()
        nums = re_mod.findall(r"[0-9]+", text)
        return int(nums[-1]) if nums else 0

    ticket_counts = {
        "final":  _read_count(26.1, 29.7, "final"),
        "stage2": _read_count(29.7, 32.7, "stage2"),
        "stage1": _read_count(32.7, 35.7, "stage1"),
    }
    for name, count in ticket_counts.items():
        log.info("Ticket %s: %d", name, count)

    # Membership tier — "Platinum Membership" blue text at y=14-19% of app
    mem_strip = app[int(0.14 * h_app):int(0.19 * h_app), :]
    membership = ocr_membership_tier(mem_strip)
    log.info("Membership: %s", membership)

    result = {
        "final_tickets": ticket_counts.get("final", 0),
        "stage2_tickets": ticket_counts.get("stage2", 0),
        "stage1_tickets": ticket_counts.get("stage1", 0),
        "membership": membership,
    }

    # Return to Stage 1
    navigate_to_tab(adb, "stage1")

    return result


def verify_card_set(adb, vision_detector, screen_w: int, screen_h: int) -> bool:
    """
    Navigate to Game Settings from the Me page and verify Card Set = Set 3 (4-color deck).
    Returns True if Set 3 confirmed, False otherwise.

    Me page layout (verified from screenshot):
      - Game Settings menu row is at approximately y=57.3% of screen
      - The row is full-width, tappable across the center
    """
    from ..vision import NAV_TABS, match_template_bool, State
    from ..vision import REGIONS as VR
    log.info("Navigating to Game Settings to verify card set")

    # Go to Me tab
    adb.tap(*NAV_TABS["me"])
    time.sleep(1.5)

    # Tap "Game Settings" row (y≈57.3% verified from Me page screenshot)
    # Row is full-width at x=50%, y=57.3%
    adb.tap(50.0, 57.3)
    time.sleep(2.0)

    screen = adb.screenshot()
    state = vision_detector.detect(screen)

    if state != State.GAME_SETTINGS:
        log.warning("Could not open Game Settings — skipping card set check")
        adb.press_back()
        time.sleep(0.5)
        navigate_to_tab(adb, "stage1")
        return False

    # Verify Set 3 using template match or OCR
    from ..vision import match_template_bool
    confirmed = match_template_bool(screen, "text_set3.png", 0.75,
                                    screen_w=screen_w, screen_h=screen_h)
    if not confirmed:
        # Fallback: OCR check
        from ..ocr import ocr_text
        text = ocr_text(screen[int(screen_h * 0.55):int(screen_h * 0.80), :]).lower()
        confirmed = "set 3" in text or "4-color" in text or "4 color" in text

    if not confirmed:
        log.warning("Card Set 3 NOT confirmed — suit detection may fail. Set Cards→Set 3 in Game Settings.")
    else:
        log.info("Card Set 3 confirmed ✓")

    # Press Confirm to exit (or Back if no changes)
    adb.press_back()
    time.sleep(0.8)
    navigate_to_tab(adb, "stage1")
    return confirmed


def register_for_tournament(
    adb,
    vision_detector,
    card_region,
    tournament_name: str,
    screen_w: int,
    screen_h: int,
) -> bool:
    """
    Brute-force registration tap sequence (no state detection between taps).

    Per Lyra's spec:
      1. Tap the tournament card
      2. Tap Register button (bottom CTA)
      3. Wait 2s, tap same spot
      4. Wait 2s, tap same spot one more time
      5. Tap the explicit back button at (714, 27)

    Vision-state checks were unreliable in the registration flow and caused
    bail-outs that left the bot stuck on the Good Luck modal. Brute-forcing
    the tap sequence is fine because every tap lands in the same CTA region
    where Register / Register-confirm / Confirm all sit.

    Two safety checks remain at the very start:
      • Verify we landed on TOURNAMENT_DETAIL after the card tap (otherwise
        bail — we'd be tapping into random screen).
      • Skip if Unregister button is visible (already registered).
    """
    from ..vision import State, REGIONS, match_template_bool
    from ..adb import Region

    # Calibrated explicit back-button coord on the tournament-detail page.
    BACK_BTN_XY = (716, 31)

    # Step 1: Tap card to open detail page
    log.info("Tapping tournament card: %s", tournament_name)
    if isinstance(card_region, Region):
        adb.tap_region(card_region)
    else:
        cx, cy = card_region
        adb.tap_abs(cx, cy)
    time.sleep(1.5)

    screen = adb.screenshot()
    state = vision_detector.detect(screen)
    if state != State.TOURNAMENT_DETAIL:
        log.warning("Expected TOURNAMENT_DETAIL after card tap, got %s", state)
        # Caller (scan_stage1_tab) handles back press after failure
        return False

    # Skip if already registered (Unregister button visible)
    if match_template_bool(screen, "btn_unregister.png",
                           screen_w=screen_w, screen_h=screen_h):
        log.info("↷ Skip (already registered): %s", tournament_name)
        adb.tap_abs(*BACK_BTN_XY)
        time.sleep(0.8)
        return False

    # ── Brute-force tap sequence: 3x same-spot CTA → back button ──────────
    log.info("Registering: %s", tournament_name)

    # Tap 1 — Register button on detail page
    adb.tap_region(REGIONS["register_button"])
    time.sleep(2.0)

    # Tap 2 — same spot, Register-confirmation modal
    adb.tap_region(REGIONS["register_button"])
    time.sleep(2.0)

    # Tap 3 — same spot, Confirm on the "Good Luck!" success modal
    adb.tap_region(REGIONS["confirm_button"])
    time.sleep(2.5)  # wait for success modal to fully animate out before back tap

    # Tap back button at calibrated coord (3.8%, 3.0%) — top-left arrow
    adb.tap_abs(*BACK_BTN_XY)
    time.sleep(1.0)

    return True
