"""
Weather service — Open-Meteo API poller.

Fetches current conditions + 4-day forecast every 30 minutes and publishes
to the WebSocket hub. Between fetches, re-broadcasts the cached payload every
60 seconds so newly-connected clients receive weather data immediately.

Uses Open-Meteo (open-meteo.com) — free, no API key required.

Location is set via environment variables, defaulting to Norwich, UK.
This is intentionally the only place location is defined: swap in live GPS
coordinates here (e.g. from a GPSD service) to enable geolocation in a
future phase.

Configuration:
  WEATHER_LAT   Latitude   default: 52.6309  (Norwich, UK)
  WEATHER_LON   Longitude  default: 1.2974
  WEATHER_MOCK  1=mock (default), 0=live API (set in systemd service)
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Optional

import httpx

import shared_state
from ws_hub import ConnectionManager

log = logging.getLogger(__name__)

LAT              = float(os.getenv("WEATHER_LAT",  "52.6309"))
LON              = float(os.getenv("WEATHER_LON",  "1.2974"))
FETCH_INTERVAL_S  = int(os.getenv("WEATHER_FETCH_INTERVAL", "1800"))   # 30 min
PUSH_INTERVAL_S   = 5     # re-broadcast cached data every 5 s so new clients get data quickly
STALE_THRESHOLD_S = 300   # flag data as stale after 5 min without a successful fetch
TIMEOUT_S         = 15

_API_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={lon}"
    "&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m"
    "&daily=weather_code,temperature_2m_max,temperature_2m_min"
    "&timezone=Europe%2FLondon"
    "&forecast_days=5"
    "&wind_speed_unit=kmh"
)


async def _fetch(lat: float, lon: float) -> Optional[dict]:
    """
    Fetch current weather + 4-day forecast from Open-Meteo.
    Returns a dict matching the 'weather' WebSocket payload, or None on failure.
    """
    url = _API_URL.format(lat=lat, lon=lon)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            r = await client.get(url)
            r.raise_for_status()
            raw = r.json()

        cur = raw["current"]
        day = raw["daily"]

        # Build 4-day forecast from daily[1:5] (daily[0] is today)
        forecast = []
        for i in range(1, 5):
            date_str  = day["time"][i]
            day_label = datetime.strptime(date_str, "%Y-%m-%d").strftime("%a")
            forecast.append({
                "day":          day_label,
                "weather_code": day["weather_code"][i],
                "high_c":       round(day["temperature_2m_max"][i], 1),
                "low_c":        round(day["temperature_2m_min"][i], 1),
            })

        return {
            "current": {
                "temp_c":        round(cur["temperature_2m"],       1),
                "humidity_pct":  round(cur["relative_humidity_2m"]),
                "weather_code":  cur["weather_code"],
                "wind_kph":      round(cur["wind_speed_10m"],        1),
            },
            "forecast": forecast,
            # Pass location through so the frontend can display it.
            # A future GPS service can populate this with an actual place name.
            "location": os.getenv("WEATHER_LOCATION", ""),
        }

    except httpx.HTTPError as exc:
        log.warning("Weather fetch HTTP error: %s", exc)
    except Exception:
        log.exception("Weather fetch failed")
    return None


async def broadcast_loop(manager: ConnectionManager) -> None:
    """
    Async entry point — called from main.py when WEATHER_MOCK=0.

    Fetches on startup, then every FETCH_INTERVAL_S seconds.
    Re-broadcasts the cached payload every PUSH_INTERVAL_S seconds so
    newly-connected clients don't wait up to 30 minutes.
    """
    cached:       Optional[dict] = None
    last_fetch    = 0.0
    last_success  = 0.0   # time of the most recent successful fetch

    while True:
        now = asyncio.get_event_loop().time()

        if now - last_fetch >= FETCH_INTERVAL_S:
            # Use live GPS coordinates if Starlink has a fix, else fall back to env vars
            lat = shared_state.gps_lat if shared_state.gps_lat is not None else LAT
            lon = shared_state.gps_lon if shared_state.gps_lon is not None else LON
            log.info("Fetching weather for %.4f, %.4f …", lat, lon)
            result = await _fetch(lat, lon)
            if result:
                cached       = result
                last_fetch   = now
                last_success = now
                log.info(
                    "Weather updated: %.1f°C, code %s",
                    cached["current"]["temp_c"],
                    cached["current"]["weather_code"],
                )
            else:
                # Network unavailable — keep cached data, retry sooner than 30 min.
                last_fetch = now - FETCH_INTERVAL_S + 300   # retry in 5 min
                log.warning("Weather fetch failed — will retry in 5 min")

        # Always broadcast every cycle so newly-connected clients get data
        # within PUSH_INTERVAL_S seconds.
        if cached is None:
            # No data yet since startup.
            await manager.broadcast({"type": "weather", "data": {"loading": True}})
        else:
            # We have data. Mark stale if last successful fetch was >5 min ago
            # so the frontend can show a signal-lost indicator while still
            # displaying the last known values.
            stale   = (now - last_success) > STALE_THRESHOLD_S
            payload = {**cached, "stale": stale}
            await manager.broadcast({"type": "weather", "data": payload})

        await asyncio.sleep(PUSH_INTERVAL_S)
