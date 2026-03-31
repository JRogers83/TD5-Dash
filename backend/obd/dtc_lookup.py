"""
TD5 DTC (Diagnostic Trouble Code) lookup table.

Wire format (PID 0x20 response)
--------------------------------
The ECU returns 2-byte pairs: [fault_index_byte][occurrence_count_byte]

Decoding:
    group = fault_index // 8 + 1
    sub   = fault_index %  8 + 1

The group-sub pair maps to Nanocom display notation, e.g. "4-6".

Sources: TD5SPY (td5spy.co.za), Nanocom community, AULRO forums.

Defender false positives
------------------------
A base Defender TD5 (no A/C, no ABS/SLABS, no tachometer, EU2 engine)
will persistently show harmless logged faults for sensors and outputs
that are simply not fitted. These are flagged with expected=True in the
decode output so the UI can display them without alarming the driver.
"""

from __future__ import annotations

# Mapping of (group, sub) → human-readable description
# (L) = Logged (stored, not currently active)
# (C) = Current / Active fault
_FAULT_TABLE: dict[tuple[int, int], str] = {
    # Group 1 — Output diagnostics (logged)
    (1, 1): "EGR inlet throttle diagnostics (L)",
    (1, 2): "Turbocharger wastegate diagnostics (L)",
    (1, 3): "EGR vacuum diagnostics (L)",
    (1, 4): "Temperature gauge diagnostics (L)",
    (1, 5): "Driver demand problem 1 (L)",
    (1, 6): "Driver demand problem 2 (L)",
    (1, 7): "Air flow circuit (L)",
    (1, 8): "Manifold pressure circuit (L)",

    # Group 2 — Sensor circuit faults (logged low)
    (2, 1): "Inlet air temperature circuit (L)",
    (2, 2): "Fuel temperature circuit (L)",
    (2, 3): "Coolant temperature circuit (L)",
    (2, 4): "Battery voltage (L)",
    (2, 5): "Reference voltage (L)",
    (2, 6): "Ambient air temperature circuit (L)",
    (2, 7): "Driver demand supply problem (L)",
    (2, 8): "Ambient pressure circuit (L)",

    # Group 3 — Output diagnostics (logged, alternate)
    (3, 1): "EGR inlet throttle diagnostics (L)",
    (3, 2): "Turbocharger wastegate diagnostics (L)",
    (3, 3): "EGR vacuum diagnostics (L)",
    (3, 4): "Temperature gauge diagnostics (L)",
    (3, 5): "Driver demand problem 1 (L)",
    (3, 6): "Driver demand problem 2 (L)",
    (3, 7): "Air flow circuit (L)",
    (3, 8): "Manifold pressure circuit (L)",

    # Group 4 — Sensor circuit faults (logged high)
    (4, 1): "Inlet air temperature circuit (L)",
    (4, 2): "Fuel temperature circuit (L)",
    (4, 3): "Coolant temperature circuit (L)",
    (4, 4): "Battery voltage (L)",
    (4, 5): "Reference voltage (L)",
    (4, 6): "Ambient air temperature circuit (L)",
    (4, 7): "Driver demand supply problem (L)",
    (4, 8): "Ambient pressure circuit (L)",

    # Group 5 — Output diagnostics (current)
    (5, 1): "EGR inlet throttle diagnostics (C)",
    (5, 2): "Turbocharger wastegate diagnostics (C)",
    (5, 3): "EGR vacuum diagnostics (C)",
    (5, 4): "Temperature gauge diagnostics (C)",
    (5, 5): "Driver demand problem 1 (C)",
    (5, 6): "Driver demand problem 2 (C)",
    (5, 7): "Air flow circuit (C)",
    (5, 8): "Manifold pressure circuit (C)",

    # Group 6 — Sensor circuit faults (current)
    (6, 1): "Inlet air temperature circuit (C)",
    (6, 2): "Fuel temperature circuit (C)",
    (6, 3): "Coolant temperature circuit (C)",
    (6, 4): "Battery voltage problem (C)",
    (6, 5): "Reference voltage (C)",
    (6, 6): "Ambient air temperature circuit (C)",
    (6, 7): "Driver demand supply problem (C)",
    (6, 8): "Ambient pressure circuit (C)",

    # Group 7 — Output over-temperature (logged)
    (7, 1): "Cruise lamp drive over temperature (L)",
    (7, 2): "Fuel used output drive over temperature (L)",
    (7, 3): "Radiator fan drive over temperature (L)",
    (7, 4): "Active engine mounting over temperature (L)",
    (7, 5): "Turbocharger wastegate short circuit (L)",
    (7, 6): "EGR inlet throttle short circuit (L)",
    (7, 7): "EGR vacuum modulator short circuit (L)",
    (7, 8): "Temperature gauge short circuit (L)",

    # Group 8 — Output over-temperature (logged, second set)
    (8, 1): "Air conditioning fan drive over temperature (L)",
    (8, 2): "Fuel pump drive over temperature (L)",
    (8, 3): "Tachometer drive over temperature (L)",
    (8, 4): "Gearbox/ABS drive over temperature (L)",
    (8, 5): "Air conditioning clutch over temperature (L)",
    (8, 6): "MIL lamp drive over temperature (L)",
    (8, 7): "Glow plug relay drive over temperature (L)",
    (8, 8): "Glow plug lamp drive over temperature (L)",

    # Group 9 — Output open load (logged)
    (9, 1): "Fuel used output drive open load (L)",
    (9, 2): "Cruise lamp drive open load (L)",
    (9, 3): "Radiator fan drive open load (L)",
    (9, 4): "Active engine mounting open load (L)",
    (9, 5): "Turbocharger wastegate open load (L)",
    (9, 6): "EGR inlet throttle open load (L)",
    (9, 7): "EGR vacuum modulator open load (L)",
    (9, 8): "Temperature gauge open load (L)",

    # Group 10 — Output open load (logged, second set)
    (10, 1): "Air conditioning fan drive open load (L)",
    (10, 2): "Fuel pump drive open load (L)",
    (10, 3): "Tachometer open load (L)",
    (10, 4): "Gearbox/ABS drive open load (L)",
    (10, 5): "Air conditioning clutch open load (L)",
    (10, 6): "MIL lamp drive open load (L)",
    (10, 7): "Glow plug lamp drive open load (L)",
    (10, 8): "Glow plug relay drive open load (L)",

    # Group 11 — Output over-temperature (current)
    (11, 1): "Cruise control lamp drive over temperature (C)",
    (11, 2): "Fuel used output drive over temperature (C)",
    (11, 3): "Radiator fan drive over temperature (C)",
    (11, 4): "Active engine mounting over temperature (C)",
    (11, 5): "Turbocharger wastegate short circuit (C)",
    (11, 6): "EGR inlet throttle short circuit (C)",
    (11, 7): "EGR vacuum modulator short circuit (C)",
    (11, 8): "Temperature gauge short circuit (C)",

    # Group 12 — Output open load (current)
    (12, 1): "Air conditioning fan drive open load (C)",
    (12, 2): "Fuel pump drive open load (C)",
    (12, 3): "Tachometer open load (C)",
    (12, 4): "Gearbox/ABS drive open load (C)",
    (12, 5): "Air conditioning clutch open load (C)",
    (12, 6): "MIL lamp drive open load (C)",
    (12, 7): "Glow plug relay drive open load (C)",
    (12, 8): "Glow plug lamp drive open load (C)",

    # Group 13 — Output over-temperature (current, second set)
    (13, 1): "Cruise control lamp drive over temperature (C)",
    (13, 2): "Fuel used output drive over temperature (C)",
    (13, 3): "Radiator fan drive over temperature (C)",
    (13, 4): "Active engine mounting over temperature (C)",
    (13, 5): "Turbocharger wastegate short circuit (C)",
    (13, 6): "EGR inlet throttle short circuit (C)",
    (13, 7): "EGR vacuum modulator short circuit (C)",
    (13, 8): "Temperature gauge short circuit (C)",

    # Group 14 — Output open load (current, second set)
    (14, 1): "Air conditioning fan drive open load (C)",
    (14, 2): "Fuel pump drive open load (C)",
    (14, 3): "Tachometer open load (C)",
    (14, 4): "Gearbox/ABS drive open load (C)",
    (14, 5): "Air conditioning clutch open load (C)",
    (14, 6): "MIL lamp drive open load (C)",
    (14, 7): "Glow plug relay drive open load (C)",
    (14, 8): "Glow plug lamp drive open load (C)",

    # Group 15 — Crank signal (logged)
    (15, 2): "High speed crank signal (L)",

    # Group 16 — Crank signal (logged)
    (16, 2): "High speed crank signal (L)",

    # Group 17 — Crank signal (current)
    (17, 2): "High speed crank signal (C)",

    # Group 19 — CAN bus errors (logged)
    (19, 2): "CAN RX/TX error (L)",
    (19, 3): "CAN TX/RX error (L)",
    (19, 6): "Noisy crank signal detected (L)",
    (19, 8): "CAN bus reset failure (L)",

    # Group 20 — Boost / EGR faults (logged)
    (20, 1): "Turbocharger under-boosting (L)",
    (20, 2): "Turbocharger over-boosting (L)",
    (20, 4): "EGR valve stuck open (L)",
    (20, 5): "EGR valve stuck closed (L)",

    # Group 21 — Driver demand / injector trim (logged)
    (21, 4): "Driver demand 1 out of range (L)",
    (21, 5): "Driver demand 2 out of range (L)",
    (21, 6): "Problem detected with driver demand (L)",
    (21, 7): "Inconsistencies found with driver demand (L)",
    (21, 8): "Injector trim data corrupted (L)",

    # Group 22 — Road speed / cruise control (logged)
    (22, 1): "Road speed signal missing (L)",
    (22, 3): "Vehicle acceleration outside bounds for cruise control (L)",
    (22, 7): "Cruise control resume switch stuck closed (L)",
    (22, 8): "Cruise control set switch stuck closed (L)",

    # Group 23 — CAN bus / boost (current)
    (23, 1): "Excessive CAN bus off events (C)",
    (23, 2): "CAN RX/TX error (C)",
    (23, 3): "CAN TX/RX error (C)",
    (23, 4): "Unable to detect remote CAN mode (C)",
    (23, 5): "Under-boost has occurred on this trip (C)",
    (23, 6): "Noisy crank signal detected (C)",

    # Group 24 — Boost / EGR / auto gearbox (current)
    (24, 1): "Turbocharger under-boosting (C)",
    (24, 2): "Turbocharger over-boosting (C)",
    (24, 3): "Over-boost has occurred this trip (C)",
    (24, 4): "EGR valve stuck open (C)",
    (24, 5): "EGR valve stuck closed (C)",
    (24, 7): "Problem detected with auto gearbox (C)",

    # Group 25 — Driver demand (mixed)
    (25, 4): "Driver demand 1 out of range (L)",
    (25, 5): "Driver demand 2 out of range (L)",
    (25, 6): "Problem detected with driver demand (C)",
    (25, 7): "Inconsistencies found with driver demand (C)",
    (25, 8): "Injector trim data corrupted (C)",

    # Group 26 — Road speed / cruise control (current)
    (26, 1): "Road speed signal missing (C)",
    (26, 2): "Cruise control system problem (C)",
    (26, 3): "Vehicle acceleration outside bounds for cruise control (C)",
    (26, 7): "Cruise control resume switch stuck closed (C)",
    (26, 8): "Cruise control set switch stuck closed (C)",

    # Group 27 — Injector peak charge long (logged)
    (27, 1): "Injector 1 peak charge long (L)",
    (27, 2): "Injector 2 peak charge long (L)",
    (27, 3): "Injector 3 peak charge long (L)",
    (27, 4): "Injector 4 peak charge long (L)",
    (27, 5): "Injector 5 peak charge long (L)",
    (27, 6): "Injector 6 peak charge long (L)",
    (27, 7): "Topside switch failed post-injection (L)",

    # Group 28 — Injector peak charge short (logged)
    (28, 1): "Injector 1 peak charge short (L)",
    (28, 2): "Injector 2 peak charge short (L)",
    (28, 3): "Injector 3 peak charge short (L)",
    (28, 4): "Injector 4 peak charge short (L)",
    (28, 5): "Injector 5 peak charge short (L)",
    (28, 6): "Injector 6 peak charge short (L)",
    (28, 7): "Topside switch failed pre-injection (L)",

    # Group 29 — Injector peak charge long (current)
    (29, 1): "Injector 1 peak charge long (C)",
    (29, 2): "Injector 2 peak charge long (C)",
    (29, 3): "Injector 3 peak charge long (C)",
    (29, 4): "Injector 4 peak charge long (C)",
    (29, 5): "Injector 5 peak charge long (C)",
    (29, 6): "Injector 6 peak charge long (C)",
    (29, 7): "Topside switch failed post-injection (C)",

    # Group 30 — Injector peak charge short (current)
    (30, 1): "Injector 1 peak charge short (C)",
    (30, 2): "Injector 2 peak charge short (C)",
    (30, 3): "Injector 3 peak charge short (C)",
    (30, 4): "Injector 4 peak charge short (C)",
    (30, 5): "Injector 5 peak charge short (C)",
    (30, 6): "Injector 6 peak charge short (C)",
    (30, 7): "Topside switch failed pre-injection (C)",

    # Group 31 — Injector open circuit (logged)
    (31, 1): "Injector 1 open circuit (L)",
    (31, 2): "Injector 2 open circuit (L)",
    (31, 3): "Injector 3 open circuit (L)",
    (31, 4): "Injector 4 open circuit (L)",
    (31, 5): "Injector 5 open circuit (L)",
    (31, 6): "Injector 6 open circuit (L)",

    # Group 32 — Injector short circuit (logged)
    (32, 1): "Injector 1 short circuit (L)",
    (32, 2): "Injector 2 short circuit (L)",
    (32, 3): "Injector 3 short circuit (L)",
    (32, 4): "Injector 4 short circuit (L)",
    (32, 5): "Injector 5 short circuit (L)",
    (32, 6): "Injector 6 short circuit (L)",

    # Group 33 — Injector open circuit (current)
    (33, 1): "Injector 1 open circuit (C)",
    (33, 2): "Injector 2 open circuit (C)",
    (33, 3): "Injector 3 open circuit (C)",
    (33, 4): "Injector 4 open circuit (C)",
    (33, 5): "Injector 5 open circuit (C)",
    (33, 6): "Injector 6 open circuit (C)",

    # Group 34 — Injector short circuit (current)
    (34, 1): "Injector 1 short circuit (C)",
    (34, 2): "Injector 2 short circuit (C)",
    (34, 3): "Injector 3 short circuit (C)",
    (34, 4): "Injector 4 short circuit (C)",
    (34, 5): "Injector 5 short circuit (C)",
    (34, 6): "Injector 6 short circuit (C)",

    # Group 35 — Injector partial short circuit (logged)
    (35, 1): "Injector 1 partial short circuit (L)",
    (35, 2): "Injector 2 partial short circuit (L)",
    (35, 3): "Injector 3 partial short circuit (L)",
    (35, 4): "Injector 4 partial short circuit (L)",
    (35, 5): "Injector 5 partial short circuit (L)",
    (35, 6): "Injector 6 partial short circuit (L)",
}

# Faults that are expected/harmless on a base Defender TD5 with no A/C,
# no ABS/SLABS, no tachometer, and EU2 engine (no 4-wire AAT sensor).
_DEFENDER_EXPECTED: frozenset[tuple[int, int]] = frozenset({
    (4, 6), (6, 6),             # Ambient air temp — no EU3 sensor
    (2, 5), (4, 5),             # Reference voltage — transient/benign
    (9, 5), (11, 5), (13, 5),  # Wastegate open/short — modulator not fitted
    (9, 6), (11, 6), (13, 6),  # EGR inlet throttle — not fitted
    (10, 1), (12, 1), (14, 1), # A/C fan open load
    (10, 3), (12, 3), (14, 3), # Tachometer open load
    (10, 4), (12, 4), (14, 4), # Gearbox/ABS drive open load
    (10, 5), (12, 5), (14, 5), # A/C clutch open load
    (10, 6), (12, 6), (14, 6), # MIL lamp open load
    (9, 4), (11, 4), (13, 4),  # Active engine mounting open load
    (24, 7),                    # Auto gearbox (no autobox)
})


def decode_faults(payload: bytes) -> list[dict]:
    """
    Decode the raw data bytes from a PID 0x20 (Faults) response.

    Wire format: 2-byte pairs → [fault_index_byte][occurrence_count_byte]
    Encoding:    group = fault_index // 8 + 1
                 sub   = fault_index %  8 + 1

    Returns a list of fault dicts:
        code        — Nanocom notation e.g. "4-6"
        description — human-readable fault text
        count       — occurrence count logged by ECU
        expected    — True if known Defender false positive
    """
    results = []
    for i in range(0, len(payload) - 1, 2):
        idx   = payload[i]
        count = payload[i + 1]
        group = idx // 8 + 1
        sub   = idx %  8 + 1
        desc  = _FAULT_TABLE.get((group, sub), f"Unknown fault ({group}-{sub})")
        results.append({
            "code":        f"{group}-{sub}",
            "description": desc,
            "count":       count,
            "expected":    (group, sub) in _DEFENDER_EXPECTED,
        })
    return results
