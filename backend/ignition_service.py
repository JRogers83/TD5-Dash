"""
Ignition sense service — monitors a GPIO pin connected to a PC817 optoisolator
that detects 12V ignition feed.

When the ignition signal drops (rising edge on the opto output, which is
active-low inverted by the optoisolator circuit — the exact polarity will be
confirmed during hardware build), a shutdown sequence is initiated.

The service must complete shutdown within ~60 seconds.

-- Configuration ---------------------------------------------------------------
  IGNITION_SENSE_PIN  GPIO BCM pin number (default: None — service is a no-op)
  IGNITION_GRACE_S    Seconds before shutdown after ignition loss (default: 30)

-- Behaviour when IGNITION_SENSE_PIN is None -----------------------------------
  The service starts normally, logs that no pin is configured, and returns
  immediately.  No error, no blocking.  This is the expected state during
  development and until the hardware is wired.

-- GPIO availability -----------------------------------------------------------
  Uses RPi.GPIO if available.  On Docker / Windows / any environment without
  RPi.GPIO the service runs in stub mode (no-op, same as pin=None).
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess

log = logging.getLogger(__name__)

# GPIO pin — None means "not wired yet, do nothing".
_pin_env = os.getenv("IGNITION_SENSE_PIN", "")
IGNITION_SENSE_PIN: int | None = int(_pin_env) if _pin_env.strip().isdigit() else None

IGNITION_GRACE_S = int(os.getenv("IGNITION_GRACE_S", "30"))

# ── GPIO availability check ──────────────────────────────────────────────────

_GPIO_AVAILABLE = False
try:
    import RPi.GPIO as GPIO  # type: ignore
    _GPIO_AVAILABLE = True
    log.info("RPi.GPIO available — ignition GPIO active.")
except ImportError:
    log.info("RPi.GPIO not available — ignition service running in stub mode (Docker/dev).")


def _gpio_setup() -> None:
    """Configure the ignition sense pin as an input with pull-down."""
    if not _GPIO_AVAILABLE or IGNITION_SENSE_PIN is None:
        return
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(IGNITION_SENSE_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    log.info("Ignition sense pin configured: GPIO %d", IGNITION_SENSE_PIN)


def _gpio_read() -> bool:
    """Read the ignition sense pin.  Returns False in stub mode."""
    if not _GPIO_AVAILABLE or IGNITION_SENSE_PIN is None:
        return False
    return bool(GPIO.input(IGNITION_SENSE_PIN))


# ── Shutdown ─────────────────────────────────────────────────────────────────

async def _shutdown_after(delay_s: int) -> None:
    """Wait delay_s seconds then issue a graceful system shutdown."""
    await asyncio.sleep(delay_s)
    log.warning("Ignition grace period expired — shutting down.")
    try:
        subprocess.run(["shutdown", "-h", "now"], check=True)
    except Exception as exc:
        log.error("Shutdown command failed: %s", exc)


# ── Main loop ────────────────────────────────────────────────────────────────

async def monitor_loop() -> None:
    """
    Async entry point — called from main.py lifespan.

    Monitors the ignition sense GPIO pin.  When the ignition signal is lost
    (pin goes low), starts a grace period countdown.  If the ignition returns
    before the grace period expires, the shutdown is cancelled.

    When IGNITION_SENSE_PIN is None the function logs and returns immediately
    so it never blocks or errors in development / unconfigured environments.
    """
    if IGNITION_SENSE_PIN is None:
        log.info("Ignition sense pin not configured (IGNITION_SENSE_PIN not set) — service inactive.")
        return

    if not _GPIO_AVAILABLE:
        log.info("RPi.GPIO not available — ignition monitoring disabled.")
        return

    _gpio_setup()

    shutdown_timer: asyncio.Task | None = None
    poll_interval_s = 1.0

    log.info(
        "Ignition monitor started on GPIO %d (grace period: %d s).",
        IGNITION_SENSE_PIN, IGNITION_GRACE_S,
    )

    while True:
        ignition_on = _gpio_read()

        if ignition_on:
            # Ignition is present — cancel any pending shutdown
            if shutdown_timer is not None and not shutdown_timer.done():
                shutdown_timer.cancel()
                shutdown_timer = None
                log.info("Shutdown cancelled — ignition restored.")
        else:
            # Ignition lost — start shutdown timer if not already running
            if shutdown_timer is None or shutdown_timer.done():
                log.info(
                    "Ignition lost — initiating %d-second shutdown grace period.",
                    IGNITION_GRACE_S,
                )
                shutdown_timer = asyncio.create_task(_shutdown_after(IGNITION_GRACE_S))

        await asyncio.sleep(poll_interval_s)
