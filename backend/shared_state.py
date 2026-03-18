"""
Shared mutable state between backend services.

Module-level variables are safe to read/write from async coroutines running
in the same event loop (FastAPI's single-threaded asyncio loop). No locking
is needed as long as no threaded service writes these synchronously (Starlink
and OBD run blocking I/O in ThreadPoolExecutor threads; they must use
asyncio.run_coroutine_threadsafe for writes, or write only from the async layer).

Currently holds:
  gps_lat / gps_lon    — most recent GPS fix from Starlink (None until first fix)
  override_mode        — CarPiHAT override switch state (leisure battery bypass)
  sidelights_on        — CarPiHAT sidelights input state (auto-brightness trigger)
"""

from __future__ import annotations

# GPS (set by starlink_service when a fix is available)
gps_lat: float | None = None
gps_lon: float | None = None

# CarPiHAT GPIO state (set by carpihat_service)
override_mode: bool = False
sidelights_on: bool = False
