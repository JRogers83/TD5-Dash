"""
Pi OBD Diagnostic — 7-stage test runner.

Triggered by POST /obd/full-test.  Runs blocking K-Line I/O in a
ThreadPoolExecutor and broadcasts per-stage progress over the existing
WebSocket as {"type": "obd_test", "data": {...}} messages.

Verbose TX/RX hex is captured by attaching a DEBUG FileHandler to the
obd.connection logger (which already emits TX:/RX: at DEBUG level).

Log files are saved to data/logs/obd_YYYY-MM-DD_HH-MM-SS.log.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from ws_hub import ConnectionManager
from .connection import KLineConnection, KLineError
from . import protocol as P
from . import decoder as D

log = logging.getLogger(__name__)

_test_running: bool = False
FTDI_URL = os.getenv("TD5_FTDI_URL", "ftdi://ftdi:232/1")
LOG_DIR  = Path(__file__).parent.parent.parent / "data" / "logs"

_STAGE_NAMES = {
    1: "FTDI Detection",
    2: "Protocol Self-Test",
    3: "Fast Init",
    4: "Diagnostic Session",
    5: "Security Access",
    6: "PID Probe",
    7: "Live Data (10s)",
}


def _broadcast_stage(
    loop: asyncio.AbstractEventLoop,
    manager: ConnectionManager,
    stage: int,
    name: str,
    status: str,
    detail: str = "",
) -> None:
    """Thread-safe WebSocket broadcast for a single stage update."""
    asyncio.run_coroutine_threadsafe(
        manager.broadcast({
            "type": "obd_test",
            "data": {
                "stage":  stage,
                "name":   name,
                "status": status,   # running | pass | fail | skip
                "detail": detail,
            },
        }),
        loop,
    )


def _run_test(
    manager: ConnectionManager,
    loop: asyncio.AbstractEventLoop,
    log_path: str,
) -> None:
    """
    Blocking test runner — called from a ThreadPoolExecutor.

    Stages 1–2 run without hardware (import / protocol self-test).
    Stages 3–7 require the KKL cable connected and ignition on.
    Any stage failure skips all subsequent hardware stages.
    """
    global _test_running

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Attach a DEBUG FileHandler to the obd loggers so every TX/RX byte
    # and all stage messages are captured in the log file.
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s.%(msecs)03d %(name)-25s %(levelname)-7s %(message)s",
                          datefmt="%H:%M:%S")
    )
    watched_loggers = [
        logging.getLogger("obd.connection"),
        logging.getLogger("obd.protocol"),
        logging.getLogger("obd.service"),
        logging.getLogger(__name__),
    ]
    saved_levels = {}
    for lg in watched_loggers:
        saved_levels[lg.name] = lg.level
        lg.setLevel(logging.DEBUG)
        lg.addHandler(file_handler)

    def emit(stage_n: int, status: str, detail: str = "") -> None:
        name = _STAGE_NAMES[stage_n]
        log.info("STAGE %d [%-7s] %s — %s", stage_n, status.upper(), name, detail)
        _broadcast_stage(loop, manager, stage_n, name, status, detail)

    passed  = 0
    failed  = 0
    skipped = 0
    skip_from: int | None = None   # if set, skip all stages >= this number

    conn    = None
    session = None

    try:
        log.info("=" * 60)
        log.info("TD5 Pi Diagnostic  —  %s", datetime.now().isoformat())
        log.info("FTDI URL: %s", FTDI_URL)
        log.info("=" * 60)

        # ── Stage 1: FTDI Detection ───────────────────────────────────────────
        emit(1, "running")
        try:
            try:
                from pyftdi.usbtools import UsbTools
            except ImportError:
                raise RuntimeError(
                    "pyftdi not installed — run: pip install pyftdi"
                )
            # FT232RL = 0x6001, FT232H = 0x6014
            devices = UsbTools.find_all([(0x0403, 0x6001), (0x0403, 0x6014)])
            if not devices:
                raise RuntimeError(
                    f"No FTDI device found — check USB cable  (URL: {FTDI_URL})"
                )
            detail = f"Found {len(devices)} FTDI device(s) — {FTDI_URL}"
            emit(1, "pass", detail)
            passed += 1
        except Exception as exc:
            emit(1, "fail", str(exc))
            failed += 1
            skip_from = 3   # Stage 2 (protocol self-test) can still run

        # ── Stage 2: Protocol Self-Test ───────────────────────────────────────
        if skip_from and skip_from <= 2:
            emit(2, "skip"); skipped += 1
        else:
            emit(2, "running")
            try:
                # StartCommunication checksum (vehicle-confirmed frame)
                sc_frame = bytes([0x81, 0x13, 0xF7, 0x81])
                cs = P.checksum(sc_frame)
                assert cs == 0x0C, f"Checksum expected 0x0C, got 0x{cs:02X}"

                # Seed-key LFSR (vehicle-confirmed seed/key pair)
                key = P.td5_seed_to_key(0xBA08)
                assert key == 0x70DC, f"Seed-key expected 0x70DC, got 0x{key:04X}"

                emit(2, "pass", "Checksum 0x0C OK  |  seed 0xBA08 → key 0x70DC OK")
                passed += 1
            except Exception as exc:
                emit(2, "fail", str(exc))
                failed += 1

        # ── Stage 3: Fast Init + StartCommunication ───────────────────────────
        if skip_from and skip_from <= 3:
            emit(3, "skip"); skipped += 1
        else:
            emit(3, "running")
            try:
                conn = KLineConnection(FTDI_URL)
                conn.open()   # fast-init pulse + StartCommunication
                emit(3, "pass", "Fast-init OK  |  StartCommunication accepted")
                passed += 1
            except Exception as exc:
                emit(3, "fail", str(exc))
                failed += 1
                skip_from = 4

        # ── Stage 4: Diagnostic Session ───────────────────────────────────────
        if skip_from and skip_from <= 4:
            emit(4, "skip"); skipped += 1
        else:
            emit(4, "running")
            try:
                from .service import TD5Session
                session = TD5Session(conn)
                session._start_diagnostic_session()
                emit(4, "pass", "StartDiagnosticSession accepted")
                passed += 1
            except Exception as exc:
                emit(4, "fail", str(exc))
                failed += 1
                skip_from = 5

        # ── Stage 5: Security Access ──────────────────────────────────────────
        if skip_from and skip_from <= 5:
            emit(5, "skip"); skipped += 1
        else:
            emit(5, "running")
            try:
                session._authenticate()
                emit(5, "pass", "Seed-key authentication accepted")
                passed += 1
            except Exception as exc:
                emit(5, "fail", str(exc))
                failed += 1
                skip_from = 6

        # ── Stage 6: PID Probe ────────────────────────────────────────────────
        if skip_from and skip_from <= 6:
            emit(6, "skip"); skipped += 1
        else:
            emit(6, "running")
            pid_tests = [
                (P.PID_TEMPS,    "Temps"),
                (P.PID_MAP_MAF,  "MAP"),
                (P.PID_SPEED,    "Speed"),
                (P.PID_FAULTS,   "Faults"),
                (P.PID_RPM,      "RPM"),
                (P.PID_BATTERY,  "Battery"),
                (P.PID_THROTTLE, "Throttle"),
            ]
            results = []
            any_hard_fail = False
            for pid, pid_name in pid_tests:
                try:
                    payload = session.read_local_id_safe(pid)
                    if payload is not None:
                        results.append(f"{pid_name}:OK({len(payload)}B)")
                        log.info("PID 0x%02X (%s): %s", pid, pid_name, payload.hex(" "))
                    else:
                        results.append(f"{pid_name}:NO_RESP")
                        log.info("PID 0x%02X (%s): no response", pid, pid_name)
                except Exception as exc:
                    results.append(f"{pid_name}:ERR")
                    log.warning("PID 0x%02X (%s): %s", pid, pid_name, exc)
                    any_hard_fail = True

            detail = "  ".join(results)
            if any_hard_fail:
                emit(6, "fail", detail)
                failed += 1
                skip_from = 7
            else:
                emit(6, "pass", detail)
                passed += 1

        # ── Stage 7: Live Data (10 s) ─────────────────────────────────────────
        if skip_from and skip_from <= 7:
            emit(7, "skip"); skipped += 1
        else:
            emit(7, "running")
            try:
                deadline = time.monotonic() + 10.0
                cycles   = 0
                last: dict = {}

                while time.monotonic() < deadline:
                    t = session.read_local_id_safe(P.PID_TEMPS)
                    if t:
                        last["coolant"] = D.decode_coolant_temp(t)
                        last["air"]     = D.decode_air_temp(t)

                    m = session.read_local_id_safe(P.PID_MAP_MAF)
                    if m:
                        last["boost"] = D.decode_boost(m)

                    r = session.read_local_id_safe(P.PID_RPM)
                    if r:
                        last["rpm"] = D.decode_rpm(r)

                    b = session.read_local_id_safe(P.PID_BATTERY)
                    if b:
                        last["batt"] = D.decode_battery(b)

                    s = session.read_local_id_safe(P.PID_SPEED)
                    if s:
                        last["spd"] = D.decode_speed(s)

                    cycles += 1
                    log.info("Cycle %d: %s", cycles,
                             "  ".join(f"{k}={v}" for k, v in last.items()))
                    time.sleep(0.3)

                summary = (
                    f"{cycles} cycles — "
                    + "  ".join(f"{k}={v}" for k, v in last.items())
                )
                emit(7, "pass", summary)
                passed += 1
            except Exception as exc:
                emit(7, "fail", str(exc))
                failed += 1

    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

        log.info("=" * 60)
        log.info("Complete — passed=%d  failed=%d  skipped=%d", passed, failed, skipped)
        log.info("Log: %s", log_path)
        log.info("=" * 60)

        # Remove file handler and restore log levels
        for lg in watched_loggers:
            lg.removeHandler(file_handler)
            lg.setLevel(saved_levels[lg.name])
        file_handler.close()

        _test_running = False

    # Broadcast completion summary
    asyncio.run_coroutine_threadsafe(
        manager.broadcast({
            "type": "obd_test",
            "data": {
                "status":   "complete",
                "log_file": Path(log_path).name,
                "passed":   passed,
                "failed":   failed,
                "skipped":  skipped,
            },
        }),
        loop,
    )


async def run_full_test(manager: ConnectionManager) -> dict:
    """
    Async entry point — called from POST /obd/full-test.

    Returns immediately; the test runs in a background thread.
    """
    global _test_running

    if _test_running:
        return {"error": "already running"}

    _test_running = True
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_filename = f"obd_{timestamp}.log"
    log_path     = str(LOG_DIR / log_filename)

    loop     = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="td5-pi-diag")
    loop.run_in_executor(executor, _run_test, manager, loop, log_path)

    return {"ok": True, "started": True, "log_file": log_filename}
