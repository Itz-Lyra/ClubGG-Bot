"""
PLO hand evaluator (custom — DO NOT use treys directly for PLO).

PLO rule: player MUST use exactly 2 hole cards + exactly 3 community cards.
treys evaluates any 5 cards without enforcing this constraint, which produces
incorrect results (e.g. a nut flush using 4 hole cards is not legal in PLO).

Implementation: enumerate all C(4,2)=6 hole card pairs × C(5,3)=10 board
triplets = 60 combinations. Evaluate each 5-card hand with treys (correctly
treating them as 5 known cards), return the best result.
"""
from __future__ import annotations

import logging
from itertools import combinations
from dataclasses import dataclass
from typing import Optional

from .cards import Card
from .position import position_is_late, position_is_blind

log = logging.getLogger(__name__)

# Hand class constants (same as NLH)
STRAIGHT_FLUSH = 1
FOUR_OF_A_KIND = 2
FULL_HOUSE = 3
FLUSH = 4
STRAIGHT = 5
SET = 6        # Three of a kind
TWO_PAIR = 7
ONE_PAIR = 8
HIGH_CARD = 9


@dataclass
class PLOHandResult:
    rank_class: int
    score: int       # treys score (lower = better)
    description: str
    best_hole: tuple[Card, Card]
    best_board: tuple[Card, Card, Card]


def evaluate_plo_best_hand(
    hole_cards: list[Card],
    community_cards: list[Card],
) -> Optional[PLOHandResult]:
    """
    Evaluate PLO hand by enumerating all legal C(4,2) × C(5,3) combinations.
    Returns the best valid 5-card hand, enforcing the must-use-2-hole-cards rule.
    """
    from treys import Evaluator

    if len(hole_cards) < 2 or len(community_cards) < 3:
        return None

    evaluator = Evaluator()
    best_score = float("inf")
    best_result = None

    # C(4,2) = 6 hole card pairs
    for hole_pair in combinations(hole_cards, 2):
        # C(5,3) = 10 board triplets (or C(3,3)=1 on flop, C(4,3)=4 on turn)
        for board_triple in combinations(community_cards, 3):
            h1, h2 = hole_pair
            b1, b2, b3 = board_triple
            try:
                # Evaluate as 5-card hand: 2 hole + 3 board
                board_treys = [b.to_treys() for b in (b1, b2, b3)]
                hand_treys = [h.to_treys() for h in (h1, h2)]
                score = evaluator.evaluate(board_treys, hand_treys)
            except Exception as exc:
                log.debug("PLO eval error for combo %s|%s: %s", hole_pair, board_triple, exc)
                continue

            if score < best_score:
                best_score = score
                rank_class = evaluator.get_rank_class(score)
                desc = evaluator.class_to_string(rank_class)
                best_result = PLOHandResult(
                    rank_class=rank_class,
                    score=score,
                    description=desc,
                    best_hole=(h1, h2),
                    best_board=(b1, b2, b3),
                )

    return best_result


# ── PLO preflop hand property helpers ───────────────────────────────────

def is_double_suited(hole_cards: list[Card]) -> bool:
    """True if hole cards contain exactly 2 pairs of same-suited cards."""
    from collections import Counter
    suit_counts = Counter(c.suit for c in hole_cards)
    pairs = sum(1 for count in suit_counts.values() if count >= 2)
    return pairs >= 2


def is_single_suited(hole_cards: list[Card]) -> bool:
    """True if at least 2 hole cards share a suit."""
    from collections import Counter
    suit_counts = Counter(c.suit for c in hole_cards)
    return any(count >= 2 for count in suit_counts.values())


def is_high_rundown(hole_cards: list[Card]) -> bool:
    """
    True if hole cards form a high connected rundown (AKQJ, KQJT, etc.)
    Four consecutive ranks with top rank >= K.
    """
    ranks = sorted([c.rank_value() for c in hole_cards], reverse=True)
    if ranks[0] < 13:  # top must be K or A
        return False
    consecutive = all(ranks[i] - ranks[i+1] == 1 for i in range(3))
    return consecutive


def is_connected(hole_cards: list[Card], max_gap: int = 1) -> bool:
    """True if hole cards are connected (max gap between consecutive ranks <= max_gap)."""
    ranks = sorted([c.rank_value() for c in hole_cards], reverse=True)
    max_actual_gap = max(ranks[i] - ranks[i+1] for i in range(len(ranks)-1)) if len(ranks) > 1 else 99
    return max_actual_gap <= max_gap


def has_pair(hole_cards: list[Card], min_rank: str = "2") -> bool:
    """True if hole cards contain a pair of min_rank or better."""
    from collections import Counter
    min_val = {"A": 14, "K": 13, "Q": 12, "J": 11, "T": 10}.get(min_rank, int(min_rank) if min_rank.isdigit() else 2)
    rank_counts = Counter(c.rank for c in hole_cards)
    for rank, count in rank_counts.items():
        if count >= 2:
            rv = {"A": 14, "K": 13, "Q": 12, "J": 11, "T": 10}.get(rank, int(rank) if rank.isdigit() else 2)
            if rv >= min_val:
                return True
    return False


def count_aces(hole_cards: list[Card]) -> int:
    return sum(1 for c in hole_cards if c.rank == "A")


def has_nut_flush_draw(hole_cards: list[Card], community_cards: list[Card]) -> bool:
    """True if we hold the nut flush draw (Ace of the flush suit + another card of same suit)."""
    from collections import Counter
    all_cards = hole_cards + community_cards
    suit_counts = Counter(c.suit for c in all_cards)

    for suit, count in suit_counts.items():
        if count == 4:  # flush draw (one more needed)
            # We need exactly 2 hole cards of this suit (PLO rule)
            hole_suited = [c for c in hole_cards if c.suit == suit]
            if len(hole_suited) == 2:
                # Check if we have the ace of that suit
                if any(c.rank == "A" for c in hole_suited):
                    return True
    return False


def has_nut_straight_draw(hole_cards: list[Card], community_cards: list[Card]) -> bool:
    """True if we have an open-ended straight draw using 2 of our hole cards."""
    if len(community_cards) < 3:
        return False

    # Enumerate hole pairs and check for OESD with board
    for hole_pair in combinations(hole_cards, 2):
        for board_triple in combinations(community_cards, 3):
            all_five = list(hole_pair) + list(board_triple)
            ranks = sorted(set(c.rank_value() for c in all_five), reverse=True)
            # Check for 4-card straight (OESD)
            for i in range(len(ranks) - 3):
                window = ranks[i:i+4]
                if window[0] - window[-1] == 3:  # 4 consecutive
                    return True
    return False


def is_top_two_pair(best: PLOHandResult, community_cards: list[Card]) -> bool:
    """True if the two pair uses the two highest community card ranks."""
    if not community_cards or best.rank_class != TWO_PAIR:
        return False
    board_ranks = sorted([c.rank_value() for c in community_cards], reverse=True)
    if len(board_ranks) < 2:
        return False
    top_two_board = set(board_ranks[:2])
    hole_ranks = set(c.rank_value() for c in best.best_hole)
    return bool(hole_ranks & top_two_board)


# ── PLO decision logic ───────────────────────────────────────────────────

def plo_decision(
    hole_cards: list[Card],
    community_cards: list[Card],
    stack_bb: float,
    position: str,
    check_available: bool = False,
) -> str:
    """
    PLO push/fold decision. Returns 'SHOVE', 'FOLD', or 'CHECK'.
    """
    street = len(community_cards)

    if street == 0:
        return _plo_preflop(hole_cards, stack_bb, position)
    else:
        return _plo_postflop(hole_cards, community_cards, check_available)


def _plo_preflop(hole_cards: list[Card], stack_bb: float, position: str) -> str:
    if len(hole_cards) < 4:
        return "FOLD"

    # Double-suited premium rundowns: always shove
    if is_double_suited(hole_cards) and is_high_rundown(hole_cards):
        return "SHOVE"

    # Double-suited with high pair
    if is_double_suited(hole_cards) and has_pair(hole_cards, min_rank="T"):
        return "SHOVE"

    # Stack-based desperation
    if stack_bb <= 8:
        return "SHOVE"

    # Any AA-containing hand
    if count_aces(hole_cards) == 2:
        return "SHOVE"

    # Connected suited in late position
    if position_is_late(position):
        if is_connected(hole_cards, max_gap=1) and is_single_suited(hole_cards):
            return "SHOVE"

    return "FOLD"


def _plo_postflop(
    hole_cards: list[Card],
    community_cards: list[Card],
    check_available: bool,
) -> str:
    best = evaluate_plo_best_hand(hole_cards, community_cards)
    if best is None:
        return "CHECK" if check_available else "FOLD"

    # Strong made hand
    if best.rank_class <= SET:
        return "SHOVE"

    # Top two pair
    if best.rank_class == TWO_PAIR and is_top_two_pair(best, community_cards):
        return "SHOVE"

    # Nut flush draw
    if has_nut_flush_draw(hole_cards, community_cards):
        return "SHOVE"

    # Nut straight draw
    if has_nut_straight_draw(hole_cards, community_cards):
        return "SHOVE"

    if check_available:
        return "CHECK"

    return "FOLD"
