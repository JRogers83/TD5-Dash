"""
TD5 K-Line Diagnostic Tool
===========================
Progressive verification tool for TD5 ECU communication.
Follows the protocol exactly as documented in
documentation/TD5-ECU-Protocol-Technical-Reference.md.

USAGE
-----
  # Software-only stages (no vehicle, no cable needed):
  python td5_diag.py

  # All stages including vehicle communication (ignition ON, cable seated):
  python td5_diag.py --vehicle

  # Verbose — show every TX/RX byte:
  python td5_diag.py --vehicle --verbose

  # Try a range of fast-init LOW pulse timings:
  python td5_diag.py --vehicle --timing-sweep

  # Override FTDI URL:
  python td5_diag.py --vehicle --url ftdi://ftdi:232/1

STAGES
------
  Stage 1  USB / FTDI detection and bitbang verification
  Stage 2  Protocol self-test — frame checksums, seed-key LFSR vectors
  --- vehicle required below ---
  Stage 3  Fast-init + StartCommunication (81 13 F7 81 0C)
  Stage 4  StartDiagnosticSession (02 10 A0 B2)
  Stage 5  SecurityAccess seed-key authentication
  Stage 6  PID probe — test PID 0x01 (fuelling) and individual PIDs
  Stage 7  Continuous poll — display decoded values in a loop

WINDOWS PREREQUISITE
--------------------
  PyFtdi requires the libusbK driver (not the default FTDI VCP driver).
  1. Download Zadig from https://zadig.akeo.ie
  2. Plug in the KKL cable
  3. Options → List All Devices → select your FT232R device
  4. Set driver to libusbK → Replace Driver
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import sys
import time
import traceback
from typing import Optional

# ── Resolve backend path ────────────────────────────────────────────────────

def _find_backend_dir() -> Optional[str]:
    """Walk up from this script to find backend/obd/protocol.py."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = here
    for _ in range(4):
        obd = os.path.join(candidate, "backend", "obd")
        if os.path.isdir(obd) and os.path.exists(os.path.join(obd, "protocol.py")):
            return os.path.join(candidate, "backend")
        parent = os.path.dirname(candidate)
        if parent == candidate:
            break
        candidate = parent
    return None


_backend_dir = _find_backend_dir()
if _backend_dir and _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

# ── Colour helpers ──────────────────────────────────────────────────────────

try:
    import ctypes
    ctypes.windll.kernel32.SetConsoleMode(
        ctypes.windll.kernel32.GetStdHandle(-11), 7)
except Exception:
    pass

GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"


class _Tee:
    """Duplicate stdout to a log file, stripping ANSI in the file copy."""
    def __init__(self, path: str) -> None:
        self._file = open(path, "w", encoding="utf-8", buffering=1)

    def write(self, data: str) -> None:
        try:
            sys.__stdout__.write(data)
        except UnicodeEncodeError:
            sys.__stdout__.write(data.encode("ascii", "replace").decode("ascii"))
        self._file.write(re.sub(r"\033\[[0-9;]*m", "", data))

    def flush(self) -> None:
        sys.__stdout__.flush()
        self._file.flush()

    def fileno(self) -> int:
        return sys.__stdout__.fileno()


# ── Output helpers ──────────────────────────────────────────────────────────

_verbose = False

def ok(msg: str) -> None:
    print(f"  {GREEN}OK{RESET}  {msg}")

def fail(msg: str) -> None:
    print(f"  {RED}FAIL{RESET}  {msg}")

def warn(msg: str) -> None:
    print(f"  {YELLOW}WARN{RESET}  {msg}")

def info(msg: str) -> None:
    print(f"     {msg}")

def hint(msg: str) -> None:
    print(f"     {YELLOW}->{RESET} {msg}")

def hexdump(label: str, data: bytes) -> None:
    """Always print hex dumps of TX/RX data in verbose mode."""
    if _verbose and data:
        print(f"     {CYAN}{label}{RESET}: {data.hex(' ')}")


# ── Low-level K-Line I/O (standalone, no backend dependency) ────────────────

def _read_exact(ftdi, count: int, timeout_s: float = 2.0) -> bytes:
    """Read exactly count bytes from FTDI, or raise on timeout."""
    buf = bytearray()
    deadline = time.monotonic() + timeout_s
    while len(buf) < count:
        if time.monotonic() > deadline:
            return bytes(buf)  # partial — caller decides what to do
        chunk = ftdi.read_data(count - len(buf))
        if chunk:
            buf.extend(chunk)
        else:
            time.sleep(0.005)
    return bytes(buf)


def _consume_echo(ftdi, frame: bytes) -> None:
    """Read and discard the TX echo of the sent frame."""
    echo = _read_exact(ftdi, len(frame), timeout_s=0.2)
    hexdump("ECHO", echo)
    if len(echo) < len(frame):
        warn(f"Echo incomplete: got {len(echo)}/{len(frame)} bytes")


_last_rx_time = 0.0  # monotonic timestamp of last received frame

def _send_frame(ftdi, frame: bytes, inter_byte_ms: float = 5.0) -> None:
    """Send a frame byte-by-byte with P3 inter-message gap and inter-byte timing."""
    global _last_rx_time
    # P3 gap: wait at least 55ms after the last ECU response before sending.
    # Critical when the engine is running — the ECU needs time between messages.
    elapsed = time.monotonic() - _last_rx_time
    p3_gap = 0.055  # 55ms per KWP2000 spec
    if _last_rx_time > 0 and elapsed < p3_gap:
        time.sleep(p3_gap - elapsed)

    hexdump("TX", frame)
    for byte in frame:
        ftdi.write_data(bytes([byte]))
        time.sleep(inter_byte_ms / 1000.0)
    _consume_echo(ftdi, frame)


def _recv_frame(ftdi, timeout_s: float = 2.0) -> Optional[bytes]:
    """
    Read a complete KWP2000 frame including checksum.
    Returns the frame bytes (without checksum) or None on timeout.
    """
    from obd import protocol as P

    # Read format/length byte
    fmt_raw = _read_exact(ftdi, 1, timeout_s)
    if not fmt_raw:
        return None

    fmt = fmt_raw[0]
    has_addr = bool(fmt & 0x80)
    data_len = fmt & 0x3F  # bits 5-0 = length

    if has_addr:
        addr = _read_exact(ftdi, 2, timeout_s=1.0)
        data = _read_exact(ftdi, data_len, timeout_s=1.0)
        frame = fmt_raw + addr + data
    else:
        data = _read_exact(ftdi, data_len, timeout_s=1.0)
        frame = fmt_raw + data

    # Read checksum byte
    cs_raw = _read_exact(ftdi, 1, timeout_s=0.5)
    if cs_raw:
        expected_cs = P.checksum(frame)
        if cs_raw[0] != expected_cs:
            warn(f"Checksum mismatch: got 0x{cs_raw[0]:02X}, expected 0x{expected_cs:02X}")
        hexdump("RX", frame + cs_raw)
    else:
        hexdump("RX (no cs)", frame)
        warn("No checksum byte received")

    global _last_rx_time
    _last_rx_time = time.monotonic()
    return frame


def _read_available(ftdi, timeout_s: float) -> bytes:
    """Read all available bytes until the line goes quiet."""
    buf = bytearray()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        chunk = ftdi.read_data(64)
        if chunk:
            buf.extend(chunk)
            deadline = time.monotonic() + 0.1
        else:
            time.sleep(0.01)
    return bytes(buf)


# ═══════════════════════════════════════════════════════════════════════════
# STAGE 1 — USB / FTDI detection
# ═══════════════════════════════════════════════════════════════════════════

def stage1_ftdi(ftdi_url: str) -> bool:
    try:
        import usb.core
        devs = list(usb.core.find(find_all=True))
        ok(f"libusb available — {len(devs)} USB device(s) visible")
    except Exception as exc:
        fail(f"libusb not available: {exc}")
        hint("Install: pip install pyusb")
        hint("Windows: ensure Zadig has swapped the driver to libusbK")
        return False

    try:
        import pyftdi
        ok(f"pyftdi {pyftdi.__version__} imported")
    except ImportError:
        fail("pyftdi not installed")
        hint("Run: pip install pyftdi")
        return False

    from pyftdi.ftdi import Ftdi

    urls = Ftdi.list_devices()
    if not urls:
        fail("No FTDI devices found")
        hint("Is the KKL cable plugged in?")
        hint("Windows: has the driver been swapped to libusbK via Zadig?")
        return False
    ok(f"{len(urls)} FTDI device(s) found")
    for url, desc in urls:
        url_str = "ftdi://" + "/".join(str(p) for p in url)
        info(f"  {url_str}  —  {desc}")

    # Verify bitbang
    ftdi = Ftdi()
    try:
        ftdi.open_from_url(ftdi_url)
        ftdi.set_bitmode(0x01, Ftdi.BitMode.BITBANG)
        ftdi.write_data(bytes([0x01]))
        time.sleep(0.005)
        ftdi.set_bitmode(0x00, Ftdi.BitMode.RESET)
        ftdi.set_baudrate(10400)
        ok("Bitbang mode verified — fast-init mechanism is functional")
        return True
    except Exception as exc:
        fail(f"Bitbang test failed: {exc}")
        return False
    finally:
        try:
            ftdi.close()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# STAGE 2 — Protocol self-test
# ═══════════════════════════════════════════════════════════════════════════

def stage2_protocol() -> bool:
    from obd import protocol as P

    passed = True

    # ── Frame checksums ──────────────────────────────────────────────────
    # Verify against TD5-ECU-Protocol-Technical-Reference.md exact bytes

    comm = P.build_start_comm()
    expected_comm = bytes([0x81, 0x13, 0xF7, 0x81, 0x0C])
    if comm == expected_comm:
        ok(f"StartCommunication frame: {comm.hex(' ')}")
    else:
        fail(f"StartCommunication wrong: {comm.hex(' ')} (expected {expected_comm.hex(' ')})")
        passed = False

    diag = P.build_frame(P.SVC_START_DIAG, 0xA0)
    expected_diag = bytes([0x02, 0x10, 0xA0, 0xB2])
    if diag == expected_diag:
        ok(f"StartDiagnosticSession frame: {diag.hex(' ')}")
    else:
        fail(f"StartDiagnosticSession wrong: {diag.hex(' ')} (expected {expected_diag.hex(' ')})")
        passed = False

    seed_req = P.build_frame(P.SVC_SECURITY_ACCESS, P.SA_REQUEST_SEED)
    expected_seed = bytes([0x02, 0x27, 0x01, 0x2A])
    if seed_req == expected_seed:
        ok(f"SecurityAccess request-seed frame: {seed_req.hex(' ')}")
    else:
        fail(f"SecurityAccess request-seed wrong: {seed_req.hex(' ')} (expected {expected_seed.hex(' ')})")
        passed = False

    # ── Checksum verification ────────────────────────────────────────────
    cs_tests = [
        (bytes([0x81, 0x13, 0xF7, 0x81]), 0x0C, "StartComm"),
        (bytes([0x03, 0xC1, 0x57, 0x8F]), 0xAA, "StartComm response"),
        (bytes([0x02, 0x10, 0xA0]),        0xB2, "DiagSession"),
        (bytes([0x01, 0x50]),              0x51, "DiagSession response"),
        (bytes([0x02, 0x27, 0x01]),        0x2A, "RequestSeed"),
    ]
    all_cs_ok = True
    for data, expected_cs, label in cs_tests:
        got = P.checksum(data)
        if got != expected_cs:
            fail(f"Checksum {label}: got 0x{got:02X}, expected 0x{expected_cs:02X}")
            all_cs_ok = False
            passed = False
    if all_cs_ok:
        ok(f"All {len(cs_tests)} checksum test vectors pass")

    # ── Seed-key LFSR ────────────────────────────────────────────────────
    seed_key_vectors = [
        # Canonical td5keygen README example
        (0x34A5, 0x54D3, "td5keygen canonical"),
        # Edge cases
        (0x0000, 0x0001, "zero seed"),
        (0x0001, 0x0001, "seed=1"),
        (0x1234, 0x8247, "3 iterations"),
        (0xABCD, 0x85AF, "14 iterations"),
        (0xFFFF, 0x8081, "16 iterations (max)"),
        # DiscoTD5.com validation set
        (0xF0DD, 0x7D51, "DiscoTD5"),
        (0xF0DE, 0xF9A1, "DiscoTD5"),
        (0xF0DF, 0xFCD1, "DiscoTD5"),
        (0xF0E0, 0x2607, "DiscoTD5"),
        (0xF0E1, 0x9303, "DiscoTD5"),
        (0xF0E2, 0x2A0F, "DiscoTD5"),
        (0xF0E3, 0x9506, "DiscoTD5"),
        (0xF0E4, 0x321E, "DiscoTD5"),
        (0xF0E5, 0x990E, "DiscoTD5"),
    ]

    all_sk_ok = True
    for seed, expected_key, label in seed_key_vectors:
        got = P.td5_seed_to_key(seed)
        if got != expected_key:
            fail(f"Seed-key {label}: 0x{seed:04X} → 0x{got:04X} (expected 0x{expected_key:04X})")
            all_sk_ok = False
            passed = False

    if all_sk_ok:
        ok(f"All {len(seed_key_vectors)} seed-key vectors pass")
    else:
        fail("Seed-key algorithm has errors — fix protocol.td5_seed_to_key() before vehicle test")

    return passed


# ═══════════════════════════════════════════════════════════════════════════
# Vehicle stages 3–7
# ═══════════════════════════════════════════════════════════════════════════
#
# CRITICAL LESSON: Once the ECU accepts StartCommunication, it enters a
# session that persists even through fast-init pulses. You CANNOT close the
# FTDI, reopen, re-init, and re-send StartComm — the ECU rejects it with
# generalReject (0x10) and each rejection resets the session timer.
#
# Therefore: the timing sweep and the full session (DiagSession, Auth, PIDs)
# MUST run on the SAME FTDI connection. When the sweep finds a working timing,
# it keeps the connection open and chains directly into Stages 4–7.

def _do_fast_init(ftdi, low_ms: float) -> None:
    """Perform the fast-init pulse sequence."""
    from pyftdi.ftdi import Ftdi
    from obd import protocol as P

    TX_PIN = 0x01

    ftdi.purge_buffers()
    ftdi.set_bitmode(TX_PIN, Ftdi.BitMode.BITBANG)
    ftdi.write_data(bytes([0x00]))               # K-Line LOW
    time.sleep(low_ms / 1000.0)
    ftdi.write_data(bytes([TX_PIN]))             # K-Line HIGH
    time.sleep(P.FAST_INIT_HIGH_MS / 1000.0)
    ftdi.set_bitmode(0x00, Ftdi.BitMode.RESET)
    ftdi.set_baudrate(P.BAUD_RATE)
    time.sleep(P.SETTLE_MS / 1000.0)
    ftdi.purge_buffers()


def _cleanup_session(ftdi_url: str) -> None:
    """
    Send StopCommunication (0x82) to kill any leftover ECU session.

    If the ECU is in an active diagnostic session from a previous run, it will
    reject all new StartCommunication attempts with generalReject (0x10) and
    each rejected request resets the P3max timer — creating a deadlock.
    """
    from pyftdi.ftdi import Ftdi
    from obd import protocol as P

    ftdi = Ftdi()
    try:
        ftdi.open_from_url(ftdi_url)
        ftdi.set_baudrate(P.BAUD_RATE)
        ftdi.purge_buffers()

        stop_frame = P.build_frame(0x82)
        _send_frame(ftdi, stop_frame)

        resp = _read_available(ftdi, 1.0)
        if resp:
            hexdump("StopComm response", resp)
            ok("Sent StopCommunication — cleared leftover session")
        else:
            info("StopCommunication sent — no active session (expected)")

        time.sleep(1.0)
    except Exception:
        pass
    finally:
        try:
            ftdi.close()
        except Exception:
            pass


def vehicle_stages(ftdi_url: str, timing_sweep: bool) -> dict:
    """
    Stages 3–7 all on ONE FTDI connection.

    The timing sweep tries each LOW pulse timing. The FIRST timing that gets
    a successful StartCommunication response keeps the connection open and
    chains directly into DiagSession → Auth → PID probe → poll.

    Returns dict with results for each stage.
    """
    from pyftdi.ftdi import Ftdi
    from obd import protocol as P

    results = {"stage3": False, "stage4": False, "stage5": False, "stage6": False}

    # ── Stage 3: Fast-init + StartCommunication ──────────────────────────
    print(f"\n{BOLD}Stage  3  Fast-init + StartCommunication{RESET}")
    print("-" * 50)
    warn("Vehicle required — ensure ignition is ON and KKL cable is seated")
    print()

    # Clean up any leftover session
    info("Sending StopCommunication to clear any leftover session...")
    _cleanup_session(ftdi_url)

    # Passive listen
    info("Passive listen (1s) — checking for existing K-Line activity...")
    try:
        ftdi_tmp = Ftdi()
        ftdi_tmp.open_from_url(ftdi_url)
        ftdi_tmp.set_baudrate(P.BAUD_RATE)
        ftdi_tmp.purge_buffers()
        noise = _read_available(ftdi_tmp, 1.0)
        ftdi_tmp.close()
        if noise:
            warn(f"Bus activity detected: {noise.hex(' ')}")
        else:
            ok("K-Line quiet — ready for fast-init")
    except Exception as exc:
        fail(f"Passive listen failed: {exc}")
        return results

    if timing_sweep:
        timings = [15, 18, 20, 22, 23, 24, 25, 26, 27, 28, 30, 33, 35]
    else:
        timings = [22, 25, 23, 27, 30]

    info(f"Sequence: fast-init -> StartCommunication (81 13 F7 81 0C)")
    info(f"Expected response: 03 C1 57 8F AA")
    info(f"LOW pulse timings to try: {timings} ms")
    print()
    info(f"  {'LOW':>5}  {'Response':>40}  {'Result':>8}")

    ftdi = None
    working_low_ms = None

    for low_ms in timings:
        ftdi = Ftdi()
        try:
            ftdi.open_from_url(ftdi_url)
            _do_fast_init(ftdi, low_ms)

            _send_frame(ftdi, P.build_start_comm())
            resp = _recv_frame(ftdi, timeout_s=2.0)

            if resp and len(resp) >= 2:
                resp_hex = resp.hex(' ')
                if resp[1] == 0xC1:
                    info(f"  {low_ms:>4}ms  {resp_hex:>40}  {GREEN}OK{RESET}")
                    print()
                    ok(f"StartCommunication accepted — LOW pulse = {low_ms}ms")
                    if low_ms != P.FAST_INIT_LOW_MS:
                        warn(f"Working timing ({low_ms}ms) differs from protocol.py ({P.FAST_INIT_LOW_MS}ms)")
                    working_low_ms = low_ms
                    results["stage3"] = True
                    break  # keep ftdi OPEN — chain into stages 4-7
                else:
                    info(f"  {low_ms:>4}ms  {resp_hex:>40}  {RED}FAIL{RESET}")
                    # Stuck session: each rejection resets P3max timer
                    if resp[1] == 0x7F and len(resp) > 3 and resp[3] == 0x10:
                        ftdi.close()
                        ftdi = None
                        info("  (ECU in active session — sending StopCommunication)")
                        _cleanup_session(ftdi_url)
                        time.sleep(2.0)
                        continue
            else:
                info(f"  {low_ms:>4}ms  {'silent':>40}  {RED}FAIL{RESET}")

            ftdi.close()
            ftdi = None
            time.sleep(6.0)

        except Exception as exc:
            info(f"  {low_ms:>4}ms  {f'error: {exc}'[:40]:>40}  {RED}ERROR{RESET}")
            if ftdi:
                try:
                    ftdi.close()
                except Exception:
                    pass
                ftdi = None
            time.sleep(6.0)

    if ftdi is None:
        print()
        fail("No timing produced a StartCommunication response")
        print()
        hint("Diagnostic checklist:")
        hint("  1. Is ignition definitely ON? (not just accessory)")
        hint("  2. Is the KKL cable fully seated in the OBD-II port?")
        hint("     TD5 OBD port: behind the centre cubby, driver's side")
        hint("  3. Does the cable have a genuine FTDI FT232RL chip?")
        hint("  4. Try cycling ignition OFF for 10+ seconds, then ON, then re-run")
        return results

    # ── From here on, ftdi is the SAME open connection from Stage 3 ──────

    try:
        # ── Stage 4: StartDiagnosticSession ──────────────────────────────
        print(f"\n{BOLD}Stage  4  StartDiagnosticSession{RESET}")
        print("-" * 50)

        diag_frame = P.build_frame(P.SVC_START_DIAG, 0xA0)
        _send_frame(ftdi, diag_frame)
        resp = _recv_frame(ftdi, timeout_s=2.0)

        if not resp or len(resp) < 2:
            fail("No response to StartDiagnosticSession")
            return results
        if resp[1] == P.SVC_START_DIAG + P.POSITIVE_RESPONSE_OFFSET:
            ok(f"StartDiagnosticSession accepted: {resp.hex(' ')}")
            results["stage4"] = True
        elif resp[1] == 0x7F:
            error_code = resp[3] if len(resp) > 3 else 0xFF
            fail(f"StartDiagnosticSession rejected — 0x{error_code:02X}: {resp.hex(' ')}")
            _decode_error(error_code)
            return results
        else:
            fail(f"Unexpected response: {resp.hex(' ')}")
            return results

        # ── Stage 5: SecurityAccess ──────────────────────────────────────
        print(f"\n{BOLD}Stage  5  SecurityAccess authentication{RESET}")
        print("-" * 50)

        _send_frame(ftdi, P.build_frame(P.SVC_SECURITY_ACCESS, P.SA_REQUEST_SEED))
        resp = _recv_frame(ftdi, timeout_s=2.0)

        if not resp or len(resp) < 5:
            fail(f"No seed response: {resp.hex(' ') if resp else 'timeout'}")
            return results
        if resp[1] == 0x7F:
            error_code = resp[3] if len(resp) > 3 else 0xFF
            fail(f"Seed request rejected — 0x{error_code:02X}: {resp.hex(' ')}")
            _decode_error(error_code)
            return results
        if resp[1] != 0x67:
            fail(f"Unexpected seed response: {resp.hex(' ')}")
            return results

        seed = (resp[3] << 8) | resp[4]
        key = P.td5_seed_to_key(seed)
        ok(f"Seed: 0x{seed:04X} -> key: 0x{key:04X}")

        key_frame = P.build_frame(
            P.SVC_SECURITY_ACCESS, P.SA_SEND_KEY,
            (key >> 8) & 0xFF, key & 0xFF,
        )
        _send_frame(ftdi, key_frame)
        resp = _recv_frame(ftdi, timeout_s=2.0)

        if not resp or len(resp) < 2:
            fail("No response to key")
            return results
        if resp[1] == 0x67:
            ok(f"Authentication successful: {resp.hex(' ')}")
            results["stage5"] = True
        elif resp[1] == 0x7F:
            error_code = resp[3] if len(resp) > 3 else 0xFF
            fail(f"Key rejected — 0x{error_code:02X}: {resp.hex(' ')}")
            _decode_error(error_code)
            return results
        else:
            fail(f"Unexpected auth response: {resp.hex(' ')}")
            return results

        # ── Stage 6: PID probe ───────────────────────────────────────────
        print(f"\n{BOLD}Stage  6  PID probe{RESET}")
        print("-" * 50)

        pids_to_try = [
            (0x01, "Fuelling (all 22 fields)"),
            (0x09, "RPM (individual)"),
            (0x0D, "Speed (individual)"),
            (0x10, "Battery (individual)"),
            (0x1A, "Temperatures (individual)"),
            (0x1B, "Throttle (individual)"),
            (0x1C, "MAP/MAF (individual)"),
            (0x08, "Input switches A"),
            (0x20, "Current faults"),
        ]

        any_worked = False
        print()
        info(f"  {'PID':>5}  {'Description':<30}  {'Bytes':>5}  {'Raw response'}")

        for pid, desc in pids_to_try:
            req = P.build_frame(P.SVC_READ_LOCAL_ID, pid)
            _send_frame(ftdi, req)
            resp = _recv_frame(ftdi, timeout_s=2.0)

            if resp and len(resp) >= 2:
                if resp[1] == P.SVC_READ_LOCAL_ID + P.POSITIVE_RESPONSE_OFFSET:
                    payload = resp[3:] if len(resp) > 3 else b''
                    info(f"  0x{pid:02X}   {desc:<30}  {len(payload):>3}B   {GREEN}{resp.hex(' ')}{RESET}")
                    any_worked = True
                elif resp[1] == 0x7F:
                    error_code = resp[3] if len(resp) > 3 else 0xFF
                    info(f"  0x{pid:02X}   {desc:<30}   --   {YELLOW}NACK 0x{error_code:02X}{RESET}")
                else:
                    info(f"  0x{pid:02X}   {desc:<30}   --   {RED}{resp.hex(' ')}{RESET}")
            else:
                info(f"  0x{pid:02X}   {desc:<30}   --   {RED}timeout{RESET}")

        print()
        if any_worked:
            ok("ECU communication is working — at least one PID responded!")
            results["stage6"] = True
        else:
            fail("No PIDs responded")
            hint("Session may have timed out, or these PIDs are not supported by this ECU variant")

        # ── Stage 7: Continuous poll (only if PIDs worked) ───────────────
        if any_worked:
            print(f"\n{BOLD}Stage  7  Continuous poll{RESET}")
            print("-" * 50)
            info("Polling for 10 cycles (press Ctrl+C to stop)...")
            print()

            try:
                for cycle in range(1, 11):
                    req = P.build_frame(P.SVC_READ_LOCAL_ID, P.PID_FUELLING)
                    _send_frame(ftdi, req)
                    resp = _recv_frame(ftdi, timeout_s=2.0)

                    if resp and len(resp) >= 3 and resp[1] == 0x61:
                        payload = resp[3:]
                        _decode_fuelling(cycle, payload)
                    elif resp and resp[1] == 0x7F:
                        if cycle == 1:
                            info("PID 0x01 not supported, trying individual PIDs...")
                        req = P.build_frame(P.SVC_READ_LOCAL_ID, 0x09)
                        _send_frame(ftdi, req)
                        resp = _recv_frame(ftdi, timeout_s=2.0)
                        if resp and resp[1] == 0x61:
                            payload = resp[3:]
                            rpm = (payload[0] << 8 | payload[1]) if len(payload) >= 2 else 0
                            info(f"  #{cycle:>3}  RPM={rpm}  (individual PID 0x09)")
                    else:
                        warn(f"Cycle {cycle}: no response — session may have dropped")
                        break

                    time.sleep(0.5)

            except KeyboardInterrupt:
                print()
                ok("Stopped by user")

    except Exception as exc:
        fail(f"Vehicle session error: {exc}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            ftdi.close()
        except Exception:
            pass

    return results


def _decode_fuelling(cycle: int, payload: bytes) -> None:
    """Decode PID 0x01 fuelling response (22 x 16-bit fields)."""
    if len(payload) < 44:
        info(f"  #{cycle:>3}  payload too short ({len(payload)} bytes): {payload.hex(' ')}")
        return

    def u16(offset: int) -> int:
        return (payload[offset] << 8) | payload[offset + 1]

    def temp_c(offset: int) -> float:
        return (u16(offset) - 2732) / 10.0

    rpm       = u16(0)
    battery   = u16(2) / 1000.0
    speed     = u16(4)
    coolant   = temp_c(6)
    ext_temp  = temp_c(8)
    inlet     = temp_c(10)
    fuel_temp = temp_c(12)
    throttle  = u16(18) / 100.0
    map_kpa   = u16(24) / 100.0

    info(f"  #{cycle:>3}  RPM={rpm:>5}  Batt={battery:>5.1f}V  Spd={speed:>3}kph  "
         f"Cool={coolant:>5.1f}C  Inlet={inlet:>5.1f}C  Fuel={fuel_temp:>5.1f}C  "
         f"Thr={throttle:>5.1f}%  MAP={map_kpa:>6.1f}kPa")


# ═══════════════════════════════════════════════════════════════════════════
# Error code decoder
# ═══════════════════════════════════════════════════════════════════════════

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

def _decode_error(code: int) -> None:
    meaning = _ERROR_CODES.get(code, "unknown")
    hint(f"Error 0x{code:02X} = {meaning}")
    if code == 0x35:
        hint("Invalid key — seed-key algorithm may be wrong")
    elif code == 0x36:
        hint("Too many failed attempts — wait 10+ seconds and try again")
    elif code == 0x33:
        hint("Security access denied — authentication required first")


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main() -> None:
    global _verbose

    parser = argparse.ArgumentParser(description="TD5 K-Line Diagnostic Tool")
    parser.add_argument("--vehicle", action="store_true",
                        help="Run vehicle stages (3–7) — requires ignition ON")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show every TX/RX byte")
    parser.add_argument("--timing-sweep", action="store_true",
                        help="Try a wide range of fast-init timings at Stage 3")
    parser.add_argument("--url", default="ftdi://ftdi:232/1",
                        help="PyFtdi device URL (default: ftdi://ftdi:232/1)")
    parser.add_argument("--stage", type=int, default=0,
                        help="Run only this stage (0 = all applicable)")
    args = parser.parse_args()

    _verbose = args.verbose

    # Log file
    log_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"td5_diag_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
    )
    sys.stdout = _Tee(log_path)

    print(f"\n{BOLD}{'=' * 66}{RESET}")
    print(f"{BOLD}  TD5 K-Line Diagnostic Tool{RESET}")
    print(f"{BOLD}{'=' * 66}{RESET}")
    print(f"  FTDI URL     : {args.url}")
    print(f"  Vehicle mode : {'YES' if args.vehicle else 'NO (software-only stages)'}")
    print(f"  Verbose      : {'YES' if args.verbose else 'NO'}")
    print(f"  Timing sweep : {'YES' if args.timing_sweep else 'NO'}")
    print(f"  Log file     : {log_path}")
    print(f"  Backend path : {_backend_dir or f'{RED}NOT FOUND{RESET}'}")
    print()

    if not _backend_dir:
        fail("Cannot find backend/obd/protocol.py — aborting")
        sys.exit(1)

    # Track results
    stages_run    = 0
    stages_passed = 0
    stages_failed = 0
    working_low_ms = None

    def run_stage(num: int, name: str, fn, *fn_args):
        nonlocal stages_run, stages_passed, stages_failed
        if args.stage and args.stage != num:
            return None
        stages_run += 1
        print(f"\n{BOLD}Stage {num:>2}  {name}{RESET}")
        print("-" * 50)
        result = fn(*fn_args)
        if result is None or result is False:
            stages_failed += 1
        else:
            stages_passed += 1
        return result

    # ── Non-vehicle stages ──────────────────────────────────────────────
    s1 = run_stage(1, "USB / FTDI detection", stage1_ftdi, args.url)
    if s1 is False and not args.stage:
        fail("Stage 1 failed — cannot proceed")
        _print_summary(stages_run, stages_passed, stages_failed, log_path)
        return

    s2 = run_stage(2, "Protocol self-test", stage2_protocol)
    if s2 is False and not args.stage:
        fail("Stage 2 failed — fix protocol code before vehicle test")
        _print_summary(stages_run, stages_passed, stages_failed, log_path)
        return

    if not args.vehicle and not (args.stage and args.stage >= 3):
        print()
        info("Non-vehicle stages complete. Run with --vehicle for ECU communication.")
        _print_summary(stages_run, stages_passed, stages_failed, log_path)
        return

    # ── Vehicle stages 3–7 on one connection ─────────────────────────────
    # The ECU does not allow re-init after a successful StartCommunication.
    # All vehicle stages MUST chain on the same FTDI connection.
    vs = vehicle_stages(args.url, args.timing_sweep)
    for stage_key in ["stage3", "stage4", "stage5", "stage6"]:
        stages_run += 1
        if vs.get(stage_key):
            stages_passed += 1
        else:
            stages_failed += 1

    _print_summary(stages_run, stages_passed, stages_failed, log_path)


def _print_summary(run: int, passed: int, failed: int, log_path: str) -> None:
    print(f"\n{BOLD}{'=' * 66}{RESET}")
    print(f"{BOLD}  SUMMARY{RESET}")
    print(f"{BOLD}{'=' * 66}{RESET}")
    print(f"  Passed: {GREEN}{passed}{RESET}   Failed: {RED}{failed}{RESET}   "
          f"Total: {run}")
    print(f"  Log saved to: {log_path}")
    print()


if __name__ == "__main__":
    main()
