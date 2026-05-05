"""
Stage 1 lobby scanning — registers for all available green-badge tournaments.
Stage 2 and Final Stage support removed — Stage 1 only.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from ..vision import (
    State, REGIONS, detect_tournament_cards, detect_badge_color,
    detect_me_badge, screen_hash, NAV_TABS,
)
from ..ocr import ocr_tournament_name
from ..rules import classify_tournament_type, resolve_tournament_rule
from ..vision import detect_eligible_text

log = logging.getLogger(__name__)


def navigate_to_tab(adb, tab: str) -> None:
    coords = NAV_TABS.get(tab, NAV_TABS["stage1"])
    adb.tap(coords[0], coords[1])
    time.sleep(1.5)


def scan_stage1_tab(
    adb,
    vision_detector,
    config,
    stats,
    log_queue,
    stop_flag,
    active_table_rules: dict,
    screen_w: int,
    screen_h: int,
) -> list[str]:
    """
    Scan Stage 1 tab and register for every available green-badge tournament.
    Loops until no more green unregistered cards are found on three consecutive
    checks (new cards can appear after each registration).
    """
    from ..states.registration import register_for_tournament

    registered: list[str] = []
    consecutive_empty = 0
    MAX_EMPTY = 3

    while not stop_flag():
        screen = adb.screenshot()
        state  = vision_detector.detect(screen)

        # Handle Go-to-Table popup — just wait, don't tap
        if state == State.GO_TO_TABLE_POPUP:
            _log(log_queue, "⏳ Table starting — waiting", "info")
            time.sleep(2.0)
            continue

        # If we drifted off Stage 1 unexpectedly, navigate back
        if state not in (State.LOBBY_STAGE1, State.LOBBY_STAGE2,
                         State.LOBBY_FINAL, State.UNKNOWN):
            log.debug("scan_stage1_tab: unexpected state %s — pressing back", state.name)
            adb.press_back()
            time.sleep(1.0)
            continue

        cards = detect_tournament_cards(
            screen, screen_w, screen_h, adb.app_left, adb.app_right
        )
        app = screen[:, adb.app_left:adb.app_right]
        target = None

        for card_info in cards:
            card_img = card_info["region"].crop(app, app.shape[1], app.shape[0])
            if card_img.size == 0:
                continue

            from ..adb import Region
            badge_crop  = Region(0, 2, 25, 40).crop(card_img, card_img.shape[1], card_img.shape[0])
            badge_color = detect_badge_color(badge_crop)

            if badge_color != "green":
                continue
            if detect_me_badge(card_img):
                continue

            eligible_text = detect_eligible_text(card_img).lower()
            if "invitational" in eligible_text:
                continue
            if "platinum" in eligible_text and config._membership_tier:
                if "platinum" not in config._membership_tier.lower():
                    continue

            config._current_scan_tab = "stage1"
            tournament_name = ocr_tournament_name(card_img) or "Unknown Tournament"
            t_type = classify_tournament_type(tournament_name, "stage1")
            rule   = resolve_tournament_rule(tournament_name, t_type, config)

            if rule.action == "SKIP":
                _log(log_queue, f"↷ SKIP: {tournament_name} — {rule.reason}", "skip")
                continue

            target = (card_info["region"], tournament_name, rule)
            break

        if target is None:
            consecutive_empty += 1
            if consecutive_empty >= MAX_EMPTY:
                break
            time.sleep(1.0)
            continue

        consecutive_empty = 0
        card_region, tournament_name, rule = target
        _log(log_queue, f"Registering: {tournament_name}", "info")

        success = register_for_tournament(
            adb, vision_detector, card_region, tournament_name, screen_w, screen_h
        )
        if success:
            registered.append(tournament_name)
            active_table_rules[tournament_name] = rule.play_mode
            if stats:
                stats.record_tournament_entered()
            _log(log_queue, f"✓ Registered: {tournament_name}", "success")
        else:
            _log(log_queue, f"✗ Failed: {tournament_name}", "error")
            adb.press_back()
            time.sleep(1.0)

    return registered


def run_full_scan(
    adb,
    vision_detector,
    config,
    stats,
    log_queue,
    stop_flag,
    active_table_rules: dict,
    screen_w: int,
    screen_h: int,
    ticket_inventory: dict,
) -> None:
    """
    Stage 1 only scan. Stage 2 and Final are no longer supported.
    """
    if config.stage1_enabled and not stop_flag():
        _log(log_queue, "Scanning Stage 1...", "info")
        config._current_scan_tab = "stage1"
        navigate_to_tab(adb, "stage1")
        time.sleep(1.0)
        found = scan_stage1_tab(
            adb, vision_detector, config, stats, log_queue, stop_flag,
            active_table_rules, screen_w, screen_h,
        )
        _log(log_queue, f"Stage 1 scan done — registered: {len(found)}", "info")

    _log(log_queue, "Scan complete. Watching...", "info")


def _log(log_queue, message: str, level: str = "info") -> None:
    if log_queue is not None:
        try:
            log_queue.put_nowait({"message": message, "level": level})
        except Exception:
            pass
    getattr(log, level if level in ("error", "warning") else "info")(message)
