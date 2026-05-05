"""
Popup handling.

Go-to-Table popup: do NOT tap any button. The game auto-routes you to the
table after the countdown. Tapping "Go to Table" followed by state-detection
uncertainty caused the leave-table popup loop. Simply wait and let the game
take us when it's ready.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)


def wait_for_table_auto_redirect(adb, vision_detector, log_queue, timeout_s: int = 90) -> bool:
    """
    Called when GO_TO_TABLE_POPUP is detected.
    Waits for the game to auto-redirect without tapping anything.
    Returns True once a table state is detected, False on timeout.
    """
    from ..vision import State

    _log(log_queue, "⏳ Table starting — waiting for auto-redirect", "info")
    deadline = time.time() + timeout_s

    while time.time() < deadline:
        time.sleep(2.0)
        try:
            screen = adb.screenshot()
        except Exception:
            continue

        state = vision_detector.detect(screen)

        if state in (
            State.IN_HAND_WAITING, State.IN_HAND_ACTION,
            State.PRE_GAME_WAIT, State.BREAK_SCREEN, State.TARGET_STACK,
        ):
            _log(log_queue, "✓ Arrived at table", "info")
            return True

        # Result screens can appear immediately on arrival
        if state in (State.WIN_SCREEN, State.BUST_OUT_SCREEN):
            return True

        # Still in popup/lobby — keep waiting
        # Unexpected non-lobby state → let caller handle
        if state not in (
            State.GO_TO_TABLE_POPUP, State.LOBBY_STAGE1, State.LOBBY_STAGE2,
            State.LOBBY_FINAL, State.UNKNOWN, State.SUCCESS_MODAL,
        ):
            return False

    _log(log_queue, "⚠️ Go-to-Table timeout — returning to lobby", "warning")
    return False


def _log(log_queue, message: str, level: str = "info") -> None:
    if log_queue is not None:
        try:
            log_queue.put_nowait({"message": message, "level": level})
        except Exception:
            pass
    getattr(log, level if level in ("error", "warning") else "info")(message)
