"""
OCR wrapper using Tesseract via pytesseract.
Auto-detects tesseract binary path for Fedora and Arch.
"""
from __future__ import annotations

import os
import sys
import shutil
import logging
import re
from typing import Optional
import numpy as np
import cv2

log = logging.getLogger(__name__)
_tesseract_configured = False


def configure_tesseract() -> None:
    """Find and configure tesseract binary. Called once at startup."""
    global _tesseract_configured
    if _tesseract_configured:
        return
    import pytesseract

    # 1. Bundled with PyInstaller
    bundled = os.path.join(getattr(sys, "_MEIPASS", ""), "tesseract")
    if os.path.exists(bundled):
        pytesseract.pytesseract.tesseract_cmd = bundled
        _tesseract_configured = True
        return

    # 2. Auto-detect on PATH
    found = shutil.which("tesseract")
    if found:
        pytesseract.pytesseract.tesseract_cmd = found
        _tesseract_configured = True
        return

    # 3. Common system paths (Fedora + Arch both use /usr/bin)
    for path in ["/usr/bin/tesseract", "/usr/local/bin/tesseract"]:
        if os.path.exists(path):
            pytesseract.pytesseract.tesseract_cmd = path
            _tesseract_configured = True
            return

    log.error("Tesseract not found. Install: sudo pacman -S tesseract  OR  sudo dnf install tesseract tesseract-langpack-eng")


def _preprocess(img: np.ndarray, scale: float = 2.0, digits_only: bool = False) -> np.ndarray:
    """
    Upscale and threshold image for better OCR accuracy.
    Tesseract works best on high-contrast, upscaled images.
    """
    if img.size == 0:
        return img
    # Upscale
    h, w = img.shape[:2]
    img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
    # Convert to gray
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img
    # Threshold: OTSU works well for dark-on-light and light-on-dark
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return thresh


def ocr_text(
    img: np.ndarray,
    digits_only: bool = False,
    scale: float = 2.0,
    lang: str = "eng",
) -> str:
    """
    Run OCR on an image region. Returns cleaned string.
    digits_only=True restricts to 0-9 for chip counts.
    """
    configure_tesseract()
    import pytesseract

    if img is None or img.size == 0:
        return ""

    preprocessed = _preprocess(img, scale=scale, digits_only=digits_only)

    config = "--psm 7"  # single line of text
    if digits_only:
        config += " -c tessedit_char_whitelist=0123456789"

    try:
        text = pytesseract.image_to_string(preprocessed, lang=lang, config=config)
        return text.strip()
    except Exception as exc:
        log.debug("OCR error: %s", exc)
        return ""


def ocr_number(img: np.ndarray, scale: float = 2.0) -> int:
    """OCR a chip count or number. Returns 0 on failure."""
    text = ocr_text(img, digits_only=True, scale=scale)
    digits = re.sub(r"[^0-9]", "", text)
    if not digits:
        return 0
    try:
        return int(digits)
    except ValueError:
        return 0


def ocr_chip_count(img: np.ndarray) -> int:
    """Read chip/pot amount — handles commas and 'k'/'K' suffixes."""
    text = ocr_text(img, scale=2.5)
    text = text.strip().replace(",", "").replace(" ", "")
    # Handle 'k' suffix (e.g. "1.5k" = 1500)
    m = re.match(r"^([0-9.]+)[kK]$", text)
    if m:
        try:
            return int(float(m.group(1)) * 1000)
        except ValueError:
            pass
    digits = re.sub(r"[^0-9]", "", text)
    if not digits:
        return 0
    try:
        return int(digits)
    except ValueError:
        return 0


def ocr_card_rank(img: np.ndarray) -> str:
    """
    OCR a single card rank character.
    Returns 'A','K','Q','J','T','9'...'2' or '' on failure.
    """
    configure_tesseract()
    import pytesseract

    preprocessed = _preprocess(img, scale=3.0)
    config = "--psm 10 -c tessedit_char_whitelist=AKQJT23456789"
    try:
        text = pytesseract.image_to_string(preprocessed, config=config).strip().upper()
        # Normalize '10' to 'T'
        if text in ("10", "1O", "IO"):
            return "T"
        if text in ("A", "K", "Q", "J", "T", "9", "8", "7", "6", "5", "4", "3", "2"):
            return text
        return ""
    except Exception:
        return ""


def ocr_blinds(img: np.ndarray) -> tuple[int, int, int]:
    """
    Parse 'Blinds: SB/BB(Ante)' from center info block.
    Returns (sb, bb, ante) or (0, 0, 0) on failure.
    """
    text = ocr_text(img)
    m = re.search(r"Blinds?:?\s*(\d+)\s*/\s*(\d+)(?:\s*\((\d+)\))?", text, re.IGNORECASE)
    if m:
        sb = int(m.group(1))
        bb = int(m.group(2))
        ante = int(m.group(3)) if m.group(3) else 0
        return sb, bb, ante
    return 0, 0, 0


def ocr_rank_info(img: np.ndarray) -> tuple[int, int]:
    """
    Parse 'My Rank Nth / N' from center info block.
    Returns (my_rank, total_players) or (0, 0).
    """
    text = ocr_text(img)
    m = re.search(r"(?:My\s+)?Rank\s+(\d+)\s*/\s*(\d+)", text, re.IGNORECASE)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 0, 0


def ocr_prize_remaining(img: np.ndarray) -> int:
    """
    Parse 'Prize Left Nx' or 'Total Prize Nx' — return N.
    Returns 0 if not found.
    """
    text = ocr_text(img)
    m = re.search(r"Prize\s+(?:Left|Remaining|Total)?\s*(\d+)", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return 0


def ocr_ticket_count(img: np.ndarray) -> int:
    """Read a ticket count number from Me page row."""
    return ocr_number(img)


def ocr_tournament_name(card_img: np.ndarray) -> str:
    """Read tournament name from card right section."""
    h, w = card_img.shape[:2]
    name_region = card_img[0:int(h*0.5), int(w*0.3):]
    return ocr_text(name_region, scale=2.0).strip()


def ocr_call_amount(img: np.ndarray) -> int:
    """
    Read call amount from Call button.
    Returns 0 if the button shows 'Check' (free action).
    """
    text = ocr_text(img)
    if re.search(r"\bcheck\b", text, re.IGNORECASE):
        return 0
    m = re.search(r"(\d[\d,]*)", text)
    if m:
        return int(m.group(1).replace(",", ""))
    return 0


def ocr_ticket_type_from_win(img: np.ndarray) -> str:
    """
    Read ticket type from win screen prize section.
    Returns 'stage1', 'stage2', 'final', or 'unknown'.
    """
    text = ocr_text(img).lower()
    if "final" in text or "stage 3" in text:
        return "final"
    if "stage 2" in text:
        return "stage2"
    if "stage 1" in text:
        return "stage1"
    return "unknown"


def ocr_rank_from_result_screen(img: np.ndarray) -> str:
    """Parse 'Rank: N / Total' from bust/win screen."""
    text = ocr_text(img)
    m = re.search(r"(\d+)\s*/\s*(\d+)", text)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return "?/?"


def ocr_play_time(img: np.ndarray) -> str:
    """Parse play time from result screen. Returns HH:MM:SS or '?'."""
    text = ocr_text(img)
    m = re.search(r"(\d{1,2}:\d{2}:\d{2}|\d{1,2}:\d{2})", text)
    return m.group(1) if m else "?"


def ocr_membership_tier(img: np.ndarray) -> str:
    """Read membership tier text from Me page."""
    text = ocr_text(img).strip().lower()
    if "platinum" in text:
        return "Platinum Membership"
    if "gold" in text:
        return "Gold Membership"
    return "Free"
