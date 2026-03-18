"""
System monitoring service — CPU temperature, load, memory, disk, uptime,
backlight brightness, and basic connectivity state.

Broadcasts {"type": "system", ...} every PUBLISH_INTERVAL_S seconds.

All reads use sysfs / procfs paths that exist on Pi OS. On Docker/Windows
the paths are absent, so every read falls back gracefully to None/-1/False
so the service still starts and the Settings view still displays.

Configuration:
  SYSTEM_PUBLISH_INTERVAL  Publish rate in seconds (default 2.0)
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import shutil
import subprocess

import shared_state
from ws_hub import ConnectionManager

log = logging.getLogger(__name__)

PUBLISH_INTERVAL_S = float(os.getenv("SYSTEM_PUBLISH_INTERVAL", "2.0"))

# sysfs paths
_THERMAL_PATH   = "/sys/class/thermal/thermal_zone0/temp"
_BACKLIGHT_GLOB = "/sys/class/backlight/*/brightness"
_WIFI_PATH      = "/sys/class/net/wlan0/operstate"

# CPU load — computed as a delta between successive reads (no sleep needed).
# Module-level state persists between broadcast_loop iterations.
_cpu_prev_idle:  int = 0
_cpu_prev_total: int = 0


def _read_cpu_temp() -> float:
    """Return CPU temperature in °C, or -1.0 if unavailable."""
    try:
        raw = int(open(_THERMAL_PATH).read().strip())
        return round(raw / 1000.0, 1)
    except Exception:
        return -1.0


def _read_cpu_load() -> float:
    """
    Return CPU load % since the last call, or -1.0 if unavailable.

    Reads /proc/stat and computes the delta against the previous sample.
    On the first call returns -1.0 (no previous baseline to diff against).
    Subsequent calls return the average load over PUBLISH_INTERVAL_S.
    """
    global _cpu_prev_idle, _cpu_prev_total
    try:
        with open("/proc/stat") as f:
            parts = f.readline().split()
        idle  = int(parts[4])
        total = sum(int(x) for x in parts[1:])

        d_total = total - _cpu_prev_total
        d_idle  = idle  - _cpu_prev_idle
        _cpu_prev_idle  = idle
        _cpu_prev_total = total

        if d_total == 0 or _cpu_prev_total == 0:
            return -1.0
        return round((1 - d_idle / d_total) * 100, 1)
    except Exception:
        return -1.0


def _read_ram_usage() -> float:
    """Return RAM usage % (used / total), or -1.0 if unavailable."""
    try:
        info: dict[str, int] = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k.strip()] = int(v.split()[0])
        total = info.get("MemTotal", 0)
        avail = info.get("MemAvailable", 0)
        if total == 0:
            return -1.0
        return round((1 - avail / total) * 100, 1)
    except Exception:
        return -1.0


def _read_disk_usage() -> float:
    """Return root filesystem usage %, or -1.0 if unavailable."""
    try:
        usage = shutil.disk_usage("/")
        return round(usage.used / usage.total * 100, 1)
    except Exception:
        return -1.0


def _read_uptime() -> int:
    """Return system uptime in seconds, or -1 if unavailable."""
    try:
        with open("/proc/uptime") as f:
            return int(float(f.read().split()[0]))
    except Exception:
        return -1


def _read_throttle() -> bool | None:
    """
    Return True if the Pi is currently throttled or undervolted.

    Uses vcgencmd get_throttled — Pi-specific binary. The lower nibble of
    the returned hex value encodes current (not historical) state:
      bit 0 — undervoltage detected
      bit 1 — arm frequency capped
      bit 2 — currently throttled
      bit 3 — soft temperature limit active

    Returns None when vcgencmd is absent (Docker / dev / non-Pi).
    """
    try:
        result = subprocess.run(
            ["vcgencmd", "get_throttled"],
            capture_output=True, text=True, timeout=2,
        )
        hex_val = result.stdout.strip().split("=")[1]
        current = int(hex_val, 16) & 0x0F   # bits 0-3 = current state only
        return current != 0
    except Exception:
        return None


def _read_brightness() -> int:
    """Return current backlight brightness (0–255), or -1 if unavailable."""
    try:
        paths = glob.glob(_BACKLIGHT_GLOB)
        if not paths:
            return -1
        return int(open(paths[0]).read().strip())
    except Exception:
        return -1


def _read_wifi() -> bool:
    """Return True if wlan0 operstate is 'up'."""
    try:
        return open(_WIFI_PATH).read().strip() == "up"
    except Exception:
        return False


def _read_bt() -> bool:
    """
    Return True if the Bluetooth adapter is powered on.
    Uses rfkill — available on Pi OS. Falls back to False when absent.
    """
    try:
        result = subprocess.run(
            ["rfkill", "list", "bluetooth"],
            capture_output=True, text=True, timeout=2,
        )
        return "Soft blocked: no" in result.stdout
    except Exception:
        return False


async def broadcast_loop(manager: ConnectionManager) -> None:
    """
    Async entry point — called from main.py lifespan.

    Reads actual system state on every iteration. Falls back gracefully
    when sysfs paths are absent (Docker / non-Pi environment).
    """
    log.info("System service starting (publish every %.1f s).", PUBLISH_INTERVAL_S)

    while True:
        cpu_temp  = _read_cpu_temp()
        cpu_load  = _read_cpu_load()
        ram       = _read_ram_usage()
        disk      = _read_disk_usage()
        uptime    = _read_uptime()
        throttle  = _read_throttle()
        brightness = _read_brightness()
        wifi      = _read_wifi()
        bt        = _read_bt()

        payload: dict = {
            "cpu_temp_c":     cpu_temp  if cpu_temp  >= 0 else None,
            "cpu_load_pct":   cpu_load  if cpu_load  >= 0 else None,
            "ram_usage_pct":  ram       if ram       >= 0 else None,
            "disk_usage_pct": disk      if disk      >= 0 else None,
            "uptime_s":       uptime    if uptime    >= 0 else None,
            "throttled":      throttle,   # None = not available, True/False on Pi
            "brightness":     brightness if brightness >= 0 else None,
            "wifi_connected": wifi,
            "bt_connected":   bt,
            # CarPiHAT state — updated by carpihat_service.monitor_loop() via shared_state
            "override_mode":  shared_state.override_mode,
            "sidelights":     shared_state.sidelights_on,
        }

        await manager.broadcast({"type": "system", "data": payload})
        await asyncio.sleep(PUBLISH_INTERVAL_S)
