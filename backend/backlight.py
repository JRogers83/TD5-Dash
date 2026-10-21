"""
Display backlight control — sysfs write + boot-time apply of the saved level.

The DB (settings table) is the single store for the day/night levels and the
active mode (`brightness_day`, `brightness_night`, `brightness_mode`). The
frontend edits them through POST /settings; this module turns them into a real
backlight value, both when the user changes it and once at service start so the
screen is at the right level from boot without waiting for a browser to push it.

Levels are 0–255 in the UI/DB. The panel's own scale is `max_brightness` (255 on
many drivers, but not all), so the value is scaled to it before writing.

On Docker/dev there is no backlight device; every call degrades to a clean
{"ok": False, ...} result rather than raising.
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os

import db

log = logging.getLogger(__name__)

_BACKLIGHT_DIR_GLOB = "/sys/class/backlight/*"

# The panel driver can register after the service starts, so retry for a while.
_STARTUP_RETRY_S = 2.0
_STARTUP_DEADLINE_S = 60.0

# Outcome of the most recent apply() — surfaced via status() so the Settings screen
# can say so when brightness changes are not reaching the panel (there is no shell
# on the vehicle to read logs from).
last_result: dict | None = None


def _clamp(value: int) -> int:
    return max(0, min(255, int(value)))


def _find_device() -> str | None:
    """Return the first backlight device directory, or None."""
    for path in sorted(glob.glob(_BACKLIGHT_DIR_GLOB)):
        if os.path.exists(os.path.join(path, "brightness")):
            return path
    return None


def _max_brightness(device: str) -> int:
    """Return the driver's max_brightness (falls back to 255 if unreadable)."""
    try:
        with open(os.path.join(device, "max_brightness")) as fh:
            mx = int(fh.read().strip())
        return mx if mx > 0 else 255
    except (OSError, ValueError):
        return 255


def apply(value: int) -> dict:
    """Write a 0–255 level to the backlight, scaled to the driver's range.

    Returns {"ok": bool, "value": clamped 0–255 level, "detail": str}. Never raises.
    """
    global last_result
    last_result = _apply(value)
    return last_result


def status() -> dict:
    """Can brightness be controlled right now? Never raises, needs no root.

    problem is None when all is well (or when there is simply no backlight, e.g.
    Docker/dev), else a short human-readable reason for the Settings screen.
    """
    device = _find_device()
    if device is None:
        return {"device": None, "writable": False, "problem": None}
    writable = os.access(os.path.join(device, "brightness"), os.W_OK)
    problem = None
    if not writable:
        problem = "Backlight is read-only for the dash — brightness changes will not reach the screen"
    elif last_result is not None and not last_result["ok"]:
        problem = f"Last brightness write failed: {last_result['detail']}"
    return {"device": device, "writable": writable, "problem": problem}


def _apply(value: int) -> dict:
    value = _clamp(value)
    device = _find_device()
    if device is None:
        return {"ok": False, "value": value, "detail": "no backlight device"}
    raw = round(value * _max_brightness(device) / 255)
    try:
        with open(os.path.join(device, "brightness"), "w") as fh:
            fh.write(str(raw))
    except OSError as exc:
        log.warning("Could not write backlight (%s): %s", device, exc)
        return {"ok": False, "value": value, "detail": str(exc)}
    return {"ok": True, "value": value, "detail": f"{device} <- {raw}"}


def saved_level() -> int:
    """The level the DB says should be showing right now (mode-dependent)."""
    mode = db.get_setting("brightness_mode", "day")
    key = "brightness_night" if mode == "night" else "brightness_day"
    return _clamp(db.get_int(key, 180 if key == "brightness_day" else 80))


async def apply_on_startup() -> None:
    """Apply the saved level, retrying until the backlight device appears."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _STARTUP_DEADLINE_S
    while True:
        result = await asyncio.to_thread(lambda: apply(saved_level()))
        if result["ok"]:
            log.info("Backlight set at startup: %s", result["detail"])
            return
        if result["detail"] != "no backlight device":
            return  # device present but write failed (permissions) — retry won't help
        if loop.time() >= deadline:
            log.info("No backlight device found; skipping startup brightness (Docker/dev)")
            return
        await asyncio.sleep(_STARTUP_RETRY_S)
