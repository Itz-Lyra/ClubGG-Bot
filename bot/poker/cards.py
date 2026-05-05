"""
Card reading from screen using Set 3 (4-color deck) suit detection and OCR rank detection.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional
import numpy as np
import cv2

from ..adb import Region
from ..vision import detect_card_suit
from ..ocr import ocr_card_rank

log = logging.getLogger(__name__)

RANKS = ("A", "K", "Q", "J", "T", "9", "8", "7", "6", "5", "4", "3", "2")
SUITS = ("spades", "hearts", "diamonds", "clubs")

SUIT_SYMBOL = {
    "spades": "♠",
    "hearts": "♥",
    "diamonds": "♦",
    "clubs": "♣",
    "unknown": "?",
}


@dataclass(frozen=True)
class Card:
    rank: str  # A K Q J T 9 8 7 6 5 4 3 2
    suit: str  # spades hearts diamonds clubs

    def __str__(self) -> str:
        return f"{self.rank}{SUIT_SYMBOL.get(self.suit, '?')}"

    def rank_value(self) -> int:
        """Numeric rank value: A=14, K=13 ... 2=2."""
        return {"A": 14, "K": 13, "Q": 12, "J": 11, "T": 10}.get(self.rank, int(self.rank) if self.rank.isdigit() else 0)

    def to_treys(self) -> int:
        """Convert to treys Card integer for hand evaluation."""
        from treys import Card as TreysCard
        suit_map = {"spades": "s", "hearts": "h", "diamonds": "d", "clubs": "c"}
        s = suit_map.get(self.suit, "s")
        r = self.rank if self.rank != "T" else "T"
        return TreysCard.new(f"{r}{s}")


def read_card_from_crop(card_img):
    """
    Read rank from top of card via OCR, suit from background color.
    Card Set 3: green=clubs, blue=diamonds, red=hearts, dark=spades.
    """
    import cv2 as _cv2
    if card_img is None or card_img.size == 0:
        return None

    h, w = card_img.shape[:2]

    # Rank: OCR top 40% of card
    rank_crop = card_img[0:int(h * 0.40), :]
    rank = ocr_card_rank(rank_crop)
    if not rank:
        rank = ocr_card_rank(card_img[0:int(h * 0.50), 0:int(w * 0.55)])
    if not rank:
        return None

    # Suit: sample center of card background color
    suit_crop = card_img[int(h*0.2):int(h*0.8), int(w*0.1):int(w*0.9)]
    suit = detect_card_suit(suit_crop)
    if suit == "unknown":
        suit = detect_card_suit(card_img)
    if suit == "unknown":
        log.debug("Could not detect suit for rank %s", rank)
        suit = "spades"

    return Card(rank=rank, suit=suit)

def split_card_strip(strip: np.ndarray, num_cards: int) -> list[np.ndarray]:
    """Split a horizontal strip image into individual card images."""
    if strip.size == 0:
        return []
    w = strip.shape[1]
    card_w = w // num_cards
    return [strip[:, i * card_w:(i + 1) * card_w] for i in range(num_cards)]


def read_hole_cards(
    screen: np.ndarray,
    screen_w: int,
    screen_h: int,
    game_type: str = "nlh",  # "nlh" or "plo"
) -> list[Card]:
    """
    Read hole cards from bottom-left of screen.
    NLH: 2 cards. PLO: 4 cards.
    """
    from ..vision import REGIONS
    num_cards = 2 if game_type == "nlh" else 4
    region = REGIONS["hole_cards"]
    card_strip = region.crop(screen, screen_w, screen_h)

    card_imgs = split_card_strip(card_strip, num_cards)
    cards = []
    for img in card_imgs:
        card = read_card_from_crop(img)
        if card:
            cards.append(card)

    if len(cards) < num_cards:
        log.debug("Only read %d/%d hole cards", len(cards), num_cards)
    return cards


def read_community_cards(
    screen: np.ndarray,
    screen_w: int,
    screen_h: int,
) -> list[Card]:
    """
    Read community cards from center of table.
    Returns 0, 3, 4, or 5 cards depending on street.
    Pre-flop the region is all green felt — detect cards by looking for
    white card rectangles before attempting OCR.
    """
    import cv2 as _cv2
    from ..vision import REGIONS
    region = REGIONS["community_cards"]
    strip = region.crop(screen, screen_w, screen_h)

    if strip.size == 0:
        return []

    # Check if cards are actually present: cards show as bright white rectangles
    gray = _cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
    white_pct = float(np.sum(gray > 220)) / gray.size * 100
    if white_pct < 3.0:
        # Less than 3% white pixels = no cards dealt yet (pre-flop green felt)
        return []

    # Try 5 cards first, fall back to fewer
    for num in (5, 4, 3):
        card_imgs = split_card_strip(strip, num)
        cards = []
        for img in card_imgs:
            card = read_card_from_crop(img)
            if card:
                cards.append(card)
        if len(cards) >= num - 1:
            return cards

    return []


def detect_game_type_from_tab(tab_img: np.ndarray) -> str:
    """
    Detect NLH vs PLO from tab bar thumbnail.
    NLH tabs show 2 cards, PLO tabs show 4 cards.
    Heuristic: count card-sized regions in the tab image.
    """
    if tab_img is None or tab_img.size == 0:
        return "nlh"
    w = tab_img.shape[1]
    # PLO tabs are roughly twice as wide as NLH tabs due to 4 cards
    # Simple heuristic: wide tabs = PLO
    if w > tab_img.shape[0] * 2.5:
        return "plo"
    return "nlh"
