"""
TD5 ECU live data decoder — per-PID response parsers.

Each function accepts the stripped payload bytes returned by
TD5Session.read_local_id() (header and checksum already removed) and returns
the decoded engineering-unit value(s).

Byte layouts and formulas are verified against three independent sources:
  github.com/EA2EGA/Ekaitza_Itzali   (main.py, main_menu.py)
  github.com/hairyone/pyTD5Tester    (TD5Tester.py)
  github.com/BennehBoy/LRDuinoTD5   (td5comm.cpp)

Known-confirmed formulas are marked [CONFIRMED].
Items that still need empirical validation on a running engine are [VERIFY].
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class EngineData:
    """Engineering-unit snapshot of all parameters polled from the TD5 ECU."""
    rpm:               float
    coolant_temp_c:    float
    inlet_air_temp_c:  float
    boost_bar:         float
    throttle_pct:      float
    battery_v:         float
    road_speed_kph:    float
    fuel_temp_c:       float


# ── PID 0x09 — RPM ─────────────────────────────────────────────────────────────
# Payload: [RPM_HIGH, RPM_LOW]
# [CONFIRMED] raw 16-bit value = RPM. No division factor.

def decode_rpm(payload: bytes) -> Optional[float]:
    if len(payload) < 2:
        return None
    raw = (payload[0] << 8) | payload[1]
    return float(raw)


# ── PID 0x1A — Temperatures ────────────────────────────────────────────────────
# Payload: 14+ bytes. Each temperature occupies 4 bytes (2 data + 2 unknown).
# Encoding: Kelvin × 10 as a 16-bit unsigned integer.
# [CONFIRMED] formula: temp_C = int16(payload[n:n+2]) / 10.0 - 273.2
#
# Byte offsets:
#   [0:2]  = coolant temperature
#   [4:6]  = inlet air temperature
#   [8:10] = external temperature (not shown on dashboard)
#   [12:14]= fuel temperature

def _decode_kelvin10(payload: bytes, offset: int) -> Optional[float]:
    """Decode a Kelvin×10 temperature at the given offset."""
    if len(payload) < offset + 2:
        return None
    raw = (payload[offset] << 8) | payload[offset + 1]
    return round(raw / 10.0 - 273.2, 1)

def decode_coolant_temp(payload: bytes) -> Optional[float]:
    return _decode_kelvin10(payload, 0)

def decode_air_temp(payload: bytes) -> Optional[float]:
    return _decode_kelvin10(payload, 4)

def decode_fuel_temp(payload: bytes) -> Optional[float]:
    return _decode_kelvin10(payload, 12)


# ── PID 0x1C — MAP / MAF ───────────────────────────────────────────────────────
# Payload: 4+ bytes. Two MAP readings (primary and secondary).
# Encoding: bar absolute × 10000 as a 16-bit integer.
# [CONFIRMED] formula: map_bar = int16(payload[0:2]) / 10000.0
# Gauge pressure = absolute − ambient (≈ 1.01325 bar at sea level).
#
# [VERIFY] which MAP reading (index 0 or 1) is the turbo manifold pressure.
#   At idle MAP ≈ 0.3–0.4 bar absolute; WOT peak ≈ 1.8–2.2 bar absolute on TD5.
#   Use MAP1 (offset 0) as primary; compare both empirically.

def decode_boost(payload: bytes) -> Optional[float]:
    if len(payload) < 2:
        return None
    raw = (payload[0] << 8) | payload[1]
    bar_absolute = raw / 10000.0
    bar_gauge    = bar_absolute - 1.01325
    return round(max(0.0, bar_gauge), 3)


# ── PID 0x10 — Battery / system voltage ────────────────────────────────────────
# Payload: 2+ bytes.
# Encoding: millivolts as a 16-bit integer (i.e. int16 / 1000.0 = volts).
# [CONFIRMED] formula: volts = int16(payload[0:2]) / 1000.0
# [VERIFY] at 12.6V resting, expect raw ≈ 12600.

def decode_battery(payload: bytes) -> Optional[float]:
    if len(payload) < 2:
        return None
    raw = (payload[0] << 8) | payload[1]
    return round(raw / 1000.0, 2)


# ── PID 0x0D — Road speed ──────────────────────────────────────────────────────
# Payload: 1 byte.
# [CONFIRMED] formula: raw = kph (single byte, integer).

def decode_speed(payload: bytes) -> Optional[float]:
    if len(payload) < 1:
        return None
    return float(payload[0])


# ── PID 0x1B — Throttle pedal position ────────────────────────────────────────
# Payload: 10 bytes.
# The TD5 throttle pedal has two dual-track pots (4 outputs total).
# [CONFIRMED] byte layout and encoding:
#   [0:2]  P1 — primary pot track A      int16 / 1000.0 = volts
#   [2:4]  P2 — primary pot track B      int16 / 1000.0 = volts
#   [4:6]  P3 — secondary pot track A    int16 / 100.0  = volts  (different scale)
#   [6:8]  P4 — secondary pot track B    int16 / 100.0  = volts
#   [8:10] Supply voltage               int16 / 1000.0 = volts  (≈ 5 V)
#
# [VERIFY] throttle percentage derivation:
#   pct = (P1 / supply) * 100  uses ratiometric calculation against supply rail.
#   Full closed ≈ 0.5 V, full open ≈ 4.5 V with 5 V supply → 10%–90% of supply.
#   Exact calibration requires measurement at pedal stops on the vehicle.

def decode_throttle(payload: bytes) -> Optional[float]:
    if len(payload) < 10:
        return None
    p1     = ((payload[0] << 8) | payload[1]) / 1000.0   # volts
    supply = ((payload[8] << 8) | payload[9]) / 1000.0   # volts
    if supply < 0.5:
        return 0.0   # guard against divide-by-zero at startup
    pct = (p1 / supply) * 100.0
    return round(min(100.0, max(0.0, pct)), 1)
