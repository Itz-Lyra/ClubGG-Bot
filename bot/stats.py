"""
Stats persistence (Addendum 3, C4).
Each bot instance writes to stats_{bot_id}.json next to the binary.
Uses atomic writes (write temp → rename) to prevent corruption.
"""
from __future__ import annotations

import json
import os
import sys
import logging
import threading
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional

log = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _stats_path(bot_id: str, stats_dir: str) -> str:
    if stats_dir == "./":
        if getattr(sys, "frozen", False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.getcwd()
        return os.path.join(base, f"stats_{bot_id}.json")
    os.makedirs(stats_dir, exist_ok=True)
    return os.path.join(stats_dir, f"stats_{bot_id}.json")


@dataclass
class WinRecord:
    tournament: str
    ticket_type: str   # stage1 | stage2 | final
    rank: str          # "12/118"
    timestamp: str = field(default_factory=_utc_now)


@dataclass
class SessionStats:
    session_id: str
    start: str
    end: Optional[str] = None
    duration_seconds: int = 0
    tournaments_entered: int = 0
    hands_played: int = 0
    tickets_won: dict = field(default_factory=lambda: {"stage1": 0, "stage2": 0, "final": 0})
    busts: int = 0
    wins: list[WinRecord] = field(default_factory=list)


@dataclass
class AllTimeStats:
    sessions: int = 0
    total_runtime_seconds: int = 0
    tournaments_entered: int = 0
    hands_played: int = 0
    tickets: dict = field(default_factory=lambda: {"stage1": 0, "stage2": 0, "final": 0})
    busts: int = 0


class StatsManager:
    """
    Manages per-bot stats. Thread-safe. Writes on events, not continuously.
    """

    def __init__(self, bot_id: str, stats_dir: str = "./"):
        self.bot_id = bot_id
        self._path = _stats_path(bot_id, stats_dir)
        self._lock = threading.Lock()
        self._all_time = AllTimeStats()
        self._sessions: list[SessionStats] = []
        self._current_session: Optional[SessionStats] = None
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path) as f:
                data = json.load(f)
            at = data.get("all_time", {})
            self._all_time = AllTimeStats(
                sessions=at.get("sessions", 0),
                total_runtime_seconds=at.get("total_runtime_seconds", 0),
                tournaments_entered=at.get("tournaments_entered", 0),
                hands_played=at.get("hands_played", 0),
                tickets=at.get("tickets", {"stage1": 0, "stage2": 0, "final": 0}),
                busts=at.get("busts", 0),
            )
            self._sessions = data.get("sessions", [])
        except Exception as exc:
            log.error("Failed to load stats: %s", exc)

    def _save(self) -> None:
        """Atomic write: temp file → rename."""
        data = {
            "bot_id": self.bot_id,
            "created_at": _utc_now(),
            "last_updated": _utc_now(),
            "all_time": asdict(self._all_time),
            "sessions": self._sessions,
        }
        tmp = self._path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2, default=_json_default)
            os.replace(tmp, self._path)
        except OSError as exc:
            log.error("Failed to save stats: %s", exc)
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def start_session(self) -> None:
        with self._lock:
            sid = f"s_{datetime.now().strftime('%Y%m%d_%H%M')}"
            self._current_session = SessionStats(
                session_id=sid,
                start=_utc_now(),
            )
            self._all_time.sessions += 1
            self._save()

    def end_session(self, reason: str = "normal") -> None:
        with self._lock:
            if self._current_session is None:
                return
            now = datetime.now(timezone.utc)
            start = datetime.fromisoformat(self._current_session.start.replace("Z", "+00:00"))
            duration = int((now - start).total_seconds())
            self._current_session.end = _utc_now()
            self._current_session.duration_seconds = duration
            self._all_time.total_runtime_seconds += duration
            self._sessions.append(asdict(self._current_session))
            # Keep last 100 sessions
            if len(self._sessions) > 100:
                self._sessions = self._sessions[-100:]
            self._current_session = None
            self._save()

    def record_ticket_win(self, tournament: str, ticket_type: str, rank: str) -> None:
        with self._lock:
            if self._current_session:
                self._current_session.tickets_won[ticket_type] = \
                    self._current_session.tickets_won.get(ticket_type, 0) + 1
                win = WinRecord(tournament=tournament, ticket_type=ticket_type, rank=rank)
                self._current_session.wins.append(win)
            self._all_time.tickets[ticket_type] = \
                self._all_time.tickets.get(ticket_type, 0) + 1
            self._save()

    def record_bust(self) -> None:
        with self._lock:
            if self._current_session:
                self._current_session.busts += 1
            self._all_time.busts += 1
            self._save()

    def record_hand_played(self) -> None:
        with self._lock:
            if self._current_session:
                self._current_session.hands_played += 1
            self._all_time.hands_played += 1
            # Save every 10 hands to reduce I/O
            if self._all_time.hands_played % 10 == 0:
                self._save()

    def record_tournament_entered(self) -> None:
        with self._lock:
            if self._current_session:
                self._current_session.tournaments_entered += 1
            self._all_time.tournaments_entered += 1
            self._save()

    def session_tickets_won(self) -> int:
        with self._lock:
            if not self._current_session:
                return 0
            t = self._current_session.tickets_won
            return t.get("stage1", 0) + t.get("stage2", 0) + t.get("final", 0)

    def session_hands(self) -> int:
        with self._lock:
            return self._current_session.hands_played if self._current_session else 0

    def session_tournaments(self) -> int:
        with self._lock:
            return self._current_session.tournaments_entered if self._current_session else 0

    def session_start_time(self) -> Optional[datetime]:
        with self._lock:
            if not self._current_session:
                return None
            return datetime.fromisoformat(self._current_session.start.replace("Z", "+00:00"))


def _json_default(obj):
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)
