"""
Debug mode runner.

Walks through every bot state and action step-by-step, taking a
timestamped screenshot before and after every single operation.
Does NOT register for tournaments or play hands — observation only.

Produces: debug/run_YYYYMMDD_HHMMSS/
  000_startup.png
  001_pre_me_tab.png
  001_post_me_tab.png
  002_pre_ticket_read.png
  002_post_ticket_read.png
  ... etc

Also writes debug/run_.../log.txt with every action and what it saw.
Send the whole folder and I can diagnose exactly what's going wrong.
"""
from __future__ import annotations

import os
import sys
import time
import logging
from datetime import datetime

log = logging.getLogger(__name__)


class DebugRunner:
    def __init__(self, config, adb, detector, screen_w: int, screen_h: int):
        self.app_left = adb.app_left
        self.app_right = adb.app_right
        self.config = config
        self.adb = adb
        self.detector = detector
        self.screen_w = screen_w
        self.screen_h = screen_h

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.out_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "debug", f"run_{ts}"
        )
        os.makedirs(self.out_dir, exist_ok=True)
        self._step = 0
        self._log_lines: list[str] = []
        self._log(f"Debug run started: {ts}")
        self._log(f"ADB: {config.waydroid_adb_host}:{config.waydroid_adb_port}")
        self._log(f"Screen: {screen_w}x{screen_h}")
        self._log(f"App viewport: x={adb.app_left}-{adb.app_right} ({adb.app_w}px, scale={adb.app_w/621:.3f}x)")

    # ── Logging ──────────────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{ts}] {msg}"
        print(line)
        self._log_lines.append(line)
        # Flush log on every write so it's readable even if run crashes
        with open(os.path.join(self.out_dir, "log.txt"), "w") as f:
            f.write("\n".join(self._log_lines))

    # ── Screenshot ───────────────────────────────────────────────────────

    def _shot(self, label: str) -> "np.ndarray":
        import cv2
        import numpy as np
        try:
            img = self.adb.screenshot()
            fname = f"{self._step:03d}_{label}.png"
            path = os.path.join(self.out_dir, fname)
            cv2.imwrite(path, img)
            state = self.detector.detect(img)
            self._log(f"  SHOT {fname} → state={state.name}")
            return img
        except Exception as e:
            self._log(f"  SHOT FAILED: {e}")
            return None

    def _step_shot(self, prefix: str, label: str) -> "np.ndarray":
        """Take a shot and increment step counter."""
        self._step += 1
        return self._shot(f"{prefix}_{label}")

    def _pre(self, label: str) -> "np.ndarray":
        return self._shot(f"{self._step:03d}_pre_{label}")

    def _post(self, label: str) -> "np.ndarray":
        img = self._shot(f"{self._step:03d}_post_{label}")
        self._step += 1
        return img

    # ── Tap with screenshot ──────────────────────────────────────────────

    def _tap(self, x_pct: float, y_pct: float, label: str) -> None:
        self._log(f"  TAP ({x_pct:.1f}%, {y_pct:.1f}%) — {label}")
        self._pre(label)
        self.adb.tap(x_pct, y_pct)
        time.sleep(1.5)
        self._post(label)

    def _tap_region(self, region, label: str) -> None:
        cx, cy = region.center_abs(self.screen_w, self.screen_h)
        x_pct = cx / self.screen_w * 100
        y_pct = cy / self.screen_h * 100
        self._tap(x_pct, y_pct, label)

    # ── Main debug walk ──────────────────────────────────────────────────

    def run(self) -> str:
        """
        Walk through every navigable state and take screenshots.
        Returns path to the output directory.
        """
        self._log("=" * 60)
        self._log("STARTING DEBUG WALK")
        self._log("=" * 60)

        # Step 0: capture startup state
        self._shot("000_startup")

        self._walk_nav_tabs()
        self._walk_stage1_lobby()
        self._walk_tournament_detail()
        self._walk_stage2_tab()
        self._walk_final_stage_tab()
        self._walk_me_page()
        self._walk_game_settings()

        self._log("=" * 60)
        self._log(f"DEBUG WALK COMPLETE")
        self._log(f"Output: {self.out_dir}")
        self._log(f"Screenshots: {self._step}")
        self._log("=" * 60)

        return self.out_dir

    # ── Individual walks ─────────────────────────────────────────────────

    def _walk_nav_tabs(self) -> None:
        """Tap every bottom nav tab and screenshot what we see."""
        self._log("\n--- NAVIGATION TABS ---")
        from .vision import NAV_TABS, State

        tabs = ["club", "live", "stage1", "stage2", "final", "me"]
        for tab in tabs:
            x, y = NAV_TABS[tab]
            self._log(f"\nTapping tab: {tab} at ({x}%, {y}%)")
            self._pre(f"nav_{tab}")
            self.adb.tap(x, y)
            time.sleep(2.0)
            self._post(f"nav_{tab}")

        # Return to Stage 1
        self.adb.tap(*NAV_TABS["stage1"])
        time.sleep(1.5)

    def _walk_stage1_lobby(self) -> None:
        """Scroll through Stage 1 lobby and screenshot every card."""
        self._log("\n--- STAGE 1 LOBBY ---")
        from .vision import NAV_TABS, State, detect_tournament_cards, detect_badge_color, detect_me_badge, REGIONS
        from .ocr import ocr_tournament_name, ocr_text

        self.adb.tap(*NAV_TABS["stage1"])
        time.sleep(1.5)

        img = self._shot("stage1_start")
        if img is None:
            return

        state = self.detector.detect(img)
        self._log(f"  Stage 1 state: {state.name}")

        # Detect and log every visible card
        cards = detect_tournament_cards(img, self.screen_w, self.screen_h, self.app_left, self.app_right)
        self._log(f"  Cards detected: {len(cards)}")

        for i, card in enumerate(cards):
            region = card["region"]
            card_img = region.crop(img, self.screen_w, self.screen_h)
            if card_img.size == 0:
                continue

            # Badge crop (left side of card)
            from .adb import Region
            badge_r = Region(0, 0, 29, 100)
            badge_img = badge_r.crop(card_img, card_img.shape[1], card_img.shape[0])
            badge_color = detect_badge_color(badge_img)
            me_badge = detect_me_badge(card_img)

            # OCR name
            name = ocr_tournament_name(card_img)

            self._log(f"  Card {i}: badge={badge_color}, me={me_badge}, name={name!r}")

        # Scroll down twice and re-detect
        for scroll_n in range(3):
            self.adb.scroll_down()
            time.sleep(0.8)
            img = self._shot(f"stage1_scroll_{scroll_n+1}")
            if img is None:
                break
            cards = detect_tournament_cards(img, self.screen_w, self.screen_h, self.app_left, self.app_right)
            self._log(f"  After scroll {scroll_n+1}: {len(cards)} cards")

        # Scroll back to top
        self.adb.swipe(50, 20, 50, 80, duration_ms=400)
        time.sleep(0.8)

    def _walk_tournament_detail(self) -> None:
        """Tap the first Registering tournament and screenshot detail + modal."""
        self._log("\n--- TOURNAMENT DETAIL ---")
        from .vision import NAV_TABS, State, detect_tournament_cards, detect_badge_color

        self.adb.tap(*NAV_TABS["stage1"])
        time.sleep(1.5)

        img = self.adb.screenshot()
        cards = detect_tournament_cards(img, self.screen_w, self.screen_h, self.app_left, self.app_right)

        target_card = None
        for card in cards:
            region = card["region"]
            card_img = region.crop(img, self.screen_w, self.screen_h)
            from .adb import Region
            badge_r = Region(0, 0, 29, 100)
            badge_img = badge_r.crop(card_img, card_img.shape[1], card_img.shape[0])
            if detect_badge_color(badge_img) == "green":
                target_card = card
                break

        if target_card is None:
            self._log("  No green-badge card found — skipping detail walk")
            return

        self._log("  Found green card — tapping it")
        region = target_card["region"]
        cx, cy = region.center_abs(self.screen_w, self.screen_h)
        self._pre("detail_tap")
        self.adb.tap_abs(cx, cy)
        time.sleep(2.0)
        self._post("detail_page")

        state = self.detector.detect(self.adb.screenshot())
        self._log(f"  After card tap: state={state.name}")

        # Press back — do NOT register
        self._log("  Pressing back (debug — not registering)")
        self.adb.press_back()
        time.sleep(1.0)
        self._shot("detail_back")

    def _walk_stage2_tab(self) -> None:
        """Navigate to Stage 2 and screenshot."""
        self._log("\n--- STAGE 2 TAB ---")
        from .vision import NAV_TABS

        self.adb.tap(*NAV_TABS["stage2"])
        time.sleep(2.0)
        img = self._shot("stage2_view")
        if img is None:
            return
        state = self.detector.detect(img)
        self._log(f"  Stage 2 state: {state.name}")

        from .vision import detect_tournament_cards
        cards = detect_tournament_cards(img, self.screen_w, self.screen_h, self.app_left, self.app_right)
        self._log(f"  Cards detected: {len(cards)}")

    def _walk_final_stage_tab(self) -> None:
        """Navigate to Final Stage and screenshot."""
        self._log("\n--- FINAL STAGE TAB ---")
        from .vision import NAV_TABS

        self.adb.tap(*NAV_TABS["final"])
        time.sleep(2.0)
        img = self._shot("final_view")
        if img is None:
            return
        state = self.detector.detect(img)
        self._log(f"  Final Stage state: {state.name}")

        from .vision import detect_tournament_cards
        cards = detect_tournament_cards(img, self.screen_w, self.screen_h, self.app_left, self.app_right)
        self._log(f"  Cards detected: {len(cards)}")

    def _walk_me_page(self) -> None:
        """Navigate to Me page and read ticket inventory."""
        self._log("\n--- ME PAGE ---")
        from .vision import NAV_TABS, State
        from .ocr import ocr_ticket_count, ocr_membership_tier
        from .adb import Region

        self.adb.tap(*NAV_TABS["me"])
        time.sleep(2.0)
        img = self._shot("me_page")
        if img is None:
            return

        state = self.detector.detect(img)
        self._log(f"  Me page state: {state.name}")

        # Read ticket regions and log what OCR sees
        ticket_regions = {
            "final":  Region(70.0, 27.5, 22.0, 4.0),
            "stage2": Region(70.0, 30.5, 22.0, 4.0),
            "stage1": Region(70.0, 33.5, 22.0, 4.0),
        }
        for name, r in ticket_regions.items():
            crop = r.crop(img, self.screen_w, self.screen_h, self.app_left, self.app_right)
            count = ocr_ticket_count(crop)
            self._log(f"  Ticket {name}: OCR={count}")
            # Save individual crop for inspection
            import cv2
            cv2.imwrite(os.path.join(self.out_dir, f"me_ticket_{name}_crop.png"), crop)

        # Membership
        mem_r = Region(42.0, 16.0, 42.0, 4.0)
        mem_crop = mem_r.crop(img, self.screen_w, self.screen_h)
        import cv2
        cv2.imwrite(os.path.join(self.out_dir, "me_membership_crop.png"), mem_crop)
        tier = ocr_membership_tier(mem_crop)
        self._log(f"  Membership OCR: {tier!r}")

        # Return to Stage 1
        self.adb.tap(*NAV_TABS["stage1"])
        time.sleep(1.5)

    def _walk_game_settings(self) -> None:
        """Navigate to Game Settings from Me page and check card set."""
        self._log("\n--- GAME SETTINGS ---")
        from .vision import NAV_TABS, State

        self.adb.tap(*NAV_TABS["me"])
        time.sleep(1.5)
        self._shot("me_before_settings")

        # Tap Game Settings row at y=57.3%
        self._log("  Tapping Game Settings row at y=57.3%")
        self._pre("game_settings_tap")
        self.adb.tap(50.0, 57.3)
        time.sleep(2.0)
        img = self._post("game_settings_page")

        if img is not None:
            state = self.detector.detect(img)
            self._log(f"  Game Settings state: {state.name}")
            from .vision import match_template_bool
            set3 = match_template_bool(img, "text_set3.png", 0.75,
                                       screen_w=self.screen_w, screen_h=self.screen_h)
            self._log(f"  Set 3 template match: {set3}")

        self.adb.press_back()
        time.sleep(0.8)
        self.adb.tap(*NAV_TABS["stage1"])
        time.sleep(1.0)
        self._shot("back_to_stage1")


def run_debug(config=None) -> None:
    """Entry point — load config, connect ADB, run debug walk."""
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if config is None:
        from bot.config import load_config
        config = load_config()

    from bot.adb import ADBClient
    from bot.vision import StateDetector
    from bot.ocr import configure_tesseract

    configure_tesseract()

    adb = ADBClient(
        host=config.waydroid_adb_host,
        port=config.waydroid_adb_port,
        tap_delay_ms=400,
        swipe_duration_ms=600,
    )

    print(f"Connecting to ADB at {config.waydroid_adb_host}:{config.waydroid_adb_port}...")
    if not adb.connect():
        print("ERROR: Could not connect to ADB. Is Waydroid running?")
        print("  Run: waydroid session start && adb connect 192.168.240.112:5555")
        sys.exit(1)

    print(f"Connected. Screen: {adb.screen_w}x{adb.screen_h}, App viewport: x={adb.app_left}-{adb.app_right} ({adb.app_w}px)")
    detector = StateDetector(adb.screen_w, adb.screen_h, app_left=adb.app_left, app_right=adb.app_right)

    runner = DebugRunner(config, adb, detector, adb.screen_w, adb.screen_h)
    out_dir = runner.run()

    print(f"\nDebug output saved to: {out_dir}")
    print(f"Zip and send that folder — it contains everything needed to diagnose issues.")


if __name__ == "__main__":
    run_debug()
