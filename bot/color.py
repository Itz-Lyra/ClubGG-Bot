"""
Color probe utilities for pixel-based UI detection.

Used to verify a UI element is on-screen by sampling a small averaged box
around a known calibrated pixel and comparing its BGR value to a target
hex within a configurable similarity threshold (default 90%).

Why averaged sampling: ClubGG renders with sub-pixel/dither variation,
so adjacent pixels can differ noticeably. Averaging a 5x5 box around the
sample point smooths that out and gives a stable color reading.
"""
from __future__ import annotations

from typing import Iterable, Tuple

import numpy as np


# Maximum distance in BGR space (3-channel 0-255 cube diagonal)
_MAX_DIST = (3 * 255 * 255) ** 0.5  # ≈ 441.673


def hex_to_bgr(h: str) -> Tuple[int, int, int]:
    """Convert '#RRGGBB' or 'RRGGBB' to a (B, G, R) tuple matching OpenCV's order."""
    h = h.lstrip("#").strip()
    if len(h) != 6:
        raise ValueError(f"Bad hex color: {h!r}")
    r = int(h[0:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    return (b, g, r)


def color_similarity(c1_bgr: Tuple[int, int, int],
                     c2_bgr: Tuple[int, int, int]) -> float:
    """Return 0.0..1.0. 1.0 = identical, 0.0 = farthest apart in BGR cube."""
    db = float(c1_bgr[0]) - float(c2_bgr[0])
    dg = float(c1_bgr[1]) - float(c2_bgr[1])
    dr = float(c1_bgr[2]) - float(c2_bgr[2])
    dist = (db * db + dg * dg + dr * dr) ** 0.5
    return max(0.0, 1.0 - dist / _MAX_DIST)


def sample_avg_bgr(screen: np.ndarray, x: int, y: int,
                   radius: int = 2) -> Tuple[int, int, int] | None:
    """
    Average BGR over a (2r+1)x(2r+1) box centred on (x, y).
    Returns None if the box is entirely outside the screen.
    """
    h, w = screen.shape[:2]
    x1 = max(0, x - radius)
    x2 = min(w, x + radius + 1)
    y1 = max(0, y - radius)
    y2 = min(h, y + radius + 1)
    if x2 <= x1 or y2 <= y1:
        return None
    roi = screen[y1:y2, x1:x2]
    avg = roi.reshape(-1, 3).mean(axis=0)
    return (int(avg[0]), int(avg[1]), int(avg[2]))


def pixel_matches(screen: np.ndarray, x: int, y: int,
                  target_bgr: Tuple[int, int, int],
                  threshold: float = 0.90,
                  radius: int = 2) -> bool:
    """
    True if averaged colour at (x, y) is at least `threshold` similar to target.
    """
    avg = sample_avg_bgr(screen, x, y, radius=radius)
    if avg is None:
        return False
    return color_similarity(avg, target_bgr) >= threshold


def all_pixels_match(screen: np.ndarray,
                     points: Iterable[Tuple[int, int]],
                     target_bgr: Tuple[int, int, int],
                     threshold: float = 0.90,
                     radius: int = 2) -> bool:
    """
    True if every (x, y) in `points` matches `target_bgr` within threshold.
    Used for multi-point confirmation (e.g. result-screen modal background).
    """
    for x, y in points:
        if not pixel_matches(screen, x, y, target_bgr, threshold, radius):
            return False
    return True
