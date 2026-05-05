"""
Win and bust-out screen handlers (Section 08).

Win screen layout (verified from SM-S948U screenshots, 621x1342 content):
  - Tournament name: Region(11.8%, 42.3%, 76.8%, 5.4%) — white rounded box
  - Rank: Region(36.2%, 29.3%, 40.3%, 4.8%) — e.g. "12/118"
  - Prize: Region(16.1%, 36.5%, 70.9%, 5.1%) — gold "Stage 2 Ticket" text
  - Play time: Region(11.8%, 46.6%, 43.8%, 2.6%) — "Play Time 00:34:41"
  - Close button (left): tap to dismiss, NEVER tap Share

Bust screen layout:
  - Tournament name: Region(11.8%, 42.7%, 76.5%, 5.0%)
  - Rank: Region(33.0%, 25.9%, 38.6%, 5.2%) — may show "-/1,270" (orange dash = unranked)
  - Play time: Region(11.8%, 46.0%, 43.8%, 2.5%)
"""
from __future__ import annotations

import logging
import re
import time

log = logging.getLogger(__name__)

# Verified regions for result screens (as % of content dimensions)
# Close-button center calibrated to app-relative (26.6%, 83.2%) on
# Waydroid 1920×1024 (app x=704-1216). _tap_close taps the region center,
# so the small 2x2 box is just to define a clean center point.
_WIN_REGIONS = {
    "tournament_name": (11.8, 42.3, 76.8, 5.4),
    "rank":            (36.2, 29.3, 40.3, 4.8),
    "prize":           (16.1, 36.5, 70.9, 5.1),
    "play_time":       (11.8, 46.6, 43.8, 2.6),
    "close_button":    (25.6, 82.2,  2.0, 2.0),
}

_BUST_REGIONS = {
    "tournament_name": (11.8, 42.7, 76.5, 5.0),
    "rank":            (33.0, 25.9, 38.6, 5.2),
    "play_time":       (11.8, 46.0, 43.8, 2.5),
    "close_button":    (25.6, 82.2,  2.0, 2.0),
}


def _crop(screen, region_pct: tuple, screen_w: int, screen_h: int):
    import numpy as np
    xp, yp, wp, hp = region_pct
    x = int(xp / 100 * screen_w)
    y = int(yp / 100 * screen_h)
    w = int(wp / 100 * screen_w)
    h = int(hp / 100 * screen_h)
    return screen[y:y+h, x:x+w]


def _ocr(img) -> str:
    from ..ocr import ocr_text
    return ocr_text(img, scale=2.5).strip()


def _parse_rank(text: str) -> str:
    """Parse 'N/total' rank from OCR text. Handles '-/1,270' (unranked)."""
    # Remove commas from numbers
    text = text.replace(",", "").replace(" ", "")
    m = re.search(r"(\d+|[-–])\s*/\s*(\d+)", text)
    if m:
        rank_part = m.group(1) if m.group(1) not in ("-", "–") else "?"
        return f"{rank_part}/{m.group(2)}"
    return "?/?"


def _parse_play_time(text: str) -> str:
    """Extract HH:MM:SS or MM:SS from OCR text."""
    m = re.search(r"(\d{1,2}:\d{2}:\d{2}|\d{1,2}:\d{2})", text)
    return m.group(1) if m else "?"


def _parse_ticket_type(text: str) -> str:
    """Detect ticket type from prize text."""
    t = text.lower()
    if "final" in t or "stage 3" in t:
        return "final"
    if "stage 2" in t:
        return "stage2"
    if "stage 1" in t:
        return "stage1"
    return "unknown"


def _parse_tournament_name(text: str) -> str:
    """Clean OCR'd tournament name — remove junk characters."""
    # Remove leading/trailing whitespace and control chars
    name = re.sub(r"[^\w\s\-\(\)\.&]", "", text).strip()
    # Collapse multiple spaces
    name = re.sub(r"\s{2,}", " ", name)
    return name if name else "Unknown"


def handle_win_screen(
    adb,
    screen,
    screen_w: int,
    screen_h: int,
    stats,
    discord_cfg: dict,
) -> dict:
    """
    Handle THANK YOU FOR PLAYING win screen.
    Reads tournament name, rank, ticket type, play time from verified regions.
    Taps Close (left button). NEVER taps Share.
    Returns dict with all parsed win info.
    """
    # Read all info regions before tapping Close
    tournament_name = _parse_tournament_name(
        _ocr(_crop(screen, _WIN_REGIONS["tournament_name"], screen_w, screen_h))
    )
    rank = _parse_rank(
        _ocr(_crop(screen, _WIN_REGIONS["rank"], screen_w, screen_h))
    )
    prize_text = _ocr(_crop(screen, _WIN_REGIONS["prize"], screen_w, screen_h))
    ticket_type = _parse_ticket_type(prize_text)
    play_time = _parse_play_time(
        _ocr(_crop(screen, _WIN_REGIONS["play_time"], screen_w, screen_h))
    )

    prize_labels = {
        "stage1": "Stage 1 Ticket",
        "stage2": "Stage 2 Ticket",
        "final":  "Final Stage Ticket",
        "unknown": "Ticket",
    }
    prize_label = prize_labels.get(ticket_type, "Ticket")

    # Record in stats (Stage 1 may pass stats=None — guard against it)
    if stats is not None:
        stats.record_ticket_win(tournament=tournament_name, ticket_type=ticket_type, rank=rank)

    log.info("★ WIN: %s | %s | Rank: %s | Time: %s", tournament_name, prize_label, rank, play_time)

    # Tap Close — left button only, NEVER Share
    _tap_close(adb, _WIN_REGIONS["close_button"], screen_w, screen_h)

    return {
        "type":         "win",
        "tournament":   tournament_name,
        "ticket_type":  ticket_type,
        "prize":        prize_label,
        "rank":         rank,
        "play_time":    play_time,
    }


def handle_bust_screen(
    adb,
    screen,
    screen_w: int,
    screen_h: int,
    stats,
) -> dict:
    """
    Handle SEE YOU NEXT TIME bust-out screen.
    Reads tournament name, rank, play time.
    Taps Close. NEVER taps Share.
    """
    tournament_name = _parse_tournament_name(
        _ocr(_crop(screen, _BUST_REGIONS["tournament_name"], screen_w, screen_h))
    )
    rank = _parse_rank(
        _ocr(_crop(screen, _BUST_REGIONS["rank"], screen_w, screen_h))
    )
    play_time = _parse_play_time(
        _ocr(_crop(screen, _BUST_REGIONS["play_time"], screen_w, screen_h))
    )

    if stats is not None:
        stats.record_bust()
    log.info("✗ BUST: %s | Rank: %s | Time: %s", tournament_name, rank, play_time)

    _tap_close(adb, _BUST_REGIONS["close_button"], screen_w, screen_h)

    return {
        "type":       "bust",
        "tournament": tournament_name,
        "rank":       rank,
        "play_time":  play_time,
    }


def _tap_close(adb, region_pct: tuple, screen_w: int, screen_h: int) -> None:
    """Tap the center of the Close button region."""
    xp, yp, wp, hp = region_pct
    cx_pct = xp + wp / 2
    cy_pct = yp + hp / 2
    adb.tap(cx_pct, cy_pct)
    time.sleep(1.0)
