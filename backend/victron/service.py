"""
Victron BLE service — async WebSocket publisher.

Reads live data from SmartShunt and MPPT via BLE and publishes the
combined state to the WebSocket hub once per second.

Configuration (environment variables):
  VICTRON_SHUNT_MAC   MAC address of the SmartShunt 500A   e.g. AA:BB:CC:DD:EE:FF
  VICTRON_SHUNT_KEY   Encryption key (32 hex chars)         e.g. 0011223344...
  VICTRON_MPPT_MAC    MAC address of the MPPT 100/30
  VICTRON_MPPT_KEY    Encryption key (32 hex chars)
  VICTRON_PUBLISH_INTERVAL  Publish rate in seconds (default 1.0)

Keys are extracted from VictronConnect — see scanner.py for instructions.
"""

from __future__ import annotations

import asyncio
import logging
import os

from ws_hub import ConnectionManager
from .scanner import VictronScanner, VictronState

log = logging.getLogger(__name__)

SHUNT_MAC        = os.getenv("VICTRON_SHUNT_MAC")
SHUNT_KEY        = os.getenv("VICTRON_SHUNT_KEY")
MPPT_MAC         = os.getenv("VICTRON_MPPT_MAC")
MPPT_KEY         = os.getenv("VICTRON_MPPT_KEY")
ORION_MAC        = os.getenv("VICTRON_ORION_MAC")
ORION_KEY        = os.getenv("VICTRON_ORION_KEY")
PUBLISH_INTERVAL = float(os.getenv("VICTRON_PUBLISH_INTERVAL", "1.0"))
RETRY_DELAY_S    = 10.0


async def broadcast_loop(manager: ConnectionManager) -> None:
    """
    Async entry point — called from main.py lifespan when VICTRON_MOCK=0.

    Starts the BLE scanner and publishes the latest VictronState to all
    connected WebSocket clients once per PUBLISH_INTERVAL seconds.

    Automatically restarts the scanner if bleak raises an unexpected error
    (e.g. the Bluetooth adapter is temporarily unavailable).
    """
    if not any([SHUNT_MAC, MPPT_MAC, ORION_MAC]):
        log.error(
            "Victron service enabled (VICTRON_MOCK=0) but no device MAC addresses "
            "are configured. Set VICTRON_SHUNT_MAC and/or VICTRON_MPPT_MAC."
        )
        return

    while True:
        try:
            await _scan_and_publish(manager)
        except Exception:
            log.exception(
                "Victron BLE loop crashed — retrying in %.0f s", RETRY_DELAY_S
            )
            await asyncio.sleep(RETRY_DELAY_S)


async def _scan_and_publish(manager: ConnectionManager) -> None:
    state   = VictronState()
    scanner = VictronScanner(
        state, SHUNT_MAC, SHUNT_KEY, MPPT_MAC, MPPT_KEY, ORION_MAC, ORION_KEY
    )

    async with scanner:
        log.info("Victron BLE scanner active. Publishing every %.1f s.", PUBLISH_INTERVAL)

        while True:
            await asyncio.sleep(PUBLISH_INTERVAL)

            payload = {
                "type": "victron",
                "data": {
                    "soc_pct":        state.soc_pct,
                    "voltage_v":      state.voltage_v,
                    "current_a":      state.current_a,
                    "solar_yield_wh": state.solar_yield_wh,
                    "charge_state":   state.charge_state,
                    "orion_state":    state.orion_state,
                    "orion_input_v":  state.orion_input_v,
                },
            }
            await manager.broadcast(payload)

            if not state.is_fresh():
                log.warning(
                    "Victron data may be stale — shunt last seen %.0fs ago, "
                    "MPPT last seen %.0fs ago",
                    _age(state.shunt_updated),
                    _age(state.mppt_updated),
                )


def _age(ts: float) -> float:
    import time
    return time.monotonic() - ts if ts > 0 else float("inf")
