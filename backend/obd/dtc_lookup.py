"""
TD5 DTC (Diagnostic Trouble Code) lookup table.

The TD5 ECU stores fault codes as 16-bit integers (PID 0x20).
This module maps known codes to human-readable descriptions.

Sources: TD5 community forums, Nanocom documentation, pyTD5Tester,
Ekaitza_Itzali, and general Lucas/MEMS ECU fault code databases.

The TD5 uses a non-standard DTC encoding — these are NOT standard OBD-II
P/B/C/U codes. The raw 16-bit value is what the ECU stores internally.
"""

from __future__ import annotations

# Mapping of 16-bit fault code → (short name, description)
# Codes are stored as integers matching the raw ECU response.
TD5_DTC_TABLE: dict[int, tuple[str, str]] = {
    # ── Injector faults ──────────────────────────────
    0x0263: ("INJ1 OPEN",     "Injector 1 circuit open"),
    0x0266: ("INJ2 OPEN",     "Injector 2 circuit open"),
    0x0269: ("INJ3 OPEN",     "Injector 3 circuit open"),
    0x026C: ("INJ4 OPEN",     "Injector 4 circuit open"),
    0x026F: ("INJ5 OPEN",     "Injector 5 circuit open"),
    0x0264: ("INJ1 SHORT",    "Injector 1 circuit short"),
    0x0267: ("INJ2 SHORT",    "Injector 2 circuit short"),
    0x026A: ("INJ3 SHORT",    "Injector 3 circuit short"),
    0x026D: ("INJ4 SHORT",    "Injector 4 circuit short"),
    0x0270: ("INJ5 SHORT",    "Injector 5 circuit short"),

    # ── Glow plug faults ─────────────────────────────
    0x0380: ("GLOW1",         "Glow plug 1 circuit fault"),
    0x0381: ("GLOW2",         "Glow plug 2 circuit fault"),
    0x0382: ("GLOW3",         "Glow plug 3 circuit fault"),
    0x0383: ("GLOW4",         "Glow plug 4 circuit fault"),
    0x0384: ("GLOW5",         "Glow plug 5 circuit fault"),
    0x0670: ("GLOW CTRL",     "Glow plug control module fault"),

    # ── Sensor faults ────────────────────────────────
    0x0100: ("MAF",           "Mass air flow sensor fault"),
    0x0105: ("IAT",           "Inlet air temperature sensor fault"),
    0x0110: ("ECT HIGH",      "Coolant temperature sensor — high"),
    0x0115: ("ECT LOW",       "Coolant temperature sensor — low"),
    0x0120: ("TPS",           "Throttle position sensor fault"),
    0x0190: ("FUEL RAIL",     "Fuel rail pressure sensor fault"),
    0x0235: ("BOOST",         "Turbo boost pressure sensor fault"),
    0x0500: ("VSS",           "Vehicle speed sensor fault"),

    # ── EGR / emissions ──────────────────────────────
    0x0400: ("EGR",           "EGR system fault"),
    0x0401: ("EGR FLOW",      "EGR insufficient flow"),
    0x0402: ("EGR EXCESS",    "EGR excessive flow"),
    0x0403: ("EGR CTRL",      "EGR control circuit fault"),

    # ── Turbo / wastegate ────────────────────────────
    0x0234: ("OVERBOOST",     "Turbocharger overboost condition"),
    0x0236: ("BOOST LOW",     "Turbo boost pressure — low"),
    0x0299: ("WASTEGATE",     "Turbo wastegate control fault"),

    # ── Fuel system ──────────────────────────────────
    0x0087: ("FRP LOW",       "Fuel rail pressure too low"),
    0x0088: ("FRP HIGH",      "Fuel rail pressure too high"),
    0x0089: ("FRP REG",       "Fuel pressure regulator fault"),
    0x0200: ("INJ CTRL",      "Injector control circuit fault"),
    0x0251: ("PUMP A",        "Injection pump fuel metering A"),
    0x0252: ("PUMP B",        "Injection pump fuel metering B"),

    # ── Electrical / power ───────────────────────────
    0x0560: ("SYS VOLT",      "System voltage fault"),
    0x0563: ("SYS VOLT HI",   "System voltage high"),
    0x0562: ("SYS VOLT LO",   "System voltage low"),
    0x0606: ("ECU PROC",      "ECU processor fault"),
    0x0340: ("CKP",           "Crankshaft position sensor fault"),
    0x0341: ("CMP",           "Camshaft position sensor fault"),

    # ── Immobiliser ──────────────────────────────────
    0x1000: ("IMMOB",         "Immobiliser fault"),
    0x1001: ("IMMOB KEY",     "Immobiliser key not recognised"),

    # ── Drivetrain ───────────────────────────────────
    0x0700: ("TCM LINK",      "Transmission control module link fault"),
    0x0A00: ("ABS LINK",      "ABS module communication fault"),

    # ── Common codes seen on TD5 (hex values from community reports) ──
    0x1DBB: ("DTC 1DBB",      "Stored fault code 0x1DBB"),
    0x0C84: ("DTC 0C84",      "Stored fault code 0x0C84"),
}


def lookup(code: int) -> tuple[str, str]:
    """
    Look up a 16-bit DTC code.

    Returns:
        (short_name, description) — from the lookup table if known,
        otherwise a generic formatted entry.
    """
    if code in TD5_DTC_TABLE:
        return TD5_DTC_TABLE[code]
    return (f"DTC {code:04X}", f"Unknown fault code 0x{code:04X}")


def decode_with_descriptions(fault_codes: list[int]) -> list[dict]:
    """
    Convert a list of raw 16-bit fault codes to dicts with descriptions.

    Returns:
        [{"code": 0x1DBB, "hex": "1DBB", "name": "...", "description": "..."}]
    """
    result = []
    for code in fault_codes:
        name, desc = lookup(code)
        result.append({
            "code": code,
            "hex": f"{code:04X}",
            "name": name,
            "description": desc,
        })
    return result
