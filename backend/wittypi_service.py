"""
Witty Pi 5 HAT+ power management service.

Handles startup logging and VIN voltage monitoring when WITTYPI_ENABLED=1.

Shutdown mechanism:
  monitor_vin() polls I2C registers 5+6 (VIN voltage in mV) every second.
  When VIN drops below WITTYPI_VIN_SHUTDOWN_THRESHOLD_MV for
  WITTYPI_VIN_DEBOUNCE_COUNT consecutive readings, it calls `shutdown -h now`.
  systemd then sends SIGTERM to this service, triggering the lifespan teardown
  in main.py (_wittypi_pre_shutdown_cleanup) which handles game mode and DB.

  This covers the relay-switching use case (VIN drops instantly to 0V on
  ignition off) which the Witty Pi's built-in threshold detection cannot
  handle because the permanent USB-C feed keeps the Pi running.

  When the hardware override switch is engaged (wired in parallel with the
  relay), VIN stays high and no shutdown is triggered — correct behaviour.

Configuration:
  WITTYPI_VIN_SHUTDOWN_THRESHOLD_MV  Trigger when VIN below this (default 1000)
  WITTYPI_ENABLED                     1 = active, 0 = disabled (default)

I2C addresses (no conflict):
  Witty Pi 5 (RTC + power management): 0x51
  Waveshare 7.9" touch (Goodix):        0x38

I2C registers used:
  5  — VIN voltage MSB (mV)
  6  — VIN voltage LSB (mV)  combined: (reg5 << 8) | reg6
  71 — Shutdown handshake: 0=none, 1=wp5d requests off, 2=Pi shutting down, 3=rebooting
       Written to 2 by _signal_pi_shutting_down() in the VIN monitoring path.

Daemon:  wp5d
Log:     /var/log/wp5d.log
Install: sudo raspi-config nonint do_i2c 0
         wget https://www.uugear.com/repo/WittyPi5/wp5_latest.deb && sudo apt install ./wp5_latest.deb
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess

log = logging.getLogger(__name__)

_WITTYPI_ADDR          = 0x51
_REG_VIN_MSB           = 5
_REG_VIN_LSB           = 6
_POLL_INTERVAL_S       = 1.0
_STARTUP_DELAY_S       = 5.0    # ignore readings during boot instability

VIN_SHUTDOWN_THRESHOLD_MV: int = int(
    os.getenv("WITTYPI_VIN_SHUTDOWN_THRESHOLD_MV", "1000")
)
VIN_DEBOUNCE_COUNT: int = 10    # consecutive below-threshold readings before shutdown


def startup_checks() -> None:
    """Called from main.py lifespan. Logs I2C info and warns on conflicting config."""
    log.info(
        "Witty Pi 5 power management active. "
        "I2C: 0x51 (Witty Pi RTC), 0x38 (Waveshare touch) — no address conflict. "
        "VIN shutdown threshold: %d mV.", VIN_SHUTDOWN_THRESHOLD_MV
    )
    if os.getenv("IGNITION_SENSE_PIN"):
        log.warning(
            "Both WITTYPI_ENABLED=1 and IGNITION_SENSE_PIN are set. "
            "Both shutdown paths are active simultaneously — this may cause races. "
            "Clear IGNITION_SENSE_PIN when using the Witty Pi."
        )


def _signal_pi_shutting_down() -> None:
    """Write I2C register 71 = 2 to signal the Witty Pi that the Pi is shutting down.

    Tells the Witty Pi this is an externally-triggered (VIN-loss) shutdown so it
    will boot the Pi again when VIN recovers above the recovery threshold.
    Called in the VIN monitoring shutdown path only — not in the SIGTERM/lifespan
    path, which is a normal OS shutdown and does not need this signal.
    """
    try:
        import smbus2
        bus = smbus2.SMBus(1)
        try:
            bus.write_byte_data(_WITTYPI_ADDR, 71, 2)
        finally:
            bus.close()
        log.info("Witty Pi I2C register 71 set to 2 (Pi is shutting down).")
    except ImportError:
        log.debug("smbus2 not available — skipping register 71 write.")
    except Exception as exc:
        log.warning(
            "Could not write Witty Pi I2C register 71: %s — proceeding with shutdown anyway.",
            exc,
        )


def _read_vin_mv() -> int | None:
    """Read VIN voltage from Witty Pi 5 I2C registers 5 and 6.

    Returns voltage in mV, or None if I2C is unavailable (dev/Docker/no Witty Pi).
    Runs synchronously — call via asyncio.to_thread in async context.
    """
    try:
        import smbus2
        bus = smbus2.SMBus(1)
        try:
            msb = bus.read_byte_data(_WITTYPI_ADDR, _REG_VIN_MSB)
            lsb = bus.read_byte_data(_WITTYPI_ADDR, _REG_VIN_LSB)
        finally:
            bus.close()
        return (msb << 8) | lsb
    except ImportError:
        return None   # smbus2 not installed (dev/Docker)
    except Exception as exc:
        log.debug("VIN I2C read failed: %s", exc)
        return None


async def monitor_vin() -> None:
    """Background task: monitor VIN voltage and shut down when ignition cuts out.

    Polls I2C every second. When VIN drops below VIN_SHUTDOWN_THRESHOLD_MV for
    VIN_DEBOUNCE_COUNT consecutive readings (~10 s), calls `sudo shutdown -h now`.
    systemd then sends SIGTERM, triggering the lifespan teardown cleanup.

    Silently idles when smbus2 is unavailable (dev/Docker).
    """
    # Wait for VIN to stabilise after boot before starting to monitor
    await asyncio.sleep(_STARTUP_DELAY_S)
    log.info(
        "VIN monitoring started (threshold: %d mV, debounce: %d readings).",
        VIN_SHUTDOWN_THRESHOLD_MV, VIN_DEBOUNCE_COUNT,
    )

    below_count = 0

    while True:
        vin_mv = await asyncio.to_thread(_read_vin_mv)

        if vin_mv is None:
            # I2C unavailable — idle silently (dev/Docker, or hardware absent)
            await asyncio.sleep(_POLL_INTERVAL_S)
            continue

        if vin_mv < VIN_SHUTDOWN_THRESHOLD_MV:
            below_count += 1
            if below_count == 1:
                log.info(
                    "VIN below threshold: %d mV (threshold %d mV) — debouncing (%d/%d).",
                    vin_mv, VIN_SHUTDOWN_THRESHOLD_MV, below_count, VIN_DEBOUNCE_COUNT,
                )
            elif below_count < VIN_DEBOUNCE_COUNT:
                log.debug("VIN still below threshold: %d/%d.", below_count, VIN_DEBOUNCE_COUNT)

            if below_count >= VIN_DEBOUNCE_COUNT:
                log.warning(
                    "VIN below %d mV for %d consecutive readings — ignition off, "
                    "initiating graceful shutdown.",
                    VIN_SHUTDOWN_THRESHOLD_MV, VIN_DEBOUNCE_COUNT,
                )
                # Signal to Witty Pi that Pi is shutting down (register 71 = 2).
                # This lets the Witty Pi associate the shutdown with VIN loss and
                # boot the Pi again when VIN recovers above the recovery threshold.
                await asyncio.to_thread(_signal_pi_shutting_down)
                subprocess.run(["sudo", "shutdown", "-h", "now"], check=False)
                return  # task exits; SIGTERM → lifespan teardown handles cleanup
        else:
            if below_count > 0:
                log.info(
                    "VIN recovered: %d mV — resetting debounce counter "
                    "(was %d/%d).",
                    vin_mv, below_count, VIN_DEBOUNCE_COUNT,
                )
            below_count = 0

        await asyncio.sleep(_POLL_INTERVAL_S)
