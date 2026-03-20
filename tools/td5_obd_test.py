"""
TD5 KKL Cable — Progressive Verification Test Suite
====================================================
Runs a series of tests from "can we see the USB cable" through to
"can we read live engine data from the TD5 ECU".

Each stage builds on the previous. If a stage fails, later stages are
skipped — there is no point attempting K-Line comms if the cable is not
visible to PyFtdi.

USAGE
-----
  # Install dependency first (once):
  pip install pyftdi

  # Run all tests (PC — no vehicle):
  python td5_obd_test.py

  # Run all tests (laptop in vehicle, ignition on):
  python td5_obd_test.py --vehicle

  # Show every raw TX/RX frame byte on the K-Line (critical for debugging):
  python td5_obd_test.py --vehicle --verbose

  # Save everything to a file (terminal + frame bytes) for later analysis:
  python td5_obd_test.py --vehicle --verbose --log td5_test_run.txt

  # Override FTDI URL if you have multiple FTDI devices:
  python td5_obd_test.py --url ftdi://ftdi:232/1

  # Re-run a single stage after a fix (e.g. after adjusting fast-init timing):
  python td5_obd_test.py --vehicle --stage 8 --verbose

WINDOWS PREREQUISITE
--------------------
  PyFtdi uses libusb directly and requires the libusbK driver instead of
  the default FTDI VCP driver.

  1. Download Zadig from https://zadig.akeo.ie
  2. Plug in the KKL cable
  3. Options → List All Devices → select your FT232R device
  4. Set driver to libusbK → Replace Driver

  After this step, the cable will no longer appear as a COM port — that is
  expected. Run this script again and Stage 1 should pass.

STAGES
------
  Stage 1  USB / libusb — is libusb available? (Windows libusbK check)
  Stage 2  PyFtdi import — is pyftdi installed?
  Stage 3  FTDI device detected — does PyFtdi see the FT232RL?
  Stage 4  FTDI open — can we open the device in UART mode?
  Stage 5  Bitbang mode — can we enter and exit GPIO bitbang mode?
  Stage 6  Protocol self-test — frame build, checksum, seed-key vectors
  Stage 7  Decoder self-test — all PID decoders against known byte sequences
  --- vehicle required below this line (--vehicle flag) ---
  Stage 8  Fast-init — K-Line wake pulse sent, ECU keyword bytes received
  Stage 9  Diagnostic session — StartDiagnosticSession positive response
  Stage 10 Authentication — seed-key handshake accepted by ECU
  Stage 11 PID sweep — every PID read and decoded, raw hex logged
"""

from __future__ import annotations

import argparse
import logging
import struct
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, List, Optional

# ── Colour output (works on Windows 10+ with ANSI enabled) ───────────────────

ANSI = True
try:
    import ctypes
    kernel32 = ctypes.windll.kernel32          # type: ignore[attr-defined]
    kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
except AttributeError:
    pass   # non-Windows — ANSI works natively
except Exception:
    ANSI = False   # Windows console doesn't support ANSI sequences

GREEN  = "\033[92m" if ANSI else ""
YELLOW = "\033[93m" if ANSI else ""
RED    = "\033[91m" if ANSI else ""
CYAN   = "\033[96m" if ANSI else ""
BOLD   = "\033[1m"  if ANSI else ""
RESET  = "\033[0m"  if ANSI else ""

def ok(msg: str)   -> None: print(f"  {GREEN}✓{RESET}  {msg}")
def warn(msg: str) -> None: print(f"  {YELLOW}⚠{RESET}  {msg}")
def fail(msg: str) -> None: print(f"  {RED}✗{RESET}  {msg}")
def info(msg: str) -> None: print(f"     {CYAN}{msg}{RESET}")
def hint(msg: str) -> None: print(f"     {YELLOW}→ {msg}{RESET}")


# ── Log/tee support ───────────────────────────────────────────────────────────

class _Tee:
    """
    Mirrors everything written to stdout to a log file simultaneously.
    Assigned to sys.stdout so both print() output and logging StreamHandlers
    are captured without any changes to the rest of the script.
    """
    def __init__(self, path: str) -> None:
        self._file = open(path, "w", encoding="utf-8", buffering=1)

    def write(self, data: str) -> None:
        sys.__stdout__.write(data)
        # Strip ANSI escape sequences for the file
        import re
        self._file.write(re.sub(r"\033\[[0-9;]*m", "", data))

    def flush(self) -> None:
        sys.__stdout__.flush()
        self._file.flush()

    def fileno(self) -> int:
        return sys.__stdout__.fileno()

    def close(self) -> None:
        self._file.close()


def _configure_logging(verbose: bool, log_path: Optional[str]) -> None:
    """
    Set up Python logging so that raw TX/RX frame bytes from connection.py
    are visible.  connection.py emits every frame at DEBUG level — without
    this setup those messages are silently discarded.

    verbose=True : DEBUG level (every TX/RX byte, timing, all detail)
    verbose=False: INFO level  (session milestones only — less noisy)
    """
    level = logging.DEBUG if verbose else logging.INFO
    fmt   = logging.Formatter("  %(name)-20s %(levelname)-7s %(message)s")

    root = logging.getLogger()
    root.setLevel(level)

    # Console handler — attached to real stdout so it survives Tee assignment
    ch = logging.StreamHandler(sys.__stdout__)
    ch.setLevel(level)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    if log_path:
        import re
        class _StripAnsi(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:
                return re.sub(r"\033\[[0-9;]*m", "", super().format(record))

        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(level)
        fh.setFormatter(_StripAnsi("  %(name)-20s %(levelname)-7s %(message)s"))
        root.addHandler(fh)


# ── Result tracking ───────────────────────────────────────────────────────────

@dataclass
class StageResult:
    number:  int
    name:    str
    passed:  bool
    skipped: bool = False
    notes:   List[str] = field(default_factory=list)


results: List[StageResult] = []
_ftdi_url: str = "ftdi://ftdi:232/1"

# Set by Stage 8 if a working timing is found; Stages 9-11 use this to skip
# re-testing and so the log contains the confirmed timing.
_working_low_ms: Optional[int] = None

# LOW pulse durations to try, in order.  25 ms is the ISO 9141-2 nominal value;
# the bracketing values cover the tolerance range observed across TD5 ECUs.
_TIMING_CANDIDATES = [25, 23, 27, 21, 29, 19, 31]


def run_stage(
    number: int,
    name: str,
    fn: Callable[[], bool],
    skip_if_failed: Optional[int] = None,
) -> bool:
    """Run a single stage, catch exceptions, record result."""
    width = 60
    label = f"Stage {number:>2}  {name}"
    print(f"\n{BOLD}{label}{RESET}")
    print("─" * min(len(label), width))

    # Skip if a prerequisite stage failed
    if skip_if_failed is not None:
        prereq = next((r for r in results if r.number == skip_if_failed), None)
        if prereq and not prereq.passed:
            warn(f"Skipped — Stage {skip_if_failed} ({prereq.name}) did not pass.")
            results.append(StageResult(number, name, passed=False, skipped=True))
            return False

    try:
        passed = fn()
    except Exception as exc:
        fail(f"Unhandled exception: {exc}")
        info(traceback.format_exc().strip())
        passed = False

    results.append(StageResult(number, name, passed=passed))
    return passed


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 1 — USB / libusb availability
# ═════════════════════════════════════════════════════════════════════════════

def stage1_libusb() -> bool:
    """
    On Windows, PyFtdi requires libusb (via the libusbK driver applied with
    Zadig). Attempt to import usb.core to check if libusb is reachable.

    NOTE: This stage always returns True — it is purely diagnostic. A failure
    here is printed as a warning with Zadig instructions but does not block
    stages 2–5. PyFtdi will give the definitive answer at stage 3.
    """
    try:
        import usb.core  # type: ignore
        devices = list(usb.core.find(find_all=True))
        ok(f"libusb available — {len(devices)} USB device(s) visible")
    except ImportError:
        warn("pyusb not installed — will be pulled in by pyftdi")
        info("This is fine; pyftdi installs pyusb automatically.")
    except Exception as exc:
        warn(f"libusb probe: {exc}")
        info("This usually means the Zadig driver swap has not been done yet.")
        hint("Download Zadig: https://zadig.akeo.ie")
        hint("Plug in KKL cable → Options → List All Devices → select FT232R device")
        hint("Set driver to libusbK → click Replace Driver")
        hint("After swapping the driver, re-run this script")
        info("Continuing anyway — PyFtdi will confirm at stage 3.")
    return True   # always non-fatal; stage 3 gives the definitive answer


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 2 — PyFtdi import
# ═════════════════════════════════════════════════════════════════════════════

def stage2_pyftdi_import() -> bool:
    try:
        import pyftdi  # noqa: F401
        import pyftdi.ftdi  # noqa: F401
        ok(f"pyftdi imported successfully (version {pyftdi.__version__})")
        return True
    except ImportError:
        fail("pyftdi is not installed")
        hint("Run:  pip install pyftdi")
        return False


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 3 — FTDI device detected
# ═════════════════════════════════════════════════════════════════════════════

def stage3_ftdi_detected() -> bool:
    from pyftdi.ftdi import Ftdi  # type: ignore

    urls = Ftdi.list_devices()

    if not urls:
        fail("No FTDI devices found")
        hint("Is the KKL cable plugged in?")
        hint("On Windows: has the driver been swapped to libusbK via Zadig?")
        hint("Try:  python -m pyftdi.ftdi  for verbose device listing")
        return False

    ok(f"{len(urls)} FTDI device(s) found:")
    for url, desc in urls:
        # url is a tuple — format it as a string
        url_str = "ftdi://" + "/".join(str(p) for p in url)
        info(f"  {url_str}  —  {desc}")

    # Check for FT232R specifically
    ft232_found = any(
        "ft232" in str(desc).lower() or "232r" in str(desc).lower()
        for _, desc in urls
    )
    if ft232_found:
        ok("FT232R confirmed — this is the expected chip for the KKL cable")
    else:
        warn("Could not confirm FT232R chip in device description — may still work")
        hint("Expected chip: FTDI FT232RL (VID 0403, PID 6001)")

    return True


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 4 — FTDI open in UART mode
# ═════════════════════════════════════════════════════════════════════════════

def stage4_ftdi_open() -> bool:
    from pyftdi.ftdi import Ftdi  # type: ignore

    ftdi = Ftdi()
    try:
        ftdi.open_from_url(_ftdi_url)
        ok(f"Opened FTDI device at {_ftdi_url}")

        # Confirm we can configure baud rate
        ftdi.set_baudrate(10400)
        ok("Baud rate set to 10,400 (TD5 K-Line rate)")

        ftdi.purge_buffers()
        ok("Buffers purged")
        return True

    except Exception as exc:
        fail(f"Could not open FTDI device: {exc}")
        hint(f"URL used: {_ftdi_url}")
        hint("If you have multiple FTDI devices, specify the correct one with --url")
        hint("Run python -m pyftdi.ftdi to see available URLs")
        return False
    finally:
        try:
            ftdi.close()
        except Exception:
            pass


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 5 — Bitbang mode (required for fast-init)
# ═════════════════════════════════════════════════════════════════════════════

def stage5_bitbang() -> bool:
    """
    Fast-init requires direct GPIO control of the TX pin via FTDI bitbang
    mode. This stage confirms we can enter bitbang mode and return to UART
    mode without errors — WITHOUT actually driving the K-Line.
    """
    from pyftdi.ftdi import Ftdi  # type: ignore

    TX_PIN = 0x01
    ftdi = Ftdi()
    try:
        ftdi.open_from_url(_ftdi_url)

        # Enter bitbang
        ftdi.set_bitmode(TX_PIN, Ftdi.BitMode.BITBANG)
        ok("Entered BITBANG mode (TX pin as GPIO output)")

        # Write a byte — TX held high (idle), not connected to anything
        ftdi.write_data(bytes([TX_PIN]))
        ok("GPIO write succeeded (TX held HIGH — safe, nothing connected)")

        time.sleep(0.005)

        # Return to UART
        ftdi.set_bitmode(0x00, Ftdi.BitMode.RESET)
        ftdi.set_baudrate(10400)
        ok("Returned to UART mode at 10,400 baud")
        ok("Bitbang round-trip confirmed — fast-init mechanism is functional")
        return True

    except Exception as exc:
        fail(f"Bitbang mode failed: {exc}")
        hint("This is unusual if Stage 4 passed — try unplugging and re-plugging the cable")
        return False
    finally:
        try:
            ftdi.close()
        except Exception:
            pass


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 6 — Protocol self-test (no hardware)
# ═════════════════════════════════════════════════════════════════════════════

def stage6_protocol() -> bool:
    """
    Tests frame building, checksum, and the seed-key LFSR algorithm.
    All pure Python — no hardware needed.
    """
    passed = True

    # ── Checksum ──────────────────────────────────────────────────────────────
    # Manual: 0x80+0x04+0x10+0xF1+0x10+0x89 = 0x11E → mod 256 = 0x1E
    from protocol import checksum, build_frame, build_start_comm, SVC_START_DIAG, td5_seed_to_key

    # ── Checksum function ─────────────────────────────────────────────────────
    # The checksum() function is retained for reference.  The confirmed TD5
    # short-format protocol does not include checksums in frames — this test
    # just verifies the function itself is arithmetically correct.
    data  = bytes([0x80, 0x04, 0x10, 0xF1, 0x10, 0x89])
    csum  = checksum(data)
    if csum == 0x1E:
        ok(f"Checksum function correct: 0x{csum:02X}  (note: not used in short-format frames)")
    else:
        fail(f"Checksum function wrong: got 0x{csum:02X}, expected 0x1E")
        passed = False

    # ── StartCommunication frame ───────────────────────────────────────────────
    # Confirmed bytes from Ekaitza_Itzali: 81 13 F7 81
    comm = build_start_comm()
    expected_comm = bytes([0x81, 0x13, 0xF7, 0x81])
    if comm == expected_comm:
        ok(f"StartCommunication frame correct: {comm.hex(' ')}")
    else:
        fail(f"StartCommunication frame wrong: {comm.hex(' ')} (expected {expected_comm.hex(' ')})")
        passed = False

    # ── StartDiagnosticSession frame ──────────────────────────────────────────
    # Confirmed bytes from Ekaitza_Itzali: 02 10 A0  (sub-fn 0xA0, no address bytes)
    frame = build_frame(SVC_START_DIAG, 0xA0)
    expected_diag = bytes([0x02, 0x10, 0xA0])
    if frame == expected_diag:
        ok(f"StartDiagnosticSession frame correct: {frame.hex(' ')}")
    else:
        fail(f"StartDiagnosticSession frame wrong: {frame.hex(' ')} (expected {expected_diag.hex(' ')})")
        passed = False

    # ── Seed-key algorithm ────────────────────────────────────────────────────
    # The TD5 uses a variable-iteration LFSR — NOT a fixed-polynomial Galois LFSR.
    # Iteration count (1–16) is derived from 4 bits of the seed itself.
    #
    # Verified against two independent primary sources:
    #   github.com/pajacobson/td5keygen  (keygen.c / keytool.py)
    #   github.com/hairyone/pyTD5Tester  (TD5Tester.py calculate_key())
    #
    # Canonical vector from td5keygen README: 0x34A5 → 0x54D3
    # To cross-check: clone td5keygen and run: python keytool.py <seed_hex>

    seed_key_vectors = [
        (0x34A5, 0x54D3),    # canonical td5keygen README example — primary reference
        (0x0000, 0x0001),    # 1 iteration: tap=0, tmp=0, bit3&bit13 both 0 → LSB forced 1
        (0x0001, 0x0001),    # 2 iterations: same tap path, LSB stays 1
        (0x1234, 0x8247),    # 3 iterations
        (0xABCD, 0x85AF),    # 14 iterations
        (0xFFFF, 0x8081),    # 16 iterations (maximum)
    ]

    info("Seed-key vectors (variable-iteration LFSR, verified vs td5keygen):")
    info(f"  {'Seed':>8}   {'Expected':>8}   {'Got':>8}   Result")

    all_ok = True
    for seed, expected_key in seed_key_vectors:
        got = td5_seed_to_key(seed)
        match = got == expected_key
        status = f"{GREEN}PASS{RESET}" if match else f"{RED}FAIL{RESET}"
        info(f"  0x{seed:04X}  →  0x{expected_key:04X}     0x{got:04X}     {status}")
        if not match:
            all_ok = False
            passed = False

    if all_ok:
        ok("All seed-key vectors match — algorithm is correct")
    else:
        fail("One or more seed-key vectors do not match")
        hint("Cross-check: github.com/pajacobson/td5keygen — python keytool.py <seed>")
        hint("An incorrect key will cause the ECU to reject authentication")

    return passed


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 7 — Decoder self-test (no hardware)
# ═════════════════════════════════════════════════════════════════════════════

def stage7_decoders() -> bool:
    """
    Tests every PID decoder against hand-computed byte sequences.
    Pure Python — no hardware needed.
    """
    from decoder import (
        decode_rpm, decode_coolant_temp, decode_air_temp,
        decode_fuel_temp, decode_boost, decode_battery,
        decode_speed, decode_throttle,
    )

    passed = True
    cases = []

    # ── RPM ──────────────────────────────────────────────────────────────────
    # 850 RPM: 850 = 0x0352
    result = decode_rpm(bytes([0x03, 0x52]))
    cases.append(("RPM",    result == 850.0,    result,  850.0,   "850 RPM"))

    # 0 RPM (engine off)
    result = decode_rpm(bytes([0x00, 0x00]))
    cases.append(("RPM",    result == 0.0,      result,  0.0,     "0 RPM (engine off)"))

    # 3000 RPM: 3000 = 0x0BB8
    result = decode_rpm(bytes([0x0B, 0xB8]))
    cases.append(("RPM",    result == 3000.0,   result,  3000.0,  "3000 RPM"))

    # ── Coolant temp ─────────────────────────────────────────────────────────
    # 90°C: (90 + 273.2) × 10 = 3632 = 0x0E30
    # 14-byte payload; coolant at offset 0
    payload_90c = bytes([0x0E, 0x30, 0x00, 0x00,   # coolant
                         0x0C, 0x1C, 0x00, 0x00,   # air ~17.6°C
                         0x0B, 0xD6, 0x00, 0x00,   # external ~7.4°C
                         0x0D, 0x5C, 0x00, 0x00])  # fuel ~67.4°C

    result = decode_coolant_temp(payload_90c)
    cases.append(("Coolant", abs(result - 90.0) < 0.15, result, 90.0, "90.0°C coolant"))

    # ── Air temp ─────────────────────────────────────────────────────────────
    # ~17.6°C at offset 4: (17.6 + 273.2) × 10 = 2908 = 0x0B5C
    payload_air = bytes([0x0E, 0x30, 0x00, 0x00,
                         0x0B, 0x5C, 0x00, 0x00,
                         0x0B, 0xD6, 0x00, 0x00,
                         0x0D, 0x5C, 0x00, 0x00])
    result = decode_air_temp(payload_air)
    cases.append(("Air temp", abs(result - 17.6) < 0.15, result, 17.6, "17.6°C air temp"))

    # ── Fuel temp ────────────────────────────────────────────────────────────
    # ~45°C at offset 12: (45 + 273.2) × 10 = 3182 = 0x0C6E
    payload_fuel = bytes([0x0E, 0x30, 0x00, 0x00,
                          0x0B, 0x5C, 0x00, 0x00,
                          0x0B, 0xD6, 0x00, 0x00,
                          0x0C, 0x6E, 0x00, 0x00])
    result = decode_fuel_temp(payload_fuel)
    cases.append(("Fuel temp", abs(result - 45.0) < 0.15, result, 45.0, "45.0°C fuel temp"))

    # ── Boost ─────────────────────────────────────────────────────────────────
    # 1.5 bar absolute (≈ 0.487 bar gauge): 15000 = 0x3A98
    result = decode_boost(bytes([0x3A, 0x98, 0x00, 0x00]))
    expected_gauge = round(1.5 - 1.01325, 3)
    cases.append(("Boost", abs(result - expected_gauge) < 0.001, result, expected_gauge,
                  f"1.5 bar abs → {expected_gauge} bar gauge"))

    # Atmospheric (no boost): 1.01325 bar absolute → 0.0 bar gauge (clamped)
    # 10133 = 0x2795
    result = decode_boost(bytes([0x27, 0x95, 0x00, 0x00]))
    cases.append(("Boost", result == 0.0, result, 0.0, "Atmospheric → 0.0 bar gauge"))

    # ── Battery ───────────────────────────────────────────────────────────────
    # 12.6V: 12600 = 0x3138
    result = decode_battery(bytes([0x31, 0x38]))
    cases.append(("Battery", result == 12.6, result, 12.6, "12.6V battery"))

    # 14.4V (charging): 14400 = 0x3840
    result = decode_battery(bytes([0x38, 0x40]))
    cases.append(("Battery", result == 14.4, result, 14.4, "14.4V charging"))

    # ── Speed ──────────────────────────────────────────────────────────────────
    result = decode_speed(bytes([0x00]))
    cases.append(("Speed", result == 0.0, result, 0.0, "0 kph (stationary)"))

    result = decode_speed(bytes([0x50]))
    cases.append(("Speed", result == 80.0, result, 80.0, "80 kph"))

    # ── Throttle ───────────────────────────────────────────────────────────────
    # Pedal at ~50%: P1 = 2.5V (2500 = 0x09C4), supply = 5.0V (5000 = 0x1388)
    # pct = (2500/5000)*100 = 50.0%
    payload_thr = struct.pack(">HHHHH", 2500, 2500, 250, 250, 5000)
    result = decode_throttle(payload_thr)
    cases.append(("Throttle", abs(result - 50.0) < 0.1, result, 50.0, "50% throttle"))

    # Pedal fully released: P1 = 0.5V (500 = 0x01F4), supply = 5.0V
    payload_idle = struct.pack(">HHHHH", 500, 500, 50, 50, 5000)
    result = decode_throttle(payload_idle)
    cases.append(("Throttle", abs(result - 10.0) < 0.1, result, 10.0, "10% throttle (idle)"))

    # Guard: supply = 0 should not divide by zero
    payload_zero_supply = struct.pack(">HHHHH", 500, 500, 50, 50, 0)
    result = decode_throttle(payload_zero_supply)
    cases.append(("Throttle", result == 0.0, result, 0.0, "Zero supply guard"))

    # ── Short payload guards ───────────────────────────────────────────────────
    cases.append(("RPM None",  decode_rpm(b'\x01') is None,      None, None, "Short payload → None"))
    cases.append(("Batt None", decode_battery(b'\x31') is None,  None, None, "Short payload → None"))
    cases.append(("Spd None",  decode_speed(b'') is None,        None, None, "Empty payload → None"))

    # ── Print results ──────────────────────────────────────────────────────────
    ok_count   = 0
    fail_count = 0

    info(f"  {'Decoder':<12}  {'Description':<35}  {'Got':>10}  {'Exp':>10}  Result")
    for name, test_passed, got, expected, desc in cases:
        got_str = f"{got}" if got is not None else "None"
        exp_str = f"{expected}" if expected is not None else "None"
        status  = f"{GREEN}PASS{RESET}" if test_passed else f"{RED}FAIL{RESET}"
        info(f"  {name:<12}  {desc:<35}  {got_str:>10}  {exp_str:>10}  {status}")
        if test_passed:
            ok_count += 1
        else:
            fail_count += 1
            passed = False

    print()
    if fail_count == 0:
        ok(f"All {ok_count} decoder tests passed")
    else:
        fail(f"{fail_count} decoder test(s) failed — check formulas in decoder.py")

    return passed


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 8 — Fast-init + first session contact (vehicle required)
# ═════════════════════════════════════════════════════════════════════════════
#
# Design notes:
#   KWP2000 fast-init does NOT require the ECU to volunteer keyword bytes after
#   the wake pulse — the ECU is completely silent until it receives a valid
#   request.  Previous versions of this stage waited for keyword bytes and
#   therefore always appeared to fail even when the K-Line was working.
#
#   This version:
#     1. Passive-listens for 1 s to confirm the line is quiet.
#     2. Tries each LOW-pulse timing in _TIMING_CANDIDATES, sending a full
#        StartDiagnosticSession request after each pulse, and waits for the
#        ECU to return a KWP2000 positive response (service byte 0x50).
#     3. Passes as soon as one timing elicits a positive response; records
#        the working LOW_MS so Stages 9–11 can report it.
#     4. If no timing works, prints per-attempt detail to aid diagnosis.



def _read_bus(ftdi, timeout_s: float = 0.5) -> bytes:
    """Read all available bytes from the FTDI device until the line goes quiet."""
    buf      = bytearray()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        chunk = ftdi.read_data(32)
        if chunk:
            buf.extend(chunk)
        else:
            time.sleep(0.01)
    return bytes(buf)


def _strip_echo(raw: bytes, sent: bytes) -> bytes:
    """Remove the TX echo of sent_frame from the start of raw."""
    if raw[:len(sent)] == sent:
        return raw[len(sent):]
    return raw


def _find_response(data: bytes, expected_svc: int):
    """
    Scan data for a KWP2000 short-format ECU response containing expected_svc.

    Confirmed short-format: [FMT][SVC][data…]
      FMT bit 7 = 0: no address bytes; bits 6-0 = data byte count
      SVC is at index [i+1]

    Returns the frame bytes starting at the match, or None.
    """
    for i in range(len(data) - 1):
        fmt = data[i]
        if fmt & 0x80:
            continue   # skip address-bearing frames (not expected in ECU responses)
        svc = data[i + 1]
        if svc == expected_svc or svc == 0x7F:
            return data[i:]
    return None


def _do_init_attempt(ftdi, low_ms: int) -> dict:
    """
    One complete fast-init attempt using the confirmed Ekaitza_Itzali sequence:
      pulse → StartCommunication (81 13 F7 81) → StartDiagnosticSession (02 10 A0)

    Returns a dict with keys:
      level   0 = no ECU response to StartCommunication
              1 = StartCommunication accepted (0xC1) but StartDiag failed/silent
              2 = full session established (StartDiag positive 0x50)
      comm_raw, diag_raw — hex strings of stripped (echo-removed) received bytes
    """
    import protocol as P
    from pyftdi.ftdi import Ftdi  # type: ignore

    TX_PIN = 0x01

    # Purge BEFORE the pulse (start with clean RX buffer)
    ftdi.purge_buffers()

    # Fast-init wake pulse
    ftdi.set_bitmode(TX_PIN, Ftdi.BitMode.BITBANG)
    ftdi.write_data(bytes([0x00]))
    time.sleep(low_ms / 1000.0)
    ftdi.write_data(bytes([TX_PIN]))
    time.sleep(P.FAST_INIT_HIGH_MS / 1000.0)

    # Return to UART then purge pulse artifacts.
    # The ECU is silent after fast-init until it receives StartCommunication,
    # so purging here does not risk losing genuine ECU data.
    ftdi.set_bitmode(0x00, Ftdi.BitMode.RESET)
    ftdi.set_baudrate(P.BAUD_RATE)
    time.sleep(P.SETTLE_MS / 1000.0)
    ftdi.purge_buffers()

    # ── Step 1: StartCommunication (81 13 F7 81) ───────────────────────────────
    comm_frame = P.build_start_comm()
    for byte in comm_frame:
        ftdi.write_data(bytes([byte]))
        time.sleep(P.P4_INTER_BYTE_MS / 1000.0)

    comm_raw     = _read_bus(ftdi, timeout_s=0.5)
    comm_stripped = _strip_echo(comm_raw, comm_frame)
    comm_resp    = _find_response(comm_stripped, P.SVC_START_COMMUNICATION + 0x40)  # 0xC1
    comm_hex     = comm_stripped.hex(' ') if comm_stripped else ''

    if comm_resp is None or comm_resp[1] == 0x7F:
        return {'level': 0, 'comm_raw': comm_hex, 'diag_raw': ''}

    # ── Step 2: StartDiagnosticSession (02 10 A0) ──────────────────────────────
    diag_frame = P.build_frame(P.SVC_START_DIAG, 0xA0)
    for byte in diag_frame:
        ftdi.write_data(bytes([byte]))
        time.sleep(P.P4_INTER_BYTE_MS / 1000.0)

    diag_raw     = _read_bus(ftdi, timeout_s=0.5)
    diag_stripped = _strip_echo(diag_raw, diag_frame)
    diag_resp    = _find_response(diag_stripped, P.SVC_START_DIAG + 0x40)  # 0x50
    diag_hex     = diag_stripped.hex(' ') if diag_stripped else ''

    if diag_resp is not None and diag_resp[1] == 0x50:
        return {'level': 2, 'comm_raw': comm_hex, 'diag_raw': diag_hex}

    return {'level': 1, 'comm_raw': comm_hex, 'diag_raw': diag_hex}


def stage8_fast_init() -> bool:
    global _working_low_ms

    import protocol as P
    from pyftdi.ftdi import Ftdi  # type: ignore

    warn("Vehicle required — ensure ignition is ON before proceeding")
    TX_PIN = 0x01

    # ── Passive listen ─────────────────────────────────────────────────────────
    info("Step 1/2  Passive listen (1 s) — K-Line should be quiet before init")
    try:
        ftdi = Ftdi()
        ftdi.open_from_url(_ftdi_url)
        ftdi.set_baudrate(P.BAUD_RATE)
        ftdi.purge_buffers()
        noise = _read_bus(ftdi, timeout_s=1.0)
        ftdi.close()
        if noise:
            warn(f"Unexpected bus activity before init: {noise.hex(' ')}")
            hint("Another device may be active on the K-Line bus")
        else:
            ok("K-Line quiet — ready to send wake pulse")
    except Exception as exc:
        fail(f"Passive listen failed: {exc}")
        return False

    # ── Timing sweep ───────────────────────────────────────────────────────────
    # Confirmed sequence (Ekaitza_Itzali):
    #   fast-init → StartCommunication (81 13 F7 81) → StartDiagnosticSession (02 10 A0)
    # ECU addr 0x13, tester addr 0xF7, sub-function 0xA0 — all baked into protocol.py
    info(f"Step 2/2  Confirmed sequence (Ekaitza_Itzali):")
    info(f"          fast-init → StartCommunication 81 13 F7 81 → StartDiagSession 02 10 A0")
    info(f"          LOW timings to try: {_TIMING_CANDIDATES} ms  "
         f"HIGH={P.FAST_INIT_HIGH_MS}ms  SETTLE={P.SETTLE_MS}ms")
    print()
    info(f"  {'LOW':>5}  {'StartComm (0xC1)':>18}  {'StartDiag (0x50)':>18}")

    for low_ms in _TIMING_CANDIDATES:
        try:
            ftdi = Ftdi()
            ftdi.open_from_url(_ftdi_url)

            result = _do_init_attempt(ftdi, low_ms)

            ftdi.close()

            level    = result['level']
            comm_str = result['comm_raw'][:25]
            diag_str = result['diag_raw'][:25]

            if level == 2:
                comm_lbl = f"{GREEN}✓ 0xC1{RESET}"
                diag_lbl = f"{GREEN}✓ 0x50{RESET}"
            elif level == 1:
                comm_lbl = f"{GREEN}✓ 0xC1{RESET}"
                diag_lbl = f"{YELLOW}✗ {diag_str or 'silent'}{RESET}"
            else:
                comm_lbl = f"  {comm_str or 'silent'}"
                diag_lbl = "  —"

            info(f"  {low_ms:>5}ms  {comm_lbl:>18}  {diag_lbl:>18}")

            if level == 2:
                _working_low_ms = low_ms
                print()
                ok(f"Full session established — LOW pulse = {low_ms} ms")
                if low_ms != P.FAST_INIT_LOW_MS:
                    warn(f"Working LOW timing ({low_ms}ms) differs from "
                         f"protocol.py FAST_INIT_LOW_MS ({P.FAST_INIT_LOW_MS}ms)")
                    hint(f"Update FAST_INIT_LOW_MS = {low_ms} in backend/obd/protocol.py")
                return True

            if low_ms != _TIMING_CANDIDATES[-1]:
                time.sleep(1.0)

        except Exception as exc:
            info(f"  {low_ms:>5}ms  exception: {exc}")
            try:
                ftdi.close()
            except Exception:
                pass
            time.sleep(1.0)

    print()
    fail("No timing produced a full session")
    hint("Is ignition definitely ON?  KKL cable fully seated in OBD-II port?")
    hint("TD5 OBD-II port: behind the centre cubby, driver's side")
    hint("Look at the table above for partial results:")
    hint("  StartComm ✓ but StartDiag silent → unexpected — sub-fn should be 0xA0 per Ekaitza_Itzali")
    hint("  All silent → ECU not responding to StartCommunication at any timing")
    hint("  No response at all → check USB cable and Zadig driver")
    return False


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 9 — StartDiagnosticSession (vehicle required)
# ═════════════════════════════════════════════════════════════════════════════

def stage9_diag_session() -> bool:
    from connection import KLineConnection, KLineError
    from service import TD5Session
    import protocol as P

    try:
        with KLineConnection(_ftdi_url) as conn:
            session = TD5Session(conn)
            session._start_diagnostic_session()
            ok("StartDiagnosticSession (0x10) — positive response received")
            return True
    except KLineError as exc:
        fail(f"StartDiagnosticSession failed: {exc}")
        hint("If fast-init passed but this fails, the session sub-function (0x89) may be wrong")
        hint("Check pyTD5Tester source for the correct sub-function byte")
        return False


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 10 — Seed-key authentication (vehicle required)
# ═════════════════════════════════════════════════════════════════════════════

def stage10_auth() -> bool:
    from connection import KLineConnection, KLineError
    from service import TD5Session
    import protocol as P

    def _try_auth(swap_seed_bytes: bool) -> tuple[bool, int, int]:
        """
        Attempt a full auth sequence on a fresh connection.

        swap_seed_bytes=False: big-endian  resp[3]=hi, resp[4]=lo  (standard)
        swap_seed_bytes=True:  little-endian resp[4]=hi, resp[3]=lo  (alternative)

        Returns (success, seed_as_interpreted, key_sent).
        Raises KLineError on comms failure; returns (False, seed, key) on ECU rejection.
        """
        with KLineConnection(_ftdi_url) as conn:
            session = TD5Session(conn)
            session._start_diagnostic_session()

            conn.send(P.build_frame(P.SVC_SECURITY_ACCESS, P.SA_REQUEST_SEED))
            resp = conn.recv_frame()
            session._assert_positive(resp, P.SVC_SECURITY_ACCESS, "seed request")

            # Short-format response: [FMT][0x67][0x01][byte_a][byte_b]
            byte_a, byte_b = resp[3], resp[4]
            if swap_seed_bytes:
                seed = (byte_b << 8) | byte_a   # little-endian: lo byte first in frame
            else:
                seed = (byte_a << 8) | byte_b   # big-endian: hi byte first in frame

            key = P.td5_seed_to_key(seed)

            if swap_seed_bytes:
                # Send key in matching little-endian order
                key_lo, key_hi = key & 0xFF, (key >> 8) & 0xFF
            else:
                key_hi, key_lo = (key >> 8) & 0xFF, key & 0xFF

            key_frame = P.build_frame(
                P.SVC_SECURITY_ACCESS, P.SA_SEND_KEY, key_hi, key_lo,
            )
            conn.send(key_frame)
            resp = conn.recv_frame()

            # Check positive response without raising — we want to detect rejection cleanly
            expected = P.SVC_SECURITY_ACCESS + P.POSITIVE_RESPONSE_OFFSET
            if len(resp) < 2 or resp[1] != expected:
                return False, seed, key

            return True, seed, key

    # ── Attempt 1: big-endian (standard interpretation) ───────────────────────
    order_label = "big-endian (hi byte first)"
    try:
        success, seed, key = _try_auth(swap_seed_bytes=False)
    except KLineError as exc:
        fail(f"Authentication (attempt 1, {order_label}): comms error — {exc}")
        hint("K-Line comms failed before key exchange; check fast-init and session setup")
        return False

    if success:
        ok(f"Seed-key authentication accepted by ECU ({order_label})")
        info(f"Seed 0x{seed:04X} → Key 0x{key:04X}")
        info("service.py seed extraction is correct as-is (big-endian)")
        return True

    # ── Attempt 2: little-endian (byte order swapped) ─────────────────────────
    warn(f"ECU rejected key with {order_label} — retrying with little-endian (lo byte first)")
    info("Opening a fresh K-Line connection for retry (ECU requires re-init after failed key)")

    order_label = "little-endian (lo byte first)"
    try:
        success, seed, key = _try_auth(swap_seed_bytes=True)
    except KLineError as exc:
        fail(f"Authentication (attempt 2, {order_label}): comms error — {exc}")
        return False

    if success:
        ok(f"Seed-key authentication accepted by ECU ({order_label})")
        info(f"Seed 0x{seed:04X} → Key 0x{key:04X}")
        warn("service.py needs updating — seed bytes are little-endian in the ECU frame:")
        hint("In service.py _authenticate(), change:")
        hint("  seed = (resp[3] << 8) | resp[4]")
        hint("to:")
        hint("  seed = (resp[4] << 8) | resp[3]")
        hint("And change key send order from (key_hi, key_lo) to (key_lo, key_hi)")
        return True

    fail("ECU rejected the key with both byte orders")
    hint("Possible causes:")
    hint("  1. Wrong polynomial in td5_seed_to_key() — cross-check with td5keygen")
    hint("     git clone github.com/pajacobson/td5keygen && python keytool.py <seed_hex>")
    hint("  2. ECU is in a locked state — cycle ignition and wait 10 s before retrying")
    hint("  3. Frame bytes resp[3] and resp[4] are not the seed — check raw frame with --verbose")
    return False


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 11 — Full PID sweep (vehicle required)
# ═════════════════════════════════════════════════════════════════════════════

def stage11_pid_sweep() -> bool:
    """
    Reads every known PID and logs both the raw hex payload and the decoded
    engineering-unit value. Run with engine off, then again at idle to
    compare and validate formulas.
    """
    from connection import KLineConnection, KLineError
    from service import TD5Session
    import protocol as P
    import decoder as D

    pids = [
        (P.PID_RPM,      "RPM",           D.decode_rpm,          "rpm",   None,    None),
        (P.PID_TEMPS,    "Coolant temp",   D.decode_coolant_temp, "°C",    -40.0,   130.0),
        (P.PID_TEMPS,    "Air temp",       D.decode_air_temp,     "°C",    -40.0,   80.0),
        (P.PID_TEMPS,    "Fuel temp",      D.decode_fuel_temp,    "°C",    -40.0,   120.0),
        (P.PID_MAP_MAF,  "Boost",          D.decode_boost,        "bar",   -0.1,    3.0),
        (P.PID_BATTERY,  "Battery",        D.decode_battery,      "V",     9.0,     16.0),
        (P.PID_SPEED,    "Road speed",     D.decode_speed,        "kph",   0.0,     300.0),
        (P.PID_THROTTLE, "Throttle",       D.decode_throttle,     "%",     0.0,     100.0),
    ]

    # Cache PID payloads so we only request each PID once from the ECU
    pid_cache: dict = {}
    passed = True

    try:
        with KLineConnection(_ftdi_url) as conn:
            session = TD5Session(conn)
            session.start()
            ok("Session started — running PID sweep")
            print()
            info(f"  {'PID':<6}  {'Parameter':<16}  {'Raw payload':<25}  {'Value':>10}  {'Unit':<6}  Sanity")

            for pid, name, decoder, unit, lo, hi in pids:
                try:
                    if pid not in pid_cache:
                        pid_cache[pid] = session.read_local_id(pid)
                    payload = pid_cache[pid]
                    value   = decoder(payload)
                    raw_hex = payload.hex(' ')[:24]

                    # Sanity check
                    if value is None:
                        sanity = f"{YELLOW}None{RESET}"
                    elif lo is not None and hi is not None and not (lo <= value <= hi):
                        sanity = f"{RED}OUT OF RANGE ({lo}–{hi}){RESET}"
                        passed = False
                    else:
                        sanity = f"{GREEN}OK{RESET}"

                    val_str = f"{value}" if value is not None else "None"
                    info(f"  0x{pid:02X}   {name:<16}  {raw_hex:<25}  {val_str:>10}  {unit:<6}  {sanity}")

                except KLineError as exc:
                    fail(f"  0x{pid:02X}   {name:<16}  FAILED — {exc}")
                    passed = False

        print()
        if passed:
            ok("All PIDs returned values within expected ranges")
            warn("IMPORTANT: verify readings make physical sense for your conditions")
            info("  Engine off  → RPM=0, coolant=ambient, boost=0, throttle≈10%")
            info("  Engine idle → RPM≈750-850, coolant rising to 88-92°C, boost≈0")
        else:
            warn("Some PIDs returned unexpected values — check raw hex against decoder.py")

        return passed

    except Exception as exc:
        fail(f"PID sweep failed: {exc}")
        return False


# ═════════════════════════════════════════════════════════════════════════════
# Main
# ═════════════════════════════════════════════════════════════════════════════

def print_summary() -> None:
    print(f"\n{'═'*62}")
    print(f"{BOLD}  TEST SUMMARY{RESET}")
    print(f"{'═'*62}")
    for r in results:
        if r.skipped:
            status = f"{YELLOW}SKIP{RESET}"
        elif r.passed:
            status = f"{GREEN}PASS{RESET}"
        else:
            status = f"{RED}FAIL{RESET}"
        print(f"  Stage {r.number:>2}  {r.name:<40}  {status}")
    print(f"{'═'*62}")

    total   = len([r for r in results if not r.skipped])
    passed  = len([r for r in results if r.passed])
    skipped = len([r for r in results if r.skipped])
    failed  = total - passed

    print(f"\n  Passed: {GREEN}{passed}{RESET}   Failed: {RED}{failed}{RESET}   Skipped: {YELLOW}{skipped}{RESET}\n")

    if failed > 0:
        print(f"  {YELLOW}First failed stage:{RESET}")
        first_fail = next(r for r in results if not r.passed and not r.skipped)
        print(f"  Stage {first_fail.number} — {first_fail.name}")
        print(f"  Scroll up to that stage's output for hints.\n")


def main() -> None:
    global _ftdi_url

    parser = argparse.ArgumentParser(description="TD5 KKL Cable Progressive Test Suite")
    parser.add_argument(
        "--vehicle", action="store_true",
        help="Include vehicle stages (8–11). Requires ignition ON and KKL cable in OBD port."
    )
    parser.add_argument(
        "--url", default="ftdi://ftdi:232/1",
        help="PyFtdi device URL (default: ftdi://ftdi:232/1)"
    )
    parser.add_argument(
        "--stage", type=int, default=None,
        help="Run a single stage number only (for re-testing after a fix)"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable DEBUG logging — shows every raw TX/RX frame byte on the K-Line"
    )
    parser.add_argument(
        "--log", metavar="FILE", default=None,
        help="Write all output (print + frame logs) to FILE as well as the terminal"
    )
    args = parser.parse_args()

    _ftdi_url = args.url

    # ── Interactive setup (only when no flags were passed) ────────────────────
    # If the user just runs `python td5_obd_test.py` with no arguments,
    # ask three questions instead of requiring them to remember the flags.
    no_flags_passed = not args.vehicle and not args.verbose and args.log is None \
                      and args.stage is None and args.url == "ftdi://ftdi:232/1"

    if no_flags_passed:
        print(f"\n{BOLD}  TD5 OBD Test — Quick Setup{RESET}")
        print("  ─────────────────────────────────────────")

        ans = input(f"  {CYAN}Are you sitting in the vehicle with ignition on? [y/N]{RESET}  ").strip().lower()
        args.vehicle = ans in ("y", "yes")

        ans = input(f"  {CYAN}Show verbose output (raw K-Line frame bytes)? [y/N]{RESET}  ").strip().lower()
        args.verbose = ans in ("y", "yes")

        ans = input(f"  {CYAN}Save a log file for later analysis? [Y/n]{RESET}  ").strip().lower()
        save_log = ans not in ("n", "no")
        if save_log:
            import datetime
            default_name = f"td5_test_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            typed = input(f"  {CYAN}Log filename [{default_name}]{RESET}  ").strip()
            args.log = typed if typed else default_name

        print()

    # ── Logging and tee setup ─────────────────────────────────────────────────
    if args.log:
        sys.stdout = _Tee(args.log)   # type: ignore[assignment]
    _configure_logging(verbose=args.verbose, log_path=args.log)

    # ── Resolve backend/obd/ path before printing anything ───────────────────
    import os

    def _find_obd_dir() -> Optional[str]:
        """Walk up the directory tree from this script looking for backend/obd/."""
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
        # Also check cwd and common relative paths
        for rel in ["backend/obd", "obd", "."]:
            p = os.path.normpath(os.path.join(os.getcwd(), rel))
            if os.path.isdir(p) and os.path.exists(os.path.join(p, "protocol.py")):
                return p
        return None

    obd_dir = _find_obd_dir()
    if obd_dir and obd_dir not in sys.path:
        sys.path.insert(0, obd_dir)

    print(f"\n{BOLD}{'═'*62}{RESET}")
    print(f"{BOLD}  TD5 KKL Cable — Progressive Verification Test Suite{RESET}")
    print(f"{BOLD}{'═'*62}{RESET}")
    print(f"  FTDI URL : {_ftdi_url}")
    print(f"  Vehicle  : {'YES — stages 8–11 will run' if args.vehicle else 'NO  — stages 8–11 skipped'}")
    print(f"  Verbose  : {'YES — raw TX/RX frame bytes will be shown' if args.verbose else 'NO  — add --verbose to see frame bytes'}")
    if args.log:
        print(f"  Log file : {args.log}")
    if obd_dir:
        print(f"  OBD path : {obd_dir}")
    else:
        print(f"  {RED}OBD path : NOT FOUND — stages 6, 7, 8–11 will fail on import{RESET}")
        print(f"  {YELLOW}Expected : TD5-Dash/backend/obd/protocol.py{RESET}")
        print(f"  {YELLOW}Script is at: {os.path.abspath(__file__)}{RESET}")
    if not args.vehicle:
        print(f"  {YELLOW}Run with --vehicle when you are in the car with ignition ON{RESET}")
    print()

    stages = [
        (1,  "USB / libusb availability",          stage1_libusb,        None),
        (2,  "PyFtdi import",                       stage2_pyftdi_import, None),
        (3,  "FTDI device detected",                stage3_ftdi_detected, 2),
        (4,  "FTDI open in UART mode",              stage4_ftdi_open,     3),
        (5,  "Bitbang mode (fast-init mechanism)",  stage5_bitbang,       4),
        (6,  "Protocol self-test (no hardware)",    stage6_protocol,      None),
        (7,  "Decoder self-test (no hardware)",     stage7_decoders,      None),
    ]

    vehicle_stages = [
        (8,  "Fast-init (K-Line wake pulse)",       stage8_fast_init,     5),
        (9,  "StartDiagnosticSession",              stage9_diag_session,  8),
        (10, "Seed-key authentication",             stage10_auth,         9),
        (11, "Full PID sweep",                      stage11_pid_sweep,    10),
    ]

    if args.vehicle:
        stages += vehicle_stages

    if args.stage:
        stages = [s for s in stages if s[0] == args.stage]
        if not stages:
            print(f"Stage {args.stage} not found (or not enabled — add --vehicle for stages 8–11)")
            sys.exit(1)

    for number, name, fn, prereq in stages:
        run_stage(number, name, fn, skip_if_failed=prereq)

    print_summary()

    all_run_passed = all(r.passed for r in results if not r.skipped)
    sys.exit(0 if all_run_passed else 1)


if __name__ == "__main__":
    main()