"""
TD5 K-Line Parameter Sweep
==========================
Automatically tries every meaningful combination of fast-init timing,
frame addressing, and session parameters to find what works with your
specific ECU and cable — without you having to edit any code between runs.

Run once in the vehicle with ignition ON:
    python td5_obd_sweep.py

The tool runs ~50 combinations and takes about 3-4 minutes. Everything
is logged to a timestamped file. A summary table at the end shows the
highest level reached for each attempt, making it obvious which (if any)
combination worked.

WHAT IS SWEPT
─────────────
  LOW pulse   : 15, 18, 20, 22, 24, 25, 26, 27, 28, 30, 33, 35ms
  PURGE mode  : purge BEFORE init (correct) vs AFTER (original bug)
  Header byte : 0x80 (physical addressing) vs 0xC1 (functional addressing)
  Session ID  : 0x89 (extended diag) vs 0x81 (default session) vs 0xA0

Each combination attempts:
  • Fast-init pulse
  • Keyword byte read (any response from ECU logged, but not required)
  • StartDiagnosticSession request — sent REGARDLESS of keyword bytes
  • If session accepted: SecurityAccess seed-key exchange

RESULT LEVELS
─────────────
  0  No ECU response at all
  1  Keyword bytes received after fast-init
  2  StartDiagnosticSession positive response (0x50)
  3  SecurityAccess seed received (0x67)
  4  SecurityAccess key accepted — full authentication (best possible)

The first combination reaching level 2+ is printed in green and the
sweep continues to confirm. At the end, the best combination is printed
with the exact constants to put in protocol.py.
"""

from __future__ import annotations

import datetime
import os
import re
import sys
import time
import traceback
from typing import Optional

# ── Colour output ─────────────────────────────────────────────────────────────

try:
    import ctypes
    ctypes.windll.kernel32.SetConsoleMode(   # type: ignore[attr-defined]
        ctypes.windll.kernel32.GetStdHandle(-11), 7)   # type: ignore[attr-defined]
except Exception:
    pass

GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

# ── Log tee ───────────────────────────────────────────────────────────────────

class _Tee:
    def __init__(self, path: str) -> None:
        self._file = open(path, "w", encoding="utf-8", buffering=1)

    def write(self, data: str) -> None:
        sys.__stdout__.write(data)
        self._file.write(re.sub(r"\033\[[0-9;]*m", "", data))

    def flush(self) -> None:
        sys.__stdout__.flush()
        self._file.flush()

    def fileno(self) -> int:
        return sys.__stdout__.fileno()


# ── OBD path resolution ───────────────────────────────────────────────────────

def _find_obd_dir() -> Optional[str]:
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = here
    for _ in range(4):
        obd = os.path.join(candidate, "backend", "obd")
        if os.path.isdir(obd) and os.path.exists(os.path.join(obd, "protocol.py")):
            return obd
        parent = os.path.dirname(candidate)
        if parent == candidate:
            break
        candidate = parent
    for rel in ["backend/obd", "obd", "."]:
        p = os.path.normpath(os.path.join(os.getcwd(), rel))
        if os.path.isdir(p) and os.path.exists(os.path.join(p, "protocol.py")):
            return p
    return None


# ── Sweep parameters ──────────────────────────────────────────────────────────

# Pulse widths to try (milliseconds)
LOW_MS_VALUES  = [15, 18, 20, 22, 24, 25, 26, 27, 28, 30, 33, 35]
HIGH_MS        = 25    # consistent with all reference implementations
SETTLE_MS      = 50    # settle after returning to UART

# Frame addressing header byte
#   0x80 = physical addressing (standard KWP2000)
#   0xC1 = functional addressing (used by some TD5 implementations)
HEADER_BYTES   = [0x80, 0xC1]

# StartDiagnosticSession sub-function
#   0x89 = extended diagnostic session (pyTD5Tester, Ekaitza_Itzali)
#   0x81 = default session
#   0xA0 = programming session (some ECUs need this first)
SESSION_SUBFNS = [0x89, 0x81, 0xA0]

# Whether to purge the RX buffer after returning to UART mode.
# False = correct: purge BEFORE the init pulse so keyword bytes aren't lost.
# True  = original behaviour: purge after (may flush keyword bytes).
PURGE_MODES    = [False, True]

ECU_ADDR    = 0x10
TESTER_ADDR = 0xF1
BAUD_RATE   = 10400

# Seconds to wait between attempts so the ECU can return to idle
INTER_ATTEMPT_DELAY = 3.0

# How long to listen for keyword bytes after fast-init
KEYWORD_TIMEOUT = 1.5

# How long to wait for a KWP2000 response frame
RESPONSE_TIMEOUT = 1.0


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _checksum(data: bytes) -> int:
    return sum(data) & 0xFF


def _build_frame(header_byte: int, service: int, *payload: int) -> bytes:
    body  = bytes([ECU_ADDR, TESTER_ADDR, service] + list(payload))
    frame = bytes([header_byte, len(body)]) + body
    return frame + bytes([_checksum(frame)])


def _read_available(ftdi, timeout_s: float) -> bytes:
    """Read all bytes available until the line goes quiet."""
    buf      = bytearray()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        chunk = ftdi.read_data(64)
        if chunk:
            buf.extend(chunk)
            deadline = time.monotonic() + 0.1  # extend on each byte received
        else:
            time.sleep(0.01)
    return bytes(buf)


def _recv_frame(ftdi, timeout_s: float = 1.0) -> Optional[bytes]:
    """
    Try to read a complete KWP2000 frame.
    Returns the raw frame bytes, or None on timeout/error.
    """
    buf      = bytearray()
    deadline = time.monotonic() + timeout_s

    # Read header (2 bytes)
    while len(buf) < 2:
        if time.monotonic() > deadline:
            return None
        chunk = ftdi.read_data(2 - len(buf))
        if chunk:
            buf.extend(chunk)
        else:
            time.sleep(0.005)

    if buf[0] not in (0x80, 0xC1):
        # Not a KWP2000 frame — return raw bytes anyway for diagnostics
        # Try to read a bit more so we have context
        extra = _read_available(ftdi, 0.3)
        return bytes(buf) + extra

    body_len = buf[1]
    needed   = body_len + 1  # body + checksum
    while len(buf) < 2 + needed:
        if time.monotonic() > deadline:
            return bytes(buf)  # partial — still useful for diagnostics
        chunk = ftdi.read_data(2 + needed - len(buf))
        if chunk:
            buf.extend(chunk)
        else:
            time.sleep(0.005)

    return bytes(buf)


def _send_bytes(ftdi, data: bytes, inter_byte_ms: float = 5.0) -> None:
    for byte in data:
        ftdi.write_data(bytes([byte]))
        time.sleep(inter_byte_ms / 1000.0)


# ── Single attempt ────────────────────────────────────────────────────────────

def _attempt(ftdi_url: str, low_ms: float, header_byte: int,
             session_subfn: int, purge_after_init: bool) -> dict:
    """
    Run one complete attempt: fast-init → keyword read → DiagSession → auth.
    Returns a result dict with keys:
        level        int  0-4 (see module docstring)
        keyword_hex  str  hex of keyword bytes or ""
        diag_hex     str  hex of DiagSession response or ""
        seed_hex     str  hex of seed bytes or ""
        key_hex      str  hex of computed key or ""
        error        str  exception message if something crashed
    """
    from pyftdi.ftdi import Ftdi   # type: ignore

    result = {
        "level": 0,
        "keyword_hex": "",
        "diag_hex":    "",
        "seed_hex":    "",
        "key_hex":     "",
        "error":       "",
    }

    ftdi = Ftdi()
    try:
        ftdi.open_from_url(ftdi_url)

        TX_PIN = 0x01

        if not purge_after_init:
            # Correct: purge BEFORE the init pulse so no old bytes linger
            ftdi.purge_buffers()

        # ── Fast-init pulse ───────────────────────────────────────────────────
        ftdi.set_bitmode(TX_PIN, Ftdi.BitMode.BITBANG)
        ftdi.write_data(bytes([0x00]))          # K-Line LOW
        time.sleep(low_ms / 1000.0)
        ftdi.write_data(bytes([TX_PIN]))        # K-Line HIGH
        time.sleep(HIGH_MS / 1000.0)
        ftdi.set_bitmode(0x00, Ftdi.BitMode.RESET)
        ftdi.set_baudrate(BAUD_RATE)

        if purge_after_init:
            ftdi.purge_buffers()               # original (buggy) behaviour

        time.sleep(SETTLE_MS / 1000.0)

        # ── Keyword bytes ─────────────────────────────────────────────────────
        kw = _read_available(ftdi, KEYWORD_TIMEOUT)
        if kw:
            result["keyword_hex"] = kw.hex(" ")
            result["level"] = max(result["level"], 1)

        # ── StartDiagnosticSession ────────────────────────────────────────────
        # Sent REGARDLESS of whether keyword bytes arrived — some TD5s wake up
        # but don't send keyword bytes, yet still respond to KWP2000 requests.
        diag_frame = _build_frame(header_byte, 0x10, session_subfn)
        _send_bytes(ftdi, diag_frame)
        diag_resp = _recv_frame(ftdi, RESPONSE_TIMEOUT)

        if diag_resp:
            result["diag_hex"] = diag_resp.hex(" ")
            # Positive response for StartDiagnosticSession = 0x50
            if len(diag_resp) >= 5 and diag_resp[4] == 0x50:
                result["level"] = max(result["level"], 2)

                # ── SecurityAccess — request seed ─────────────────────────────
                seed_frame = _build_frame(header_byte, 0x27, 0x01)
                _send_bytes(ftdi, seed_frame)
                seed_resp = _recv_frame(ftdi, RESPONSE_TIMEOUT)

                if seed_resp and len(seed_resp) >= 8 and seed_resp[4] == 0x67:
                    seed = (seed_resp[6] << 8) | seed_resp[7]
                    result["seed_hex"] = f"0x{seed:04X}"
                    result["level"]    = max(result["level"], 3)

                    # ── SecurityAccess — send key ──────────────────────────────
                    from protocol import td5_seed_to_key   # noqa
                    key       = td5_seed_to_key(seed)
                    result["key_hex"] = f"0x{key:04X}"
                    key_frame = _build_frame(
                        header_byte, 0x27, 0x02,
                        (key >> 8) & 0xFF, key & 0xFF,
                    )
                    _send_bytes(ftdi, key_frame)
                    key_resp = _recv_frame(ftdi, RESPONSE_TIMEOUT)

                    if key_resp and len(key_resp) >= 5 and key_resp[4] == 0x67:
                        result["level"] = 4

    except Exception as exc:
        result["error"] = str(exc)
    finally:
        try:
            ftdi.close()
        except Exception:
            pass

    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    # ── Setup ─────────────────────────────────────────────────────────────────
    log_path = f"td5_sweep_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    sys.stdout = _Tee(log_path)   # type: ignore[assignment]

    obd_dir = _find_obd_dir()
    if obd_dir and obd_dir not in sys.path:
        sys.path.insert(0, obd_dir)

    ftdi_url = "ftdi://ftdi:232/1"

    print(f"\n{BOLD}{'═'*66}{RESET}")
    print(f"{BOLD}  TD5 K-Line Parameter Sweep{RESET}")
    print(f"{BOLD}{'═'*66}{RESET}")
    print(f"  FTDI URL : {ftdi_url}")
    print(f"  Log file : {log_path}")
    print(f"  OBD path : {obd_dir or f'{RED}NOT FOUND{RESET}'}")
    print()

    if not obd_dir:
        print(f"  {RED}Cannot find backend/obd/protocol.py — aborting.{RESET}")
        sys.exit(1)

    # ── Passive listen test ───────────────────────────────────────────────────
    # Before doing anything, open the UART and listen for 2 seconds.
    # If the ECU or cable has any activity on the line, it will show here.
    # This also confirms the RX path works.
    print(f"{BOLD}── Passive Listen (2s, no init pulse) ──────────────────────────────{RESET}")
    print("  Opening UART and listening for any K-Line activity...")
    try:
        from pyftdi.ftdi import Ftdi   # type: ignore
        ftdi = Ftdi()
        ftdi.open_from_url(ftdi_url)
        ftdi.set_baudrate(BAUD_RATE)
        ftdi.purge_buffers()
        passive = _read_available(ftdi, 2.0)
        ftdi.close()
        if passive:
            print(f"  {GREEN}Bytes received passively: {passive.hex(' ')}{RESET}")
            print(f"  → The RX path is working. ECU may already be active.")
        else:
            print(f"  {YELLOW}No bytes received passively — K-Line is quiet (expected with ignition off){RESET}")
            print(f"  → RX path may still work; fast-init is needed to wake the ECU.")
    except Exception as exc:
        print(f"  {RED}Passive listen failed: {exc}{RESET}")
    print()

    # ── Build combination list ────────────────────────────────────────────────
    combos = []
    for low_ms in LOW_MS_VALUES:
        for header in HEADER_BYTES:
            for subfn in SESSION_SUBFNS:
                for purge_after in PURGE_MODES:
                    combos.append((low_ms, header, subfn, purge_after))

    total = len(combos)
    print(f"{BOLD}── Sweep: {total} combinations ──────────────────────────────────────────{RESET}")
    print(f"  LOW pulse  : {LOW_MS_VALUES}")
    print(f"  Header     : {[hex(h) for h in HEADER_BYTES]}")
    print(f"  Session ID : {[hex(s) for s in SESSION_SUBFNS]}")
    print(f"  Purge mode : False=correct, True=original")
    print(f"  HIGH={HIGH_MS}ms  SETTLE={SETTLE_MS}ms  Recovery={INTER_ATTEMPT_DELAY}s between attempts")
    print()
    print(f"  Estimated time: {total * (KEYWORD_TIMEOUT + RESPONSE_TIMEOUT + INTER_ATTEMPT_DELAY) / 60:.0f}–{total * (KEYWORD_TIMEOUT + RESPONSE_TIMEOUT + INTER_ATTEMPT_DELAY + 1) / 60:.0f} minutes")
    print()

    # Column header
    col = f"  {'#':>3}  {'LOW':>4}  {'HDR':>4}  {'SUBFN':>5}  {'PURGE':>5}  {'KW bytes':>20}  {'DIAG resp':>20}  {'Level':>5}  Note"
    print(col)
    print(f"  {'─'*len(col.rstrip())}")

    results   = []
    best      = None

    for i, (low_ms, header, subfn, purge_after) in enumerate(combos, 1):
        label = (
            f"  {i:>3}  {low_ms:>3}ms  "
            f"0x{header:02X}  "
            f"0x{subfn:02X}  "
            f"{'after':>5}"
            if purge_after else
            f"  {i:>3}  {low_ms:>3}ms  "
            f"0x{header:02X}  "
            f"0x{subfn:02X}  "
            f"{'before':>5}"
        )

        r = _attempt(ftdi_url, low_ms, header, subfn, purge_after)
        results.append((low_ms, header, subfn, purge_after, r))

        level_colours = {0: RED, 1: YELLOW, 2: CYAN, 3: CYAN, 4: GREEN}
        lc = level_colours.get(r["level"], "")

        kw_str   = (r["keyword_hex"][:18] + "…") if len(r["keyword_hex"]) > 18 else r["keyword_hex"] or "—"
        diag_str = (r["diag_hex"][:18] + "…")    if len(r["diag_hex"]) > 18    else r["diag_hex"]    or "—"
        note     = r["error"][:30] if r["error"] else {
            0: "",
            1: "kw bytes only",
            2: "DiagSession OK!",
            3: f"seed={r['seed_hex']} key={r['key_hex']}",
            4: "FULL AUTH OK",
        }.get(r["level"], "")

        print(
            f"{label}  "
            f"{kw_str:>20}  "
            f"{diag_str:>20}  "
            f"{lc}{r['level']:>5}{RESET}  "
            f"{note}"
        )

        if r["level"] >= 2 and best is None:
            best = (low_ms, header, subfn, purge_after, r)
            print(f"\n  {GREEN}{BOLD}★ First working combination found above! Continuing to confirm...{RESET}\n")

        # Inter-attempt recovery delay
        time.sleep(INTER_ATTEMPT_DELAY)

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{BOLD}{'═'*66}{RESET}")
    print(f"{BOLD}  SWEEP SUMMARY{RESET}")
    print(f"{BOLD}{'═'*66}{RESET}")

    max_level = max(r["level"] for *_, r in results)

    if max_level == 0:
        print(f"\n  {RED}No ECU response in any combination.{RESET}")
        print()
        print("  Possible causes:")
        print("  1. Ignition was not ON during the sweep")
        print("  2. The KKL cable is not seated in the OBD port")
        print("  3. The cable's K-Line level shifter is not working")
        print("     → Check: does the cable get warm at all when plugged into OBD?")
        print("     → Check: the OBD port is behind the centre cubby, driver's side")
        print("  4. The FTDI TX pin is not wired to the K-Line in this cable")
        print("     → Some KKL cables use a different pin mapping")
        print("  5. The cable may need the 12V K-Line voltage to activate its")
        print("     level shifter before the FTDI TX can drive the bus")
        print()
        print("  Next step: try ELM327 or a dedicated OBD scanner to confirm")
        print("  the OBD port is alive, then report back.")

    elif max_level == 1:
        print(f"\n  {YELLOW}ECU sent keyword bytes but rejected all session requests.{RESET}")
        print()
        best_kw = [(low, hdr, sub, pur, r) for low, hdr, sub, pur, r in results if r["level"] >= 1]
        print(f"  Keyword bytes received in {len(best_kw)} combination(s):")
        for low, hdr, sub, pur, r in best_kw:
            print(f"    LOW={low}ms  HDR=0x{hdr:02X}  SUB=0x{sub:02X}  purge={'after' if pur else 'before'}  kw={r['keyword_hex']}")
        print()
        print("  This is progress — the ECU IS alive and responding to fast-init.")
        print("  The session request framing or sub-function may need adjusting.")
        print("  Try running the sweep again — the ECU may respond to a timing variant.")

    else:
        working = [(low, hdr, sub, pur, r) for low, hdr, sub, pur, r in results if r["level"] >= 2]
        print(f"\n  {GREEN}SUCCESS — {len(working)} working combination(s) found.{RESET}")
        print()
        print("  Best combination(s) to use in protocol.py:")
        print()
        seen = set()
        for low, hdr, sub, pur, r in working:
            key = (low, hdr, sub)
            if key in seen:
                continue
            seen.add(key)
            print(f"  {GREEN}★{RESET}  LOW={low}ms  HDR=0x{hdr:02X}  SESSION_SUBFN=0x{sub:02X}  level={r['level']}")
            if r["level"] == 4:
                print(f"      Full auth: seed={r['seed_hex']} → key={r['key_hex']}")

        print()
        low, hdr, sub, pur, r = working[0]
        print(f"  Update backend/obd/protocol.py:")
        print(f"    FAST_INIT_LOW_MS   = {low}")
        if hdr != 0x80:
            print(f"  Update backend/obd/protocol.py build_frame() header byte:")
            print(f"    Change 0x80 → 0x{hdr:02X}")
        if sub != 0x89:
            print(f"  Update backend/obd/service.py _start_diagnostic_session():")
            print(f"    Change sub-function 0x89 → 0x{sub:02X}")

    print(f"\n  Full log saved to: {log_path}")
    print()


if __name__ == "__main__":
    main()
