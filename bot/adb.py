"""
ADB interface layer.

All tap/swipe coordinates are accepted as relative percentages of the
APP CONTENT AREA (not the full display). On startup the bot detects the
actual ClubGG viewport within the Waydroid display (which may be centered
in a wider screen), then transforms all percentage coordinates accordingly.

This handles Waydroid running on a wide display (e.g. 2560x1380) where
ClubGG renders a phone-layout column centered in the middle of the screen.
"""
from __future__ import annotations

import subprocess
import time
import threading
import logging
from dataclasses import dataclass
from typing import Optional
import numpy as np

log = logging.getLogger(__name__)


@dataclass
class Region:
    """
    Screen region as relative percentages of the APP CONTENT AREA.
    Percentages are relative to the phone-layout content column, not the
    full display width — so they match the template capture resolution exactly.
    """
    x_pct: float
    y_pct: float
    w_pct: float
    h_pct: float

    def to_abs(self, screen_w: int, screen_h: int,
               app_left: int = 0, app_right: Optional[int] = None) -> tuple[int, int, int, int]:
        """
        Convert to absolute pixels using the app content bounds.
        app_left/app_right define the content column within the full display.
        """
        if app_right is None:
            app_right = screen_w
        app_w = app_right - app_left

        x = app_left + int(self.x_pct / 100.0 * app_w)
        y = int(self.y_pct / 100.0 * screen_h)
        w = int(self.w_pct / 100.0 * app_w)
        h = int(self.h_pct / 100.0 * screen_h)
        return x, y, w, h

    def crop(self, img: np.ndarray, screen_w: int, screen_h: int,
             app_left: int = 0, app_right: Optional[int] = None) -> np.ndarray:
        x, y, w, h = self.to_abs(screen_w, screen_h, app_left, app_right)
        return img[y:y + h, x:x + w]

    def center_abs(self, screen_w: int, screen_h: int,
                   app_left: int = 0, app_right: Optional[int] = None) -> tuple[int, int]:
        x, y, w, h = self.to_abs(screen_w, screen_h, app_left, app_right)
        return x + w // 2, y + h // 2


class ADBError(Exception):
    pass


class ADBClient:
    """
    Wraps all ADB operations for one Waydroid instance.
    Auto-detects app viewport on connect.
    Auto-reconnects on connection loss.
    Thread-safe.
    """

    MAX_RECONNECT_ATTEMPTS = 3
    RECONNECT_DELAY_S = 5.0

    def __init__(self, host: str, port: int, tap_delay_ms: int = 300, swipe_duration_ms: int = 800):
        self.host = host
        self.port = port
        self.tap_delay_ms = tap_delay_ms
        self.swipe_duration_ms = swipe_duration_ms
        self._device = f"{host}:{port}"
        self._lock = threading.Lock()
        self._screen_w: Optional[int] = None
        self._screen_h: Optional[int] = None
        # App viewport within the display (handles centered phone-layout in wide displays)
        self._app_left:   int = 0
        self._app_right:  Optional[int] = None
        self._app_top:    int = 0
        self._app_bottom: Optional[int] = None

    @property
    def app_left(self) -> int:
        return self._app_left

    @property
    def app_right(self) -> int:
        return self._app_right if self._app_right is not None else (self._screen_w or 0)

    @property
    def app_w(self) -> int:
        return self.app_right - self._app_left

    def connect(self) -> bool:
        try:
            result = self._run_raw(["adb", "connect", self._device], timeout=10)
            connected = "connected" in result.lower() or "already connected" in result.lower()
            if connected:
                self._refresh_screen_size()
                self._detect_app_viewport()
                log.info("ADB connected to %s (%dx%d, app x=%d-%d width=%d)",
                         self._device, self._screen_w, self._screen_h,
                         self._app_left, self.app_right, self.app_w)
            else:
                log.error("ADB connect failed: %s", result.strip())
            return connected
        except Exception as exc:
            log.error("ADB connect exception: %s", exc)
            return False

    def disconnect(self) -> None:
        try:
            self._run_raw(["adb", "disconnect", self._device], timeout=5)
        except Exception:
            pass

    def is_connected(self) -> bool:
        try:
            result = self._run_raw(["adb", "-s", self._device, "get-state"], timeout=5)
            return "device" in result.lower()
        except Exception:
            return False

    def _auto_reconnect(self) -> bool:
        for attempt in range(1, self.MAX_RECONNECT_ATTEMPTS + 1):
            log.warning("ADB reconnect attempt %d/%d to %s", attempt, self.MAX_RECONNECT_ATTEMPTS, self._device)
            if self.connect():
                return True
            time.sleep(self.RECONNECT_DELAY_S)
        return False

    def _refresh_screen_size(self) -> None:
        raw = self._run_raw(["adb", "-s", self._device, "shell", "wm size"], timeout=10)
        for line in raw.splitlines():
            if "size" in line.lower() and "x" in line:
                parts = line.split(":")[-1].strip().split("x")
                if len(parts) == 2:
                    try:
                        self._screen_w = int(parts[0].strip())
                        self._screen_h = int(parts[1].strip())
                        return
                    except ValueError:
                        pass
        raise ADBError(f"Could not parse screen size from: {raw!r}")

    def _detect_app_viewport(self) -> None:
        """
        Detect ClubGG content bounds within the display by scanning for
        the bright white/content area vs the dark pillarbox background.

        Works for both full-width installs (phone/tablet) and centered
        phone-layout in wide landscape displays.

        Scanning strategy:
          X: average column brightness across middle 60% of height.
             Content area is bright (>30), pillarbox is dark (~17).
          Y: average row brightness across detected content columns.
             Top/bottom chrome may be dark; content rows are bright.
        """
        import cv2
        # Retry up to 3 times — on first launch Waydroid display may not be ready
        for attempt in range(3):
            try:
                screen = self._screenshot_locked()
                if screen is not None and screen.size > 0:
                    break
            except Exception:
                pass
            time.sleep(2.0)
        else:
            # All attempts failed — use full display
            self._app_left   = 0
            self._app_right  = self._screen_w
            self._app_top    = 0
            self._app_bottom = self._screen_h
            return
        try:
            h, w = screen.shape[:2]
            gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)

            # ── X bounds ──────────────────────────────────────────────────
            mid = gray[int(0.15*h):int(0.85*h), :]
            col_means = np.mean(mid, axis=0)

            # Use threshold=30 — dark background averages ~17, content >30
            THRESH = 30
            content_cols = np.where(col_means > THRESH)[0]

            if len(content_cols) == 0:
                self._app_left  = 0
                self._app_right = w
                log.warning("Viewport X detection failed — using full width %dpx", w)
            else:
                self._app_left  = int(content_cols[0])
                self._app_right = int(content_cols[-1])
                # Sanity: content must be ≥20% of screen
                if (self._app_right - self._app_left) < w * 0.20:
                    log.warning("Detected content too narrow — using full width")
                    self._app_left  = 0
                    self._app_right = w

            # ── Y bounds ──────────────────────────────────────────────────
            center_col = gray[:, (self._app_left + self._app_right) // 2]
            content_rows = np.where(center_col > THRESH)[0]
            if len(content_rows) == 0:
                self._app_top    = 0
                self._app_bottom = h
            else:
                self._app_top    = int(content_rows[0])
                self._app_bottom = int(content_rows[-1])

            aw = self._app_right - self._app_left
            ah = self._app_bottom - self._app_top
            log.info(
                "App viewport: x=%d-%d (%dpx, %.1f%%-%.1f%%)  "
                "y=%d-%d (%dpx)  scale=%.3fx",
                self._app_left, self._app_right, aw,
                self._app_left / w * 100, self._app_right / w * 100,
                self._app_top, self._app_bottom, ah,
                aw / 621,   # 621 = template capture width
            )

        except Exception as exc:
            log.warning("Viewport detection error: %s — using full display", exc)
            self._app_left   = 0
            self._app_right  = self._screen_w
            self._app_top    = 0
            self._app_bottom = self._screen_h

    def refresh_app_viewport(self) -> None:
        """Re-detect app bounds (call if display changes)."""
        self._detect_app_viewport()

    @property
    def screen_w(self) -> int:
        if self._screen_w is None:
            self._refresh_screen_size()
        return self._screen_w

    @property
    def screen_h(self) -> int:
        if self._screen_h is None:
            self._refresh_screen_size()
        return self._screen_h

    def _run_raw(self, cmd: list[str], timeout: float = 30) -> str:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout + result.stderr

    def shell(self, command: str, timeout: float = 10) -> str:
        with self._lock:
            return self._shell_locked(command, timeout)

    def _shell_locked(self, command: str, timeout: float) -> str:
        cmd = ["adb", "-s", self._device, "shell", command]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if result.returncode != 0 and "error:" in result.stderr.lower():
                raise ADBError(result.stderr.strip())
            return result.stdout
        except subprocess.TimeoutExpired:
            raise ADBError(f"ADB shell timeout: {command!r}")
        except ADBError:
            if self._auto_reconnect():
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
                return result.stdout
            raise ADBError("ADB connection lost and reconnect failed")

    def screenshot(self) -> np.ndarray:
        import cv2
        with self._lock:
            return self._screenshot_locked()

    def _screenshot_locked(self) -> np.ndarray:
        import cv2
        import tempfile, os

        # Method 1: exec-out (fast, works on most systems)
        cmd = ["adb", "-s", self._device, "exec-out", "screencap", "-p"]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=30)
            if result.stdout:
                raw = result.stdout.replace(b"\r\n", b"\n")
                data = np.frombuffer(raw, dtype=np.uint8)
                img = cv2.imdecode(data, cv2.IMREAD_COLOR)
                if img is not None:
                    return img
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass

        # Method 2: save to sdcard and pull (slower but works on Fedora/Nobara)
        tmp = "/sdcard/bot_screen.png"
        local = tempfile.mktemp(suffix=".png")
        try:
            subprocess.run(
                ["adb", "-s", self._device, "shell", f"screencap -p {tmp}"],
                capture_output=True, timeout=30,
            )
            subprocess.run(
                ["adb", "-s", self._device, "pull", tmp, local],
                capture_output=True, timeout=30,
            )
            img = cv2.imread(local)
            if img is not None:
                return img
            raise ADBError("Failed to decode screenshot PNG")
        except subprocess.TimeoutExpired:
            raise ADBError("Screenshot timeout")
        finally:
            try:
                os.unlink(local)
            except Exception:
                pass

    # ── Coordinate helpers ───────────────────────────────────────────────

    def _pct_to_abs_x(self, x_pct: float) -> int:
        """Convert x percentage (of app content) to absolute screen pixels."""
        return self._app_left + int(x_pct / 100.0 * self.app_w)

    def _pct_to_abs_y(self, y_pct: float) -> int:
        """Convert y percentage to absolute screen pixels."""
        return int(y_pct / 100.0 * self.screen_h)

    # ── Touch input ──────────────────────────────────────────────────────

    def tap(self, x_pct: float, y_pct: float) -> None:
        """Tap at percentage of APP CONTENT area."""
        ax = self._pct_to_abs_x(x_pct)
        ay = self._pct_to_abs_y(y_pct)
        self.shell(f"input tap {ax} {ay}")
        if self.tap_delay_ms > 0:
            time.sleep(self.tap_delay_ms / 1000.0)

    def tap_region(self, region: Region) -> None:
        cx, cy = region.center_abs(self.screen_w, self.screen_h, self._app_left, self._app_right)
        self.shell(f"input tap {cx} {cy}")
        if self.tap_delay_ms > 0:
            time.sleep(self.tap_delay_ms / 1000.0)

    def tap_abs(self, ax: int, ay: int) -> None:
        self.shell(f"input tap {ax} {ay}")
        if self.tap_delay_ms > 0:
            time.sleep(self.tap_delay_ms / 1000.0)

    def swipe(self, x1_pct: float, y1_pct: float,
              x2_pct: float, y2_pct: float,
              duration_ms: Optional[int] = None) -> None:
        ax1 = self._pct_to_abs_x(x1_pct)
        ay1 = self._pct_to_abs_y(y1_pct)
        ax2 = self._pct_to_abs_x(x2_pct)
        ay2 = self._pct_to_abs_y(y2_pct)
        dur = duration_ms if duration_ms is not None else self.swipe_duration_ms
        self.shell(f"input swipe {ax1} {ay1} {ax2} {ay2} {dur}")

    def scroll_down(self) -> None:
        self.swipe(50.0, 70.0, 50.0, 30.0, duration_ms=400)
        time.sleep(0.5)

    def long_press_drag(self, x: int, y: int, y_end: int, hold_ms: int = 600, drag_ms: int = 500) -> None:
        """
        True long-press-drag using sendevent to bypass Waydroid gesture interception.
        Falls back to input swipe if sendevent device not found.
        """
        dev = self._find_touch_device()
        if dev:
            self._sendevent_long_press_drag(dev, x, y, y_end, hold_ms, drag_ms)
        else:
            # Fallback: slow swipe
            dur = hold_ms + drag_ms
            self.shell(f"input swipe {x} {y} {x} {y_end} {dur}")

    def _find_touch_device(self) -> str:
        """Find the touchscreen input device path on the Android device."""
        out = self.shell("getevent -p 2>/dev/null")
        device = ""
        for line in out.splitlines():
            if line.startswith("add device"):
                device = line.split(":")[-1].strip()
            if "ABS_MT_POSITION_X" in line and device:
                return device
        return ""

    def _sendevent_long_press_drag(self, dev: str, x: int, y: int, y_end: int, 
                                    hold_ms: int, drag_ms: int) -> None:
        """Inject raw touch events for long-press-drag."""
        import time as _time
        # Touch DOWN at (x, y)
        self.shell(f"sendevent {dev} 3 57 1")   # ABS_MT_TRACKING_ID
        self.shell(f"sendevent {dev} 3 53 {x}") # ABS_MT_POSITION_X
        self.shell(f"sendevent {dev} 3 54 {y}") # ABS_MT_POSITION_Y
        self.shell(f"sendevent {dev} 3 58 50")  # ABS_MT_PRESSURE
        self.shell(f"sendevent {dev} 1 330 1")  # BTN_TOUCH DOWN
        self.shell(f"sendevent {dev} 0 0 0")    # SYN_REPORT
        # Hold for hold_ms (long press threshold)
        _time.sleep(hold_ms / 1000.0)
        # Drag upward in steps
        steps = 20
        for i in range(1, steps + 1):
            yi = y + int((y_end - y) * i / steps)
            self.shell(f"sendevent {dev} 3 53 {x}")
            self.shell(f"sendevent {dev} 3 54 {yi}")
            self.shell(f"sendevent {dev} 0 0 0")
            _time.sleep(drag_ms / 1000.0 / steps)
        # Touch UP
        self.shell(f"sendevent {dev} 3 57 -1")  # ABS_MT_TRACKING_ID = -1 (release)
        self.shell(f"sendevent {dev} 1 330 0")  # BTN_TOUCH UP
        self.shell(f"sendevent {dev} 0 0 0")    # SYN_REPORT

    def all_in_swipe(self, caret_region: Region, retry_duration_ms: Optional[int] = None) -> None:
        cx, cy = caret_region.center_abs(self.screen_w, self.screen_h, self._app_left, self._app_right)
        action_panel_h = int(0.18 * self.screen_h)
        y_end = max(0, cy - int(action_panel_h * 0.9))
        dur = retry_duration_ms if retry_duration_ms is not None else self.swipe_duration_ms
        log.debug("All-in swipe abs: (%d,%d) -> (%d,%d) dur=%dms", cx, cy, cx, y_end, dur)
        self.shell(f"input swipe {cx} {cy} {cx} {y_end} {dur}")

    def type_text(self, text: str) -> None:
        _KEYCODE = {
            "@": "KEYCODE_AT", ".": "KEYCODE_PERIOD",
            "!": "KEYCODE_EXCLAM", "_": "KEYCODE_UNDERSCORE",
            "-": "KEYCODE_MINUS", " ": "KEYCODE_SPACE",
            "#": "KEYCODE_POUND", "$": "KEYCODE_DOLLAR",
            "&": "KEYCODE_AMPERSAND", "+": "KEYCODE_PLUS",
        }
        safe = all(c.isalnum() or c in "._-+" for c in text)
        if safe:
            self.shell(f'input text "{text}"')
            return
        for char in text:
            if char in _KEYCODE:
                self.shell(f"input keyevent {_KEYCODE[char]}")
            else:
                self.shell(f'input text "{char}"')
            time.sleep(0.05)

    def clear_field_and_type(self, text: str) -> None:
        self.shell("input keyevent KEYCODE_CTRL_A")
        time.sleep(0.1)
        self.shell("input keyevent KEYCODE_DEL")
        time.sleep(0.1)
        self.type_text(text)

    def press_back(self) -> None:
        self.shell("input keyevent KEYCODE_BACK")
        time.sleep(0.5)

    def force_stop_app(self, package: str = "com.gg.clubgg") -> None:
        self.shell(f"am force-stop {package}")
        time.sleep(2.0)

    def launch_app(self, package: str = "com.gg.clubgg") -> None:
        self.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
        time.sleep(5.0)

    def is_app_running(self, package: str = "com.gg.clubgg") -> bool:
        """
        Check if ClubGG is running.
        Uses 'pidof' first, falls back to 'ps' for Waydroid compatibility.
        On Waydroid, pidof may not find Android apps — ps is more reliable.
        """
        # Try pidof first (fast)
        output = self.shell(f"pidof {package}")
        if output.strip():
            return True
        # Fallback: ps grep (works in Waydroid Android shell)
        ps_out = self.shell(f"ps -A 2>/dev/null | grep {package}")
        if package in ps_out:
            return True
        # Second fallback: dumpsys activity (most reliable but slowest)
        dump = self.shell(f"dumpsys activity {package} 2>/dev/null | grep -c 'mResumed=true'")
        try:
            if int(dump.strip()) > 0:
                return True
        except ValueError:
            pass
        return False

    def restart_app(self, package: str = "com.gg.clubgg") -> None:
        log.warning("Force-stopping and relaunching ClubGG")
        self.force_stop_app(package)
        self.launch_app(package)
