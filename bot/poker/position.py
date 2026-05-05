"""
Dealer button and seat position detection.
Calculates our position (BTN/SB/BB/CO/MP/UTG) relative to dealer.
"""
from __future__ import annotations

import logging
import numpy as np
import cv2

log = logging.getLogger(__name__)

# Position labels ordered from dealer clockwise
POSITIONS = ["BTN", "SB", "BB", "UTG", "UTG+1", "MP", "MP+1", "HJ", "CO"]


def detect_dealer_button_position(
    screen: np.ndarray,
    screen_w: int,
    screen_h: int,
) -> tuple[int, int] | None:
    """
    Find the gold 'D' dealer button on screen.
    Returns (x, y) pixel position or None if not found.
    The dealer button is a gold/yellow circular button with 'D' text.
    """
    # Look in the table area (exclude top nav and bottom action panel)
    table_y1 = int(0.12 * screen_h)
    table_y2 = int(0.82 * screen_h)
    table = screen[table_y1:table_y2, :]

    hsv = cv2.cvtColor(table, cv2.COLOR_BGR2HSV)
    # Gold/yellow: H=20-35, S>100, V>150
    lo, hi = np.array([20, 100, 150]), np.array([35, 255, 255])
    mask = cv2.inRange(hsv, lo, hi)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if 200 < area < 2000:  # dealer button is small circle
            mx, my, mw, mh = cv2.boundingRect(cnt)
            # Roughly square (circular)
            if 0.6 < mw / max(mh, 1) < 1.6:
                cx = mx + mw // 2
                cy = table_y1 + my + mh // 2
                return (cx, cy)
    return None


def detect_seat_positions(
    screen: np.ndarray,
    screen_w: int,
    screen_h: int,
) -> list[tuple[int, int]]:
    """
    Detect approximate (x, y) positions of all player seats on the table.
    Returns list of seat center positions, ordered clockwise from top.

    Seat layout for up to 9 players (approximate screen positions):
    0=top-center, 1=top-right, 2=right, 3=bottom-right,
    4=bottom-left (our seat = Mercy09), 5=left, 6=top-left
    """
    # Standard approximate seat positions as percentage of screen
    seat_pcts = [
        (50, 18),   # 0: top-center
        (75, 22),   # 1: top-right
        (88, 45),   # 2: right
        (75, 68),   # 3: bottom-right
        (25, 68),   # 4: bottom-left (our seat)
        (12, 45),   # 5: left
        (25, 22),   # 6: top-left
    ]
    return [
        (int(xp / 100 * screen_w), int(yp / 100 * screen_h))
        for xp, yp in seat_pcts
    ]


def find_our_seat_index(
    screen: np.ndarray,
    screen_w: int,
    screen_h: int,
) -> int:
    """
    Our avatar (Mercy09) is always at bottom-left of the table.
    Returns seat index 4 (bottom-left) — fixed per screen layout.
    """
    return 4  # always bottom-left in ClubGG


def calculate_position(
    dealer_pos: tuple[int, int] | None,
    our_seat_idx: int,
    all_seats: list[tuple[int, int]],
    active_players: int,
) -> str:
    """
    Calculate our position string given dealer button location.
    Returns 'BTN', 'SB', 'BB', 'UTG', 'CO', 'MP', etc.
    """
    if dealer_pos is None or not all_seats:
        return "MP"  # safe default

    # Find which seat is closest to dealer button
    min_dist = float("inf")
    dealer_seat_idx = 0
    for i, (sx, sy) in enumerate(all_seats):
        dist = ((dealer_pos[0] - sx) ** 2 + (dealer_pos[1] - sy) ** 2) ** 0.5
        if dist < min_dist:
            min_dist = dist
            dealer_seat_idx = i

    n = len(all_seats)
    if n == 0:
        return "MP"

    # Calculate how many seats clockwise we are from dealer
    steps_from_dealer = (our_seat_idx - dealer_seat_idx) % n

    # Map steps to position name
    pos_map = {
        0: "BTN",
        1: "SB",
        2: "BB",
        3: "UTG",
        4: "UTG+1",
        5: "MP",
        6: "HJ",
        7: "CO",
        8: "BTN",  # wraps
    }

    if active_players <= 3:
        # Heads-up / 3-handed adjustments
        short_map = {0: "BTN", 1: "SB", 2: "BB"}
        return short_map.get(steps_from_dealer % 3, "BTN")

    if active_players <= 6:
        short_map = {0: "BTN", 1: "SB", 2: "BB", 3: "UTG", 4: "MP", 5: "CO"}
        return short_map.get(steps_from_dealer % 6, "MP")

    return pos_map.get(steps_from_dealer, "MP")


def position_is_late(position: str) -> bool:
    """Return True if position is considered late (BTN, CO)."""
    return position in ("BTN", "CO")


def position_is_blind(position: str) -> bool:
    return position in ("SB", "BB")


def position_is_early(position: str) -> bool:
    return position in ("UTG", "UTG+1")
