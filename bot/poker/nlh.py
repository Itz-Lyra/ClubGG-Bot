"""
NLH hand evaluator and push/fold decision logic.
Uses treys for fast 5-7 card hand evaluation.
Smart mode is push/fold only — no calling, no partial raises.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from .cards import Card
from .position import position_is_late, position_is_blind, position_is_early

log = logging.getLogger(__name__)

# Hand rank constants (treys: lower = better, but we use our own labels)
# treys rank classes: 1=straight flush, 2=four of a kind ... 9=high card
STRAIGHT_FLUSH = 1
FOUR_OF_A_KIND = 2
FULL_HOUSE = 3
FLUSH = 4
STRAIGHT = 5
THREE_OF_A_KIND = 6
TWO_PAIR = 7
ONE_PAIR = 8
HIGH_CARD = 9

# Top pair with good kicker threshold
TOP_PAIR_GOOD_KICKER = 8  # pair + kicker J or better


@dataclass
class NLHHandResult:
    rank_class: int   # 1=SF ... 9=high card (treys class)
    score: int        # raw treys score (lower = better)
    description: str


def evaluate_nlh(hole_cards: list[Card], community_cards: list[Card]) -> Optional[NLHHandResult]:
    """Evaluate NLH hand using treys. Works for 2+3, 2+4, 2+5 card combos."""
    from treys import Evaluator, Card as TreysCard

    if len(hole_cards) < 2:
        return None

    try:
        evaluator = Evaluator()
        board = [c.to_treys() for c in community_cards]
        hand = [c.to_treys() for c in hole_cards]

        if len(board) == 0:
            # Preflop — can't evaluate made hand
            return NLHHandResult(rank_class=HIGH_CARD, score=9999, description="preflop")

        score = evaluator.evaluate(board, hand)
        rank_class = evaluator.get_rank_class(score)
        description = evaluator.class_to_string(rank_class)
        return NLHHandResult(rank_class=rank_class, score=score, description=description)
    except Exception as exc:
        log.debug("NLH evaluate error: %s", exc)
        return None


def count_outs(hole_cards: list[Card], community_cards: list[Card]) -> int:
    """
    Count approximate outs for draws (flush draw = 9, OESD = 8, gutshot = 4).
    This is a heuristic, not exhaustive enumeration.
    """
    if len(community_cards) < 3:
        return 0

    all_cards = hole_cards + community_cards
    outs = 0

    # Flush draw: 4 cards of same suit
    from collections import Counter
    suit_counts = Counter(c.suit for c in all_cards)
    max_suited = max(suit_counts.values()) if suit_counts else 0
    if max_suited == 4:
        outs += 9  # flush draw

    # Straight draws: check for 4-card straight (OESD = 8 outs, gutshot = 4)
    rank_values = sorted(set(c.rank_value() for c in all_cards))
    for i in range(len(rank_values) - 3):
        window = rank_values[i:i+4]
        span = window[-1] - window[0]
        if span == 3:
            outs += 8  # open-ended
        elif span == 4 and len(window) == 4:
            outs += 4  # gutshot

    return min(outs, 15)  # cap at reasonable number


def _sorted_ranks(hole_cards: list[Card]) -> tuple[str, str]:
    """Return (higher_rank, lower_rank) of hole cards by value."""
    sorted_cards = sorted(hole_cards, key=lambda c: c.rank_value(), reverse=True)
    return sorted_cards[0].rank, sorted_cards[1].rank


def nlh_decision(
    hole_cards: list[Card],
    community_cards: list[Card],
    stack_bb: float,
    position: str,
    pot: int,
    call_amount: int,
    my_rank: int,
    total_players: int,
    prizes_remaining: int,
    check_available: bool = False,
) -> str:
    """
    Evaluate NLH hand and return 'SHOVE', 'FOLD', or 'CHECK'.
    Smart mode — push/fold only except when check is free.
    """
    street = len(community_cards)  # 0=preflop, 3=flop, 4=turn, 5=river

    if street == 0:
        return _nlh_preflop(hole_cards, stack_bb, position)
    else:
        return _nlh_postflop(hole_cards, community_cards, stack_bb, position,
                             my_rank, total_players, prizes_remaining, check_available)


def _nlh_preflop(hole_cards: list[Card], stack_bb: float, position: str) -> str:
    if len(hole_cards) < 2:
        return "FOLD"

    suited = hole_cards[0].suit == hole_cards[1].suit
    hi, lo = _sorted_ranks(hole_cards)
    pair = (hi == lo)
    hand = (hi, lo)

    # Premium: always shove
    PREMIUM = {("A","A"), ("K","K"), ("Q","Q"), ("J","J"), ("A","K")}
    if frozenset(hand) in {frozenset(p) for p in PREMIUM} or (pair and hi in ("A","K","Q","J")):
        return "SHOVE"

    # Desperate stack: shove any 2
    if stack_bb <= 10:
        return "SHOVE"

    # Short stack
    if stack_bb <= 20:
        SHOVE_20 = {("T","T"), ("9","9"), ("8","8"), ("A","Q"), ("A","J"), ("K","Q")}
        if frozenset(hand) in {frozenset(h) for h in SHOVE_20}:
            return "SHOVE"
        SUITED_20 = {("A","T"), ("K","J"), ("Q","J")}
        if suited and frozenset(hand) in {frozenset(h) for h in SUITED_20}:
            return "SHOVE"
        return "FOLD"

    # Normal stack — position aware
    if position_is_late(position):
        SHOVE_LATE = {("T","T"), ("9","9"), ("8","8"), ("7","7"), ("A","Q"),
                      ("A","J"), ("A","T"), ("K","Q")}
        if frozenset(hand) in {frozenset(h) for h in SHOVE_LATE}:
            return "SHOVE"
        SUITED_LATE = {("K","J"), ("Q","J"), ("J","T"), ("T","9")}
        if suited and frozenset(hand) in {frozenset(h) for h in SUITED_LATE}:
            return "SHOVE"
        return "FOLD"

    if position_is_blind(position):
        SHOVE_BLIND = {("9","9"), ("8","8"), ("A","Q"), ("A","J")}
        if frozenset(hand) in {frozenset(h) for h in SHOVE_BLIND}:
            return "SHOVE"
        return "FOLD"

    # Early/middle position
    SHOVE_EARLY = {("T","T"), ("9","9"), ("A","Q")}
    if frozenset(hand) in {frozenset(h) for h in SHOVE_EARLY}:
        return "SHOVE"
    return "FOLD"


def _nlh_postflop(
    hole_cards: list[Card],
    community_cards: list[Card],
    stack_bb: float,
    position: str,
    my_rank: int,
    total_players: int,
    prizes_remaining: int,
    check_available: bool,
) -> str:
    result = evaluate_nlh(hole_cards, community_cards)
    if result is None:
        return "CHECK" if check_available else "FOLD"

    rc = result.rank_class
    street = len(community_cards)

    # Strong made hand: always shove
    if rc <= TWO_PAIR:
        return "SHOVE"

    # Top pair with good kicker — check kicker via score ranking
    # One pair with low score (low treys score = strong pair)
    if rc == ONE_PAIR:
        # Estimate kicker: if our best hole card is J or better paired, shove
        best_hole_rank = max(c.rank_value() for c in hole_cards)
        if best_hole_rank >= 11:  # J = 11
            return "SHOVE"

    # Strong draws on flop/turn
    if street <= 4:
        outs = count_outs(hole_cards, community_cards)
        if outs >= 8:
            return "SHOVE"

    # ICM bubble pressure
    if prizes_remaining > 0 and total_players > 0:
        bubble_threshold = prizes_remaining * 1.5
        if my_rank > 0 and my_rank <= bubble_threshold:
            if rc <= ONE_PAIR:
                return "SHOVE"

    # Free check: never fold for free
    if check_available:
        return "CHECK"

    return "FOLD"
