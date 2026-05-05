"""
ClubGG Bot — entry point.
Launches PyQt6 GUI. Bot logic runs on background thread.
Handles distro detection and QT_QPA_PLATFORM for Fedora Wayland.
"""
from __future__ import annotations

import os
import sys
import logging

# Ensure project root is on sys.path so absolute imports work
# when running as `python3 main.py` from the project directory
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Auto-update from GitHub before launching
try:
    from updater import check_and_update
    check_and_update()
except Exception as _exc:
    # Don't crash on update failure, but DO surface the error
    print(f"[main] updater import/run failed: {type(_exc).__name__}: {_exc}",
          flush=True)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )


def _configure_qt_platform() -> None:
    """
    Set QT_QPA_PLATFORM based on display server.
    Fedora + Wayland needs 'wayland'; fall back to 'xcb' if Wayland fails.
    """
    if "QT_QPA_PLATFORM" in os.environ:
        return  # respect user override
    if "WAYLAND_DISPLAY" in os.environ:
        os.environ.setdefault("QT_QPA_PLATFORM", "wayland")
    elif "DISPLAY" in os.environ:
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")


def _get_distro() -> str:
    try:
        import distro
        name = distro.id().lower()
        if "fedora" in name:
            return "fedora"
        if "arch" in name or "cachyos" in name or "manjaro" in name:
            return "arch"
        return name
    except ImportError:
        # Fallback: read /etc/os-release
        try:
            with open("/etc/os-release") as f:
                content = f.read().lower()
            if "fedora" in content:
                return "fedora"
            if "arch" in content or "cachyos" in content:
                return "arch"
        except OSError:
            pass
        return "unknown"


def main() -> int:
    _configure_logging()
    _configure_qt_platform()

    log = logging.getLogger(__name__)
    distro = _get_distro()
    log.info("Platform: %s | Python %s", distro, sys.version.split()[0])

    if distro == "fedora":
        log.info("Fedora detected — ensure: SELinux permissive, binder_linux loaded, firewalld configured")

    # Load config
    from bot.config import load_config
    config = load_config()
    log.info("Config loaded: bot_id=%s adb=%s:%d", config.bot_id, config.waydroid_adb_host, config.waydroid_adb_port)

    # Configure tesseract (do it early to surface errors before GUI)
    from bot.ocr import configure_tesseract
    try:
        configure_tesseract()
    except Exception as exc:
        log.warning("Tesseract config warning: %s", exc)

    # Launch GUI
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QFontDatabase, QFont
    from gui.window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("ClubGG Bot")
    app.setApplicationVersion("1.1.0")

    # Try to set JetBrains Mono if available
    font_families = QFontDatabase.families()
    if "JetBrains Mono" in font_families:
        app.setFont(QFont("JetBrains Mono", 11))
    elif "Consolas" in font_families:
        app.setFont(QFont("Consolas", 11))

    window = MainWindow(config=config)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
