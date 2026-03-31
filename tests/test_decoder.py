"""
Tests for backend/obd/decoder.py — TD5 ECU live data decoders.

Test vectors confirmed on a real vehicle (2026-03-21).
"""

import pytest
from obd.decoder import (
    decode_rpm,
    decode_battery,
    decode_speed,
    decode_faults,
    decode_coolant_temp,
    decode_air_temp,
    decode_external_temp,
    decode_fuel_temp,
    decode_boost,
    decode_throttle,
    _decode_kelvin10,
    EngineData,
)


# ── RPM ──────────────────────────────────────────────────────────────────────

class TestDecodeRPM:
    def test_idle_768(self):
        """Vehicle-confirmed: 768 RPM at idle."""
        assert decode_rpm(b'\x03\x00') == 768.0

    def test_zero(self):
        assert decode_rpm(b'\x00\x00') == 0.0

    def test_max_16bit(self):
        assert decode_rpm(b'\xFF\xFF') == 65535.0

    def test_too_short(self):
        assert decode_rpm(b'\x03') is None

    def test_empty(self):
        assert decode_rpm(b'') is None


# ── Battery ──────────────────────────────────────────────────────────────────

class TestDecodeBattery:
    def test_vehicle_confirmed_14_23(self):
        """Vehicle-confirmed: 0x3793 = 14227 mV = 14.23 V."""
        assert decode_battery(b'\x37\x93') == 14.23

    def test_12v_nominal(self):
        # 12000 mV = 0x2EE0
        assert decode_battery(b'\x2E\xE0') == 12.0

    def test_too_short(self):
        assert decode_battery(b'\x37') is None

    def test_empty(self):
        assert decode_battery(b'') is None


# ── Speed ────────────────────────────────────────────────────────────────────

class TestDecodeSpeed:
    def test_stationary(self):
        """Vehicle-confirmed: 0 kph when stationary."""
        assert decode_speed(b'\x00') == 0.0

    def test_highway(self):
        assert decode_speed(b'\x70') == 112.0

    def test_max(self):
        assert decode_speed(b'\xFF') == 255.0

    def test_empty(self):
        assert decode_speed(b'') is None


# ── Fault Codes ──────────────────────────────────────────────────────────────

class TestDecodeFaults:
    def test_vehicle_confirmed_two_faults(self):
        """Vehicle-confirmed: 1D BB 0C 84 = two faults.
        0x1D=29: group=4 sub=6 = ambient air temp (L), Defender false positive
        0x0C=12: group=2 sub=5 = reference voltage (L), Defender false positive
        """
        result = decode_faults(b'\x1D\xBB\x0C\x84')
        assert len(result) == 2
        assert result[0]['code'] == '4-6'
        assert result[0]['count'] == 0xBB
        assert result[0]['expected'] is True
        assert result[1]['code'] == '2-5'
        assert result[1]['count'] == 0x84
        assert result[1]['expected'] is True

    def test_no_faults(self):
        assert decode_faults(b'') == []

    def test_single_fault(self):
        result = decode_faults(b'\x1D\xBB')
        assert len(result) == 1
        assert result[0]['code'] == '4-6'
        assert result[0]['count'] == 0xBB

    def test_all_pairs_decoded(self):
        """All 2-byte pairs are decoded — no zero filtering."""
        result = decode_faults(b'\x00\x01\x1D\xBB')
        assert len(result) == 2

    def test_odd_byte_count(self):
        """Trailing single byte is ignored (need pairs)."""
        result = decode_faults(b'\x1D\xBB\x0C')
        assert len(result) == 1
        assert result[0]['code'] == '4-6'

    def test_description_present(self):
        result = decode_faults(b'\x1D\xBB')
        assert 'description' in result[0]
        assert len(result[0]['description']) > 0


# ── Temperature Decoders ────────────────────────────────────────────────────

class TestDecodeTemperatures:
    def test_kelvin10_known_value(self):
        """2912 = 291.2 K = 18.0 C"""
        raw = 2912
        payload = bytes([(raw >> 8) & 0xFF, raw & 0xFF])
        result = _decode_kelvin10(payload, 0)
        assert result == 18.0

    def test_kelvin10_freezing(self):
        """2732 = 273.2 K = 0.0 C"""
        raw = 2732
        payload = bytes([(raw >> 8) & 0xFF, raw & 0xFF])
        assert _decode_kelvin10(payload, 0) == 0.0

    def test_kelvin10_too_short(self):
        assert _decode_kelvin10(b'\x0B', 0) is None

    def test_coolant_temp(self):
        """Coolant is at offset 0 in the 16-byte temps payload."""
        # 2912 = 291.2 K = 18.0 C at offset 0
        raw = 2912
        payload = bytes([(raw >> 8) & 0xFF, raw & 0xFF]) + b'\x00' * 14
        assert decode_coolant_temp(payload) == 18.0

    def test_air_temp(self):
        """Inlet air temp is at offset 4."""
        # 2879 = 287.9 K = 14.7 C at offset 4
        raw = 2879
        payload = b'\x00' * 4 + bytes([(raw >> 8) & 0xFF, raw & 0xFF]) + b'\x00' * 10
        assert decode_air_temp(payload) == 14.7

    def test_external_temp(self):
        """External temp is at offset 8."""
        # 2860 = 286.0 K = 12.8 C at offset 8
        raw = 2860
        payload = b'\x00' * 8 + bytes([(raw >> 8) & 0xFF, raw & 0xFF]) + b'\x00' * 6
        assert decode_external_temp(payload) == 12.8

    def test_fuel_temp(self):
        """Fuel temp is at offset 12."""
        # 2873 = 287.3 K = 14.1 C at offset 12
        raw = 2873
        payload = b'\x00' * 12 + bytes([(raw >> 8) & 0xFF, raw & 0xFF]) + b'\x00' * 2
        assert decode_fuel_temp(payload) == 14.1


# ── Boost / MAP ──────────────────────────────────────────────────────────────

class TestDecodeBoost:
    def test_atmospheric_no_boost(self):
        """MAP at atmospheric pressure (~1.01 bar) should clamp to 0 gauge."""
        # 10125 = 1.0125 bar absolute (confirmed vehicle idle)
        raw = 10125
        payload = bytes([(raw >> 8) & 0xFF, raw & 0xFF])
        assert decode_boost(payload) == 0.0

    def test_positive_boost(self):
        """1.5 bar absolute = ~0.487 bar gauge."""
        raw = 15000  # 1.5 bar
        payload = bytes([(raw >> 8) & 0xFF, raw & 0xFF])
        result = decode_boost(payload)
        assert result == pytest.approx(0.487, abs=0.001)

    def test_too_short(self):
        assert decode_boost(b'\x27') is None

    def test_empty(self):
        assert decode_boost(b'') is None


# ── Throttle ─────────────────────────────────────────────────────────────────

class TestDecodeThrottle:
    def test_vehicle_confirmed_idle(self):
        """Vehicle-confirmed: P1=910mV, Supply=5016mV = 18.1%."""
        # P1 = 910 = 0x038E, Supply = 5016 = 0x1398
        # P2, P3, P4 don't affect the result — fill with zeros
        p1 = 910
        supply = 5016
        payload = (
            bytes([(p1 >> 8) & 0xFF, p1 & 0xFF])     # P1 [0:2]
            + b'\x00\x00'                              # P2 [2:4]
            + b'\x00\x00'                              # P3 [4:6]
            + b'\x00\x00'                              # P4 [6:8]
            + bytes([(supply >> 8) & 0xFF, supply & 0xFF])  # Supply [8:10]
        )
        # decode_throttle applies calibration; decode_throttle_raw returns the raw %
        from obd.decoder import decode_throttle_raw
        result = decode_throttle_raw(payload)
        assert result == pytest.approx(18.1, abs=0.1)
        # Calibrated result should be near 0% at idle (default idle=18.0)
        calibrated = decode_throttle(payload)
        assert calibrated == pytest.approx(0.1, abs=0.5)

    def test_full_throttle(self):
        """Supply voltage equals P1 should give 100%."""
        val = 5000
        payload = (
            bytes([(val >> 8) & 0xFF, val & 0xFF])
            + b'\x00' * 6
            + bytes([(val >> 8) & 0xFF, val & 0xFF])
        )
        assert decode_throttle(payload) == 100.0

    def test_zero_supply_guard(self):
        """Low supply voltage should return 0.0, not divide-by-zero."""
        payload = b'\x03\x8E' + b'\x00' * 6 + b'\x00\x00'
        assert decode_throttle(payload) == 0.0

    def test_too_short(self):
        assert decode_throttle(b'\x03\x8E\x00') is None


# ── EngineData dataclass ────────────────────────────────────────────────────

class TestEngineData:
    def test_default_fault_codes(self):
        """fault_codes defaults to empty list."""
        data = EngineData(
            rpm=768.0, coolant_temp_c=18.0, inlet_air_temp_c=14.7,
            external_temp_c=12.8, boost_bar=0.0, throttle_pct=18.1,
            battery_v=14.23, road_speed_kph=0.0, fuel_temp_c=14.1,
        )
        assert data.fault_codes == []
        assert data.rpm == 768.0
