"""
TD5 OBD service — session management and async WebSocket publisher.

Manages the full K-Line lifecycle:
  open connection → start diagnostic session → seed-key auth → poll loop

Runs the blocking K-Line I/O in a ThreadPoolExecutor so FastAPI's async
event loop is never blocked. On any connection failure the thread retries
automatically after a short delay — the frontend will simply stop receiving
engine updates until the ECU is back.

PIDs 0x09 (RPM), 0x10 (Battery), and 0x1B (Throttle) only respond when the
engine is running. With ignition-only, the poll loop broadcasts available data
(temps, speed, MAP) with defaults for the unavailable fields.

Configuration (environment variables):
  TD5_FTDI_URL      PyFtdi device URL        default: ftdi://ftdi:232/1
  TD5_POLL_INTERVAL Poll interval in seconds  default: 1.0
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

from ws_hub import ConnectionManager
from .connection import KLineConnection, KLineError
from . import decoder as D
from . import protocol as P

log = logging.getLogger(__name__)

FTDI_URL      = os.getenv("TD5_FTDI_URL",      "ftdi://ftdi:232/1")
POLL_INTERVAL = float(os.getenv("TD5_POLL_INTERVAL", "1.0"))
RETRY_DELAY_S = 5.0   # seconds to wait before reconnecting after a failure
FAULT_POLL_INTERVAL_S = 30.0  # read fault codes every N seconds


# ── Session-level K-Line operations ───────────────────────────────────────────

class TD5Session:
    """
    Manages a single authenticated KWP2000 session with the TD5 ECU.

    One session = one open KLineConnection that has been initialised,
    authenticated, and is ready to accept live data requests.
    """

    def __init__(self, conn: KLineConnection) -> None:
        self._conn = conn

    def start(self) -> None:
        """Full session init: StartDiagnosticSession → SecurityAccess auth."""
        self._start_diagnostic_session()
        self._authenticate()

    def _start_diagnostic_session(self) -> None:
        """
        KWP2000 service 0x10 — StartDiagnosticSession.

        Sub-function 0xA0 = TD5 manufacturer-specific diagnostic mode.
        Frame sent: 02 10 A0 B2  →  ECU replies: 01 50 51
        """
        frame = P.build_frame(P.SVC_START_DIAG, 0xA0)
        self._conn.send(frame)
        resp = self._conn.recv_frame()
        self._assert_positive(resp, P.SVC_START_DIAG, "StartDiagnosticSession")
        log.info("Diagnostic session started.")

    def _authenticate(self) -> None:
        """
        KWP2000 service 0x27 — SecurityAccess seed-key handshake.

        Confirmed on vehicle: seed 0xBA08 → key 0x70DC (engine running).
        """
        # Step 1 — request seed
        self._conn.send(P.build_frame(P.SVC_SECURITY_ACCESS, P.SA_REQUEST_SEED))
        resp = self._conn.recv_frame()
        self._assert_positive(resp, P.SVC_SECURITY_ACCESS, "SecurityAccess/seed")

        seed = (resp[3] << 8) | resp[4]
        log.debug("ECU seed: 0x%04X", seed)

        # Step 2 — compute key
        key = P.td5_seed_to_key(seed)
        log.debug("Computed key: 0x%04X", key)

        # Step 3 — send key
        key_frame = P.build_frame(
            P.SVC_SECURITY_ACCESS,
            P.SA_SEND_KEY,
            (key >> 8) & 0xFF,
            key & 0xFF,
        )
        self._conn.send(key_frame)
        resp = self._conn.recv_frame()
        self._assert_positive(resp, P.SVC_SECURITY_ACCESS, "SecurityAccess/key")
        log.info("ECU authentication successful.")

    def read_local_id(self, pid: int) -> bytes:
        """
        KWP2000 service 0x21 — ReadDataByLocalIdentifier.

        Returns the data payload bytes with header stripped.
        Raises KLineError on timeout or negative response.
        """
        self._conn.send(P.build_frame(P.SVC_READ_LOCAL_ID, pid))
        resp = self._conn.recv_frame()
        self._assert_positive(resp, P.SVC_READ_LOCAL_ID, f"ReadLocalId(0x{pid:02X})")
        return resp[3:]

    def read_local_id_safe(self, pid: int) -> bytes | None:
        """read_local_id() but returns None on failure instead of raising.

        Used in the poll loop for PIDs that only respond with the engine running
        (0x09 RPM, 0x10 Battery, 0x1B Throttle). A timeout on these is expected
        with ignition-only and should not crash the poll cycle.
        """
        try:
            return self.read_local_id(pid)
        except KLineError:
            log.debug("PID 0x%02X unavailable (engine may not be running)", pid)
            return None

    def send_tester_present(self) -> None:
        """Send TesterPresent (0x3E) keepalive to prevent session timeout."""
        try:
            self._conn.send(P.build_frame(P.SVC_TESTER_PRESENT))
            resp = self._conn.recv_frame()
            # Positive response = 0x7E
        except KLineError:
            log.debug("TesterPresent got no response — session may have dropped")

    # Standard KWP2000 negative response error codes
    _ERROR_CODES = {
        0x10: "generalReject",
        0x11: "serviceNotSupported",
        0x12: "subFunctionNotSupported",
        0x13: "incorrectMessageLengthOrInvalidFormat",
        0x22: "conditionsNotCorrect",
        0x31: "requestOutOfRange",
        0x33: "securityAccessDenied",
        0x35: "invalidKey",
        0x36: "exceededNumberOfAttempts",
        0x78: "requestCorrectlyReceivedResponsePending",
    }

    @staticmethod
    def _assert_positive(frame: bytes, service: int, name: str) -> None:
        """Check that frame is a positive response; raise KLineError with
        decoded error code if it's a negative response (0x7F)."""
        expected = service + P.POSITIVE_RESPONSE_OFFSET
        if len(frame) < 2 or frame[1] != expected:
            actual = frame[1] if len(frame) >= 2 else 0xFF
            detail = ""
            if actual == 0x7F and len(frame) >= 4:
                code = frame[3]
                meaning = TD5Session._ERROR_CODES.get(code, "unknown")
                detail = f" — NRC 0x{code:02X} ({meaning})"
            raise KLineError(
                f"{name}: expected 0x{expected:02X}, got 0x{actual:02X}"
                f"{detail} — frame: {frame.hex(' ')}"
            )


# ── Blocking poll loop (runs in a worker thread) ───────────────────────────────

def _poll_loop(manager: ConnectionManager, loop: asyncio.AbstractEventLoop) -> None:
    """
    Blocking poll loop — runs in a dedicated ThreadPoolExecutor thread.

    Continuously re-establishes the K-Line session if the connection drops.
    Thread-safe: uses asyncio.run_coroutine_threadsafe to post data back to
    the event loop for WebSocket broadcast.

    PIDs that only respond with the engine running (0x09, 0x10, 0x1B) are read
    with read_local_id_safe() — timeouts on these produce default values (0)
    rather than crashing the poll cycle.
    """
    while True:
        log.info("Connecting to TD5 ECU at %s …", FTDI_URL)
        try:
            with KLineConnection(FTDI_URL) as conn:
                session = TD5Session(conn)
                session.start()
                log.info("TD5 session active. Polling every %.1f s.", POLL_INTERVAL)

                # Read fault codes once at session start
                fault_codes: list[int] = []
                try:
                    fault_payload = session.read_local_id(P.PID_FAULTS)
                    fault_codes = D.decode_faults(fault_payload)
                    if fault_codes:
                        log.info("Stored fault codes: %s",
                                 [f"0x{c:04X}" for c in fault_codes])
                except KLineError:
                    log.debug("Could not read fault codes at session start")

                last_fault_read = time.monotonic()
                last_successful_read = time.monotonic()

                while True:
                    try:
                        # ── Always-available PIDs ────────────────────────
                        temps = session.read_local_id(P.PID_TEMPS)
                        coolant   = D.decode_coolant_temp(temps)
                        air_temp  = D.decode_air_temp(temps)
                        ext_temp  = D.decode_external_temp(temps)
                        fuel_temp = D.decode_fuel_temp(temps)

                        boost = D.decode_boost(
                            session.read_local_id(P.PID_MAP_MAF))
                        speed = D.decode_speed(
                            session.read_local_id(P.PID_SPEED))

                        last_successful_read = time.monotonic()

                        # ── Engine-running PIDs (safe — None on timeout) ─
                        rpm_payload = session.read_local_id_safe(P.PID_RPM)
                        rpm = D.decode_rpm(rpm_payload) if rpm_payload else None

                        batt_payload = session.read_local_id_safe(P.PID_BATTERY)
                        battery = D.decode_battery(batt_payload) if batt_payload else None

                        thr_payload = session.read_local_id_safe(P.PID_THROTTLE)
                        throttle = D.decode_throttle(thr_payload) if thr_payload else None

                        # ── Periodic fault code refresh ──────────────────
                        if time.monotonic() - last_fault_read > FAULT_POLL_INTERVAL_S:
                            try:
                                fault_payload = session.read_local_id(P.PID_FAULTS)
                                fault_codes = D.decode_faults(fault_payload)
                                last_fault_read = time.monotonic()
                            except KLineError:
                                pass  # keep previous fault_codes

                        # ── Broadcast whatever data we have ──────────────
                        asyncio.run_coroutine_threadsafe(
                            manager.broadcast({
                                "type": "engine",
                                "data": {
                                    "rpm":              round(rpm) if rpm is not None else 0,
                                    "coolant_temp_c":   coolant,
                                    "inlet_air_temp_c": air_temp,
                                    "external_temp_c":  ext_temp,
                                    "boost_bar":        boost,
                                    "throttle_pct":     throttle if throttle is not None else 0.0,
                                    "battery_v":        battery if battery is not None else 0.0,
                                    "road_speed_kph":   round(speed) if speed is not None else 0,
                                    "fuel_temp_c":      fuel_temp,
                                    "fault_codes":      fault_codes,
                                },
                            }),
                            loop,
                        )

                        time.sleep(POLL_INTERVAL)

                    except KLineError as exc:
                        # A failure on an always-available PID means the
                        # connection is broken — reconnect.
                        log.warning("K-Line read error: %s — reconnecting", exc)
                        break

                    # If all PIDs timed out (engine off, ECU sluggish), send
                    # a keepalive to prevent the session from expiring.
                    if time.monotonic() - last_successful_read > 3.0:
                        session.send_tester_present()

        except KLineError as exc:
            log.error("K-Line connection failed: %s — retrying in %.0f s", exc, RETRY_DELAY_S)
        except Exception:
            log.exception("Unexpected error in OBD poll loop — retrying in %.0f s", RETRY_DELAY_S)

        time.sleep(RETRY_DELAY_S)


# ── Async entry point ──────────────────────────────────────────────────────────

async def broadcast_loop(manager: ConnectionManager) -> None:
    """
    Async entry point — called from main.py lifespan when TD5_MOCK=0.

    Runs the blocking K-Line I/O in a dedicated background thread so
    FastAPI's event loop remains free for WebSocket and HTTP handling.
    """
    loop     = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="td5-obd")
    await loop.run_in_executor(executor, _poll_loop, manager, loop)
