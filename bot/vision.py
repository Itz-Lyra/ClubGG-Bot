"""
Color-based state detection. Scale-invariant — works at any Waydroid resolution.

Primary detection hierarchy:
  1. DARK SCREEN (table_green<15%, dark>45%) → modal states (win/bust/popup/pregame)
  2. TABLE (table_green>35%) → in_hand/break/wait/target_stack
  3. LIGHT SCREEN (white>55%) → lobby/me/settings/detail/modal

Lobby tab (stage1/stage2/final) is determined by NAVIGATION — the bot taps
the tab, then scans. The scanner knows which tab it's on by context.
State detection returns LOBBY_STAGE1 as the generic "light lobby screen" state
for scanner-managed navigation.
"""
from __future__ import annotations

import os, sys, logging
from enum import Enum, auto
from typing import Optional
import numpy as np
import cv2
import numpy as np
import numpy as np

from .adb import Region

log = logging.getLogger(__name__)


class State(Enum):
    LOGGED_OUT         = auto()
    GO_TO_TABLE_POPUP  = auto()
    WIN_SCREEN         = auto()
    BUST_OUT_SCREEN    = auto()
    SUCCESS_MODAL      = auto()
    REGISTRATION_MODAL = auto()
    PRE_GAME_WAIT      = auto()
    BREAK_SCREEN       = auto()
    IN_HAND_ACTION     = auto()
    IN_HAND_WAITING    = auto()
    TARGET_STACK       = auto()
    TOURNAMENT_DETAIL  = auto()
    LOBBY_STAGE1       = auto()   # generic light lobby — also returned for Stage2/Final
    LOBBY_STAGE2       = auto()
    LOBBY_FINAL        = auto()
    LOBBY_LIVE_EVENT   = auto()
    ME_PAGE            = auto()
    GAME_SETTINGS      = auto()
    UNKNOWN            = auto()


# ── App viewport globals ──────────────────────────────────────────────────

_APP_LEFT:  int           = 0
_APP_RIGHT: Optional[int] = None
_APP_W:     int           = 621
_TEMPLATE_CAPTURE_W       = 621
_TEMPLATE_CAPTURE_H       = 1342
_SCALED_CACHE: dict[str, Optional[np.ndarray]] = {}


def set_app_viewport(app_left: int, app_right: int) -> None:
    global _APP_LEFT, _APP_RIGHT, _APP_W
    _APP_LEFT  = app_left
    _APP_RIGHT = app_right
    _APP_W     = app_right - app_left
    _SCALED_CACHE.clear()
    log.info("Vision: app viewport x=%d-%d (%dpx, %.3fx scale)",
             app_left, app_right, _APP_W, _APP_W / _TEMPLATE_CAPTURE_W)


# ── Screen regions ────────────────────────────────────────────────────────

REGIONS = {
    "bottom_nav":      Region(0,    93.5, 100,  6.5),
    "top_tab_bar":     Region(0,    2.5,  90,   5.5),
    "hole_cards":      Region(1.9,  75.6, 23.0, 8.8),
    "community_cards": Region(19.3, 46.3, 61.2, 8.2),
    "pot_label":       Region(40.3, 41.7, 20.9, 4.5),
    "our_chips":       Region(1.9,  80.5, 22.2, 3.0),
    "center_info":     Region(13.7, 61.0, 73.3, 13.6),
    "fold_button":     Region(2.1,  92.0, 32.5, 7.1),
    "caret_button":    Region(49.9, 91.5, 13.7, 6.5),
    "call_button":     Region(49.9, 92.0, 48.3, 7.1),
    "register_button": Region(3.7,  93.5, 92.9, 5.6),
    "confirm_button":  Region(3.7,  93.5, 92.9, 5.6),
    "close_button":    Region(3.7,  93.5, 46.4, 5.6),
    "go_to_table_btn": Region(49.1, 26.1, 46.7, 6.0),
    "cancel_btn":      Region(4.8,  26.1, 43.0, 6.0),
    "center_modal":    Region(4.8,  8.6,  91.0, 23.1),
    "me_badge":        Region(85.3, 0,    14.7, 20.0),
    "badge_status":    Region(0,    0,    29.0, 100.0),
    "sign_in_button":  Region(6.4,  84.4, 86.9, 7.2),
    "login_button":    Region(6.4,  84.4, 86.9, 7.2),
}

# Tab x positions measured from actual device screenshots (709px app content at x=925-1634)
# Verified: all taps land inside app on 2560x1380 Waydroid display
# y=96.7% = nav bar center (screen_h=1380, nav_y=1334)
NAV_TABS = {
    "club":   (8.0,  96.7),
    "live":   (25.7, 96.7),
    "stage1": (41.7, 96.7),
    "stage2": (57.3, 96.7),
    "final":  (73.4, 96.7),
    "me":     (92.5, 96.7),
}


# ── Color helpers ─────────────────────────────────────────────────────────

def _app_crop(screen: np.ndarray) -> np.ndarray:
    al = _APP_LEFT
    ar = _APP_RIGHT if _APP_RIGHT else screen.shape[1]
    return screen[:, al:ar]


def _slice(img: np.ndarray, y1p: float, y2p: float,
           x1p: float = 0.0, x2p: float = 100.0) -> np.ndarray:
    h, w = img.shape[:2]
    return img[int(y1p/100*h):int(y2p/100*h), int(x1p/100*w):int(x2p/100*w)]


def _white(img: np.ndarray, thresh: int = 200) -> float:
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
    return float(np.sum(g > thresh)) / g.size * 100


def _dark(img: np.ndarray, thresh: int = 30) -> float:
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
    return float(np.sum(g < thresh)) / g.size * 100


def _table_green(img: np.ndarray) -> float:
    """Dark/medium green felt pixels — broad range to catch various table themes."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    # ClubGG table felt: try H=35-100 (covers yellow-green through teal-green)
    # S > 30 (colored, not grey), V = 15-180 (dark to medium)
    m = cv2.inRange(hsv, np.array([35, 30, 15]), np.array([100, 255, 180]))
    return float(np.sum(m > 0)) / (img.shape[0]*img.shape[1]) * 100


def _bright_green(img: np.ndarray) -> float:
    """Bright green — badges, buttons, countdown arc."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, np.array([40,100,150]), np.array([85,255,255]))
    return float(np.sum(m > 0)) / (img.shape[0]*img.shape[1]) * 100


def _red(img: np.ndarray) -> float:
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m = cv2.bitwise_or(
        cv2.inRange(hsv, np.array([0,80,80]),   np.array([15,255,255])),
        cv2.inRange(hsv, np.array([165,80,80]), np.array([180,255,255])))
    return float(np.sum(m > 0)) / (img.shape[0]*img.shape[1]) * 100


def _has_green_arc(app: np.ndarray) -> bool:
    """Bright green circular arc = pre-game countdown."""
    center = _slice(app, 35, 75, 20, 80)
    return _bright_green(center) > 3.0


def _has_raise_panel(app: np.ndarray) -> bool:
    """
    Action buttons visible = it's our turn.

    Requires BOTH:
      - Fold button region (left side) has white text
      - Call/Raise region (right side) has white text

    Requiring both sides eliminates false positives from partial overlays
    (win/bust screens, break banners) that only light up one side.
    Additionally, the table must have green felt visible above the buttons,
    ruling out pure-white lobby/modal screens.
    """
    # Must have table green felt above the action panel
    table_area = _slice(app, 20, 75)
    if _table_green(table_area) < 10.0:
        return False

    left  = _slice(app, 88, 97, 2, 38)
    right = _slice(app, 88, 97, 50, 98)

    left_white  = left.size  > 0 and _white(left,  200) > 1.5
    right_white = right.size > 0 and _white(right, 200) > 1.5

    return left_white and right_white


def _has_hole_cards(app: np.ndarray) -> bool:
    """
    Bottom-left card fan (hole cards) is white when visible.
    Returns False during break (no cards on table) or pre-game.
    """
    cards = _slice(app, 76, 88, 0, 25)
    if cards.size == 0:
        return False
    return _white(cards, 220) > 10.0


def _has_white_box(app: np.ndarray, y1p: float, y2p: float,
                   x1p: float = 8.0, x2p: float = 92.0) -> bool:
    region = _slice(app, y1p, y2p, x1p, x2p)
    if region.size == 0:
        return False
    return _white(region, 210) > 30.0


def _bottom_green_button(app: np.ndarray) -> bool:
    btn = _slice(app, 93, 100, 4, 96)
    return _bright_green(btn) > 8.0


def _bottom_red_button(app: np.ndarray) -> bool:
    btn = _slice(app, 93, 100, 4, 96)
    return _red(btn) > 8.0


def _ocr_header(app: np.ndarray) -> str:
    from .ocr import ocr_text
    header = _slice(app, 5, 18)
    if header.size == 0:
        return ""
    return ocr_text(header, scale=2.0).lower().strip()


# ── Badge / suit detection ────────────────────────────────────────────────

_BADGE_HSV = {
    "green": ((40,  60,  80),  (80,  255, 255)),
    "blue":  ((100, 60,  80),  (130, 255, 255)),
    "red":   ((0,   80,  80),  (15,  255, 255)),
    "red2":  ((165, 80,  80),  (180, 255, 255)),
    "grey":  ((0,   0,   50),  (180, 40,  130)),
}


def detect_badge_color(img: np.ndarray) -> str:
    if img is None or img.size == 0:
        return "unknown"
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    cy, cx = img.shape[0]//2, img.shape[1]//2
    center = hsv[max(0,cy-8):cy+8, max(0,cx-8):cx+8]
    if center.size == 0:
        return "unknown"
    for color, (lo, hi) in _BADGE_HSV.items():
        mask = cv2.inRange(center, np.array(lo), np.array(hi))
        if np.sum(mask > 0) > center.shape[0]*center.shape[1]*0.20:
            return "red" if color == "red2" else color
    return "unknown"


_SUIT_HSV = {
    "spades":  ((0,   0,   0),   (180, 50,  70)),
    "hearts":  ((0,   80,  80),  (15,  255, 255)),
    "hearts2": ((165, 80,  80),  (180, 255, 255)),
    "diamonds":((100, 60,  60),  (130, 255, 255)),
    "clubs":   ((40,  60,  60),  (80,  255, 255)),
}


def detect_card_suit(card_img: np.ndarray) -> str:
    if card_img is None or card_img.size == 0:
        return "unknown"
    hsv = cv2.cvtColor(card_img, cv2.COLOR_BGR2HSV)
    counts: dict[str, int] = {}
    for suit, (lo, hi) in _SUIT_HSV.items():
        counts[suit] = int(np.sum(cv2.inRange(hsv, np.array(lo), np.array(hi)) > 0))
    counts["hearts"] = counts.pop("hearts", 0) + counts.pop("hearts2", 0)
    best = max(counts, key=counts.__getitem__)
    return "unknown" if counts[best] < 50 else best


def screen_hash(img: np.ndarray) -> str:
    import hashlib
    small = cv2.resize(img, (32, 32), interpolation=cv2.INTER_AREA)
    gray  = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return hashlib.md5(gray.tobytes()).hexdigest()


def detect_me_badge(card_img: np.ndarray) -> bool:
    if card_img is None or card_img.size == 0:
        return False
    h, w = card_img.shape[:2]
    top_right = card_img[0:int(h*0.25), int(w*0.80):]
    return _red(top_right) > 2.0 if top_right.size else False


def detect_eligible_text(card_img: np.ndarray) -> str:
    from .ocr import ocr_text
    h, w = card_img.shape[:2]
    return ocr_text(card_img[int(h*0.45):int(h*0.75), int(w*0.22):]).strip()


def detect_counter_text(card_img: np.ndarray) -> tuple[int, int]:
    from .ocr import ocr_text
    import re
    h, w = card_img.shape[:2]
    text = ocr_text(card_img[int(h*0.3):int(h*0.75), 0:int(w*0.28)])
    m = re.search(r"(\d+)\s*/\s*(\d+)", text)
    return (int(m.group(1)), int(m.group(2))) if m else (-1, -1)


# ── Template matching (element location only) ─────────────────────────────

def _assets_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "assets", "templates")  # type: ignore
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "templates")


_TEMPLATE_CACHE: dict[str, Optional[np.ndarray]] = {}


def _load_template(name: str) -> Optional[np.ndarray]:
    if name not in _TEMPLATE_CACHE:
        path = os.path.join(_assets_dir(), name)
        _TEMPLATE_CACHE[name] = cv2.imread(path) if os.path.exists(path) else None
    return _TEMPLATE_CACHE[name]


def _scaled_template(name: str) -> Optional[np.ndarray]:
    if name not in _SCALED_CACHE:
        tpl = _load_template(name)
        if tpl is None:
            _SCALED_CACHE[name] = None
        else:
            sx = _APP_W / _TEMPLATE_CAPTURE_W
            if abs(sx - 1.0) < 0.05:
                _SCALED_CACHE[name] = tpl
            else:
                th, tw = tpl.shape[:2]
                _SCALED_CACHE[name] = cv2.resize(tpl, (max(1,int(tw*sx)), max(1,int(th*sx))),
                                                  interpolation=cv2.INTER_AREA)
    return _SCALED_CACHE[name]


def match_template(screen, template_name, threshold=0.80,
                   region=None, screen_w=None, screen_h=None):
    tpl = _scaled_template(template_name)
    if tpl is None:
        return None
    al = _APP_LEFT
    ar = _APP_RIGHT if _APP_RIGHT else (screen_w or screen.shape[1])
    if region is not None and screen_w and screen_h:
        x0, y0, rw, rh = region.to_abs(screen_w, screen_h, al, ar)
    else:
        x0, y0, rw, rh = al, 0, ar - al, screen.shape[0]
    search = screen[y0:y0+rh, x0:x0+rw]
    if search.shape[0] < tpl.shape[0] or search.shape[1] < tpl.shape[1]:
        return None
    result = cv2.matchTemplate(search, tpl, cv2.TM_CCOEFF_NORMED)
    _, mv, _, ml = cv2.minMaxLoc(result)
    if mv >= threshold:
        th, tw = tpl.shape[:2]
        return (ml[0]+x0, ml[1]+y0, tw, th)
    return None


def match_template_bool(screen, template_name, threshold=0.80,
                        region=None, screen_w=None, screen_h=None) -> bool:
    return match_template(screen, template_name, threshold,
                          region, screen_w, screen_h) is not None


# ── Tab bar helpers ───────────────────────────────────────────────────────

def detect_tab_bar_exclamation(screen, screen_w, screen_h, app_left=0, app_right=None):
    """
    Find tabs with a red ! badge (action required).
    Returns list of (abs_x, abs_y) pixel positions to tap.

    Calibrated tab positions (absolute screen px, y≈50):
      Tab 1: x=764   Tab 2: x=884   Tab 3: x=1001   Tab 4: x=1116
    These are used as a reliable fallback when color detection finds a hit
    near one of these x positions.
    """
    # Calibrated absolute tab x positions and their y
    _TAB_ABS = [(764, 35), (884, 39), (1001, 34), (1116, 41)]
    _TAB_Y   = 50  # absolute y to tap

    al = app_left or _APP_LEFT
    ar = app_right or _APP_RIGHT or screen_w

    # Scan the top strip of the full screen (not just app viewport)
    # Tabs live at absolute y≈35-65px regardless of app bounds
    y0, y1 = 30, 70
    strip = screen[y0:y1, al:ar]
    if strip.size == 0:
        return []

    hsv = cv2.cvtColor(strip, cv2.COLOR_BGR2HSV)
    m = cv2.bitwise_or(
        cv2.inRange(hsv, np.array([0,  120, 120]), np.array([15,  255, 255])),
        cv2.inRange(hsv, np.array([165, 120, 120]), np.array([180, 255, 255])),
    )
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    hits = []
    for cnt in contours:
        if 20 < cv2.contourArea(cnt) < 800:
            mx, my, mw, mh = cv2.boundingRect(cnt)
            hit_x = al + mx + mw // 2  # absolute x
            # Snap to nearest calibrated tab position
            nearest = min(_TAB_ABS, key=lambda t: abs(t[0] - hit_x))
            if abs(nearest[0] - hit_x) < 80:  # within 80px = same tab
                hits.append(nearest)

    # Deduplicate by tab position
    seen = set()
    deduped = []
    for pos in hits:
        if pos not in seen:
            seen.add(pos)
            deduped.append(pos)

    return sorted(deduped, key=lambda p: p[0])


def detect_active_tables(screen, screen_w, screen_h, app_left=0, app_right=None):
    al = app_left or _APP_LEFT
    ar = app_right or _APP_RIGHT or screen_w
    r = REGIONS["top_tab_bar"]
    x0, y0, rw, rh = r.to_abs(screen_w, screen_h, al, ar)
    strip = screen[y0:y0+rh, x0:x0+rw]
    hsv = cv2.cvtColor(strip, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, np.array([40,40,40]), np.array([85,255,200]))
    m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_RECT, (20,5)))
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    results, seen = [], []
    for cnt in contours:
        if cv2.contourArea(cnt) < 500:
            continue
        mx, my, mw, mh = cv2.boundingRect(cnt)
        cx = x0+mx+mw//2
        cy = y0+my+mh//2
        if any(abs(cx-sx) < 50 for sx in seen):
            continue
        seen.append(cx)
        results.append((cx, cy))
    return sorted(results, key=lambda p: p[0])


def detect_tournament_cards(screen, screen_w, screen_h, app_left=0, app_right=None):
    """
    Detect tournament cards by scanning the left badge column for colored pill badges.
    Badge colors: green=Registering, blue=Running, red=LateReg.
    This approach works regardless of card content complexity.
    """
    al = app_left if app_left is not None else _APP_LEFT
    ar = app_right if app_right is not None else (_APP_RIGHT if _APP_RIGHT else screen_w)
    h  = screen.shape[0]
    app_w = ar - al

    list_y1    = int(0.12 * h)
    list_y2    = int(0.88 * h)  # stop before nav bar
    card_h_min = int(0.07 * h)
    card_h_max = int(0.14 * h)
    card_h_px  = int(0.10 * h)

    # Scan badge column (left 28% of app) for colored pills
    badge_col_w = int(0.28 * app_w)
    badge_col = screen[list_y1:list_y2, al:al + badge_col_w]
    hsv = cv2.cvtColor(badge_col, cv2.COLOR_BGR2HSV)

    green = cv2.inRange(hsv, np.array([40,  80, 80]), np.array([85,  255, 255]))
    blue  = cv2.inRange(hsv, np.array([95,  60, 60]), np.array([130, 255, 255]))
    red1  = cv2.inRange(hsv, np.array([0,   80, 80]), np.array([15,  255, 255]))
    red2  = cv2.inRange(hsv, np.array([165, 80, 80]), np.array([180, 255, 255]))
    mask  = cv2.bitwise_or(green, cv2.bitwise_or(blue, cv2.bitwise_or(red1, red2)))

    row_counts = np.sum(mask > 0, axis=1)

    # Find card tops: rows with >= 50 badge pixels
    in_badge, card_tops = False, []
    for y, count in enumerate(row_counts):
        if count >= 90 and not in_badge:
            in_badge = True
            card_tops.append(list_y1 + y)
        elif count < 50:
            in_badge = False

    # Build Region for each card
    cards = []
    for i, y_top in enumerate(card_tops):
        next_top = card_tops[i + 1] if i + 1 < len(card_tops) else y_top + card_h_px
        card_h   = max(card_h_min, min(card_h_max, next_top - y_top))
        cards.append({
            "region": Region(0, y_top / h * 100, 100, card_h / h * 100),
            "y_abs":  y_top,
        })

    # Fallback
    if not cards:
        y = int(0.15 * h)
        while y + card_h_px < list_y2:
            cards.append({"region": Region(0, y / h * 100, 100, card_h_px / h * 100), "y_abs": y})
            y += card_h_px + 2

    return cards


class StateDetector:
    """
    Color-based state detection. Detection hierarchy:
      1. Dark screen → modal/popup/table states
      2. Table green → in-hand/break/wait
      3. Light screen → lobby/me/settings/detail
    """

    def __init__(self, screen_w: int, screen_h: int,
                 app_left: int = 0, app_right: Optional[int] = None):
        self.screen_w = screen_w
        self.screen_h = screen_h
        self.app_left  = app_left
        self.app_right = app_right if app_right is not None else screen_w
        set_app_viewport(app_left, self.app_right)

    def detect(self, screen: np.ndarray) -> State:
        app = _app_crop(screen)
        if app.shape[0] == 0 or app.shape[1] == 0:
            return State.UNKNOWN

        white  = _white(app, 200)
        dark   = _dark(app, 30)
        tgreen = _table_green(app)
        bgreen = _bright_green(app)
        red    = _red(app)

        # ── 1. Logged out (ClubGG splash: bright white + TWO green buttons stacked)
        # Splash has Join button (y≈75-83%) AND Sign In button (y≈84-92%)
        # Game Settings has ONE green Confirm button at very bottom only (y≈91-97%)
        # Signal: green present in MIDDLE-BOTTOM zone y=75-88% = splash only
        if white > 50 and dark < 5 and tgreen < 2:
            mid_bottom = _slice(app, 75, 88)
            if _bright_green(mid_bottom) > 8.0:
                return State.LOGGED_OUT

        # ── 2. Go to Table popup: very dark + white box in top 40% ───────────
        if dark > 65 and _has_white_box(app, 8, 42):
            return State.GO_TO_TABLE_POPUP

        # ── 3. Win / Bust: dark overlay (modal on table background) ──────────
        # Lower threshold to 30% dark — "See You Next Time" shows partial table bg
        if dark > 30 and white < 25 and tgreen < 35:
            modal_white = _white(_slice(app, 18, 82, 5, 95), 200)
            if modal_white > 1.5:
                prize = _slice(app, 33, 48)
                hsv_p = cv2.cvtColor(prize, cv2.COLOR_BGR2HSV)
                gold  = cv2.inRange(hsv_p, np.array([20,150,150]), np.array([40,255,255]))
                gold_pct = float(np.sum(gold>0)) / (prize.shape[0]*prize.shape[1]) * 100
                if gold_pct > 0.5:
                    return State.WIN_SCREEN
                from .ocr import ocr_text
                hdr = _slice(app, 5, 35, 5, 95)
                t = ocr_text(hdr, scale=2.0).lower()
                if "thank" in t or "playing" in t:
                    return State.WIN_SCREEN
                if "see you" in t or "next time" in t or "sorry" in t or "knocked" in t:
                    return State.BUST_OUT_SCREEN
                if "rank" in t or "place" in t or "prize" in t:
                    return State.WIN_SCREEN if gold_pct > 0.0 else State.BUST_OUT_SCREEN
                # Still has modal content — check close/share buttons at bottom
                bottom = _slice(app, 80, 95, 5, 95)
                bt = ocr_text(bottom, scale=1.5).lower()
                if "close" in bt or "share" in bt:
                    return State.BUST_OUT_SCREEN

        # ── 4. Pre-game countdown: very dark + bright green arc ───────────────
        if dark > 65 and tgreen > 3 and _has_green_arc(app):
            return State.PRE_GAME_WAIT

        # ── 5. Table states (dark green felt dominant) ─────────────────────
        if tgreen > 35:
            top = _slice(app, 7, 16)
            if _bright_green(top) > 10.0:
                return State.TARGET_STACK
            # Check for action buttons (raise slider panel) — highest priority
            if _has_raise_panel(app):
                return State.IN_HAND_ACTION
            # Check for actual break banner: "On Break" text in center of screen
            # Only call BREAK_SCREEN if break text is visible, not just "no cards"
            center = _slice(app, 35, 65, 15, 85)
            if _white(center, 200) > 60:
                # Large white area in center = break overlay or pre-game message
                return State.BREAK_SCREEN
            # Default: at table, waiting (between hands or opponent's turn)
            return State.IN_HAND_WAITING

        # ── 6. Light-background states (lobby/modals/settings) ───────────────
        if white > 40:
            # Bottom sheet modal (SUCCESS / REGISTRATION): white box bottom 55%
            bot_white = _white(_slice(app, 44, 92, 4, 96), 205)
            if bot_white > 50:
                # Game Settings: very white (>85%) + green Confirm at bottom
                # Must check BEFORE modal OCR to avoid SUCCESS_MODAL false positive
                # But first: if there's a green Registering badge in top area, it's
                # a Tournament Detail page, not Game Settings
                very_white_early = _white(app, 230)
                if very_white_early > 85 and _bottom_green_button(app):
                    top_badge = _slice(app, 8, 20, 0, 22)
                    top_green = np.sum(cv2.inRange(
                        cv2.cvtColor(top_badge, cv2.COLOR_BGR2HSV),
                        np.array([40, 80, 80]), np.array([85, 255, 255])
                    ) > 0)
                    if top_green > 30:
                        return State.TOURNAMENT_DETAIL
                    return State.GAME_SETTINGS
                if _bottom_green_button(app):
                    from .ocr import ocr_text
                    t = ocr_text(_slice(app, 44, 58), scale=2.0).lower()
                    if "good luck" in t or "successfully" in t:
                        return State.SUCCESS_MODAL
                    if "registrat" in t or "tournament" in t:
                        return State.REGISTRATION_MODAL
                    return State.SUCCESS_MODAL

            # Tournament detail: red unregister button at very bottom
            if _bottom_red_button(app):
                return State.TOURNAMENT_DETAIL

            # Lobby detection: check for colored badge pills in left badge column
            # (green=Registering, blue=Running) — absent on Me page and Game Settings
            h_a, w_a = app.shape[:2]
            badge_col = app[int(0.18*h_a):int(0.90*h_a), 0:int(0.26*w_a)]
            if badge_col.size > 0:
                hsv_b = cv2.cvtColor(badge_col, cv2.COLOR_BGR2HSV)
                badge_green = np.sum(cv2.inRange(hsv_b,
                    np.array([40,100,100]), np.array([80,255,255])) > 0)
                badge_blue  = np.sum(cv2.inRange(hsv_b,
                    np.array([100,60,80]), np.array([130,255,255])) > 0)
                if badge_green > 1000 or badge_blue > 1000:
                    # We are in a lobby — which one?
                    # Stage 2 only has green Registering badges (no blue Running)
                    # Stage 1 has mixed: green + blue
                    # Final Stage typically only has green
                    # Use green button detection for tournament detail override
                    if _bottom_green_button(app):
                        return State.TOURNAMENT_DETAIL
                    # Nav tab colour: active tab indicator darkens its circle
                    # Use OCR header as tiebreaker — but it's often garbage
                    # Default: if all green = likely Stage 2 (on-demand SnGs)
                    if badge_blue > 1000:
                        return State.LOBBY_STAGE1
                    # Distinguish Stage2 vs Final by nav bar active indicator
                    h_nav, w_nav = app.shape[:2]
                    nav_g = cv2.cvtColor(app[int(0.92*h_nav):, :], cv2.COLOR_BGR2GRAY)
                    col_d = np.sum(nav_g < 80, axis=0)
                    wpx   = int(0.04 * w_nav)
                    fcx   = int(0.734 * w_nav)
                    s2cx  = int(0.573 * w_nav)
                    fscore  = int(np.sum(col_d[max(0,fcx-wpx):fcx+wpx]))
                    s2score = int(np.sum(col_d[max(0,s2cx-wpx):s2cx+wpx]))
                    if fscore > s2score and fscore > 100:
                        return State.LOBBY_FINAL
                    return State.LOBBY_STAGE2

            # Very white (>88%) with no lobby badges = Me page or Game Settings
            very_white = _white(app, 230)
            if very_white > 85:
                # Game Settings has a green Confirm button at very bottom
                if _bottom_green_button(app):
                    return State.GAME_SETTINGS
                return State.ME_PAGE

            # Tournament detail with green register button (fallback if top badge check missed)
            if _bottom_green_button(app):
                top_badge = _slice(app, 8, 20, 0, 22)
                top_green = np.sum(cv2.inRange(
                    cv2.cvtColor(top_badge, cv2.COLOR_BGR2HSV),
                    np.array([40, 80, 80]), np.array([85, 255, 255])
                ) > 0)
                if top_green > 30:
                    return State.TOURNAMENT_DETAIL

            # Live Event tab: dark card backgrounds
            if _dark(_slice(app, 15, 85), 60) > 30:
                return State.LOBBY_LIVE_EVENT

            # Default light screen = Stage 1 lobby
            return State.LOBBY_STAGE1

        return State.UNKNOWN
