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

  # Override FTDI URL if you have multiple FTDI devices:
  python td5_obd_test.py --url ftdi://ftdi:232/1

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
    from protocol import checksum, build_frame, SVC_START_DIAG, td5_seed_to_key

    data  = bytes([0x80, 0x04, 0x10, 0xF1, 0x10, 0x89])
    csum  = checksum(data)
    if csum == 0x1E:
        ok(f"Checksum correct: 0x{csum:02X}")
    else:
        fail(f"Checksum wrong: got 0x{csum:02X}, expected 0x1E")
        passed = False

    # ── Frame build ───────────────────────────────────────────────────────────
    frame = build_frame(SVC_START_DIAG, 0x89)
    expected_body = bytes([0x80, 0x04, 0x10, 0xF1, 0x10, 0x89, 0x1E])
    if frame == expected_body:
        ok(f"Frame build correct: {frame.hex(' ')}")
    else:
        fail(f"Frame build wrong: {frame.hex(' ')} (expected {expected_body.hex(' ')})")
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
# STAGE 8 — Fast-init (vehicle required — ignition ON, engine optional)
# ═════════════════════════════════════════════════════════════════════════════

def stage8_fast_init() -> bool:
    """
    Drives the K-Line wake pulse and waits for the ECU to acknowledge with
    keyword bytes. This is the first stage that requires the car.

    Ignition ON, engine does not need to be running.
    """
    import protocol as P

    warn("Vehicle required — ensure ignition is ON before proceeding")
    info("Attempting fast-init (K-Line 25ms LOW pulse) …")

    try:
        # We do fast-init manually here so we can report each sub-step
        from pyftdi.ftdi import Ftdi  # type: ignore

        TX_PIN = 0x01
        ftdi = Ftdi()
        ftdi.open_from_url(_ftdi_url)

        # Bitbang: K-Line LOW for 25ms
        ftdi.set_bitmode(TX_PIN, Ftdi.BitMode.BITBANG)
        ftdi.write_data(bytes([0x00]))
        ok("K-Line LOW pulse started (25ms)")
        time.sleep(P.FAST_INIT_LOW_MS / 1000.0)

        ftdi.write_data(bytes([TX_PIN]))
        ok("K-Line HIGH (idle)")
        time.sleep(P.FAST_INIT_HIGH_MS / 1000.0)

        # Return to UART
        ftdi.set_bitmode(0x00, Ftdi.BitMode.RESET)
        ftdi.set_baudrate(P.BAUD_RATE)
        ftdi.purge_buffers()
        time.sleep(P.SETTLE_MS / 1000.0)
        ok("Returned to UART mode")

        # Try to read keyword bytes — ECU should respond
        # We read up to 3 bytes with a generous timeout
        deadline = time.monotonic() + 1.0
        buf = bytearray()
        while len(buf) < 3 and time.monotonic() < deadline:
            chunk = ftdi.read_data(3 - len(buf))
            if chunk:
                buf.extend(chunk)
            else:
                time.sleep(0.01)

        ftdi.close()

        if len(buf) > 0:
            ok(f"ECU responded with {len(buf)} byte(s) after fast-init: {buf.hex(' ')}")
            # TD5 keyword bytes confirmed by pyTD5Tester and Ekaitza_Itzali
            if len(buf) >= 2 and buf[0] == 0xC1 and buf[1] == 0x57:
                ok("Keyword bytes 0xC1 0x57 confirmed — genuine TD5 ECU response")
            elif len(buf) >= 1:
                warn(f"Unexpected keyword bytes: {buf.hex(' ')} (expected C1 57)")
                hint("ECU is alive but keyword bytes differ from expected — may still work")
            return True
        else:
            warn("No bytes received after fast-init — ECU did not respond")
            hint("Is ignition definitely ON?")
            hint("Is the KKL cable seated fully in the OBD-II port?")
            hint("TD5 OBD-II port: behind the centre cubby, driver's side")
            hint("Try adjusting FAST_INIT_LOW_MS in protocol.py by ±2ms")
            hint("Some TD5 ECUs are sensitive to init pulse timing")
            return False

    except Exception as exc:
        fail(f"Fast-init failed: {exc}")
        hint("Check the KKL cable is plugged into both the PC USB port and the vehicle OBD port")
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

    try:
        with KLineConnection(_ftdi_url) as conn:
            session = TD5Session(conn)
            session._start_diagnostic_session()

            # Manually step through auth so we can log the seed
            import protocol as P
            conn.send(P.build_frame(P.SVC_SECURITY_ACCESS, P.SA_REQUEST_SEED))
            resp = conn.recv_frame()
            session._assert_positive(resp, P.SVC_SECURITY_ACCESS, "seed request")

            seed = (resp[6] << 8) | resp[7]
            key  = P.td5_seed_to_key(seed)
            info(f"ECU seed: 0x{seed:04X}")
            info(f"Computed key: 0x{key:04X}")

            key_frame = P.build_frame(
                P.SVC_SECURITY_ACCESS, P.SA_SEND_KEY,
                (key >> 8) & 0xFF, key & 0xFF,
            )
            conn.send(key_frame)
            resp = conn.recv_frame()
            session._assert_positive(resp, P.SVC_SECURITY_ACCESS, "key response")

            ok("Seed-key authentication accepted by ECU")
            info(f"Seed 0x{seed:04X} → Key 0x{key:04X} — note these for td5keygen cross-check")
            return True

    except Exception as exc:
        fail(f"Authentication failed: {exc}")
        hint("Most likely cause: incorrect polynomial in protocol.td5_seed_to_key()")
        hint("Cross-check with: github.com/pajacobson/td5keygen")
        hint("Also check: seed bytes at resp[6:8] — are they plausible non-zero values?")
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
    args = parser.parse_args()

    _ftdi_url = args.url

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