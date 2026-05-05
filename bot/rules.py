"""
Tournament filter — decides whether to register for a tournament.
Play mode is always shove. Only question is register or skip.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from .config import BotConfig

log = logging.getLogger(__name__)


@dataclass
class TournamentRule:
    action: str    # "REGISTER" | "SKIP"
    play_mode: str # always "shove"
    reason: str


def classify_tournament_type(name: str, current_tab: str = "stage1") -> str:
    n = name.lower()
    if "daily freeroll" in n:
        return "daily_freeroll"
    if "flip out" in n:
        return "platinum_flip_out"
    if "hyper turbo" in n:
        return "hyper_turbo_plo" if "plo" in n else "hyper_turbo_nlh"
    if "double stack" in n:
        return "double_stack_plo" if "plo" in n else "double_stack_nlh"
    if "target stack" in n:
        return "hyper_turbo_plo" if "plo" in n else "hyper_turbo_nlh"
    return "stage1_unknown"


def resolve_tournament_rule(name: str, type_key: str, config: BotConfig) -> TournamentRule:
    rule = config.type_rules.get(type_key, {"register": True})
    if not rule.get("register", True):
        return TournamentRule(action="SKIP", play_mode="shove", reason=f"{type_key} disabled in filter")
    return TournamentRule(action="REGISTER", play_mode="shove", reason="enabled")
