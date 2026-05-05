"""
Activity log panel (right side of main window).
Color-coded entries. Auto-scrolls. Export to file.
"""
from __future__ import annotations

import os
from datetime import datetime
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPlainTextEdit,
    QPushButton, QLabel, QFileDialog
)
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QTextCharFormat, QColor, QTextCursor, QFont

# Catppuccin Mocha log colors
LEVEL_COLORS = {
    "info":    "#cdd6f4",  # base text
    "success": "#a6e3a1",  # green
    "error":   "#f38ba8",  # red
    "warning": "#f9e2af",  # yellow
    "action":  "#f9e2af",  # yellow (table actions)
    "win":     "#f9e2af",  # gold/yellow for wins
    "bust":    "#f38ba8",  # red for busts
    "popup":   "#89dceb",  # teal for popups
    "skip":    "#6c7086",  # muted grey for skips
    "break":   "#cba6f7",  # purple for breaks
}


class LogPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("logPanel")
        self._session_log: list[str] = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Header row
        header_row = QHBoxLayout()
        title = QLabel("ACTIVITY LOG")
        title.setObjectName("sectionHeader")
        header_row.addWidget(title)
        header_row.addStretch()

        self._export_btn = QPushButton("Export")
        self._export_btn.setFixedWidth(70)
        self._export_btn.clicked.connect(self._export_log)
        header_row.addWidget(self._export_btn)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setFixedWidth(60)
        self._clear_btn.clicked.connect(self._clear_log)
        header_row.addWidget(self._clear_btn)

        layout.addLayout(header_row)

        # Log area
        self._log_area = QPlainTextEdit()
        self._log_area.setObjectName("logArea")
        self._log_area.setReadOnly(True)
        self._log_area.setMaximumBlockCount(2000)  # keep last 2000 lines in memory
        font = QFont("JetBrains Mono", 11)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self._log_area.setFont(font)
        layout.addWidget(self._log_area)

    @pyqtSlot(str, str)
    def append_entry(self, message: str, level: str = "info") -> None:
        """
        Append a timestamped, color-coded log entry.
        Called from GUI thread only (use signals from bot thread).
        """
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {message}"
        self._session_log.append(line)

        color = LEVEL_COLORS.get(level, LEVEL_COLORS["info"])

        cursor = self._log_area.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))

        cursor.insertText(line + "\n", fmt)

        # Auto-scroll to bottom unless user has scrolled up
        scrollbar = self._log_area.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 20
        if at_bottom:
            self._log_area.ensureCursorVisible()

    def _export_log(self) -> None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"clubgg_bot_log_{ts}.txt"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Log", default_name, "Text Files (*.txt)"
        )
        if path:
            try:
                with open(path, "w") as f:
                    f.write("\n".join(self._session_log))
            except OSError:
                pass

    def _clear_log(self) -> None:
        self._log_area.clear()
        # Keep session log for export even after clear
