"""
Filter window — enable/disable tournament types for registration.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox,
    QPushButton, QScrollArea, QWidget, QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal

from bot.config import BotConfig, save_config

_STAGE1_TYPES = [
    ("daily_freeroll",    "Daily Freeroll"),
    ("platinum_flip_out", "Platinum Flip Out"),
    ("hyper_turbo_nlh",   "Hyper Turbo NLH"),
    ("hyper_turbo_plo",   "Hyper Turbo PLO"),
    ("double_stack_nlh",  "Double Stack NLH"),
    ("double_stack_plo",  "Double Stack PLO"),
    ("stage1_unknown",    "Unknown / New Type"),
]


class FilterWindow(QDialog):
    saved = pyqtSignal()

    def __init__(self, config: BotConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Tournament Filter")
        self.setMinimumSize(360, 420)
        self.setModal(False)
        self._rows: dict[str, QCheckBox] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Which tournament types should the bot register for?")
        title.setWordWrap(True)
        title.setStyleSheet("color: #cdd6f4; font-size: 12px;")
        layout.addWidget(title)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(line)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setSpacing(6)

        for type_key, display_name in _STAGE1_TYPES:
            rule = self.config.type_rules.get(type_key, {"register": True})
            chk = QCheckBox(display_name)
            chk.setChecked(rule.get("register", True))
            chk.setStyleSheet("font-size: 13px; padding: 4px;")
            self._rows[type_key] = chk
            inner_layout.addWidget(chk)

        inner_layout.addStretch()
        scroll.setWidget(inner)
        layout.addWidget(scroll)

        line2 = QFrame()
        line2.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(line2)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        enable_all = QPushButton("Enable All")
        enable_all.clicked.connect(lambda: [c.setChecked(True) for c in self._rows.values()])
        btn_row.addWidget(enable_all)

        save_btn = QPushButton("Save")
        save_btn.setObjectName("startButton")
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        layout.addLayout(btn_row)

    def _save(self):
        for type_key, chk in self._rows.items():
            self.config.type_rules[type_key] = {
                "register": chk.isChecked(),
                "play_mode": "shove",
            }
        save_config(self.config)
        self.saved.emit()
        self.close()
