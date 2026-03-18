"""
TD5 OBD service — session management and async WebSocket publisher.

Manages the full K-Line lifecycle:
  open connection → start diagnostic session → seed-key auth → poll loop

Runs the blocking K-Line I/O in a ThreadPoolExecutor so FastAPI's async
event loop is never blocked. On any connection failure the thread retries
automatically after a short delay — the frontend will simply stop receiving
engine updates until the ECU is back.

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

        Sub-function 0x89 selects the ECU's extended diagnostic mode which
        enables live data access. Verify sub-function against pyTD5Tester if
        the ECU rejects the request.
        """
        frame = P.build_frame(P.SVC_START_DIAG, 0x89)
        self._conn.send(frame)
        resp = self._conn.recv_frame()
        self._assert_positive(resp, P.SVC_START_DIAG, "StartDiagnosticSession")
        log.info("Diagnostic session started.")

    def _authenticate(self) -> None:
        """
        KWP2000 service 0x27 — SecurityAccess seed-key handshake.

        Step 1: request seed  (subfunction 0x01)
        Step 2: compute key from seed using TD5 LFSR algorithm
        Step 3: send key      (subfunction 0x02)
        Step 4: verify positive response

        If authentication is rejected, the most likely cause is an incorrect
        key polynomial in protocol.td5_seed_to_key(). Verify against
        github.com/pajacobson/td5keygen before debugging hardware.
        """
        # Step 1 — request seed
        self._conn.send(P.build_frame(P.SVC_SECURITY_ACCESS, P.SA_REQUEST_SEED))
        resp = self._conn.recv_frame()
        self._assert_positive(resp, P.SVC_SECURITY_ACCESS, "SecurityAccess/seed")

        # Seed is the two bytes immediately after [svc_response, subfunction]
        # Frame layout: [0x80][len][ECU][TST][0x67][0x01][seed_hi][seed_lo][csum]
        seed = (resp[6] << 8) | resp[7]
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
        KWP2000 service 0x21 — ReadDataByLocalIdentifier for a single PID.

        The TD5 ECU does not return all parameters in one frame; each parameter
        group requires a separate request with its own sub-identifier (pid).
        See protocol.py for PID constants and payload layouts.

        Returns the data payload bytes with header, service byte, identifier
        echo, and checksum already stripped. Pass to the appropriate
        decoder function in decoder.py.
        """
        self._conn.send(P.build_frame(P.SVC_READ_LOCAL_ID, pid))
        resp = self._conn.recv_frame()
        self._assert_positive(resp, P.SVC_READ_LOCAL_ID, f"ReadDataByLocalIdentifier(0x{pid:02X})")
        # Strip: [0x80][len][ECU_addr][TST_addr][svc_response][pid_echo] = 6 bytes
        # Last byte (checksum) already validated by recv_frame
        return resp[6:-1]

    @staticmethod
    def _assert_positive(frame: bytes, service: int, name: str) -> None:
        expected = service + P.POSITIVE_RESPONSE_OFFSET
        if len(frame) < 5 or frame[4] != expected:
            actual = frame[4] if len(frame) >= 5 else 0xFF
            raise KLineError(
                f"{name}: expected positive response 0x{expected:02X}, "
                f"got 0x{actual:02X} — full frame: {frame.hex(' ')}"
            )


# ── Blocking poll loop (runs in a worker thread) ───────────────────────────────

def _poll_loop(manager: ConnectionManager, loop: asyncio.AbstractEventLoop) -> None:
    """
    Blocking poll loop — runs in a dedicated ThreadPoolExecutor thread.

    Continuously re-establishes the K-Line session if the connection drops.
    Thread-safe: uses asyncio.run_coroutine_threadsafe to post data back to
    the event loop for WebSocket broadcast.
    """
    while True:
        log.info("Connecting to TD5 ECU at %s …", FTDI_URL)
        try:
            with KLineConnection(FTDI_URL) as conn:
                session = TD5Session(conn)
                session.start()
                log.info("TD5 session active. Polling every %.1f s.", POLL_INTERVAL)

                while True:
                    try:
                        # The TD5 ECU uses separate per-PID requests — no monolithic frame.
                        rpm       = D.decode_rpm(      session.read_local_id(P.PID_RPM))
                        temps     = session.read_local_id(P.PID_TEMPS)
                        coolant   = D.decode_coolant_temp(temps)
                        air_temp  = D.decode_air_temp(temps)
                        fuel_temp = D.decode_fuel_temp(temps)
                        boost     = D.decode_boost(    session.read_local_id(P.PID_MAP_MAF))
                        battery   = D.decode_battery(  session.read_local_id(P.PID_BATTERY))
                        speed     = D.decode_speed(    session.read_local_id(P.PID_SPEED))
                        throttle  = D.decode_throttle( session.read_local_id(P.PID_THROTTLE))

                        # Only broadcast if all critical readings decoded successfully
                        if None in (rpm, coolant, air_temp, fuel_temp, boost, battery, speed, throttle):
                            log.warning(
                                "One or more PID reads returned None — "
                                "rpm=%s coolant=%s air=%s fuel=%s boost=%s batt=%s spd=%s thr=%s",
                                rpm, coolant, air_temp, fuel_temp, boost, battery, speed, throttle,
                            )
                        else:
                            asyncio.run_coroutine_threadsafe(
                                manager.broadcast({
                                    "type": "engine",
                                    "data": {
                                        "rpm":              round(rpm),
                                        "coolant_temp_c":   coolant,
                                        "inlet_air_temp_c": air_temp,
                                        "boost_bar":        boost,
                                        "throttle_pct":     throttle,
                                        "battery_v":        battery,
                                        "road_speed_kph":   round(speed),
                                        "fuel_temp_c":      fuel_temp,
                                    },
                                }),
                                loop,
                            )

                        time.sleep(POLL_INTERVAL)

                    except KLineError as exc:
                        log.warning("K-Line read error: %s — reconnecting", exc)
                        break   # break inner loop → re-init outer loop

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
