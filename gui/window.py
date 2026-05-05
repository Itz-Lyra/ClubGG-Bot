"""
Main application window (Section 09).
Two-panel layout: settings (left) + log (right).
Bot runs on background thread; GUI communicates via Qt signals/log_queue.
"""
from __future__ import annotations

import os
import sys
import queue
import logging
from datetime import timezone
from typing import Optional

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QSplitter, QStatusBar, QApplication
)
from PyQt6.QtCore import Qt, QTimer, pyqtSlot, QObject, pyqtSignal
from PyQt6.QtGui import QFont

from .settings_panel import SettingsPanel
from .log_panel import LogPanel
from bot.config import BotConfig, save_config
from bot.controller import BotController

log = logging.getLogger(__name__)

VERSION = "v1.1.0"


class LogForwarder(QObject):
    """Bridges bot-thread log_queue to GUI-thread signals."""
    entry_received = pyqtSignal(str, str)  # message, level

    def __init__(self):
        super().__init__()
        self.log_queue: queue.Queue = queue.Queue()


class MainWindow(QMainWindow):
    def __init__(self, config: BotConfig):
        super().__init__()
        self.config = config
        self._controller: Optional[BotController] = None
        self._log_forwarder = LogForwarder()
        self._rules_window = None

        self._setup_window()
        self._setup_ui()
        self._setup_timers()
        self._apply_stylesheet()

        # Forward queued log messages to GUI
        self._log_forwarder.entry_received.connect(self._on_log_entry)

    # ── Window setup ─────────────────────────────────────────────────

    def _setup_window(self):
        self.setWindowTitle(f"ClubGG Bot  {VERSION}")
        self.setMinimumSize(760, 560)
        self.resize(820, 620)

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Title bar ────────────────────────────────────────────────
        title_bar = QWidget()
        title_bar.setStyleSheet("background-color: #181825; border-bottom: 1px solid #313244;")
        title_bar.setFixedHeight(40)
        tb_layout = QHBoxLayout(title_bar)
        tb_layout.setContentsMargins(14, 0, 14, 0)

        title_lbl = QLabel("CLUBGG BOT")
        title_lbl.setObjectName("titleLabel")
        tb_layout.addWidget(title_lbl)
        tb_layout.addStretch()

        ver_lbl = QLabel(VERSION)
        ver_lbl.setObjectName("versionLabel")
        tb_layout.addWidget(ver_lbl)

        main_layout.addWidget(title_bar)

        # ── Panels ───────────────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet("QSplitter::handle { background-color: #313244; }")

        self._settings_panel = SettingsPanel(config=self.config)
        self._settings_panel.start_requested.connect(self._start_bot)
        self._settings_panel.stop_requested.connect(self._stop_bot)
        self._settings_panel.rules_requested.connect(self._open_rules)
        self._settings_panel.settings_changed.connect(self._save_settings)
        splitter.addWidget(self._settings_panel)

        self._log_panel = LogPanel()
        splitter.addWidget(self._log_panel)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        main_layout.addWidget(splitter, stretch=1)

        # ── Status bar ───────────────────────────────────────────────
        self.statusBar().showMessage(f"ClubGG Bot {VERSION}  ·  Ready")

    def _apply_stylesheet(self):
        qss_path = os.path.join(os.path.dirname(__file__), "styles.qss")
        if os.path.exists(qss_path):
            with open(qss_path) as f:
                self.setStyleSheet(f.read())

    def _setup_timers(self):
        # Poll log_queue every 50ms (20 fps — fast enough to feel live)
        self._log_timer = QTimer(self)
        self._log_timer.timeout.connect(self._drain_log_queue)
        self._log_timer.start(50)

        # Update session timer every second
        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._update_stats_display)
        self._stats_timer.start(1000)

    # ── Bot lifecycle ────────────────────────────────────────────────

    @pyqtSlot()
    def _start_bot(self):
        if self._controller and self._controller.is_running:
            return

        self._save_settings()
        self._controller = BotController(
            config=self.config,
            log_queue=self._log_forwarder.log_queue,
            stats_update_cb=lambda: None,  # stats drawn by _stats_timer
        )
        self._controller.start()
        self._settings_panel.set_running(True)
        self._settings_panel.set_connected(True, f"{self.config.waydroid_adb_host}:{self.config.waydroid_adb_port}")
        self.statusBar().showMessage("Bot running...")
        self._log_panel.append_entry("Bot started", "success")

    @pyqtSlot()
    def _stop_bot(self):
        if self._controller:
            self._controller.stop()
        self._settings_panel.set_running(False)
        self.statusBar().showMessage("Bot stopping...")
        self._log_panel.append_entry("Stop requested — finishing current action...", "warning")

    # ── Rules window ─────────────────────────────────────────────────

    @pyqtSlot()
    def _open_rules(self):
        from .rules_window import FilterWindow
        if self._rules_window is None or not self._rules_window.isVisible():
            self._rules_window = FilterWindow(config=self.config, parent=self)
            self._rules_window.saved.connect(
                lambda: self._log_panel.append_entry("Rules saved — takes effect on next scan", "info")
            )
        self._rules_window.show()
        self._rules_window.raise_()

    # ── Settings persistence ──────────────────────────────────────────

    @pyqtSlot()
    def _save_settings(self):
        save_config(self.config)

    # ── Log queue drain ───────────────────────────────────────────────

    @pyqtSlot()
    def _drain_log_queue(self):
        q = self._log_forwarder.log_queue
        count = 0
        while count < 50:  # drain up to 50 entries per tick
            try:
                entry = q.get_nowait()
                msg = entry.get("message", "")
                level = entry.get("level", "info")
                self._log_panel.append_entry(msg, level)
                count += 1
            except queue.Empty:
                break

        # Update connected status if controller changed state
        if self._controller:
            if not self._controller.is_running:
                self._settings_panel.set_running(False)
                self.statusBar().showMessage("Bot stopped")

    @pyqtSlot(str, str)
    def _on_log_entry(self, message: str, level: str):
        self._log_panel.append_entry(message, level)

    # ── Stats display update ──────────────────────────────────────────

    @pyqtSlot()
    def _update_stats_display(self):
        if self._controller is None:
            return
        self._settings_panel.update_stats(
            session_time=self._controller.session_elapsed()
        )

    def closeEvent(self, event):
        if self._controller and self._controller.is_running:
            self._controller.stop()
        self._save_settings()
        event.accept()
