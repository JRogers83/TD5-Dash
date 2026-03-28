# Pi OBD Diagnostic — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a full 7-stage OBD diagnostic test triggered from the Diagnostics UI screen, with per-stage pass/fail progress broadcast over WebSocket and verbose TX/RX logs saved to `data/logs/`; also add a Shutdown Pi button to the same screen.

**Architecture:** A new `backend/obd/pi_diag.py` module runs the blocking K-Line stages in a `ThreadPoolExecutor`, broadcasting `obd_test` WebSocket messages for each stage update using `asyncio.run_coroutine_threadsafe`. Two new REST endpoints in `main.py` (`POST /obd/full-test`, `POST /system/shutdown`) trigger the test and shutdown respectively. The frontend receives `obd_test` WS messages and renders a 7-row stage progress panel in the Diagnostics screen.

**Tech Stack:** Python 3.11, FastAPI, PyFtdi, asyncio, ThreadPoolExecutor, vanilla JS/HTML/CSS

---

## File Map

| Action | File | What changes |
|--------|------|--------------|
| Rename | `tools/td5_diag.py` → `tools/td5_pc_diag.py` | Clarify PC-only tool |
| Modify | `.gitignore` | Add `data/logs/` |
| Modify | `deploy/setup.sh` | Add sudoers entry for shutdown |
| **Create** | `backend/obd/pi_diag.py` | 7-stage test runner |
| **Create** | `tests/test_pi_diag.py` | Unit tests for pi_diag |
| Modify | `backend/main.py` | Add `/obd/full-test` and `/system/shutdown` endpoints |
| Modify | `frontend/index.html` | Stage progress panel + shutdown UI |
| Modify | `frontend/app.js` | `obd_test` WS handler + button functions |

---

## Task 1: Housekeeping

**Files:**
- Rename: `tools/td5_diag.py` → `tools/td5_pc_diag.py`
- Modify: `.gitignore`
- Modify: `deploy/setup.sh`

- [ ] **Step 1: Rename the PC diag script**

```bash
git mv tools/td5_diag.py tools/td5_pc_diag.py
```

- [ ] **Step 2: Add `data/logs/` to .gitignore**

In `.gitignore`, replace the existing diagnostic log patterns block:

```
# Diagnostic logs
data/diag_*.log

# OBD test/sweep tool log files
tools/td5_test_*.txt
tools/td5_sweep_*.txt
tools/td5_diag_*.txt
```

with:

```
# Diagnostic logs
data/diag_*.log

# Pi OBD diagnostic log files (created by /obd/full-test endpoint)
data/logs/

# OBD test/sweep tool log files
tools/td5_test_*.txt
tools/td5_sweep_*.txt
tools/td5_diag_*.txt
tools/td5_pc_diag_*.txt
```

- [ ] **Step 3: Add shutdown sudoers entry to `deploy/setup.sh`**

Find this line (around line 262):
```bash
echo "$SERVICE_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart td5-dash" > "$SUDOERS_FILE"
```

Replace it with:
```bash
cat > "$SUDOERS_FILE" <<EOF
$SERVICE_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart td5-dash
$SERVICE_USER ALL=(ALL) NOPASSWD: /usr/sbin/shutdown -h now
EOF
```

- [ ] **Step 4: Commit**

```bash
git add tools/td5_pc_diag.py .gitignore deploy/setup.sh
git commit -m "Housekeeping: rename PC diag, add data/logs gitignore, shutdown sudoers"
```

Expected: commit succeeds, `tools/td5_diag.py` no longer exists.

---

## Task 2: `backend/obd/pi_diag.py` — Test Runner Module

**Files:**
- Create: `backend/obd/pi_diag.py`
- Create: `tests/test_pi_diag.py`

The module runs 7 sequential stages in a worker thread. Each stage calls `_broadcast_stage()` on start and completion. Stages 3–7 require hardware; if any stage fails, all subsequent hardware stages are skipped. Verbose TX/RX bytes are captured by attaching a DEBUG-level `FileHandler` to the `obd.connection` logger (which already logs `TX:` and `RX:` at DEBUG level).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pi_diag.py`:

```python
"""Tests for backend/obd/pi_diag.py — concurrency guard, log creation, message format."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import obd.pi_diag as pi_diag


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_manager():
    mgr = MagicMock()
    mgr.broadcast = AsyncMock()
    return mgr


# ── Concurrency guard ─────────────────────────────────────────────────────────

def test_run_full_test_rejects_when_already_running():
    """POST /obd/full-test returns error dict when a test is already in progress."""
    pi_diag._test_running = True
    try:
        result = asyncio.get_event_loop().run_until_complete(
            pi_diag.run_full_test(_make_manager())
        )
        assert result == {"error": "already running"}
    finally:
        pi_diag._test_running = False


# ── Stage message format ──────────────────────────────────────────────────────

def test_broadcast_stage_message_format():
    """_broadcast_stage sends the correct obd_test WS message structure."""
    loop = asyncio.new_event_loop()
    manager = _make_manager()

    pi_diag._broadcast_stage(loop, manager, 2, "Protocol Self-Test", "pass", "Checksum OK")

    loop.run_until_complete(asyncio.sleep(0))  # flush coroutine
    manager.broadcast.assert_called_once()
    payload = manager.broadcast.call_args[0][0]
    assert payload["type"] == "obd_test"
    assert payload["data"]["stage"] == 2
    assert payload["data"]["name"] == "Protocol Self-Test"
    assert payload["data"]["status"] == "pass"
    assert payload["data"]["detail"] == "Checksum OK"
    loop.close()


# ── Log file creation ─────────────────────────────────────────────────────────

def test_run_test_creates_log_file(tmp_path):
    """_run_test creates a log file at the given path and writes stage info."""
    loop = asyncio.new_event_loop()
    manager = _make_manager()
    log_path = str(tmp_path / "obd_test.log")

    # Patch KLineConnection.open to raise immediately (simulates no hardware)
    # so the test completes quickly.
    with patch("obd.pi_diag.KLineConnection") as mock_conn_cls:
        mock_conn_cls.return_value.__enter__ = MagicMock(side_effect=Exception("no hardware"))
        mock_conn_cls.return_value.open = MagicMock(side_effect=Exception("no hardware"))
        pi_diag._run_test(manager, loop, log_path)

    assert Path(log_path).exists()
    content = Path(log_path).read_text()
    assert "STAGE 1" in content
    assert "STAGE 2" in content
    loop.close()


# ── Protocol self-test vectors ────────────────────────────────────────────────

def test_protocol_self_test_vectors():
    """Verify the known checksum and seed-key values used in Stage 2."""
    from obd import protocol as P

    # StartCommunication checksum — vehicle-confirmed
    frame = bytes([0x81, 0x13, 0xF7, 0x81])
    assert P.checksum(frame) == 0x0C

    # Seed-key LFSR — vehicle-confirmed
    assert P.td5_seed_to_key(0xBA08) == 0x70DC
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && python -m pytest ../tests/test_pi_diag.py -v 2>&1 | head -30
```

Expected: `ImportError: cannot import name 'pi_diag'` or `ModuleNotFoundError`.

- [ ] **Step 3: Create `backend/obd/pi_diag.py`**

```python
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
```

- [ ] **Step 4: Run tests**

```bash
cd backend && python -m pytest ../tests/test_pi_diag.py -v
```

Expected output:
```
tests/test_pi_diag.py::test_run_full_test_rejects_when_already_running PASSED
tests/test_pi_diag.py::test_broadcast_stage_message_format PASSED
tests/test_pi_diag.py::test_run_test_creates_log_file PASSED
tests/test_pi_diag.py::test_protocol_self_test_vectors PASSED
4 passed
```

- [ ] **Step 5: Commit**

```bash
git add backend/obd/pi_diag.py tests/test_pi_diag.py
git commit -m "Add pi_diag: 7-stage OBD test runner with WS broadcast and file logging"
```

---

## Task 3: Backend Endpoints

**Files:**
- Modify: `backend/main.py`

Add two new endpoints after the existing `/obd/clear-dtc` endpoint.

- [ ] **Step 1: Find the insertion point in `main.py`**

Locate the existing `/obd/clear-dtc` endpoint. The two new endpoints go immediately after it.

```bash
grep -n "clear-dtc\|obd/clear" backend/main.py
```

Expected: a line like `@app.post("/obd/clear-dtc")`.

- [ ] **Step 2: Add the two new endpoints**

After the `clear_dtc` endpoint function (and before the next section), insert:

```python
# ── API: Pi OBD diagnostic ────────────────────────────────────────────────────

@app.post("/obd/full-test")
async def obd_full_test() -> dict:
    """
    Start the 7-stage Pi OBD diagnostic test.

    Returns immediately — progress is broadcast over WebSocket as
    {"type": "obd_test", "data": {...}} messages.
    Only one test may run at a time.
    """
    from obd.pi_diag import run_full_test
    return await run_full_test(manager)


# ── System: shutdown ──────────────────────────────────────────────────────────

async def _delayed_shutdown() -> None:
    """Wait briefly so the HTTP response is sent, then shut down."""
    await asyncio.sleep(1.5)
    if subprocess.run(["which", "shutdown"], capture_output=True).returncode == 0:
        subprocess.Popen(["sudo", "shutdown", "-h", "now"],
                         start_new_session=True)
    else:
        log.info("Shutdown: 'shutdown' command not available (Docker/dev)")


@app.post("/system/shutdown")
async def system_shutdown() -> dict:
    """Shut down the Pi cleanly."""
    asyncio.create_task(_delayed_shutdown())
    return {"ok": True, "shutting_down": True}
```

- [ ] **Step 3: Verify the app still imports cleanly**

```bash
cd backend && python -c "import main; print('OK')"
```

Expected: `OK` (no import errors).

- [ ] **Step 4: Verify both endpoints are registered**

```bash
cd backend && python -c "
import main
routes = [r.path for r in main.app.routes]
assert '/obd/full-test' in routes, 'missing /obd/full-test'
assert '/system/shutdown' in routes, 'missing /system/shutdown'
print('Both endpoints registered OK')
"
```

Expected: `Both endpoints registered OK`

- [ ] **Step 5: Commit**

```bash
git add backend/main.py
git commit -m "Add /obd/full-test and /system/shutdown endpoints"
```

---

## Task 4: Frontend HTML — Diagnostics Screen

**Files:**
- Modify: `frontend/index.html`

The existing diagnostics layout (Settings layer 3) has two columns:
- Left: Service Health grid + WS client count + Refresh button
- Right: OBD Tests section with 3 buttons + `diag-test-result` div

Add below the existing OBD Tests section: a Full OBD Test button, a hidden stage progress panel (shown when test runs), and a Shutdown section.

- [ ] **Step 1: Replace the OBD Tests column content**

Find this block in `index.html` (around line 651):

```html
            <div class="diag-section">
              <div class="stat-label">OBD Tests</div>
              <div class="diag-tests">
                <button class="relay-btn" onclick="runOBDTest('init')">
                  <span class="relay-btn__label">Fast-Init Test</span>
                </button>
                <button class="relay-btn" onclick="runOBDTest('seedkey')">
                  <span class="relay-btn__label">Seed-Key Test</span>
                </button>
                <button class="relay-btn" onclick="runOBDTest('pid')">
                  <span class="relay-btn__label">PID Probe</span>
                </button>
              </div>
              <div class="diag-result" id="diag-test-result"></div>
            </div>
```

Replace with:

```html
            <div class="diag-section">
              <div class="stat-label">OBD Tests</div>
              <div class="diag-tests">
                <button class="relay-btn" onclick="runOBDTest('init')">
                  <span class="relay-btn__label">Fast-Init Test</span>
                </button>
                <button class="relay-btn" onclick="runOBDTest('seedkey')">
                  <span class="relay-btn__label">Seed-Key Test</span>
                </button>
                <button class="relay-btn" onclick="runOBDTest('pid')">
                  <span class="relay-btn__label">PID Probe</span>
                </button>
                <button class="relay-btn relay-btn--primary" id="btn-full-obd-test" onclick="runFullOBDTest()">
                  <span class="relay-btn__label" id="lbl-full-obd-test">Full OBD Test</span>
                </button>
              </div>
              <div class="diag-result" id="diag-test-result"></div>
              <!-- Stage progress panel — shown while test is running or complete -->
              <div class="diag-stage-panel" id="diag-stage-panel" style="display:none">
                <div class="diag-stage-list" id="diag-stage-list"></div>
                <div class="diag-log-name" id="diag-log-name"></div>
              </div>
              <!-- Shutdown -->
              <div class="diag-shutdown">
                <button class="relay-btn relay-btn--danger" onclick="confirmShutdown()">
                  <span class="relay-btn__label">Shutdown Pi</span>
                </button>
                <div class="diag-shutdown-confirm" id="diag-shutdown-confirm" style="display:none">
                  <span class="stat-label">Shut down now?</span>
                  <button class="wizard-btn wizard-btn--danger" onclick="doShutdown()">Yes</button>
                  <button class="wizard-btn" onclick="cancelShutdown()">No</button>
                </div>
              </div>
            </div>
```

- [ ] **Step 2: Add CSS for new elements to `frontend/style.css`**

At the end of the diagnostics section in `style.css` (after `.diag-result`), add:

```css
/* ── Full OBD test — stage progress panel ─── */
.diag-stage-panel {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 8px;
}

.diag-stage-list {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.diag-stage-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 8px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 5px;
}

.diag-stage-num {
  font-size: 16px;
  color: var(--text-dim);
  font-variant-numeric: tabular-nums;
  flex: 0 0 20px;
  text-align: right;
}

.diag-stage-name {
  font-size: 18px;
  font-weight: 600;
  color: var(--text);
  flex: 0 0 160px;
}

.diag-stage-detail {
  font-size: 16px;
  color: var(--text-muted);
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.diag-log-name {
  font-size: 16px;
  color: var(--text-muted);
  margin-top: 4px;
}

/* ── Shutdown section ─────────────────────── */
.diag-shutdown {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.diag-shutdown-confirm {
  display: flex;
  align-items: center;
  gap: 12px;
}

/* relay-btn--primary and relay-btn--danger variants */
.relay-btn--primary {
  background: color-mix(in srgb, var(--c-green) 15%, transparent);
  border-color: var(--c-green);
}
.relay-btn--primary .relay-btn__label { color: var(--c-green); }

.relay-btn--danger {
  background: color-mix(in srgb, var(--c-red) 15%, transparent);
  border-color: var(--c-red);
}
.relay-btn--danger .relay-btn__label { color: var(--c-red); }
```

- [ ] **Step 3: Verify HTML is well-formed**

Open `frontend/index.html` in a browser (or run a quick parse check):

```bash
python -c "
from html.parser import HTMLParser
class Check(HTMLParser): pass
p = Check()
p.feed(open('frontend/index.html').read())
print('HTML parses OK')
"
```

Expected: `HTML parses OK`

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html frontend/style.css
git commit -m "Add Full OBD Test stage panel and Shutdown button to diagnostics screen"
```

---

## Task 5: Frontend JavaScript

**Files:**
- Modify: `frontend/app.js`

Three changes:
1. Add `case 'obd_test'` to the WebSocket message switch
2. Add `runFullOBDTest()`, `_updateOBDStage()`, `_finaliseOBDTest()` functions
3. Add `confirmShutdown()`, `cancelShutdown()`, `doShutdown()` functions

- [ ] **Step 1: Add `obd_test` to the WS message switch**

Find the `ws.onmessage` switch in `connect()`:

```javascript
    switch (type) {
      case 'engine':  handleEngine(data);  break;
      case 'spotify': handleSpotify(data); break;
      case 'victron': handleVictron(data); break;
      case 'system':   handleSystem(data);   break;
      case 'starlink': handleStarlink(data); break;
      case 'gps':      handleGps(data);      break;
      case 'weather':  handleWeather(data);  break;
    }
```

Add the new case:

```javascript
    switch (type) {
      case 'engine':   handleEngine(data);   break;
      case 'spotify':  handleSpotify(data);  break;
      case 'victron':  handleVictron(data);  break;
      case 'system':   handleSystem(data);   break;
      case 'starlink': handleStarlink(data); break;
      case 'gps':      handleGps(data);      break;
      case 'weather':  handleWeather(data);  break;
      case 'obd_test': handleOBDTest(data);  break;
    }
```

- [ ] **Step 2: Add OBD test functions**

Find the `async function runOBDTest(test)` function and replace it with:

```javascript
// ── Full OBD test ────────────────────────────────

const _OBD_STAGE_NAMES = {
  1: 'FTDI Detection',
  2: 'Protocol Self-Test',
  3: 'Fast Init',
  4: 'Diagnostic Session',
  5: 'Security Access',
  6: 'PID Probe',
  7: 'Live Data (10s)',
};

function _initOBDStagePanel() {
  const list = document.getElementById('diag-stage-list');
  list.innerHTML = Object.entries(_OBD_STAGE_NAMES).map(([n, name]) =>
    `<div class="diag-stage-row" id="diag-stage-row-${n}">
      <span class="diag-stage-num">${n}</span>
      <span class="diag-stage-name">${name}</span>
      <div class="status-dot" id="diag-stage-dot-${n}"></div>
      <span class="diag-stage-detail" id="diag-stage-detail-${n}">—</span>
    </div>`
  ).join('');
  document.getElementById('diag-log-name').textContent = '';
  document.getElementById('diag-stage-panel').style.display = 'flex';
}

function handleOBDTest(data) {
  if (data.status === 'complete') {
    _finaliseOBDTest(data);
    return;
  }
  _updateOBDStage(data.stage, data.status, data.detail || '');
}

function _updateOBDStage(stage, status, detail) {
  const dot    = document.getElementById(`diag-stage-dot-${stage}`);
  const detEl  = document.getElementById(`diag-stage-detail-${stage}`);
  if (!dot) return;

  const dotClass = {
    running: 'warn',
    pass:    'on',
    fail:    'red',
    skip:    'off',
  }[status] || '';

  dot.className    = `status-dot ${dotClass}`;
  detEl.textContent = detail;
}

function _finaliseOBDTest(data) {
  const btn = document.getElementById('btn-full-obd-test');
  const lbl = document.getElementById('lbl-full-obd-test');
  if (btn) btn.disabled = false;
  if (lbl) lbl.textContent = 'Full OBD Test';

  const logEl = document.getElementById('diag-log-name');
  if (logEl && data.log_file) {
    logEl.textContent =
      `Log: ${data.log_file}  (${data.passed}✓ ${data.failed}✗ ${data.skipped}–)`;
  }
}

async function runFullOBDTest() {
  const btn = document.getElementById('btn-full-obd-test');
  const lbl = document.getElementById('lbl-full-obd-test');
  btn.disabled = true;
  lbl.textContent = 'Running…';
  _initOBDStagePanel();
  try {
    const r    = await fetch('/obd/full-test', { method: 'POST' });
    const data = await r.json();
    if (data.error) {
      lbl.textContent = 'Already Running';
      btn.disabled = false;
    }
  } catch (_) {
    lbl.textContent = 'Full OBD Test';
    btn.disabled = false;
  }
}

async function runOBDTest(test) {
  const result = document.getElementById('diag-test-result');
  result.textContent = `${test} test: not yet implemented (requires live OBD connection)`;
}

// ── Shutdown ─────────────────────────────────────

function confirmShutdown() {
  document.getElementById('diag-shutdown-confirm').style.display = 'flex';
}

function cancelShutdown() {
  document.getElementById('diag-shutdown-confirm').style.display = 'none';
}

async function doShutdown() {
  document.getElementById('diag-shutdown-confirm').style.display = 'none';
  await fetch('/system/shutdown', { method: 'POST' }).catch(() => {});
}
```

- [ ] **Step 3: Verify no JS syntax errors**

```bash
node --check frontend/app.js && echo "JS syntax OK"
```

Expected: `JS syntax OK`

- [ ] **Step 4: Commit**

```bash
git add frontend/app.js
git commit -m "Add OBD test WS handler, stage progress UI, and shutdown button JS"
```

---

## Task 6: Final Wiring and Push

- [ ] **Step 1: Run the full test suite to confirm nothing is broken**

```bash
cd backend && python -m pytest ../tests/ -v 2>&1 | tail -20
```

Expected: all existing tests pass plus the 4 new `test_pi_diag` tests.

- [ ] **Step 2: Verify the app starts cleanly**

```bash
cd backend && python -c "
import main
print('Routes:', [r.path for r in main.app.routes if hasattr(r, 'path')])
" 2>&1 | grep -E "Routes:|obd|shutdown"
```

Expected output includes `/obd/full-test` and `/system/shutdown`.

- [ ] **Step 3: Push to remote**

```bash
git push origin main
```

- [ ] **Step 4: On the Pi — pull, update sudoers, restart**

```bash
cd ~/TD5-Dash && git pull
# Update sudoers with shutdown entry:
sudo bash deploy/setup.sh   # re-runs setup to write the new sudoers entry
# OR manually:
echo "pi ALL=(ALL) NOPASSWD: /usr/sbin/shutdown -h now" | sudo tee -a /etc/sudoers.d/td5-dash
sudo chmod 440 /etc/sudoers.d/td5-dash
sudo systemctl restart td5-dash
```

Expected: Dashboard reloads, Diagnostics screen shows Full OBD Test button and Shutdown Pi button.
