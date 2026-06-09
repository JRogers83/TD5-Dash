"""
Witty Pi 5 HAT+ power management service.

Handles startup logging when WITTYPI_ENABLED=1.

Shutdown cleanup is driven by SIGTERM from systemd. When the Witty Pi 5
detects VIN loss (ignition off), wp5d calls `shutdown -h now`. systemd
sends SIGTERM to this service, which triggers the lifespan teardown in
main.py — see _wittypi_pre_shutdown_cleanup(). No shell script hook or
I2C polling is required.

All Witty Pi 5 communication is via I2C at address 0x51.

I2C addresses (no conflict):
  Witty Pi 5 (RTC + power management): 0x51
  Waveshare 7.9" touch (Goodix):        0x38

I2C register 71 — shutdown handshake (from wp5 manual):
  0 = none
  1 = Witty Pi requests Pi to turn off  (wp5d sets this, then calls shutdown)
  2 = Pi is shutting down               (Pi should ideally set this — not yet implemented)
  3 = Pi is rebooting

Daemon:  wp5d
Log:     /var/log/wp5d.log
Install: sudo raspi-config nonint do_i2c 0
         wget https://www.uugear.com/repo/WittyPi5/wp5_latest.deb && sudo apt install ./wp5_latest.deb
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


def startup_checks() -> None:
    """Called from main.py lifespan. Logs I2C info and warns on conflicting config."""
    log.info(
        "Witty Pi 5 power management active. "
        "I2C: 0x51 (Witty Pi RTC), 0x38 (Waveshare touch) — no address conflict."
    )
    if os.getenv("IGNITION_SENSE_PIN"):
        log.warning(
            "Both WITTYPI_ENABLED=1 and IGNITION_SENSE_PIN are set. "
            "Both shutdown paths are active simultaneously — this may cause races. "
            "Clear IGNITION_SENSE_PIN when using the Witty Pi."
        )
