"""
Discord webhook notifications (Addendum 3, C3).
All sends happen in daemon threads — never blocks the bot loop.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

log = logging.getLogger(__name__)

# Discord embed colors
COLOR_GREEN  = 0x86EFAC
COLOR_YELLOW = 0xFDE68A
COLOR_RED    = 0xFCA5A5
COLOR_BLUE   = 0x93C5FD


def _send(webhook_url: str, payload: dict) -> None:
    """Fire-and-forget webhook POST. Errors are logged, never raised."""
    try:
        import requests
        resp = requests.post(webhook_url, json=payload, timeout=5)
        if resp.status_code not in (200, 204):
            log.warning("Discord webhook HTTP %d: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        log.debug("Discord webhook error: %s", exc)


def _fire(webhook_url: Optional[str], payload: dict) -> None:
    """Send webhook in a daemon thread. No-op if url is empty."""
    if not webhook_url:
        return
    t = threading.Thread(target=_send, args=(webhook_url, payload), daemon=True)
    t.start()


def _embed(title: str, color: int, fields: list[dict], description: str = "") -> dict:
    from datetime import datetime, timezone
    embed: dict = {
        "title": title,
        "color": color,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if description:
        embed["description"] = description
    if fields:
        embed["fields"] = fields
    return embed


def notify_session_start(
    webhook_url: Optional[str],
    bot_id: str,
    play_mode: str,
    stages: str,
    final_tickets: int,
    stage2_tickets: int,
) -> None:
    embed = _embed(
        title=f"🟢 {bot_id} Online",
        color=COLOR_GREEN,
        fields=[
            {"name": "Mode",    "value": play_mode.capitalize(), "inline": True},
            {"name": "Stages",  "value": stages,                  "inline": True},
            {"name": "Tickets", "value": f"Final:{final_tickets} S2:{stage2_tickets}", "inline": True},
        ],
    )
    _fire(webhook_url, {"embeds": [embed]})


def notify_ticket_win(
    webhook_url: Optional[str],
    bot_id: str,
    tournament: str,
    prize: str,
    rank: str,
    play_time: str,
    session_wins: int,
    all_time_wins: int,
) -> None:
    embed = _embed(
        title=f"🎫 {bot_id} — TICKET WIN",
        color=COLOR_YELLOW,
        fields=[
            {"name": "Tournament",    "value": tournament,         "inline": True},
            {"name": "Prize",         "value": prize,              "inline": True},
            {"name": "Rank",          "value": rank,               "inline": True},
            {"name": "Play Time",     "value": play_time,          "inline": True},
            {"name": "Session Wins",  "value": str(session_wins),  "inline": True},
            {"name": "All-Time",      "value": str(all_time_wins), "inline": True},
        ],
    )
    _fire(webhook_url, {"embeds": [embed]})


def notify_bust(
    webhook_url: Optional[str],
    bot_id: str,
    tournament: str,
    rank: str,
    play_time: str,
) -> None:
    embed = _embed(
        title=f"💀 {bot_id} — Bust",
        color=COLOR_RED,
        fields=[
            {"name": "Tournament", "value": tournament, "inline": True},
            {"name": "Rank",       "value": rank,       "inline": True},
            {"name": "Play Time",  "value": play_time,  "inline": True},
        ],
    )
    _fire(webhook_url, {"embeds": [embed]})


def notify_session_end(
    webhook_url: Optional[str],
    bot_id: str,
    duration: str,
    tickets_won: int,
    tournaments: int,
    hands: int,
    s1: int, s2: int, final: int,
) -> None:
    embed = _embed(
        title=f"🔴 {bot_id} Offline — Session Summary",
        color=COLOR_RED,
        fields=[
            {"name": "Duration",     "value": duration,        "inline": True},
            {"name": "Tickets Won",  "value": str(tickets_won),"inline": True},
            {"name": "Tournaments",  "value": str(tournaments),"inline": True},
            {"name": "Hands Played", "value": str(hands),      "inline": True},
            {"name": "S1 Tickets",   "value": str(s1),         "inline": True},
            {"name": "S2 Tickets",   "value": str(s2),         "inline": True},
            {"name": "Final Tickets","value": str(final),      "inline": True},
        ],
    )
    _fire(webhook_url, {"embeds": [embed]})


def notify_error(webhook_url: Optional[str], bot_id: str, message: str) -> None:
    embed = _embed(
        title=f"⚠️ {bot_id} — Error",
        color=COLOR_RED,
        description=message,
        fields=[],
    )
    _fire(webhook_url, {"embeds": [embed]})


def notify_low_tickets(webhook_url: Optional[str], bot_id: str, stage: str) -> None:
    embed = _embed(
        title=f"⚠️ {bot_id} — Tickets Exhausted",
        color=COLOR_YELLOW,
        description=f"{stage} tickets: 0. {stage} scanning disabled.",
        fields=[],
    )
    _fire(webhook_url, {"embeds": [embed]})


def notify_logged_out(webhook_url: Optional[str], bot_id: str, auto_relogin: bool) -> None:
    action = "Attempting auto re-login..." if auto_relogin else "Bot stopped. Manual re-login required."
    embed = _embed(
        title=f"🔐 {bot_id} — Logged Out",
        color=COLOR_RED,
        description=f"ClubGG session expired. {action}",
        fields=[],
    )
    _fire(webhook_url, {"embeds": [embed]})


def notify_relogin_success(webhook_url: Optional[str], bot_id: str) -> None:
    embed = _embed(
        title=f"✅ {bot_id} — Re-login Successful",
        color=COLOR_GREEN,
        description="Session restored. Resuming bot.",
        fields=[],
    )
    _fire(webhook_url, {"embeds": [embed]})
