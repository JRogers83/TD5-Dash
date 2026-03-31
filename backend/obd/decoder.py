"""
TD5 ECU live data decoder — per-PID response parsers.

Each function accepts the stripped payload bytes returned by
TD5Session.read_local_id() (header and checksum already removed) and returns
the decoded engineering-unit value(s).

All formulas vehicle-confirmed on 2026-03-21 (engine running at idle).
See documentation/TD5-ECU-Confirmed-Protocol.md for the full session trace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EngineData:
    """Engineering-unit snapshot of all parameters polled from the TD5 ECU."""
    rpm:               float
    coolant_temp_c:    float
    inlet_air_temp_c:  float
    external_temp_c:   float
    boost_bar:         float
    throttle_pct:      float
    battery_v:         float
    road_speed_kph:    float
    fuel_temp_c:       float
    fault_codes:       list[int] = field(default_factory=list)


# ── PID 0x09 — RPM ─────────────────────────────────────────────────────────────
# Payload: 2 bytes [RPM_HIGH, RPM_LOW]
# [CONFIRMED] raw 16-bit value = RPM. No division factor.
# Vehicle: 768 RPM at idle.  Only responds with engine running.

def decode_rpm(payload: bytes) -> Optional[float]:
    if len(payload) < 2:
        return None
    raw = (payload[0] << 8) | payload[1]
    return float(raw)


# ── PID 0x1A — Temperatures ────────────────────────────────────────────────────
# Payload: 16 bytes (8 × 16-bit values). Primary temps at 4-byte stride.
# Encoding: Kelvin × 10 as a 16-bit unsigned integer.
# [CONFIRMED] formula: temp_C = int16(payload[n:n+2]) / 10.0 - 273.2
#
# Byte offsets (confirmed):
#   [0:2]  = coolant temperature      (18.1 C on test day, engine just started)
#   [4:6]  = inlet air temperature    (14.7 C)
#   [8:10] = external temperature     (12.8 C)
#   [12:14]= fuel temperature         (14.1 C)
# Alternating positions [2:4], [6:8], [10:12], [14:16] contain related values
# (possibly filtered/averaged readings — exact meaning unconfirmed).

def _decode_kelvin10(payload: bytes, offset: int) -> Optional[float]:
    """Decode a Kelvin x 10 temperature at the given offset."""
    if len(payload) < offset + 2:
        return None
    raw = (payload[offset] << 8) | payload[offset + 1]
    return round(raw / 10.0 - 273.2, 1)

def decode_coolant_temp(payload: bytes) -> Optional[float]:
    return _decode_kelvin10(payload, 0)

def decode_air_temp(payload: bytes) -> Optional[float]:
    return _decode_kelvin10(payload, 4)

def decode_external_temp(payload: bytes) -> Optional[float]:
    return _decode_kelvin10(payload, 8)

def decode_fuel_temp(payload: bytes) -> Optional[float]:
    return _decode_kelvin10(payload, 12)


# ── PID 0x1C — MAP / MAF ───────────────────────────────────────────────────────
# Payload: 8 bytes. Two MAP readings + two MAF values.
# [CONFIRMED] formula: map_bar = int16(payload[0:2]) / 10000.0
# Vehicle: MAP1 = 1.0125 bar, MAP2 = 1.0187 bar at idle (atmospheric).
# Gauge pressure = absolute - ambient (1.01325 bar at sea level).

def decode_boost(payload: bytes) -> Optional[float]:
    if len(payload) < 2:
        return None
    raw = (payload[0] << 8) | payload[1]
    bar_absolute = raw / 10000.0
    bar_gauge    = bar_absolute - 1.01325
    return round(max(0.0, bar_gauge), 3)


# ── PID 0x10 — Battery / system voltage ────────────────────────────────────────
# Payload: 4 bytes (two 16-bit readings, nearly identical).
# [CONFIRMED] formula: volts = int16(payload[0:2]) / 1000.0
# Vehicle: 14.227V / 14.230V with alternator charging.  Only responds with
# engine running.

def decode_battery(payload: bytes) -> Optional[float]:
    if len(payload) < 2:
        return None
    raw = (payload[0] << 8) | payload[1]
    return round(raw / 1000.0, 2)


# ── PID 0x0D — Road speed ──────────────────────────────────────────────────────
# Payload: 1 byte.
# [CONFIRMED] raw = kph (single byte, integer).  Vehicle: 0 kph stationary.

def decode_speed(payload: bytes) -> Optional[float]:
    if len(payload) < 1:
        return None
    return float(payload[0])


# ── PID 0x1B — Throttle pedal position ────────────────────────────────────────
# Payload: 10 bytes (5 × 16-bit values).
# [CONFIRMED] byte layout:
#   [0:2]  P1 — primary pot track A      int16 / 1000.0 = volts
#   [2:4]  P2 — primary pot track B      int16 / 1000.0 = volts
#   [4:6]  P3 — secondary pot track A    int16 / 1000.0 = volts
#   [6:8]  P4 — secondary pot track B    int16 / 1000.0 = volts
#   [8:10] Supply voltage               int16 / 1000.0 = volts  (~ 5 V)
#
# [CONFIRMED] pct = (P1 / supply) * 100.  Vehicle: P1=910mV, Supply=5016mV = 18.1%
# at idle (foot off pedal).  Only responds with engine running.

# Throttle calibration values — read from SQLite settings table at startup.
# decode_throttle_raw returns the raw ratiometric percentage (P1/supply * 100).
# decode_throttle applies linear calibration: (raw - idle) / (wot - idle) * 100.
_throttle_idle: float = 18.0   # default — overridden by db.get_float('throttle_idle')
_throttle_wot:  float = 90.0   # default — overridden by db.get_float('throttle_wot')


def set_throttle_calibration(idle: float, wot: float) -> None:
    """Update throttle calibration values at runtime (called from setup wizard)."""
    global _throttle_idle, _throttle_wot
    _throttle_idle = idle
    _throttle_wot = wot


def decode_throttle_raw(payload: bytes) -> Optional[float]:
    """Decode raw ratiometric throttle percentage (before calibration)."""
    if len(payload) < 10:
        return None
    p1     = ((payload[0] << 8) | payload[1]) / 1000.0   # volts
    supply = ((payload[8] << 8) | payload[9]) / 1000.0   # volts
    if supply < 0.5:
        return 0.0
    pct = (p1 / supply) * 100.0
    return round(min(100.0, max(0.0, pct)), 1)


def decode_throttle(payload: bytes) -> Optional[float]:
    raw = decode_throttle_raw(payload)
    if raw is None:
        return None
    # Apply linear calibration: (raw - idle) / (wot - idle) * 100
    span = _throttle_wot - _throttle_idle
    if span < 1.0:
        return raw  # guard against miscalibration
    calibrated = (raw - _throttle_idle) / span * 100.0
    return round(min(100.0, max(0.0, calibrated)), 1)


# ── PID 0x20 — Stored fault codes ────────────────────────────────────────────
# Payload: 2-byte pairs — [fault_index_byte][occurrence_count_byte]
# [CONFIRMED] Vehicle returned 4 bytes: 1D BB 0C 84
#   → fault 0x1D (index 29) count 0xBB (187) = group 4 sub 6 = ambient air temp (L)
#   → fault 0x0C (index 12) count 0x84 (132) = group 2 sub 5 = reference voltage (L)
# Both are known Defender EU2 false positives (no 4-wire AAT, benign ref voltage).
# Encoding: group = fault_index // 8 + 1, sub = fault_index % 8 + 1

def decode_faults(payload: bytes) -> list[dict]:
    """
    Decode stored fault codes from a PID 0x20 response.

    Returns a list of fault dicts with keys:
        code        — Nanocom notation e.g. "4-6"
        description — human-readable fault text
        count       — occurrence count logged by ECU
        expected    — True if known Defender false positive
    """
    from .dtc_lookup import decode_faults as _decode
    return _decode(payload)
