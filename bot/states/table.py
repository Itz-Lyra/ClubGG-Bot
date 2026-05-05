"""
Table play engine — Stage 1 only.

Polling loop priorities (checked every 500ms):
  1. Result screens (win / bust) — close them, return to lobby
  2. Sitting out detection — tap I'm Back immediately
  3. Our turn to act (IN_HAND_ACTION) — evaluate and act
  4. Waiting / break / pre-game — idle with urgent-tab scan
  5. Non-table state — count up, exit loop when confirmed gone

Modes:
  shove  — always go all-in
  smart  — preflop: use NLH hand chart, shove strong hands, fold weak
           postflop: check if free, otherwise fold (never risk chips blind)

Sitting-out prevention:
  Any time we detect "sitting out" text we immediately tap I'm Back.
  This fires during IN_HAND_WAITING and BREAK_SCREEN.

Result screen handling:
  Aggressively retries close up to 5 times with 1.5s waits between attempts.
  Only returns once the screen is gone or we've exhausted retries.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import numpy as np
import cv2

from ..vision import (
    State, REGIONS,
    detect_tab_bar_exclamation, detect_active_tables,
)
from ..ocr import (
    ocr_chip_count, ocr_call_amount, ocr_blinds, ocr_rank_info,
    ocr_prize_remaining, ocr_text,
)
from ..poker.cards import read_hole_cards, read_community_cards
from ..poker.position import (
    detect_dealer_button_position, detect_seat_positions,
    find_our_seat_index, calculate_position,
)
from ..states.results import handle_win_screen, handle_bust_screen
from ..color import hex_to_bgr, pixel_matches, all_pixels_match

log = logging.getLogger(__name__)


# ── Calibrated absolute pixel coords (Waydroid 1920×1040, app x=696-1224) ──
# All coords are SCREEN-absolute. Recalibrate via tools/calibrate.py if your
# Waydroid display geometry changes.

# Result screen (See You Next Time / Thank You for Playing) ──────────────
# 7 sample points inside the dark modal background — all should be
# uniformly the modal-bg color when a result screen is up.
_RESULT_BG_HEX = "#202329"
_RESULT_BG_BGR = hex_to_bgr(_RESULT_BG_HEX)
_RESULT_BG_SAMPLES = [
    (744, 183),  (1160, 187),               # top row (above headline)
    (755, 659),  (1161, 665),               # bottom row (below info card)
    (845, 613),  (950, 574),  (1056, 614),  # mid row (around prize area)
]
_RESULT_CLOSE_XY = (840, 851)  # left "Close" button — never the right "Share"

# Call / All-in action panel ─────────────────────────────────────────────
# Probe (1004, 993) for the Call button bg color → confirms it's our turn.
# Probe (1010, 929) for the raise/slider bg color → all-in is available.
_CALL_BTN_HEX     = "#232425"
_CALL_BTN_BGR     = hex_to_bgr(_CALL_BTN_HEX)
_CALL_BTN_PROBE   = (1004, 993)
_CALL_BTN_TAP_XY  = (1004, 993)

_SLIDER_HEX       = "#1e2021"
_SLIDER_BGR       = hex_to_bgr(_SLIDER_HEX)
_SLIDER_PROBE     = (1010, 929)
_SLIDER_TAP_XY    = (1010, 929)  # also the swipe origin

# Leave-confirmation modal ──────────────────────────────────────────────
# Shows after busting out or winning a ticket — needs to be dismissed.
# Detection requires BOTH:
#   • The leave/confirm button at (913, 852) reads #39404a, AND
#   • All 5 modal-background sample points read #202328.
# Both conditions must hold to avoid false positives during table-switch
# animations, which can briefly tint dark regions but lack the leave button.
_LEAVE_BG_HEX     = "#202328"
_LEAVE_BG_BGR     = hex_to_bgr(_LEAVE_BG_HEX)
_LEAVE_BG_SAMPLES = [
    (741, 156),    # top-left quadrant of modal
    (970, 145),    # top-center
    (1175, 157),   # top-right quadrant
    (1175, 678),   # bottom-right quadrant
    (745, 559),    # mid-left
]
_LEAVE_BTN_HEX    = "#39404a"
_LEAVE_BTN_BGR    = hex_to_bgr(_LEAVE_BTN_HEX)
_LEAVE_BTN_PROBE  = (913, 852)
_LEAVE_TAP_XY     = (913, 852)

# Color similarity threshold (0.0–1.0). Lyra found 90% reliable.
_COLOR_THRESHOLD  = 0.90


# ── helpers ───────────────────────────────────────────────────────────────

def _log(log_queue, message: str, level: str = "info") -> None:
    if log_queue is not None:
        try:
            log_queue.put_nowait({"message": message, "level": level})
        except Exception:
            pass
    getattr(log, level if level in ("error", "warning") else "info")(message)


# ── Color-based UI element detection ──────────────────────────────────────

def _is_result_screen_by_color(screen) -> bool:
    """
    True when all 7 modal-background sample points read the dark `#202329`
    of the See You Next Time / Thank You for Playing screens. Highly reliable
    multi-point AND check — false positives are essentially impossible because
    no other ClubGG screen has 7 specific points all hitting that exact dark.
    """
    return all_pixels_match(
        screen, _RESULT_BG_SAMPLES, _RESULT_BG_BGR,
        threshold=_COLOR_THRESHOLD,
    )


def _is_our_turn_by_color(screen) -> bool:
    """True when the Call button is on screen (it's our turn)."""
    x, y = _CALL_BTN_PROBE
    return pixel_matches(screen, x, y, _CALL_BTN_BGR, threshold=_COLOR_THRESHOLD)


def _is_slider_visible_by_color(screen, log_queue=None) -> bool:
    """True when the all-in slider / raise caret button is up.

    When `log_queue` is provided and the probe fails, logs the actual sampled
    BGR color so we can diagnose miscalibration.
    """
    from ..color import sample_avg_bgr, color_similarity
    x, y = _SLIDER_PROBE
    avg = sample_avg_bgr(screen, x, y, radius=2)
    if avg is None:
        return False
    sim = color_similarity(avg, _SLIDER_BGR)
    if sim < _COLOR_THRESHOLD and log_queue is not None:
        b, g, r = avg
        _log(log_queue,
             f"slider probe @({x},{y}) read BGR={avg} (#{r:02x}{g:02x}{b:02x}) sim={sim:.3f}",
             "warning")
    return sim >= _COLOR_THRESHOLD


def _is_leave_screen_visible(screen) -> bool:
    """True only when the leave-confirmation modal is fully up.

    Requires BOTH:
      1. The leave button at (913, 852) reads #39404a
      2. All 5 modal-background sample points read #202328

    Multi-point AND check prevents false positives during the table-switch
    sliding animation, which can briefly produce dark regions but never
    matches all 6 specific points at once.
    """
    bx, by = _LEAVE_BTN_PROBE
    if not pixel_matches(screen, bx, by, _LEAVE_BTN_BGR,
                         threshold=_COLOR_THRESHOLD):
        return False
    return all_pixels_match(screen, _LEAVE_BG_SAMPLES, _LEAVE_BG_BGR,
                            threshold=_COLOR_THRESHOLD)


def _close_result_screen(adb, vision_detector, screen, screen_w: int, screen_h: int,
                          stats, log_queue, state: State) -> None:
    """
    Close a win or bust result screen.

    Strategy: read OCR info ONCE for logging, then loop tapping the calibrated
    Close button coord until the screen is gone (max 5 attempts, 1.5s wait).
    NEVER taps the right-side Share button.

    Detection of "still up" uses the color check first (fast & reliable),
    falling back to the vision state detector.
    """
    # Log result info once (handle_*_screen reads OCR and taps close internally)
    if state == State.WIN_SCREEN:
        result = handle_win_screen(adb, screen, screen_w, screen_h, stats, {})
        _log(log_queue,
             f"★ WIN: {result['tournament']} | {result['prize']} | Rank: {result['rank']}",
             "win")
    else:
        result = handle_bust_screen(adb, screen, screen_w, screen_h, stats)
        _log(log_queue,
             f"✗ BUST: {result['tournament']} | Rank: {result['rank']} | Time: {result['play_time']}",
             "bust")

    # handle_*_screen already tapped close once; verify dismissal via vision
    # detector (NOT colour, which false-positives on active tables) and retry
    # only while vision still reports a result-screen state.
    MAX_TRIES = 5
    cx, cy = _RESULT_CLOSE_XY
    for attempt in range(MAX_TRIES):
        time.sleep(1.5)
        screen2 = adb.screenshot()
        new_state = vision_detector.detect(screen2)
        if new_state not in (State.WIN_SCREEN, State.BUST_OUT_SCREEN):
            return
        _log(log_queue,
             f"Result screen still up ({attempt+1}/{MAX_TRIES}) — tapping Close ({cx},{cy})",
             "warning")
        adb.tap_abs(cx, cy)

    _log(log_queue, "⚠️ Could not close result screen — pressing back", "warning")
    adb.press_back()
    time.sleep(1.0)


def _try_close_unknown(adb, screen, screen_w: int, screen_h: int, log_queue,
                        attempt_count: list) -> bool:
    """
    When state is UNKNOWN, check if it looks like a result screen and tap Close.
    After 3 failed attempts, press Back instead to escape any stuck menu.
    Returns True if we did something.
    """
    import cv2 as _cv2
    import numpy as _np

    # After 3 attempts tapping close with no result, press Back to escape
    if attempt_count[0] >= 3:
        _log(log_queue, "⚠️ UNKNOWN persisting — pressing Back to escape", "warning")
        adb.press_back()
        time.sleep(1.0)
        attempt_count[0] = 0
        return True

    h, w = screen.shape[:2]
    center = screen[int(0.15*h):int(0.85*h), int(0.02*w):int(0.50*w)]
    if center.size == 0:
        return False
    gray = _cv2.cvtColor(center, _cv2.COLOR_BGR2GRAY)
    bright_pct = float(_np.sum(gray > 160)) / gray.size * 100
    if bright_pct < 15.0:
        return False

    cx, cy = _RESULT_CLOSE_XY
    _log(log_queue, f"⚠️ UNKNOWN looks like result screen — tapping Close ({cx},{cy})", "warning")
    adb.tap_abs(cx, cy)
    time.sleep(1.0)
    attempt_count[0] += 1
    return True


def _check_sitting_out(adb, screen, screen_w: int, screen_h: int, log_queue) -> bool:
    """
    Detect sitting-out by sampling the exact known pixel position of the
    "I'm Back" button.  Calibrated center: abs (1166, 995).

    The button is a BRIGHT saturated green (H=40-85, S>150, V>180).
    Table felt is a darker, less-saturated green (S<120, V<160).
    Sampling a tight 50x30px box around the exact button center makes
    this check pixel-precise and immune to table-felt false positives.

    Also checks the "Sitting Out" label pixel (top-left of our avatar area)
    as a secondary confirmation.
    """
    import cv2 as _cv2
    import numpy as _np

    _IM_BACK_X = 1166
    _IM_BACK_Y = 995

    h, w = screen.shape[:2]

    # ── Primary: sample tight box around the known button center ──────────
    # 50px wide x 30px tall centred on (1166, 995)
    x1 = max(0, _IM_BACK_X - 25)
    x2 = min(w, _IM_BACK_X + 25)
    y1 = max(0, _IM_BACK_Y - 15)
    y2 = min(h, _IM_BACK_Y + 15)

    roi = screen[y1:y2, x1:x2]
    if roi.size > 0:
        hsv = _cv2.cvtColor(roi, _cv2.COLOR_BGR2HSV)
        # BRIGHT green only — table felt has V<160 and S<120
        bright_green = _cv2.inRange(
            hsv,
            _np.array([38, 130, 160]),   # H=38-85, S>130, V>160
            _np.array([85, 255, 255]),
        )
        bright_pct = float(_np.sum(bright_green > 0)) / bright_green.size * 100
        if bright_pct > 40.0:
            _log(log_queue, "⚠️ Sitting out — tapping I'm Back", "warning")
            adb.tap_abs(_IM_BACK_X, _IM_BACK_Y)
            time.sleep(1.2)
            # Tap a second time in case the first didn't register
            adb.tap_abs(_IM_BACK_X, _IM_BACK_Y)
            time.sleep(0.5)
            return True

    # ── Fallback: OCR the bottom 35% of the app area ─────────────────────
    app = screen[:, adb.app_left:adb.app_right]
    ah = app.shape[0]
    bottom = app[int(0.65 * ah):, :]
    if bottom.size == 0:
        return False
    text = ocr_text(bottom, scale=1.5).lower()
    if any(kw in text for kw in ("sitting out", "sit out", "i'm back", "im back")):
        _log(log_queue, "⚠️ Sitting out (OCR) — tapping I'm Back", "warning")
        adb.tap_abs(_IM_BACK_X, _IM_BACK_Y)
        time.sleep(1.2)
        adb.tap_abs(_IM_BACK_X, _IM_BACK_Y)
        time.sleep(0.5)
        return True

    return False




def _is_leave_confirmation(screen, screen_w: int, screen_h: int, adb) -> bool:
    """
    Detect "Leave Table" confirmation dialog.
    Has a bright modal card in the center with Cancel + Leave buttons.
    We always tap Cancel — we never want to voluntarily leave a table.
    """
    import cv2 as _cv2
    import numpy as _np

    h, w = screen.shape[:2]
    center = screen[int(0.30*h):int(0.75*h), int(0.10*w):int(0.90*w)]
    if center.size == 0:
        return False
    gray = _cv2.cvtColor(center, _cv2.COLOR_BGR2GRAY)
    bright_pct = float(_np.sum(gray > 200)) / gray.size * 100
    if bright_pct < 15.0:
        return False
    text = ocr_text(center, scale=1.5).lower()
    return "leave" in text and ("cancel" in text or "table" in text)


def _tap_leave_cancel(adb, screen_w: int, screen_h: int) -> None:
    """
    Tap Cancel on the Leave Table dialog.
    Cancel is the left/grey button. Leave is the right/green button.
    Calibrated for both resolutions using percentage positions.
    """
    # Cancel button sits at roughly 35% x, 68% y of screen
    cx = int(0.35 * screen_w)
    cy = int(0.68 * screen_h)
    adb.tap_abs(cx, cy)


def _is_spectating(state, screen, screen_w: int, screen_h: int, adb) -> bool:
    """
    Detect stuck-spectating: we're in IN_HAND_WAITING but have no chips.
    Only use this as a last resort — OCR-based chip checks are unreliable.
    Just return False here; the consecutive_non_table counter in the main loop
    handles the case where we're truly stuck. The spectating logic is now
    handled via the _spectate_timer in the polling loop instead.
    """
    return False


def _handle_urgent_tabs(adb, vision_detector, config, stats, log_queue,
                         active_table_rules, screen_w, screen_h) -> None:
    """
    Check tab bar for ! badges and act on any that need action.
    Loops until no more urgent tabs remain.
    """
    for _ in range(8):
        screen = adb.screenshot()
        urgent = detect_tab_bar_exclamation(
            screen, screen_w, screen_h, adb.app_left, adb.app_right
        )
        if not urgent:
            break
        for tx, ty in urgent:
            adb.tap_abs(tx, ty)
            time.sleep(0.5)
            _execute_action(adb, vision_detector, config, stats, log_queue,
                            active_table_rules, screen_w, screen_h)
            time.sleep(0.3)


# ── main polling loop ─────────────────────────────────────────────────────

def run_table_polling_loop(
    adb,
    vision_detector,
    config,
    stats,
    discord,
    log_queue,
    stop_flag,
    active_table_rules: dict,
    screen_w: int,
    screen_h: int,
    on_all_tables_done,
) -> None:
    """
    Poll table state and act. Exits when no table states remain.
    """
    poll_interval = config.poll_interval_ms / 1000.0
    consecutive_non_table = 0
    MAX_NON_TABLE = 6
    _last_state = None

    _unknown_close_attempts = [0]  # mutable counter for _try_close_unknown

    while not stop_flag():
        try:
            screen = adb.screenshot()
        except Exception as exc:
            _log(log_queue, f"Screenshot failed: {exc}", "error")
            time.sleep(1.0)
            continue

        # ── Sitting-out check: runs every poll cycle unconditionally ───────
        # Must happen before state detection so we never act while sitting out.
        if _check_sitting_out(adb, screen, screen_w, screen_h, log_queue):
            time.sleep(poll_interval)
            continue

        # ── Leave-screen probe — bust or ticket-win confirmation ──────────
        # Color #39404a at (913, 852) means a leave-confirmation modal is up.
        # Tap the button to leave the table; then check if any tables remain.
        if _is_leave_screen_visible(screen):
            _unknown_close_attempts[0] = 0
            lx, ly = _LEAVE_TAP_XY
            _log(log_queue, f"⇠ Leave screen up — tapping ({lx},{ly})", "info")
            adb.tap_abs(lx, ly)
            time.sleep(1.5)
            _last_state = None
            consecutive_non_table = 0
            screen2 = adb.screenshot()
            remaining = detect_active_tables(screen2, screen_w, screen_h,
                                             adb.app_left, adb.app_right)
            if not remaining:
                s2 = vision_detector.detect(screen2)
                if s2 not in (State.WIN_SCREEN, State.BUST_OUT_SCREEN,
                              State.IN_HAND_WAITING, State.IN_HAND_ACTION,
                              State.BREAK_SCREEN, State.PRE_GAME_WAIT):
                    _log(log_queue, "All tables finished", "info")
                    on_all_tables_done()
                    return
            continue

        # ── Result-screen color probe (REMOVED) ────────────────────────────
        # The old 7-point color probe at #202329 fired false positives on the
        # active poker table — its sample points landed on dark UI elements
        # (player info boxes, sitting-out indicators) on the new geometry.
        # Real bust/win modals are still handled below by:
        #   (a) the leave-screen probe above (button + bg multi-point AND), and
        #   (b) the vision detector's BUST_OUT_SCREEN / WIN_SCREEN states,
        #       which use OCR for "see you" / "knocked" / "thank you" text.

        state = vision_detector.detect(screen)

        # ── 1. Result screens — close aggressively ─────────────────────
        if state in (State.WIN_SCREEN, State.BUST_OUT_SCREEN):
            _unknown_close_attempts[0] = 0
            _close_result_screen(adb, vision_detector, screen, screen_w, screen_h,
                                 stats, log_queue, state)
            _last_state = None
            consecutive_non_table = 0
            time.sleep(0.5)
            screen2 = adb.screenshot()
            remaining = detect_active_tables(screen2, screen_w, screen_h,
                                             adb.app_left, adb.app_right)
            if not remaining:
                s2 = vision_detector.detect(screen2)
                if s2 not in (State.WIN_SCREEN, State.BUST_OUT_SCREEN,
                              State.IN_HAND_WAITING, State.IN_HAND_ACTION,
                              State.BREAK_SCREEN, State.PRE_GAME_WAIT):
                    _log(log_queue, "All tables finished", "info")
                    on_all_tables_done()
                    return
            continue

        # ── 1b. Leave confirmation dialog — tap Cancel, we never want to leave ──
        if _is_leave_confirmation(screen, screen_w, screen_h, adb):
            _log(log_queue, "Leave Table dialog — tapping Cancel", "warning")
            _tap_leave_cancel(adb, screen_w, screen_h)
            time.sleep(1.0)
            _last_state = None
            continue

        # ── 2. Go-to-Table popup — do nothing, game auto-redirects ─────
        if state == State.GO_TO_TABLE_POPUP:
            if _last_state != state:
                _log(log_queue, "⏳ Table starting — waiting for auto-redirect", "info")
            _last_state = state
            time.sleep(2.0)
            continue

        # ── 3. Target stack banner ─────────────────────────────────────
        if state == State.TARGET_STACK:
            if _last_state != state:
                _log(log_queue, "⏳ Target Stack — waiting", "info")
            _last_state = state
            consecutive_non_table = 0
            time.sleep(4.0)
            continue

        # ── 4. Break screen ────────────────────────────────────────────
        if state == State.BREAK_SCREEN:
            if _last_state != state:
                # Parse break duration from screen
                text = ocr_text(screen[int(screen_h * 0.4):int(screen_h * 0.7), :])
                import re
                m = re.search(r"(\d+)\s*min", text, re.IGNORECASE)
                mins = m.group(1) if m else "?"
                _log(log_queue, f"⏸ Break — ~{mins}min", "break")
            _check_sitting_out(adb, screen, screen_w, screen_h, log_queue)  # safety belt
            # Serve other tables during break
            urgent = detect_tab_bar_exclamation(screen, screen_w, screen_h,
                                                adb.app_left, adb.app_right)
            if urgent:
                tx, ty = urgent[0]
                adb.tap_abs(tx, ty)
                time.sleep(0.5)
                _last_state = None
                consecutive_non_table = 0
                continue
            _last_state = state
            consecutive_non_table = 0
            time.sleep(poll_interval)
            continue

        # ── 5. Pre-game countdown ──────────────────────────────────────
        if state == State.PRE_GAME_WAIT:
            if _last_state != state:
                _log(log_queue, "⏳ Pre-game countdown", "info")
            _last_state = state
            consecutive_non_table = 0
            time.sleep(poll_interval)
            continue

        # ── 6. Waiting for our turn ────────────────────────────────────
        if state == State.IN_HAND_WAITING:
            # Serve other urgent tables
            urgent = detect_tab_bar_exclamation(screen, screen_w, screen_h,
                                                adb.app_left, adb.app_right)
            if urgent:
                tx, ty = urgent[0]
                _log(log_queue, "⚡ Switching to urgent tab", "popup")
                adb.tap_abs(tx, ty)
                time.sleep(0.5)
                _last_state = None
                consecutive_non_table = 0
                continue
            if _last_state != state:
                _log(log_queue, "⏳ Waiting for turn", "info")
            _last_state = state
            consecutive_non_table = 0
            time.sleep(poll_interval)
            continue

        # ── 7. Our turn — act ──────────────────────────────────────────
        if state == State.IN_HAND_ACTION:
            _log(log_queue, "🃏 Our turn — acting", "info")
            _last_state = None
            consecutive_non_table = 0
            _execute_action(adb, vision_detector, config, stats, log_queue,
                            active_table_rules, screen_w, screen_h, screen=screen)
            # After acting, drain any other urgent tabs
            _handle_urgent_tabs(adb, vision_detector, config, stats, log_queue,
                                 active_table_rules, screen_w, screen_h)
            # Longer sleep after action so animation plays out
            time.sleep(max(poll_interval, 1.2))
            continue

        # ── 8. Not a table state ───────────────────────────────────────
        # Before counting as non-table, check if it looks like a result
        # screen that failed to detect — tap Close and try again.
        if _try_close_unknown(adb, screen, screen_w, screen_h, log_queue,
                               _unknown_close_attempts):
            _last_state = None
            time.sleep(1.0)
            continue

        _unknown_close_attempts[0] = 0  # reset when we get a known state
        _last_state = None
        consecutive_non_table += 1
        _log(log_queue, f"Non-table: {state.name} ({consecutive_non_table}/{MAX_NON_TABLE})", "info")

        if consecutive_non_table >= MAX_NON_TABLE:
            # Lobby/me-page states are definitive — we're done at tables
            _DONE_STATES = (
                State.LOBBY_STAGE1, State.LOBBY_STAGE2, State.LOBBY_FINAL,
                State.ME_PAGE, State.LOBBY_LIVE_EVENT, State.GAME_SETTINGS,
            )
            if state in _DONE_STATES:
                _log(log_queue, "Back in lobby — tables done", "info")
                on_all_tables_done()
                return

            # Unknown/transitional — check for actual game table tab markers
            all_tabs = detect_active_tables(screen, screen_w, screen_h,
                                            adb.app_left, adb.app_right)
            if not all_tabs:
                _log(log_queue, "No tables remaining — returning to lobby", "info")
                on_all_tables_done()
                return
            # Real table tabs still present — tap first and give it another chance
            adb.tap_abs(all_tabs[0][0], all_tabs[0][1])
            consecutive_non_table = 0

        time.sleep(poll_interval)


# ── action execution ──────────────────────────────────────────────────────

def _execute_action(
    adb,
    vision_detector,
    config,
    stats,
    log_queue,
    active_table_rules: dict,
    screen_w: int,
    screen_h: int,
    screen=None,
) -> None:
    """
    Read game state, decide, and execute one action.
    Accepts a pre-taken screen to avoid a redundant screenshot.
    """
    if screen is None:
        screen = adb.screenshot()

    # Re-confirm it's still our turn (animation may have played)
    state = vision_detector.detect(screen)
    if state == State.WIN_SCREEN:
        _close_result_screen(adb, vision_detector, screen, screen_w, screen_h,
                              stats, log_queue, state)
        return
    if state == State.BUST_OUT_SCREEN:
        _close_result_screen(adb, vision_detector, screen, screen_w, screen_h,
                              stats, log_queue, state)
        return
    if state == State.BREAK_SCREEN:
        return
    if state == State.PRE_GAME_WAIT:
        return
    if state == State.TARGET_STACK:
        time.sleep(4.0)
        return
    if state != State.IN_HAND_ACTION:
        log.debug("_execute_action: state changed to %s before acting", state.name)
        return

    # Read game state from screen
    gs = _read_game_state(screen, screen_w, screen_h)

    decision = _decide(gs, "shove")

    # Log what we're doing
    hole_str  = " ".join(str(c) for c in gs.get("hole_cards", [])) or "?"
    board_str = " ".join(str(c) for c in gs.get("community_cards", [])) or "-"
    bb        = gs.get("bb", 0)
    stack_bb  = gs.get("stack_bb", 0)
    _log(log_queue,
         f"♠ {gs.get('game_type','NLH').upper()} | {hole_str} | board:{board_str} | "
         f"stack:{stack_bb:.0f}bb | → {decision}",
         "action")

    if decision == "SHOVE":
        _execute_shove(adb, screen_w, screen_h, log_queue, config)
    elif decision == "CALL":
        # Calibrated Call button position (1920×1024 Waydroid)
        _log(log_queue, "→ CALL", "action")
        adb.tap_abs(*_CALL_BTN_TAP_XY)
    elif decision == "CHECK":
        # Check is in same region as Call — tap the same calibrated coord
        adb.tap_abs(*_CALL_BTN_TAP_XY)
    else:
        adb.tap_region(REGIONS["fold_button"])

    if stats:
        stats.record_hand_played()


def _read_game_state(screen, screen_w: int, screen_h: int) -> dict:
    """Read all relevant game info from the current screen."""
    # Detect game type: try PLO (4 cards) first
    hole_plo = read_hole_cards(screen, screen_w, screen_h, game_type="plo")
    if len(hole_plo) >= 3:
        game_type, hole_cards = "plo", hole_plo
    else:
        game_type = "nlh"
        hole_cards = read_hole_cards(screen, screen_w, screen_h, game_type="nlh")

    community_cards = read_community_cards(screen, screen_w, screen_h)

    our_chips    = ocr_chip_count(REGIONS["our_chips"].crop(screen, screen_w, screen_h))
    total_pot    = ocr_chip_count(REGIONS["pot_label"].crop(screen, screen_w, screen_h))
    call_amount  = ocr_call_amount(REGIONS["call_button"].crop(screen, screen_w, screen_h))
    check_avail  = (call_amount == 0)

    center_crop  = REGIONS["center_info"].crop(screen, screen_w, screen_h)
    sb, bb, ante = ocr_blinds(center_crop)
    my_rank, total_players = ocr_rank_info(center_crop)
    prizes_remaining = ocr_prize_remaining(center_crop)

    dealer_pos = detect_dealer_button_position(screen, screen_w, screen_h)
    seats      = detect_seat_positions(screen, screen_w, screen_h)
    our_seat   = find_our_seat_index(screen, screen_w, screen_h)
    position   = calculate_position(dealer_pos, our_seat, seats, total_players or 9)

    stack_bb = (our_chips / bb) if bb > 0 else 20.0

    # Detect whether the raise caret button is actually present.
    # The caret button region (center of action panel right side) shows a
    # dark button with a "^" symbol when raise is available.
    # When only Fold+Call is shown this region has no distinct dark button.
    caret_visible = _is_caret_visible(screen, screen_w, screen_h)

    return {
        "game_type": game_type,
        "hole_cards": hole_cards,
        "community_cards": community_cards,
        "our_chips": our_chips,
        "total_pot": total_pot,
        "call_amount": call_amount,
        "check_available": check_avail,
        "caret_visible": caret_visible,
        "sb": sb, "bb": bb, "ante": ante,
        "my_rank": my_rank,
        "total_players": total_players,
        "prizes_remaining": prizes_remaining,
        "position": position,
        "stack_bb": stack_bb,
    }


def _is_caret_visible(screen, screen_w: int, screen_h: int) -> bool:
    """
    Check whether the raise ^ caret button is visible.
    Uses the color-probe path (calibrated pixel + dark-button colour).
    Kept for backward compatibility with _read_game_state.
    """
    return _is_slider_visible_by_color(screen)


def _decide(gs: dict, play_mode: str) -> str:
    """Always shove. Returns SHOVE or CALL (when no raise button available)."""
    return "SHOVE"


# ── all-in swipe ──────────────────────────────────────────────────────────

def _call_button_visible(adb, screen_w: int, screen_h: int) -> bool:
    """
    Check if the Call button is visible by sampling its known pixel position.
    Call button is at abs (968, 993) on his 1920x1040 display.
    It has a dark rounded button background with white text.
    """
    import cv2 as _cv2
    import numpy as _np

    try:
        screen = adb.screenshot()
        x, y = 968, 993
        roi = screen[max(0,y-20):y+20, max(0,x-60):x+60]
        if roi.size == 0:
            return False
        gray = _cv2.cvtColor(roi, _cv2.COLOR_BGR2GRAY)
        # Call button: dark background with white text
        dark_pct  = float(_np.sum(gray < 80))  / gray.size * 100
        white_pct = float(_np.sum(gray > 180)) / gray.size * 100
        return dark_pct > 20.0 and white_pct > 5.0
    except Exception:
        return True  # assume visible if we can't tell


def _execute_shove(adb, screen_w: int, screen_h: int, log_queue, config=None) -> None:
    """
    Execute all-in (or fall back to Call when there's no slider).

    Logic per Lyra's spec:
      1. Probe the Call button position. Must read #232425 — if not, the
         action panel isn't up and we bail (defensive, vision should already
         have confirmed our turn).
      2. Probe the slider position. If it reads #1e2021, the all-in slider
         is available → swipe up to max it, then reverse-swipe to dismiss.
      3. If only the Call button reads dark (no slider) → tap Call.
    """
    from ..color import sample_avg_bgr, color_similarity

    # Re-screenshot for fresh color sample
    screen = adb.screenshot()

    # ── Step 1: confirm the Call button is visible (it's our turn) ────────
    cx, cy = _CALL_BTN_PROBE
    avg = sample_avg_bgr(screen, cx, cy, radius=2)
    if avg is None:
        _log(log_queue, f"call probe @({cx},{cy}) out of bounds", "warning")
        return
    sim = color_similarity(avg, _CALL_BTN_BGR)
    if sim < _COLOR_THRESHOLD:
        b, g, r = avg
        _log(log_queue,
             f"call probe @({cx},{cy}) read BGR={avg} (#{r:02x}{g:02x}{b:02x}) "
             f"sim={sim:.3f} — action panel gone, skipping",
             "warning")
        return

    # ── Step 2: route based on slider visibility ──────────────────────────
    if not _is_slider_visible_by_color(screen, log_queue=log_queue):
        # Call-only panel — no raise option, just tap Call
        _log(log_queue, "Call button visible, no raise slider — tapping Call", "action")
        adb.tap_abs(*_CALL_BTN_TAP_XY)
        return

    # ── Step 3: slider visible — swipe up to max, then reverse to dismiss ─
    sx1, sy1 = _SLIDER_TAP_XY                       # swipe origin (caret btn)
    sx2 = sx1                                       # straight vertical
    sy2 = int(screen_h * 0.62)                      # ~halfway up the screen
    if config is not None:
        # Allow per-user overrides via config
        sx1 = int(getattr(config, "shove_x1", sx1))
        sy1 = int(getattr(config, "shove_y1", sy1))
        sx2 = int(getattr(config, "shove_x2", sx2))
        sy2 = int(getattr(config, "shove_y2", sy2))

    _log(log_queue, f"All-in swipe ({sx1},{sy1})→({sx2},{sy2})", "action")
    adb.shell(f"input swipe {sx1} {sy1} {sx2} {sy2} 400")
    time.sleep(1.0)
    # Reverse swipe to close the slider menu
    adb.shell(f"input swipe {sx2} {sy2} {sx1} {sy1} 400")
    # Wait long enough for animation to play out before any further action.
    # Do NOT check state here — if we immediately switch to another table the
    # screenshot will show that table as IN_HAND_ACTION and we'd wrongly call.
    time.sleep(1.5)
