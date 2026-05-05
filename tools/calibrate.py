#!/usr/bin/env python3
"""
ClubGG Bot Calibration Tool

Click elements in the screenshot to get coordinates and colours.

Controls:
  Left-click    coords + region %  (for taps and region constants)
  Middle-click  colour sample      (for bot/color.py probe targets)
  Left-drag     swipe coords       (for slider swipes)
  Right-click   tap device         (verify the tap location)
  S             refresh screenshot
  Q             quit

The colour sample logs both the exact pixel and a 5x5 averaged value —
use the averaged hex when setting up bot color probes, since that is
exactly what the bot's color.py samples and matches against.

Usage:  python3 tools/calibrate.py
"""
import sys, os, subprocess, time, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PyQt6.QtWidgets import (QApplication, QMainWindow, QLabel, QVBoxLayout,
                              QHBoxLayout, QWidget, QTextEdit, QScrollArea)
from PyQt6.QtCore    import Qt, QPoint
from PyQt6.QtGui     import (QPixmap, QImage, QPainter, QPen, QColor, QFont)


DEVICE = None  # set at startup


# ── Screenshot (save to /sdcard/, pull, read) ─────────────────────────────

def screenshot() -> np.ndarray:
    import cv2
    remote = "/sdcard/calib_shot.png"
    local  = "/tmp/calib_shot.png"
    # Capture on device
    subprocess.run(["adb", "-s", DEVICE, "shell", "screencap", "-p", remote],
                   timeout=15, check=True)
    # Pull to local
    subprocess.run(["adb", "-s", DEVICE, "pull", remote, local],
                   capture_output=True, timeout=15, check=True)
    img = cv2.imread(local)
    if img is None:
        raise RuntimeError(f"Could not read {local}")
    return img


def detect_bounds(img: np.ndarray) -> tuple[int,int,int,int]:
    import cv2
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    mid  = gray[int(0.15*h):int(0.85*h), :]
    col_means = np.mean(mid, axis=0)
    cols = np.where(col_means > 30)[0]
    al = int(cols[0])  if len(cols) else 0
    ar = int(cols[-1]) if len(cols) else w
    ctr = gray[:, (al+ar)//2]
    rows = np.where(ctr > 30)[0]
    at = int(rows[0])  if len(rows) else 0
    ab = int(rows[-1]) if len(rows) else h
    # Safety: if detected width < 50px something went wrong, use full
    if (ar - al) < 50:
        al, ar = 0, w
    if (ab - at) < 50:
        at, ab = 0, h
    return al, at, ar, ab


def cv_to_qpix(img: np.ndarray, dw: int, dh: int) -> QPixmap:
    rgb = img[:, :, ::-1].copy()
    h, w, ch = rgb.shape
    qi = QImage(rgb.data, w, h, w*ch, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qi).scaled(
        dw, dh,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation)


# ── Canvas ────────────────────────────────────────────────────────────────

class Canvas(QLabel):
    def __init__(self, log):
        super().__init__()
        self.log   = log
        self.img   = None
        self.scale = 1.0
        self.al = self.at = 0
        self.ar = self.ab = 1
        self._drag0: QPoint | None = None
        self._cur:   QPoint | None = None
        self._marks = []
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.setMouseTracking(True)

    @property
    def aw(self): return max(1, self.ar - self.al)
    @property
    def ah(self): return max(1, self.ab - self.at)

    def d2s(self, p: QPoint):
        return int(p.x()/self.scale), int(p.y()/self.scale)

    def s2pct(self, sx, sy):
        xp = round((sx - self.al) / self.aw * 100, 1)
        yp = round((sy - self.at) / self.ah * 100, 1)
        return xp, yp

    def load(self, img, al, at, ar, ab):
        self.img = img
        self.al, self.at, self.ar, self.ab = al, at, ar, ab
        self._marks = []
        MAX_H = 820
        h, w = img.shape[:2]
        self.scale = min(1.0, MAX_H / h)
        self._redraw()

    def _redraw(self):
        if self.img is None:
            return
        h, w = self.img.shape[:2]
        dw = int(w * self.scale)
        dh = int(h * self.scale)
        pix = cv_to_qpix(self.img, dw, dh)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        def ds(v): return int(v * self.scale)

        # App bounds (green)
        pen = QPen(QColor(0,255,0), 2)
        p.setPen(pen)
        p.drawRect(ds(self.al), ds(self.at),
                   ds(self.ar)-ds(self.al), ds(self.ab)-ds(self.at))

        # Marks
        for kind, p1, p2 in self._marks:
            if kind == "click":
                pen = QPen(QColor(255,50,50), 2)
                p.setPen(pen)
                x,y = p1.x(), p1.y()
                p.drawLine(x-16,y,x+16,y)
                p.drawLine(x,y-16,x,y+16)
            elif kind == "color":
                # Cyan circle for colour samples
                pen = QPen(QColor(0,255,255), 2)
                p.setPen(pen)
                x, y = p1.x(), p1.y()
                p.drawEllipse(x-8, y-8, 16, 16)
                p.drawLine(x-12, y, x-4, y)
                p.drawLine(x+4, y, x+12, y)
                p.drawLine(x, y-12, x, y-4)
                p.drawLine(x, y+4, x, y+12)
            elif kind == "drag":
                pen = QPen(QColor(255,165,0), 2)
                p.setPen(pen)
                p.drawLine(p1, p2)
                p.setPen(QPen(QColor(0,255,0), 6))
                p.drawPoint(p1)
                p.setPen(QPen(QColor(255,0,0), 6))
                p.drawPoint(p2)

        # Live drag
        if self._drag0 and self._cur:
            p.setPen(QPen(QColor(255,200,0), 1, Qt.PenStyle.DashLine))
            p.drawLine(self._drag0, self._cur)

        p.end()
        self.setPixmap(pix)
        self.resize(dw, dh)

    def _sample_color(self, sx: int, sy: int):
        """Sample exact pixel + 5x5 averaged box at screen coord (sx, sy).

        Logs both as #RRGGBB hex and (B, G, R). The averaged value is what the
        bot's color probes use, so it's the ground-truth target for tuning.
        """
        if self.img is None:
            return
        h, w = self.img.shape[:2]
        if not (0 <= sx < w and 0 <= sy < h):
            self.log("  (out of bounds)")
            return

        # Exact pixel (BGR)
        b, g, r = (int(v) for v in self.img[sy, sx])

        # 5x5 averaged box centred on (sx, sy), clamped to image
        x1, x2 = max(0, sx - 2), min(w, sx + 3)
        y1, y2 = max(0, sy - 2), min(h, sy + 3)
        roi = self.img[y1:y2, x1:x2].reshape(-1, 3).mean(axis=0)
        ab, ag, ar = (int(v) for v in roi)

        xp, yp = self.s2pct(sx, sy)
        self.log("── COLOR ─────────────────────────────")
        self.log(f"  Screen px : ({sx}, {sy})")
        self.log(f"  App %     : ({xp}%, {yp}%)")
        self.log(f"  Pixel     : #{r:02x}{g:02x}{b:02x}  BGR=({b}, {g}, {r})")
        self.log(f"  Avg 5x5   : #{ar:02x}{ag:02x}{ab:02x}  BGR=({ab}, {ag}, {ar})  ← use this")

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag0 = e.pos()
            self._cur   = e.pos()
            sx,sy = self.d2s(e.pos())
            xp,yp = self.s2pct(sx,sy)
            self._marks = [("click", e.pos(), e.pos())]
            self._redraw()
            self.log("── CLICK ─────────────────────────────")
            self.log(f"  Screen px : ({sx}, {sy})")
            self.log(f"  App %     : ({xp}%, {yp}%)")
            self.log(f"  Region    : Region({xp}, {yp}, W, H)")

        elif e.button() == Qt.MouseButton.RightButton:
            sx,sy = self.d2s(e.pos())
            xp,yp = self.s2pct(sx,sy)
            self.log(f"── TAPPING ({sx},{sy}) [{xp}%, {yp}%] ──")
            subprocess.run(["adb","-s",DEVICE,"shell","input","tap",str(sx),str(sy)],
                           capture_output=True, timeout=10)
            self.log("  Done.")

        elif e.button() == Qt.MouseButton.MiddleButton:
            sx, sy = self.d2s(e.pos())
            self._marks = [("color", e.pos(), e.pos())]
            self._redraw()
            self._sample_color(sx, sy)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton and self._drag0:
            self._cur = e.pos()
            self._redraw()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._drag0:
            p1,p2 = self._drag0, e.pos()
            dist = ((p2.x()-p1.x())**2+(p2.y()-p1.y())**2)**0.5
            if dist > 15:
                sx1,sy1 = self.d2s(p1)
                sx2,sy2 = self.d2s(p2)
                xp1,yp1 = self.s2pct(sx1,sy1)
                xp2,yp2 = self.s2pct(sx2,sy2)
                self._marks = [("drag",p1,p2)]
                self._redraw()
                self.log("── SWIPE ─────────────────────────────")
                self.log(f"  From: ({xp1}%, {yp1}%)")
                self.log(f"  To  : ({xp2}%, {yp2}%)")
                self.log(f"  ADB : adb shell input swipe {sx1} {sy1} {sx2} {sy2} 400")
            self._drag0 = self._cur = None


# ── Main window ───────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(
            "ClubGG Calibrator  |  L=coords  M=color  R=tap  S=refresh  Q=quit"
        )

        self._log_widget = QTextEdit()
        self._log_widget.setReadOnly(True)
        self._log_widget.setFont(QFont("Monospace", 9))
        self._log_widget.setFixedWidth(400)
        self._log_widget.setStyleSheet("background:#111; color:#00ff00;")

        self._canvas = Canvas(self._log)

        # Canvas in a scroll area so it doesn't get clipped
        scroll = QScrollArea()
        scroll.setWidget(self._canvas)
        scroll.setWidgetResizable(False)

        row = QWidget()
        hl  = QHBoxLayout(row)
        hl.setContentsMargins(0,0,0,0)
        hl.addWidget(scroll, stretch=1)
        hl.addWidget(self._log_widget)
        self.setCentralWidget(row)

        self._log("Ready. Controls:")
        self._log("  Left-click   = show coordinates")
        self._log("  Middle-click = sample colour (hex + BGR, with 5x5 avg)")
        self._log("  Left-drag    = show swipe coords")
        self._log("  Right-click  = tap device")
        self._log("  S = refresh screenshot")
        self._log("  Q = quit")
        self._log("Green box = detected app bounds")
        self._log("─" * 38)

    def _log(self, text: str):
        self._log_widget.append(text)
        sb = self._log_widget.verticalScrollBar()
        sb.setValue(sb.maximum())
        print(text)

    def refresh(self):
        self._log("Taking screenshot...")
        try:
            img = screenshot()
            al, at, ar, ab = detect_bounds(img)
            h, w = img.shape[:2]
            self._log(f"Screen: {w}×{h}")
            self._log(f"App: x={al}-{ar} ({ar-al}px)  y={at}-{ab} ({ab-at}px)")
            self._canvas.load(img, al, at, ar, ab)
        except Exception as e:
            self._log(f"ERROR: {e}")

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Q, Qt.Key.Key_Escape):
            self.close()
        elif e.key() == Qt.Key.Key_S:
            self.refresh()


if __name__ == "__main__":
    from bot.config import load_config
    cfg = load_config()
    DEVICE = f"{cfg.waydroid_adb_host}:{cfg.waydroid_adb_port}"

    print(f"Connecting to {DEVICE}...")
    r = subprocess.run(["adb","connect",DEVICE], capture_output=True, text=True, timeout=10)
    out = r.stdout.strip()
    if "connected" not in out.lower() and "already" not in out.lower():
        print(f"ADB connect failed: {out}")
        sys.exit(1)
    print(f"Connected: {out}")

    app = QApplication(sys.argv)
    win = MainWindow()
    win.resize(1100, 860)
    win.show()
    win.refresh()
    sys.exit(app.exec())
