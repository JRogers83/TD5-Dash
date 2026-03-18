import asyncio
from ws_hub import ConnectionManager

# Static mock data — replaced topic-by-topic as real services come online.
_MOCK: dict = {
    "engine": {
        "rpm": 850,
        "coolant_temp_c": 88,
        "inlet_air_temp_c": 22,
        "boost_bar": 0.0,
        "throttle_pct": 0,
        "battery_v": 14.1,
        "road_speed_kph": 0,
        "fuel_temp_c": 35,
    },
    "victron": {
        "soc_pct": 87,
        "voltage_v": 13.2,
        "current_a": -2.1,
        "solar_yield_wh": 423,
        "charge_state": "float",
        "orion_state": "bulk",
        "orion_input_v": 14.3,
    },
    "spotify": {
        "connected": True,
        "playing": True,
        "track": "The Chain",
        "artist": "Fleetwood Mac",
        "album": "Rumours",
        "album_art_url": None,
        "progress_s": 152,
        "duration_s": 271,
        "device_name": "Defender",
    },
    "system": {
        "brightness":     180,
        "override_mode":  False,
        "wifi_connected": False,
        "bt_connected":   False,
        "cpu_temp_c":     45.2,
        "cpu_load_pct":   12.5,
        "ram_usage_pct":  38.2,
        "disk_usage_pct": 22.1,
        "uptime_s":       8040,
        "throttled":      False,
    },
    "starlink": {
        "state":           "connected",
        "down_mbps":       187.3,
        "up_mbps":         12.4,
        "latency_ms":      42,
        "ping_drop_pct":   0.2,
        "obstructed":      False,
        "obstruction_pct": 3.0,
        "roaming":         False,
        "uptime_s":        8040,   # 2h 14m
        "alerts":          [],
    },
    "gps": {
        "lat": 52.6309,
        "lon": 1.2974,
        "alt": 142.0,
    },
    "weather": {
        "current": {
            "temp_c":       9.2,
            "humidity_pct": 82,
            "weather_code": 61,   # light rain
            "wind_kph":     22.0,
        },
        "forecast": [
            {"day": "Tue", "weather_code":  3, "high_c": 13.0, "low_c":  8.0},
            {"day": "Wed", "weather_code": 63, "high_c": 11.0, "low_c":  7.0},
            {"day": "Thu", "weather_code":  1, "high_c": 15.0, "low_c":  9.0},
            {"day": "Fri", "weather_code":  0, "high_c": 17.0, "low_c": 10.0},
        ],
        "location": "Norwich, UK",
    },
}


async def _topic_loop(
    manager: ConnectionManager, topic: str, interval_s: float = 1.0
) -> None:
    while True:
        await manager.broadcast({"type": topic, "data": _MOCK[topic]})
        await asyncio.sleep(interval_s)


async def mock_engine_loop(manager: ConnectionManager, interval_s: float = 1.0) -> None:
    await _topic_loop(manager, "engine", interval_s)


async def mock_victron_loop(manager: ConnectionManager, interval_s: float = 1.0) -> None:
    await _topic_loop(manager, "victron", interval_s)


async def mock_spotify_loop(manager: ConnectionManager, interval_s: float = 1.0) -> None:
    await _topic_loop(manager, "spotify", interval_s)


async def mock_system_loop(manager: ConnectionManager, interval_s: float = 1.0) -> None:
    await _topic_loop(manager, "system", interval_s)


async def mock_starlink_loop(manager: ConnectionManager, interval_s: float = 2.0) -> None:
    """Publishes both starlink status and GPS together, mirroring the real service."""
    while True:
        await manager.broadcast({"type": "starlink", "data": _MOCK["starlink"]})
        await manager.broadcast({"type": "gps",      "data": _MOCK["gps"]})
        await asyncio.sleep(interval_s)


async def mock_weather_loop(manager: ConnectionManager, interval_s: float = 5.0) -> None:
    await _topic_loop(manager, "weather", interval_s)
