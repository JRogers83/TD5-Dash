"""
Witty Pi 5 HAT+ power management service.

Registers the /system/shutdown-prepare REST endpoint which is called by
deploy/beforeShutdown.sh immediately before the Witty Pi daemon halts the Pi.

The endpoint performs ordered cleanup:
  1. Stop game mode if active (unfreezes Chromium)
  2. Flush SQLite WAL journal
  3. Log clean shutdown to journal

Returns HTTP 409 if shared_state.override_mode is True — this signals
beforeShutdown.sh to abort the shutdown (future "stay on" button hook).
Returns HTTP 501 if WITTYPI_ENABLED != "1".

I2C addresses:
  Witty Pi 5 RTC: 0x51
  Waveshare 7.9" touch (Goodix): 0x38
  → No conflict.

# hw-verify: VIN shutdown threshold, shutdown delay, and beforeShutdown.sh
# hook registration must be verified against the physical Witty Pi unit.
# See documentation/pi-setup.md for setup instructions.
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException

import db
import shared_state

log = logging.getLogger(__name__)
router = APIRouter()

WITTYPI_ENABLED: bool = os.getenv("WITTYPI_ENABLED", "0") == "1"


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


def _get_game_service_state():
    """
    Returns (status, stop_fn) for the active game session, or (None, None).
    Isolated into a function so tests can monkeypatch without importing game_service
    at module level (game_service has heavy psutil/process dependencies).
    """
    try:
        import game_service
        proc = game_service._launcher_proc
        if proc is not None and proc.poll() is None:
            return "running", game_service._stop_internal
    except Exception:
        pass
    return None, None


@router.post("/system/shutdown-prepare")
async def shutdown_prepare() -> dict:
    """
    Pre-shutdown cleanup hook. Called by deploy/beforeShutdown.sh before halt.

    MUST complete within 8 seconds (beforeShutdown.sh uses a 10s curl timeout).

    Returns:
      200 {"ok": True, "cleaned_up": [...]}  — proceed with shutdown
      409 {"error": "override_active"}       — abort shutdown (override mode on)
      501 {"error": "wittypi_not_enabled"}   — endpoint inactive
    """
    if not WITTYPI_ENABLED:
        raise HTTPException(501, {"error": "wittypi_not_enabled"})

    if shared_state.override_mode:
        log.info("Shutdown aborted: override_mode is active")
        raise HTTPException(409, {"error": "override_active"})

    actions: list[str] = []

    # 1. Stop game mode if active (unfreezes Chromium, kills launcher, cleans PulseAudio)
    status, stop_fn = _get_game_service_state()
    if status == "running" and stop_fn is not None:
        try:
            await stop_fn()
            actions.append("game_mode_stopped")
        except Exception as exc:
            log.warning("Game mode cleanup failed: %s", exc)
            actions.append("game_mode_stop_failed")

    # 2. Flush SQLite WAL journal to main database file
    try:
        db.wal_checkpoint()
        actions.append("db_checkpointed")
    except Exception as exc:
        log.warning("WAL checkpoint failed: %s", exc)
        actions.append("db_checkpoint_failed")

    # 3. Log clean shutdown
    log.info("Witty Pi initiated shutdown — cleanup complete: %s", actions)
    actions.append("shutdown_logged")

    return {"ok": True, "cleaned_up": actions}
