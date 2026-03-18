"""
Victron BLE advertisement scanner.

Listens passively for BLE advertisements from:
  - Victron SmartShunt 500A   → SoC, voltage, current
  - Victron MPPT 100/30       → solar yield, charge state
  - Victron Orion XS 12/12-50A → DC-DC charger state, input voltage

Both devices broadcast approximately once per second using Victron's
proprietary encrypted advertisement format. Encryption keys are extracted
from the VictronConnect app (see KEY EXTRACTION below).

This module maintains a VictronState dataclass that is updated as
advertisements arrive. The service module reads this state and publishes
it to the WebSocket hub.

KEY EXTRACTION
──────────────
1. Open VictronConnect on your phone.
2. Connect to the SmartShunt (or MPPT).
3. Go to the device menu → Product info → scroll to the bottom.
4. Copy the value labelled "Encryption key" (32 hex characters = 16 bytes).
5. Repeat for the other device.
6. Set the env vars:
     VICTRON_SHUNT_MAC=AA:BB:CC:DD:EE:FF
     VICTRON_SHUNT_KEY=0011223344556677889900aabbccddeeff
     VICTRON_MPPT_MAC=AA:BB:CC:DD:EE:FF
     VICTRON_MPPT_KEY=0011223344556677889900aabbccddeeff
     VICTRON_ORION_MAC=AA:BB:CC:DD:EE:FF
     VICTRON_ORION_KEY=0011223344556677889900aabbccddeeff

MAC addresses are printed on the device label and shown in VictronConnect.

References:
  github.com/keshavdv/victron-ble
  github.com/keshavdv/victron-ble/tree/main/victron_ble/devices
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)

# Victron's Bluetooth manufacturer ID — present in every Victron advertisement.
VICTRON_MANUFACTURER_ID = 0x02E1


# ── Charge state mapping ───────────────────────────────────────────────────────
# Maps victron-ble DeviceState enum values to the string keys the frontend
# expects (defined in CHARGE_STATE_LABELS in app.js).

_CHARGE_STATE_MAP: dict[int, str] = {
    0:   "off",
    1:   "low_power",
    2:   "fault",
    3:   "bulk",
    4:   "absorption",
    5:   "float",
    6:   "storage",
    7:   "equalize",
    9:   "off",       # inverting — not applicable, treat as off
    11:  "low_power",
    252: "off",       # external control
}


# ── Shared state ───────────────────────────────────────────────────────────────

@dataclass
class VictronState:
    """
    Combined live state from SmartShunt + MPPT + Orion XS.
    Updated independently as each device's advertisement arrives.
    Fields match the 'victron' WebSocket message payload.
    """
    # SmartShunt
    soc_pct:        float = 0.0
    voltage_v:      float = 0.0
    current_a:      float = 0.0

    # MPPT
    solar_yield_wh: float = 0.0    # stored as Wh (victron-ble gives kWh — converted)
    charge_state:   str   = "off"

    # Orion XS DC-DC charger
    orion_state:    str   = "off"  # off / bulk / absorption / float / fault
    orion_input_v:  float = 0.0    # vehicle / alternator voltage

    # Timestamps of last update — used to detect stale data (device out of range).
    shunt_updated:  float = field(default=0.0, repr=False)
    mppt_updated:   float = field(default=0.0, repr=False)
    orion_updated:  float = field(default=0.0, repr=False)

    def is_fresh(self, max_age_s: float = 10.0) -> bool:
        """True if all configured devices have reported within max_age_s seconds."""
        now = time.monotonic()
        return (
            (now - self.shunt_updated) < max_age_s and
            (now - self.mppt_updated)  < max_age_s and
            (now - self.orion_updated) < max_age_s
        )


# ── Advertisement handler ──────────────────────────────────────────────────────

class VictronScanner:
    """
    Wraps a bleak BleakScanner to receive and decode Victron BLE advertisements.

    Usage:
        state   = VictronState()
        scanner = VictronScanner(state, shunt_mac, shunt_key, mppt_mac, mppt_key)
        async with scanner:
            while True:
                await asyncio.sleep(1)
                publish(state)
    """

    def __init__(
        self,
        state:     VictronState,
        shunt_mac: Optional[str],
        shunt_key: Optional[str],
        mppt_mac:  Optional[str],
        mppt_key:  Optional[str],
        orion_mac: Optional[str] = None,
        orion_key: Optional[str] = None,
    ) -> None:
        self._state    = state
        self._devices: dict[str, str] = {}   # normalised MAC → hex key string

        if shunt_mac and shunt_key:
            self._devices[shunt_mac.upper()] = shunt_key
            self._shunt_mac = shunt_mac.upper()
        else:
            self._shunt_mac = None
            log.warning("SmartShunt MAC/key not configured — shunt data unavailable")

        if mppt_mac and mppt_key:
            self._devices[mppt_mac.upper()] = mppt_key
            self._mppt_mac = mppt_mac.upper()
        else:
            self._mppt_mac = None
            log.warning("MPPT MAC/key not configured — solar/charge-state data unavailable")

        if orion_mac and orion_key:
            self._devices[orion_mac.upper()] = orion_key
            self._orion_mac = orion_mac.upper()
        else:
            self._orion_mac = None
            log.info("Orion XS MAC/key not configured — DC-DC charger data unavailable")

        self._scanner = None

    async def __aenter__(self) -> "VictronScanner":
        from bleak import BleakScanner
        self._scanner = BleakScanner(self._on_advertisement)
        await self._scanner.start()
        log.info(
            "Victron BLE scanner started — watching %d device(s)", len(self._devices)
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._scanner:
            await self._scanner.stop()

    def _on_advertisement(self, device, advertisement_data) -> None:
        """
        Called by bleak on every BLE advertisement in range.
        Filters for known Victron devices and dispatches to the correct parser.
        """
        mfr = advertisement_data.manufacturer_data
        if VICTRON_MANUFACTURER_ID not in mfr:
            return

        addr = device.address.upper()
        if addr not in self._devices:
            return

        raw = mfr[VICTRON_MANUFACTURER_ID]
        key = self._devices[addr]

        try:
            from victron_ble.devices import detect_device_type
            device_class = detect_device_type(raw)
            if device_class is None:
                log.debug("Unknown Victron device type from %s", addr)
                return

            parsed = device_class(key).parse(raw)

            if addr == self._shunt_mac:
                self._update_from_shunt(parsed)
            elif addr == self._mppt_mac:
                self._update_from_mppt(parsed)
            elif addr == self._orion_mac:
                self._update_from_orion(parsed)

        except Exception as exc:
            log.debug("Failed to parse advertisement from %s: %s", addr, exc)

    def _update_from_shunt(self, parsed) -> None:
        """
        Extract SoC, voltage, and current from a SmartShunt advertisement.

        victron-ble SmartShunt API:
          parsed.get_state_of_charge() → float | None  (percent, 0–100)
          parsed.get_voltage()         → float | None  (volts)
          parsed.get_current()         → float | None  (amps, negative = discharging)

        TODO: verify method names against victron-ble source for your installed
              version — names have changed between library versions.
        """
        try:
            soc     = parsed.get_state_of_charge()
            voltage = parsed.get_voltage()
            current = parsed.get_current()

            if soc     is not None: self._state.soc_pct   = round(soc,     1)
            if voltage is not None: self._state.voltage_v = round(voltage, 2)
            if current is not None: self._state.current_a = round(current, 2)

            self._state.shunt_updated = time.monotonic()
            log.debug("Shunt: SoC=%.1f%% V=%.2fV I=%.2fA", soc, voltage, current)

        except Exception as exc:
            log.warning("Error reading SmartShunt data: %s", exc)

    def _update_from_mppt(self, parsed) -> None:
        """
        Extract solar yield and charge state from an MPPT advertisement.

        victron-ble SolarCharger API:
          parsed.get_device_state()  → DeviceState enum | None
          parsed.get_yield_today()   → float | None  (kWh — converted to Wh below)
          parsed.get_solar_power()   → float | None  (W, useful for future display)

        TODO: verify method names against victron-ble source for your installed
              version. Also confirm yield_today units (kWh vs Wh varies by version).
        """
        try:
            state_enum  = parsed.get_device_state()
            yield_today = parsed.get_yield_today()     # kWh from library

            if yield_today is not None:
                self._state.solar_yield_wh = round(yield_today * 1000)  # kWh → Wh

            if state_enum is not None:
                state_int = state_enum.value if hasattr(state_enum, 'value') else int(state_enum)
                self._state.charge_state = _CHARGE_STATE_MAP.get(state_int, "off")

            self._state.mppt_updated = time.monotonic()
            log.debug(
                "MPPT: yield=%.0fWh state=%s", self._state.solar_yield_wh, self._state.charge_state
            )

        except Exception as exc:
            log.warning("Error reading MPPT data: %s", exc)

    def _update_from_orion(self, parsed) -> None:
        """
        Extract DC-DC charger state and input voltage from an Orion XS advertisement.

        The Orion XS broadcasts as a DC-DC converter. victron-ble may expose it
        via an OrionXs or DcDcConverter device class — detect_device_type handles
        the dispatch automatically.

        victron-ble Orion XS / DcDcConverter API:
          parsed.get_device_state()  → DeviceState enum | None  (Off/Bulk/Absorption/Float)
          parsed.get_input_voltage() → float | None  (vehicle / alternator voltage in V)
          parsed.get_output_current() → float | None  (charging current in A)
          parsed.get_charger_error()  → ChargerError enum | None

        TODO: verify method names against victron-ble source for your installed
              version — the Orion XS is a newer device and API names may differ
              from the MPPT. If get_input_voltage() doesn't exist, try
              get_input_voltage_dc() or similar.
        """
        try:
            state_enum   = parsed.get_device_state()
            input_v      = parsed.get_input_voltage()

            if state_enum is not None:
                state_int = state_enum.value if hasattr(state_enum, 'value') else int(state_enum)
                self._state.orion_state = _CHARGE_STATE_MAP.get(state_int, "off")

            if input_v is not None:
                self._state.orion_input_v = round(input_v, 1)

            self._state.orion_updated = time.monotonic()
            log.debug(
                "Orion XS: state=%s input=%.1fV",
                self._state.orion_state, self._state.orion_input_v,
            )

        except Exception as exc:
            log.warning("Error reading Orion XS data: %s", exc)
