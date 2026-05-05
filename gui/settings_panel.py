"""
Settings panel — left side. Shove mode only. Minimal stats.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame,
)
from PyQt6.QtCore import pyqtSignal


def _sep() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    return line


class SettingsPanel(QWidget):
    start_requested  = pyqtSignal()
    stop_requested   = pyqtSignal()
    rules_requested  = pyqtSignal()
    settings_changed = pyqtSignal()

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.setObjectName("settingsPanel")
        self.setFixedWidth(200)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(7)

        # Connection status
        conn_row = QHBoxLayout()
        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet("color: #6c7086;")
        conn_row.addWidget(self._status_dot)
        self._status_label = QLabel("DISCONNECTED")
        self._status_label.setStyleSheet("color: #6c7086; font-size: 10px; font-weight: bold;")
        conn_row.addWidget(self._status_label)
        conn_row.addStretch()
        layout.addLayout(conn_row)

        self._device_label = QLabel("")
        self._device_label.setStyleSheet("color: #585b70; font-size: 9px;")
        layout.addWidget(self._device_label)

        layout.addWidget(_sep())

        mode_lbl = QLabel("MODE:  Shove All-In")
        mode_lbl.setStyleSheet("color: #cba6f7; font-size: 11px; font-weight: bold; padding: 2px 0;")
        layout.addWidget(mode_lbl)

        layout.addWidget(_sep())

        self._filter_btn = QPushButton("Filter →")
        self._filter_btn.setObjectName("rulesButton")
        self._filter_btn.clicked.connect(self.rules_requested)
        layout.addWidget(self._filter_btn)

        layout.addWidget(_sep())

        session_row = QHBoxLayout()
        lbl = QLabel("Session:")
        lbl.setStyleSheet("color: #6c7086; font-size: 10px;")
        session_row.addWidget(lbl)
        session_row.addStretch()
        self._stat_session = QLabel("00:00:00")
        self._stat_session.setObjectName("statValue")
        session_row.addWidget(self._stat_session)
        layout.addLayout(session_row)

        layout.addStretch()

        self._start_btn = QPushButton("START BOT")
        self._start_btn.setObjectName("startButton")
        self._start_btn.clicked.connect(self.start_requested)
        layout.addWidget(self._start_btn)

        self._stop_btn = QPushButton("STOP BOT")
        self._stop_btn.setObjectName("stopButton")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self.stop_requested)
        layout.addWidget(self._stop_btn)

        self._device_label.setText(
            f"{self.config.waydroid_adb_host}:{self.config.waydroid_adb_port}"
        )

    def set_connected(self, connected: bool, device: str = "") -> None:
        if connected:
            self._status_dot.setStyleSheet("color: #a6e3a1;")
            self._status_label.setText("CONNECTED")
            self._status_label.setStyleSheet("color: #a6e3a1; font-size: 10px; font-weight: bold;")
        else:
            self._status_dot.setStyleSheet("color: #f38ba8;")
            self._status_label.setText("DISCONNECTED")
            self._status_label.setStyleSheet("color: #f38ba8; font-size: 10px; font-weight: bold;")
        if device:
            self._device_label.setText(device)

    def set_running(self, running: bool) -> None:
        self._start_btn.setEnabled(not running)
        self._stop_btn.setEnabled(running)

    def update_stats(self, session_time: str = "00:00:00", **kwargs) -> None:
        self._stat_session.setText(session_time)

    # kept for compat
    def update_tickets(self, *args, **kwargs): pass
