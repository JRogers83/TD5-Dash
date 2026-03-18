"""
CarPiHAT PRO 5 / GPIO service — ignition detection, safe shutdown, sidelights,
and relay control.

⚠  HARDWARE PENDING — GPIO pin numbers are provisional and must be confirmed
   against the CarPiHAT PRO 5 pinout once the board arrives.
   The service will run on Docker / Windows (no-op GPIO stub), so it is safe
   to include in the normal service startup without the hardware present.

── GPIO pin assignments (to be confirmed) ──────────────────────────────────────
  GPIO 17  IN1  — 12V opto-isolated ignition sense
  GPIO 27  IN2  — Carling Contura V override switch (leisure battery bypass)
  GPIO 22  IN3  — Sidelights sense (auto-brightness: day ↔ night mode)
  GPIO 23  OUT1 — Amplifier relay (switched 12V output)

── Ignition-off sequence ────────────────────────────────────────────────────────
  Ignition low  →  30-second grace period starts (frontend shows countdown)
  Ignition high →  grace period cancelled
  Grace expires →  `systemctl poweroff`

── Override mode ────────────────────────────────────────────────────────────────
  Override switch active → shutdown suppressed; system topic broadcasts
  override_mode=True so the Settings view shows the indicator.

── Sidelights / auto-brightness ─────────────────────────────────────────────────
  When sidelights are detected, broadcast a system event that the frontend
  uses to switch to night_brightness automatically.

── Amplifier relay ──────────────────────────────────────────────────────────────
  set_relay("amp", True/False) is called by the POST /system/relay endpoint.
  On Pi with RPi.GPIO available, writes the GPIO output pin.
  On Docker / dev, logs the command and returns.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess

log = logging.getLogger(__name__)

# GPIO pin assignments — provisional, confirm with CarPiHAT PRO 5 documentation
GPIO_IGNITION  = int(os.getenv("CARPIHAT_GPIO_IGNITION",  "17"))
GPIO_OVERRIDE  = int(os.getenv("CARPIHAT_GPIO_OVERRIDE",  "27"))
GPIO_SIDELIGHTS= int(os.getenv("CARPIHAT_GPIO_SIDELIGHTS","22"))
GPIO_AMP_RELAY = int(os.getenv("CARPIHAT_GPIO_AMP_RELAY", "23"))

SHUTDOWN_GRACE_S = int(os.getenv("CARPIHAT_SHUTDOWN_GRACE", "30"))
POLL_INTERVAL_S  = 1.0

# ── GPIO availability check ───────────────────────────────────────────────────

try:
    import RPi.GPIO as GPIO  # type: ignore
    _GPIO_AVAILABLE = True
    log.info("RPi.GPIO available — CarPiHAT GPIO active.")
except ImportError:
    _GPIO_AVAILABLE = False
    log.info("RPi.GPIO not available — CarPiHAT running in stub mode (Docker/dev).")


def _gpio_setup() -> None:
    if not _GPIO_AVAILABLE:
        return
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(GPIO_IGNITION,   GPIO.IN,  pull_up_down=GPIO.PUD_DOWN)
    GPIO.setup(GPIO_OVERRIDE,   GPIO.IN,  pull_up_down=GPIO.PUD_DOWN)
    GPIO.setup(GPIO_SIDELIGHTS, GPIO.IN,  pull_up_down=GPIO.PUD_DOWN)
    GPIO.setup(GPIO_AMP_RELAY,  GPIO.OUT, initial=GPIO.LOW)
    log.info(
        "GPIO configured: IGN=%d, OVERRIDE=%d, SIDELIGHTS=%d, AMP=%d",
        GPIO_IGNITION, GPIO_OVERRIDE, GPIO_SIDELIGHTS, GPIO_AMP_RELAY,
    )


def _gpio_read(pin: int) -> bool:
    if not _GPIO_AVAILABLE:
        return False
    return bool(GPIO.input(pin))


def _gpio_write(pin: int, state: bool) -> None:
    if not _GPIO_AVAILABLE:
        return
    GPIO.output(pin, GPIO.HIGH if state else GPIO.LOW)


# ── Relay control (called by /system/relay endpoint) ─────────────────────────

_relay_pins: dict[str, int] = {
    "amp": GPIO_AMP_RELAY,
}


def set_relay(name: str, state: bool) -> None:
    """
    Set a named relay output. Called by the POST /system/relay endpoint.
    No-op in stub mode; writes GPIO pin on Pi.
    """
    pin = _relay_pins.get(name)
    if pin is None:
        log.warning("Unknown relay name: '%s'", name)
        return
    if _GPIO_AVAILABLE:
        _gpio_write(pin, state)
        log.info("Relay '%s' (GPIO %d) → %s", name, pin, "ON" if state else "OFF")
    else:
        log.info("Relay '%s' → %s (stub — GPIO not available)", name, "ON" if state else "OFF")


# ── Ignition monitoring loop ──────────────────────────────────────────────────

async def monitor_loop() -> None:
    """
    Async entry point — monitors ignition, override switch, and sidelights.

    Does NOT broadcast directly. Instead it updates shared_state so that
    system_service picks up the values on its next publish cycle. This keeps
    the system topic to a single publisher.

    In stub mode (no RPi.GPIO) the loop runs but reads all pins as False,
    so shared_state.override_mode stays False and no shutdown is triggered.
    """
    import shared_state

    _gpio_setup()

    shutdown_timer: asyncio.Task | None = None

    while True:
        ignition   = _gpio_read(GPIO_IGNITION)
        override   = _gpio_read(GPIO_OVERRIDE)
        sidelights = _gpio_read(GPIO_SIDELIGHTS)

        # Update shared state for system_service to pick up
        shared_state.override_mode = override
        shared_state.sidelights_on = sidelights

        if ignition or override:
            # Engine running or override active — cancel any pending shutdown
            if shutdown_timer and not shutdown_timer.done():
                shutdown_timer.cancel()
                shutdown_timer = None
                log.info("Shutdown cancelled — ignition or override restored.")
        else:
            # Ignition off and no override — start shutdown timer if not already running
            if shutdown_timer is None or shutdown_timer.done():
                log.info(
                    "Ignition off — initiating %d-second shutdown grace period.",
                    SHUTDOWN_GRACE_S,
                )
                shutdown_timer = asyncio.create_task(_shutdown_after(SHUTDOWN_GRACE_S))

        await asyncio.sleep(POLL_INTERVAL_S)


async def _shutdown_after(delay_s: int) -> None:
    """Wait delay_s seconds then issue a graceful system shutdown."""
    await asyncio.sleep(delay_s)
    log.warning("Grace period expired — shutting down.")
    try:
        subprocess.run(["systemctl", "poweroff"], check=True)
    except Exception as exc:
        log.error("Shutdown command failed: %s", exc)
