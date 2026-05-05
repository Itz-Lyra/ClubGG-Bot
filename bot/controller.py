"""
Bot controller — Stage 1 only, no Discord, no stats tracking.
"""
from __future__ import annotations

import logging
import threading
import time
import queue
import os
from datetime import datetime, timezone
from typing import Optional, Callable

from .adb import ADBClient, ADBError
from .config import BotConfig, save_config
from .vision import StateDetector, State
from .ocr import configure_tesseract
from .states.lobby import run_full_scan, navigate_to_tab, _log
from .states.table import run_table_polling_loop

log = logging.getLogger(__name__)

MAX_UNKNOWN_RETRIES = 5


class BotController:
    def __init__(self, config: BotConfig, log_queue: queue.Queue, stats_update_cb: Callable):
        self.config          = config
        self.log_queue       = log_queue
        self.stats_update_cb = stats_update_cb

        self._stop_requested = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running = False

        self.adb = ADBClient(
            host=config.waydroid_adb_host,
            port=config.waydroid_adb_port,
            tap_delay_ms=config.adb_tap_delay_ms,
            swipe_duration_ms=config.swipe_duration_ms,
        )

        self.detector: Optional[StateDetector] = None
        self.active_table_rules: dict[str, str] = {}
        self.ticket_inventory: dict = {}
        self._unknown_count = 0
        self._session_start: Optional[datetime] = None

        config._membership_tier  = ""
        config._current_scan_tab = "stage1"
        os.makedirs(config.debug_screenshot_dir, exist_ok=True)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def active_table_count(self) -> int:
        return getattr(self, "_active_table_count", 0)

    def session_elapsed(self) -> str:
        if not self._session_start:
            return "00:00:00"
        secs = int((datetime.now(timezone.utc) - self._session_start).total_seconds())
        return f"{secs//3600:02d}:{(secs%3600)//60:02d}:{secs%60:02d}"

    def start(self) -> None:
        if self._running:
            return
        self._stop_requested.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="BotController")
        self._thread.start()

    def stop(self) -> None:
        self._stop_requested.set()

    def _stop_flag(self) -> bool:
        return self._stop_requested.is_set()

    def _log(self, message: str, level: str = "info") -> None:
        _log(self.log_queue, message, level)

    def _run_loop(self) -> None:
        self._running = True
        try:
            self._startup()
            self._main_loop()
        except ADBError as exc:
            self._log(f"✗ ADB fatal: {exc}", "error")
        except Exception as exc:
            import traceback
            for line in traceback.format_exc().splitlines():
                if line.strip():
                    self._log(f"✗ {line}", "error")
        finally:
            self._running = False
            self._log("Bot stopped", "info")

    def _startup(self) -> None:
        configure_tesseract()
        self._log("Connecting to ADB...", "info")

        if not self.adb.connect():
            raise ADBError(
                f"Could not connect to ADB at "
                f"{self.config.waydroid_adb_host}:{self.config.waydroid_adb_port}"
            )

        self.detector = StateDetector(
            self.adb.screen_w, self.adb.screen_h,
            app_left=self.adb.app_left, app_right=self.adb.app_right,
        )
        self._log(
            f"Connected — {self.adb.screen_w}×{self.adb.screen_h} | "
            f"App: x={self.adb.app_left}-{self.adb.app_right} ({self.adb.app_w}px)",
            "info",
        )

        navigate_to_tab(self.adb, "stage1")
        time.sleep(1.0)
        self._session_start = datetime.now(timezone.utc)
        self._log("Bot started — Stage 1 mode", "info")

    def _main_loop(self) -> None:
        self._log("Main loop started", "info")

        while not self._stop_flag():
            if not self.adb.is_connected():
                self._log("ADB disconnected — reconnecting...", "warning")
                if not self.adb.connect():
                    self._log("✗ ADB reconnect failed", "error")
                    for _ in range(30):
                        if self._stop_flag(): return
                        time.sleep(1.0)
                    continue

            try:
                screen = self.adb.screenshot()
            except ADBError as exc:
                self._log(f"Screenshot failed: {exc}", "error")
                time.sleep(2.0)
                continue

            state = self.detector.detect(screen)

            if state == State.LOGGED_OUT:
                self._log("⚠️ Logged out — bot stopping. Re-login then restart.", "error")
                self._stop_requested.set()
                return

            if state == State.LOBBY_LIVE_EVENT:
                navigate_to_tab(self.adb, "stage1")
                continue

            if state == State.GAME_SETTINGS:
                self.adb.press_back()
                time.sleep(1.0)
                continue

            if state == State.ME_PAGE:
                self._log("Me page — pressing back", "info")
                self.adb.press_back()
                time.sleep(0.8)
                self.adb.press_back()
                time.sleep(0.8)
                navigate_to_tab(self.adb, "stage1")
                time.sleep(1.0)
                continue

            if state == State.GO_TO_TABLE_POPUP:
                time.sleep(2.0)
                continue

            if state == State.UNKNOWN:
                self._unknown_count += 1
                self._log(f"UNKNOWN state ({self._unknown_count}/{MAX_UNKNOWN_RETRIES})", "warning")
                if self._unknown_count >= MAX_UNKNOWN_RETRIES:
                    navigate_to_tab(self.adb, "stage1")
                    self._unknown_count = 0
                else:
                    self.adb.press_back()
                    time.sleep(1.0)
                continue

            self._unknown_count = 0

            _TABLE_STATES = (
                State.IN_HAND_ACTION, State.IN_HAND_WAITING,
                State.BREAK_SCREEN, State.PRE_GAME_WAIT,
                State.TARGET_STACK, State.WIN_SCREEN, State.BUST_OUT_SCREEN,
            )
            _LOBBY_STATES = (
                State.LOBBY_STAGE1, State.LOBBY_STAGE2, State.LOBBY_FINAL,
                State.ME_PAGE, State.GAME_SETTINGS, State.TOURNAMENT_DETAIL,
                State.REGISTRATION_MODAL, State.SUCCESS_MODAL,
            )

            if state in _LOBBY_STATES:
                active_tabs = []
            else:
                from .vision import detect_active_tables
                active_tabs = detect_active_tables(
                    screen, self.adb.screen_w, self.adb.screen_h,
                    self.adb.app_left, self.adb.app_right,
                )
            self._active_table_count = len(active_tabs)

            if active_tabs or state in _TABLE_STATES:
                self._enter_table_loop()
                if self._stop_flag(): return
                self.adb.press_back()
                time.sleep(0.5)
                navigate_to_tab(self.adb, "stage1")
                time.sleep(1.2)

            self._log("Starting Stage 1 scan", "info")
            run_full_scan(
                adb=self.adb,
                vision_detector=self.detector,
                config=self.config,
                stats=None,
                log_queue=self.log_queue,
                stop_flag=self._stop_flag,
                active_table_rules=self.active_table_rules,
                screen_w=self.adb.screen_w,
                screen_h=self.adb.screen_h,
                ticket_inventory={},
            )

            if self._stop_flag(): return

            self._log(f"Watch mode — next scan in {self.config.lobby_scan_idle_s}s", "info")
            for _ in range(self.config.lobby_scan_idle_s):
                if self._stop_flag(): return
                try:
                    screen = self.adb.screenshot()
                    idle_state = self.detector.detect(screen)
                    if idle_state == State.LOGGED_OUT:
                        self._log("⚠️ Logged out — stopping", "error")
                        self._stop_requested.set()
                        return
                    if idle_state in (
                        State.IN_HAND_ACTION, State.IN_HAND_WAITING,
                        State.WIN_SCREEN, State.BUST_OUT_SCREEN,
                        State.BREAK_SCREEN, State.PRE_GAME_WAIT,
                    ):
                        break
                except ADBError:
                    pass
                time.sleep(1.0)

    def _enter_table_loop(self) -> None:
        self._log("Entering table play loop", "info")
        run_table_polling_loop(
            adb=self.adb,
            vision_detector=self.detector,
            config=self.config,
            stats=None,
            discord=None,
            log_queue=self.log_queue,
            stop_flag=self._stop_flag,
            active_table_rules=self.active_table_rules,
            screen_w=self.adb.screen_w,
            screen_h=self.adb.screen_h,
            on_all_tables_done=lambda: self._log("All tables done — resuming scan", "info"),
        )
