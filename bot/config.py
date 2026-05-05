"""
Config loading/saving. Reads config.json from the directory containing
the running binary (or CWD during development). All settings are
represented as a dataclass for type safety. Missing keys get defaults.
"""
from __future__ import annotations

import json
import os
import sys
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

log = logging.getLogger(__name__)

# ── Default type rules ──────────────────────────────────────────────────

DEFAULT_TYPE_RULES: dict[str, dict] = {
    "daily_freeroll":           {"register": True, "play_mode": "global"},
    "platinum_flip_out":        {"register": True, "play_mode": "shove"},
    "hyper_turbo_nlh":          {"register": True, "play_mode": "global"},
    "hyper_turbo_plo":          {"register": True, "play_mode": "smart"},
    "double_stack_nlh":         {"register": True, "play_mode": "global"},
    "double_stack_plo":         {"register": True, "play_mode": "smart"},
    "stage1_unknown":           {"register": True, "play_mode": "global"},
    "sng_nlh_6max":             {"register": True, "play_mode": "shove"},
    "sng_nlh_12max":            {"register": True, "play_mode": "shove"},
    "sng_plo_6max":             {"register": True, "play_mode": "smart"},
    "sng_plo_12max":            {"register": True, "play_mode": "smart"},
    "sng_hyper_turbo_nlh_6max": {"register": True, "play_mode": "shove"},
    "sng_hyper_turbo_nlh_12max":{"register": True, "play_mode": "shove"},
    "sng_hyper_turbo_plo_6max": {"register": True, "play_mode": "smart"},
    "sng_hyper_turbo_plo_12max":{"register": True, "play_mode": "smart"},
    "final_turbo":              {"register": True, "play_mode": "shove"},
    "final_plo_6max":           {"register": True, "play_mode": "smart"},
    "final_deepstack":          {"register": True, "play_mode": "smart"},
    "final_superstack":         {"register": True, "play_mode": "smart"},
    "final_sunday_main":        {"register": True, "play_mode": "smart"},
    "final_plo_7max":           {"register": True, "play_mode": "smart"},
    "final_unknown":            {"register": True, "play_mode": "global"},
    "stage2_unknown":           {"register": True, "play_mode": "global"},
}


@dataclass
class DiscordNotifications:
    ticket_win: bool = True
    bust_out: bool = False
    session_start: bool = True
    session_end: bool = True
    error: bool = True
    low_tickets: bool = True


@dataclass
class Credentials:
    email: str = ""
    password: str = ""


@dataclass
class NameOverride:
    pattern: str = ""
    register: bool = True
    play_mode: str = "global"  # global | shove | smart


@dataclass
class BotConfig:
    # Identity
    bot_id: str = "BOT-01"
    version: int = 1

    # ADB
    waydroid_adb_host: str = "192.168.240.112"
    waydroid_adb_port: int = 5555
    adb_screenshot_method: str = "exec-out"

    # Play
    global_play_mode: str = "smart"  # shove | smart

    # Stage scanning
    stage1_enabled: bool = True

    # Discord
    discord_webhook_url: str = ""
    discord_notifications: DiscordNotifications = field(default_factory=DiscordNotifications)

    # Stats
    stats_dir: str = "./"
    stats_sync_method: str = "local"  # local | webhook | http
    stats_server_url: str = ""

    # Tournament rules
    type_rules: dict[str, dict] = field(default_factory=lambda: dict(DEFAULT_TYPE_RULES))
    name_overrides: list[NameOverride] = field(default_factory=list)

    # Timing
    poll_interval_ms: int = 500
    lobby_scan_idle_s: int = 30
    swipe_duration_ms: int = 800
    adb_tap_delay_ms: int = 300
    state_detect_retries: int = 5

    # Caret (^) raise button position — as % of APP content area
    caret_x_pct: float = 55.5
    caret_y_pct: float = 89.4

    # All-in swipe: absolute screen pixel coordinates (Waydroid 1920×1040).
    # Origin = caret/raise button center; destination = ~halfway up screen.
    # Recalibrate via tools/calibrate.py if your Waydroid geometry changes.
    shove_x1: int = 1010
    shove_y1: int = 929
    shove_x2: int = 1010
    shove_y2: int = 632

    # Debug
    save_debug_screenshots: bool = True
    debug_screenshot_dir: str = "./debug/"
    max_debug_screenshots: int = 20

    # Re-login (disabled — bot sends Discord notification and stops on logout)
    # These fields kept for config compatibility but are not used
    auto_relogin: bool = False
    credentials: Credentials = field(default_factory=Credentials)

    # Runtime (not persisted)
    _config_path: str = field(default="", repr=False, compare=False)


def _config_path() -> str:
    """Config file lives next to the binary (or CWD during dev)."""
    if getattr(sys, "frozen", False):
        # PyInstaller binary — use directory containing the executable
        base = os.path.dirname(sys.executable)
    else:
        base = os.getcwd()
    return os.path.join(base, "config.json")


def load_config() -> BotConfig:
    path = _config_path()
    cfg = BotConfig()
    cfg._config_path = path

    if not os.path.exists(path):
        log.info("No config.json found at %s — using defaults", path)
        save_config(cfg)
        return cfg

    try:
        with open(path, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        log.error("Failed to load config.json: %s — using defaults", exc)
        return cfg

    # Scalar fields
    for field_name in [
        "bot_id", "version", "waydroid_adb_host", "waydroid_adb_port",
        "adb_screenshot_method", "global_play_mode",
        "stage1_enabled",         "discord_webhook_url", "stats_dir", "stats_sync_method",
        "stats_server_url", "poll_interval_ms", "lobby_scan_idle_s",
        "swipe_duration_ms", "adb_tap_delay_ms", "state_detect_retries",
        "save_debug_screenshots", "debug_screenshot_dir",
        "max_debug_screenshots", "auto_relogin",
    ]:
        if field_name in data:
            setattr(cfg, field_name, data[field_name])

    # Nested objects
    if "discord_notifications" in data:
        dn = data["discord_notifications"]
        cfg.discord_notifications = DiscordNotifications(**{
            k: dn.get(k, getattr(cfg.discord_notifications, k))
            for k in DiscordNotifications.__dataclass_fields__
        })

    if "credentials" in data:
        cr = data["credentials"]
        cfg.credentials = Credentials(
            email=cr.get("email", ""),
            password=cr.get("password", ""),
        )

    if "type_rules" in data:
        cfg.type_rules = {**DEFAULT_TYPE_RULES, **data["type_rules"]}

    if "name_overrides" in data:
        cfg.name_overrides = [
            NameOverride(**o) for o in data["name_overrides"]
        ]

    cfg._config_path = path

    # Protect credentials file
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass

    return cfg


def save_config(cfg: BotConfig) -> None:
    path = cfg._config_path or _config_path()

    data = {
        "bot_id": cfg.bot_id,
        "version": cfg.version,
        "waydroid_adb_host": cfg.waydroid_adb_host,
        "waydroid_adb_port": cfg.waydroid_adb_port,
        "adb_screenshot_method": cfg.adb_screenshot_method,
        "global_play_mode": cfg.global_play_mode,
        "stage1_enabled": cfg.stage1_enabled,
        "discord_webhook_url": cfg.discord_webhook_url,
        "discord_notifications": asdict(cfg.discord_notifications),
        "stats_dir": cfg.stats_dir,
        "stats_sync_method": cfg.stats_sync_method,
        "stats_server_url": cfg.stats_server_url,
        "type_rules": cfg.type_rules,
        "name_overrides": [asdict(o) for o in cfg.name_overrides],
        "poll_interval_ms": cfg.poll_interval_ms,
        "lobby_scan_idle_s": cfg.lobby_scan_idle_s,
        "swipe_duration_ms": cfg.swipe_duration_ms,
        "adb_tap_delay_ms": cfg.adb_tap_delay_ms,
        "state_detect_retries": cfg.state_detect_retries,
        "save_debug_screenshots": cfg.save_debug_screenshots,
        "debug_screenshot_dir": cfg.debug_screenshot_dir,
        "max_debug_screenshots": cfg.max_debug_screenshots,
        "auto_relogin": cfg.auto_relogin,
        "credentials": asdict(cfg.credentials),
    }

    # Atomic write: write temp file, then rename
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError as exc:
        log.error("Failed to save config: %s", exc)
        try:
            os.unlink(tmp)
        except OSError:
            pass
