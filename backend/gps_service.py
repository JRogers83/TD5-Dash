"""
GPS service — polls gpsd for NMEA fix data and broadcasts via WebSocket.

Reads from gpsd using the Python gps library (python3-gps package).
Controlled by GPS_MOCK env var (default 1 = mock, matches other service toggles).
Falls back gracefully when gpsd is unavailable (retries with exponential backoff).

Configuration:
  GPS_MOCK=0       Use real gpsd (requires gpsd running + GPS receiver)
  GPSD_HOST        gpsd hostname (default: localhost)
  GPSD_PORT        gpsd port (default: 2947)

WebSocket message published when fix available:
  {"type": "gps", "data": {"lat": 52.6309, "lon": 1.2974,
                            "speed_kmh": 0.0, "heading_deg": 0.0, "fix": 3}}

WebSocket message when no fix:
  {"type": "gps", "data": {"lat": null, "lon": null,
                            "speed_kmh": null, "heading_deg": null, "fix": 0}}

# hw-verify: /dev/ttyACM0 device path and gpsd auto-detection of u-blox
# UBX-G7020-KT must be confirmed on real hardware (see documentation/pi-setup.md).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

import shared_state
from ws_hub import ConnectionManager

log = logging.getLogger(__name__)

GPSD_HOST    = os.getenv("GPSD_HOST", "localhost")
GPSD_PORT    = int(os.getenv("GPSD_PORT", "2947"))
_RETRY_MIN_S = 5.0
_RETRY_MAX_S = 60.0

_NO_FIX_DATA: dict = {
    "lat":         None,
    "lon":         None,
    "speed_kmh":   None,
    "heading_deg": None,
    "fix":         0,
}


def _parse_tpv(report) -> dict | None:
    """
    Parse a gpsd TPV report object into our GPS data dict.

    Returns None when mode < 2 (no usable position fix).
    Accepts any object with .mode, .lat, .lon, .speed, .track attributes —
    duck-typing makes this unit-testable without a real gpsd connection.

    mode: 0=no data, 1=no fix, 2=2D fix, 3=3D fix
    speed: m/s from gpsd — converted to km/h here
    track: degrees true from north (heading)
    """
    mode = int(report.get('mode', 0) or 0)
    if mode < 2:
        return None
    return {
        "lat":         round(float(report.get('lat',   0.0) or 0.0), 6),
        "lon":         round(float(report.get('lon',   0.0) or 0.0), 6),
        "speed_kmh":   round(float(report.get('speed', 0.0) or 0.0) * 3.6, 1),
        "heading_deg": round(float(report.get('track', 0.0) or 0.0), 1),
        "fix":         mode,
    }


def _poll_loop(manager: ConnectionManager, loop: asyncio.AbstractEventLoop) -> None:
    """Blocking gpsd poll loop — runs in a dedicated ThreadPoolExecutor thread.

    Uses a direct socket connection with gpsd's JSON protocol rather than the
    gps Python library, avoiding version-mismatch issues between the pip package
    and the installed gpsd daemon.
    """
    import socket
    import json as _json

    def _broadcast(data: dict) -> None:
        asyncio.run_coroutine_threadsafe(
            manager.broadcast({"type": "gps", "data": data}), loop
        )

    retry_delay = _RETRY_MIN_S

    while True:
        log.info("Connecting to gpsd at %s:%d …", GPSD_HOST, GPSD_PORT)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10.0)
            sock.connect((GPSD_HOST, GPSD_PORT))
            # Enable JSON streaming
            sock.sendall(b'?WATCH={"enable":true,"json":true}\n')
            log.info("gpsd connected. Polling for GPS fix.")
            retry_delay = _RETRY_MIN_S

            buf = ""
            while True:
                chunk = sock.recv(4096).decode("utf-8", errors="replace")
                if not chunk:
                    raise OSError("gpsd closed connection")
                buf += chunk
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        report = _json.loads(line)
                    except _json.JSONDecodeError:
                        continue
                    if report.get("class") != "TPV":
                        continue

                    data = _parse_tpv(report)
                    if data is None:
                        _broadcast({**_NO_FIX_DATA})
                        shared_state.gps_lat         = None
                        shared_state.gps_lon         = None
                        shared_state.gps_speed_kmh   = None
                        shared_state.gps_heading_deg = None
                        shared_state.gps_fix         = 0
                    else:
                        _broadcast(data)
                        shared_state.gps_lat         = data["lat"]
                        shared_state.gps_lon         = data["lon"]
                        shared_state.gps_speed_kmh   = data["speed_kmh"]
                        shared_state.gps_heading_deg = data["heading_deg"]
                        shared_state.gps_fix         = data["fix"]

        except Exception:
            if retry_delay > _RETRY_MIN_S:
                log.warning("gpsd unavailable — retrying in %.0f s", retry_delay)
            else:
                log.exception("gpsd connection error — retrying in %.0f s", retry_delay)
            _broadcast({**_NO_FIX_DATA})
            shared_state.gps_fix = 0
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, _RETRY_MAX_S)


async def broadcast_loop(manager: ConnectionManager) -> None:
    """Async entry point — called from main.py lifespan when GPS_MOCK=0."""
    loop     = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gps")
    await loop.run_in_executor(executor, _poll_loop, manager, loop)
