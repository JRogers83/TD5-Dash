"""
Tests for backend/mock_service.py — verify mock payloads match the
expected WebSocket message schema defined in CLAUDE.md.
"""

import pytest
from mock_service import _MOCK


# ── Schema definitions ───────────────────────────────────────────────────────
# Expected fields and types for each topic, derived from the WebSocket
# message format specification in CLAUDE.md.

EXPECTED_SCHEMAS = {
    "engine": {
        "rpm":              (int, float),
        "coolant_temp_c":   (int, float),
        "inlet_air_temp_c": (int, float),
        "external_temp_c":  (int, float),
        "boost_bar":        (int, float),
        "throttle_pct":     (int, float),
        "battery_v":        (int, float),
        "road_speed_kph":   (int, float),
        "fuel_temp_c":      (int, float),
        "fault_codes":      list,
    },
    "victron": {
        "soc_pct":        (int, float),
        "voltage_v":      (int, float),
        "current_a":      (int, float),
        "solar_yield_wh": (int, float),
        "charge_state":   str,
        "orion_state":    str,
        "orion_input_v":  (int, float),
    },
    "spotify": {
        "connected":     bool,
        "playing":       bool,
        "track":         str,
        "artist":        str,
        "album":         str,
        "album_art_url": (str, type(None)),
        "progress_s":    (int, float),
        "duration_s":    (int, float),
        "device_name":   str,
    },
    "system": {
        "brightness":     (int, float),
        "override_mode":  bool,
        "wifi_connected": bool,
        "bt_connected":   bool,
        "cpu_temp_c":     (int, float),
        "cpu_load_pct":   (int, float),
        "ram_usage_pct":  (int, float),
        "disk_usage_pct": (int, float),
        "uptime_s":       (int, float),
        "throttled":      bool,
    },
    "starlink": {
        "state":           str,
        "down_mbps":       (int, float),
        "up_mbps":         (int, float),
        "latency_ms":      (int, float),
        "ping_drop_pct":   (int, float),
        "obstructed":      bool,
        "obstruction_pct": (int, float),
        "roaming":         bool,
        "uptime_s":        (int, float),
        "alerts":          list,
    },
    "weather": {
        "current":  dict,
        "forecast": list,
        "location": str,
    },
}


# ── Topic completeness ──────────────────────────────────────────────────────

class TestMockTopicCompleteness:
    def test_all_topics_present(self):
        """Mock data must include every topic from the WebSocket spec."""
        for topic in EXPECTED_SCHEMAS:
            assert topic in _MOCK, f"Missing mock topic: {topic}"

    def test_no_extra_topics(self):
        """Mock data should not contain topics not in the spec."""
        for topic in _MOCK:
            assert topic in EXPECTED_SCHEMAS, f"Unexpected mock topic: {topic}"


# ── Per-topic field validation ──────────────────────────────────────────────

class TestEngineFields:
    def test_has_all_required_fields(self):
        for field in EXPECTED_SCHEMAS["engine"]:
            assert field in _MOCK["engine"], f"engine missing field: {field}"

    def test_field_types(self):
        for field, expected_type in EXPECTED_SCHEMAS["engine"].items():
            assert isinstance(_MOCK["engine"][field], expected_type), \
                f"engine.{field} has wrong type: {type(_MOCK['engine'][field])}"

    def test_fault_codes_is_list(self):
        assert isinstance(_MOCK["engine"]["fault_codes"], list)


class TestVictronFields:
    def test_has_all_required_fields(self):
        for field in EXPECTED_SCHEMAS["victron"]:
            assert field in _MOCK["victron"], f"victron missing field: {field}"

    def test_field_types(self):
        for field, expected_type in EXPECTED_SCHEMAS["victron"].items():
            assert isinstance(_MOCK["victron"][field], expected_type), \
                f"victron.{field} has wrong type: {type(_MOCK['victron'][field])}"


class TestSpotifyFields:
    def test_has_all_required_fields(self):
        for field in EXPECTED_SCHEMAS["spotify"]:
            assert field in _MOCK["spotify"], f"spotify missing field: {field}"

    def test_field_types(self):
        for field, expected_type in EXPECTED_SCHEMAS["spotify"].items():
            assert isinstance(_MOCK["spotify"][field], expected_type), \
                f"spotify.{field} has wrong type: {type(_MOCK['spotify'][field])}"


class TestSystemFields:
    def test_has_all_required_fields(self):
        for field in EXPECTED_SCHEMAS["system"]:
            assert field in _MOCK["system"], f"system missing field: {field}"

    def test_field_types(self):
        for field, expected_type in EXPECTED_SCHEMAS["system"].items():
            assert isinstance(_MOCK["system"][field], expected_type), \
                f"system.{field} has wrong type: {type(_MOCK['system'][field])}"


class TestStarlinkFields:
    def test_has_all_required_fields(self):
        for field in EXPECTED_SCHEMAS["starlink"]:
            assert field in _MOCK["starlink"], f"starlink missing field: {field}"

    def test_field_types(self):
        for field, expected_type in EXPECTED_SCHEMAS["starlink"].items():
            assert isinstance(_MOCK["starlink"][field], expected_type), \
                f"starlink.{field} has wrong type: {type(_MOCK['starlink'][field])}"


class TestWeatherFields:
    def test_has_all_required_fields(self):
        for field in EXPECTED_SCHEMAS["weather"]:
            assert field in _MOCK["weather"], f"weather missing field: {field}"

    def test_field_types(self):
        for field, expected_type in EXPECTED_SCHEMAS["weather"].items():
            assert isinstance(_MOCK["weather"][field], expected_type), \
                f"weather.{field} has wrong type: {type(_MOCK['weather'][field])}"

    def test_current_has_required_subfields(self):
        current = _MOCK["weather"]["current"]
        for field in ["temp_c", "humidity_pct", "weather_code", "wind_kph"]:
            assert field in current, f"weather.current missing: {field}"

    def test_forecast_is_nonempty(self):
        assert len(_MOCK["weather"]["forecast"]) > 0

    def test_forecast_entries_have_required_fields(self):
        for entry in _MOCK["weather"]["forecast"]:
            for field in ["day", "weather_code", "high_c", "low_c"]:
                assert field in entry, f"weather.forecast entry missing: {field}"


# ── Value sanity checks ─────────────────────────────────────────────────────

class TestMockValueSanity:
    def test_rpm_reasonable(self):
        assert 0 <= _MOCK["engine"]["rpm"] <= 5000

    def test_battery_reasonable(self):
        assert 10.0 <= _MOCK["engine"]["battery_v"] <= 16.0

    def test_soc_percentage_range(self):
        assert 0 <= _MOCK["victron"]["soc_pct"] <= 100

    def test_brightness_range(self):
        assert 0 <= _MOCK["system"]["brightness"] <= 255

