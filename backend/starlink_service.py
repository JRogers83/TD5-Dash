"""
Starlink Mini service — gRPC status poller.

Polls the Starlink dish at 192.168.100.1:9200 every POLL_INTERVAL_S seconds
and publishes two WebSocket topics:

  {"type": "starlink", "data": {state, down_mbps, up_mbps, ...}}
  {"type": "gps",      "data": {lat, lon, alt}}     ← only when GPS enabled

The gRPC API is unauthenticated and available on the local network only.
The Pi is assumed to be connected to the Starlink Mini's integrated Wi-Fi,
which makes 192.168.100.1 natively reachable without any static routes.

GPS requires one-time opt-in in the Starlink app:
  Settings → Advanced → Debug Data → Starlink Location → Allow on local network
The GPS section of the Starlink view will read "Not enabled" until this is done.

Configuration:
  STARLINK_HOST          gRPC target (host:port)  default: 192.168.100.1:9200
  STARLINK_POLL_INTERVAL Poll interval in seconds  default: 2

⚠  NOTE: This service has not yet been tested against real Starlink hardware.
   The starlink_grpc API surface (field names, return types) should be verified
   against a live dish and the sparky8512/starlink-grpc-tools README before
   assuming correctness. Suspect areas are annotated with # hw-verify.
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

HOST          = os.getenv("STARLINK_HOST",          "192.168.100.1:9200")
POLL_INTERVAL = int(os.getenv("STARLINK_POLL_INTERVAL", "2"))
RETRY_DELAY_S = 10.0

# Alert field names from the gRPC API — iterated to build the active-alerts list.
# alert_roaming is excluded here; it is surfaced as a dedicated badge instead.
_ALERT_FIELDS = [
    "alert_motors_stuck",
    "alert_thermal_throttle",
    "alert_thermal_shutdown",
    "alert_mast_not_near_vertical",
    "alert_unexpected_location",
    "alert_slow_ethernet_speeds",
    "alert_install_pending",
    "alert_is_heating",
    "alert_power_supply_thermal_throttle",
    "alert_is_power_save_idle",
]

# Payload broadcast when the dish is unreachable.
_OFFLINE_DATA: dict = {
    "state":           "offline",
    "down_mbps":       0.0,
    "up_mbps":         0.0,
    "latency_ms":      0,
    "ping_drop_pct":   0.0,
    "obstructed":      False,
    "obstruction_pct": 0.0,
    "roaming":         False,
    "uptime_s":        0,
    "alerts":          [],
}


def _to_dict(obj) -> dict:
    """Convert a starlink_grpc namedtuple or plain dict to a plain dict."""
    if hasattr(obj, "_asdict"):
        return obj._asdict()
    return dict(obj)


def _poll_status(ctx) -> dict:
    """
    Blocking status poll — runs in the worker thread.

    Returns a dict ready for WebSocket broadcast.
    """
    import starlink_grpc  # imported here so the main process doesn't error on import

    status, obstruction, alerts = starlink_grpc.status_data(ctx)  # hw-verify: 3-tuple

    s = _to_dict(status)
    o = _to_dict(obstruction)
    a = _to_dict(alerts)

    # State comes as e.g. "CONNECTED" or "DishState.CONNECTED" — normalise.  # hw-verify
    state_raw = str(s.get("state", "unknown")).split(".")[-1].upper()
    state = state_raw.lower() if state_raw in {
        "CONNECTED", "SEARCHING", "BOOTING", "SLEEPING", "OFFLINE", "UNKNOWN"
    } else "unknown"

    active_alerts = [f for f in _ALERT_FIELDS if a.get(f, False)]

    return {
        "state":           state,
        "down_mbps":       round(s.get("downlink_throughput_bps", 0) / 1_000_000, 1),
        "up_mbps":         round(s.get("uplink_throughput_bps",   0) / 1_000_000, 1),
        "latency_ms":      round(s.get("pop_ping_latency_ms",      0)),
        "ping_drop_pct":   round(s.get("pop_ping_drop_rate",       0) * 100, 1),
        "obstructed":      bool(o.get("currently_obstructed",      False)),
        "obstruction_pct": round(o.get("fraction_obstructed",      0)   * 100, 1),
        "roaming":         bool(a.get("alert_roaming",             False)),
        "uptime_s":        int(s.get("uptime",                     0)),
        "alerts":          active_alerts,
    }


def _poll_gps(ctx) -> dict | None:
    """
    Blocking GPS poll — runs in the worker thread.

    Returns {lat, lon, alt} if GPS is enabled and has a fix, else None.
    (0.0, 0.0) means GPS is disabled or has no lock yet — we treat it as None.
    """
    import starlink_grpc

    try:
        location = _to_dict(starlink_grpc.location_data(ctx))  # hw-verify
        lat = float(location.get("latitude",  0.0) or 0.0)
        lon = float(location.get("longitude", 0.0) or 0.0)
        alt = float(location.get("altitude",  0.0) or 0.0)
        if lat == 0.0 and lon == 0.0:
            return None   # GPS disabled or no satellite lock
        return {"lat": round(lat, 6), "lon": round(lon, 6), "alt": round(alt, 0)}
    except Exception:
        return None   # GPS not enabled raises; treat as not available


# ── Blocking poll loop (runs in a worker thread) ───────────────────────────────

def _poll_loop(manager: ConnectionManager, loop: asyncio.AbstractEventLoop) -> None:
    """
    Blocking poll loop — runs in a dedicated ThreadPoolExecutor thread.

    Re-establishes the gRPC channel on any failure and retries indefinitely.
    Broadcasts the offline payload while the dish is unreachable so the
    frontend shows the correct disconnected state rather than going stale.
    """
    import starlink_grpc

    def _broadcast(payload: dict) -> None:
        asyncio.run_coroutine_threadsafe(manager.broadcast(payload), loop)

    while True:
        log.info("Connecting to Starlink gRPC at %s …", HOST)
        try:
            ctx = starlink_grpc.ChannelContext(target=HOST)
            log.info("Starlink gRPC channel open. Polling every %d s.", POLL_INTERVAL)

            while True:
                try:
                    data = _poll_status(ctx)
                    _broadcast({"type": "starlink", "data": data})

                    gps = _poll_gps(ctx)
                    if gps:
                        _broadcast({"type": "gps", "data": gps})
                        # Update shared GPS state so weather_service can use live coordinates
                        shared_state.gps_lat = gps["lat"]
                        shared_state.gps_lon = gps["lon"]

                except starlink_grpc.GrpcError as exc:
                    log.warning("Starlink gRPC error: %s — will retry", exc)
                    _broadcast({"type": "starlink", "data": _OFFLINE_DATA})
                    break   # break inner loop → re-open channel

                time.sleep(POLL_INTERVAL)

        except Exception:
            log.exception("Starlink channel error — retrying in %.0f s", RETRY_DELAY_S)
            _broadcast({"type": "starlink", "data": _OFFLINE_DATA})

        time.sleep(RETRY_DELAY_S)


# ── Async entry point ──────────────────────────────────────────────────────────

async def broadcast_loop(manager: ConnectionManager) -> None:
    """
    Async entry point — called from main.py lifespan when STARLINK_MOCK=0.

    Runs the blocking gRPC I/O in a dedicated background thread so
    FastAPI's event loop remains free for WebSocket handling.
    """
    loop     = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="starlink")
    await loop.run_in_executor(executor, _poll_loop, manager, loop)
