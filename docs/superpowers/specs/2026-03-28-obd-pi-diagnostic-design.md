# Pi OBD Diagnostic — Design Spec
**Date:** 2026-03-28

## Overview

A full OBD diagnostic test suite triggered from the dashboard UI. The user takes the Pi to the vehicle, presses a button, waits for the 7-stage test to complete, brings the Pi back, and analyses the verbose log file on their PC.

Complements the existing `tools/td5_pc_diag.py` (PC/laptop version using direct FTDI USB) with a Pi-native equivalent that integrates with the running backend.

---

## Files Changed / Created

| Action | File | Purpose |
|--------|------|---------|
| Rename | `tools/td5_diag.py` → `tools/td5_pc_diag.py` | Clarify this is the PC/laptop version |
| New | `backend/obd/pi_diag.py` | Pi-side test runner module |
| Modified | `backend/main.py` | Two new endpoints |
| Modified | `frontend/index.html` | Diagnostics screen UI additions |
| Modified | `frontend/app.js` | WS handler + button logic |
| Modified | `deploy/setup.sh` | Sudoers entry for shutdown |
| Modified | `.gitignore` | Ignore `data/logs/` |

---

## Test Stages (`backend/obd/pi_diag.py`)

Seven sequential stages. Each stage emits a WebSocket message on start and on completion. If a stage fails, remaining stages are skipped (marked as skipped, not failed).

| # | Name | What it tests | Vehicle needed |
|---|------|---------------|----------------|
| 1 | FTDI Detection | PyFtdi enumerates the KKL cable via `TD5_FTDI_URL` | No |
| 2 | Protocol Self-Test | Checksum vectors + seed-key LFSR against known vectors | No |
| 3 | Fast Init | K-Line fast-init pulse + StartCommunication `81 13 F7 81 0C` | Yes |
| 4 | Diagnostic Session | `StartDiagnosticSession` response validates | Yes |
| 5 | Security Access | Seed-key authentication completes | Yes |
| 6 | PID Probe | All PIDs the service polls: RPM, temps, MAP, battery, speed, throttle, fuelling | Yes |
| 7 | Live Data (10s) | Continuous poll — decoded values captured every cycle for 10 seconds | Yes |

### WebSocket message format

Each stage update:
```json
{"type": "obd_test", "data": {"stage": 3, "name": "Fast Init", "status": "running|pass|fail|skip", "detail": "StartComm OK — ECU replied 0xC1 0xD0"}}
```

Test completion:
```json
{"type": "obd_test", "data": {"status": "complete", "log_file": "obd_2026-03-28_15-30-00.log", "passed": 5, "failed": 1, "skipped": 1}}
```

### Log file

- Saved to `data/logs/obd_YYYY-MM-DD_HH-MM-SS.log`
- Contains every TX/RX byte (hex), decoded values, stage pass/fail, timing
- Plain text, no ANSI codes — readable in any editor
- `data/logs/` added to `.gitignore`

### Concurrency

Only one test may run at a time. `POST /obd/full-test` returns `{"error": "already running"}` if a test is in progress. A module-level `_test_running` flag guards this.

---

## Backend Endpoints

### `POST /obd/full-test`
Starts the diagnostic as an asyncio background task. Returns immediately.

```json
// Success
{"ok": true, "started": true}

// Already running
{"error": "already running"}
```

### `POST /system/shutdown`
Shuts down the Pi cleanly.

```json
{"ok": true, "shutting_down": true}
```

Calls `sudo shutdown -h now` via `subprocess.Popen` with `start_new_session=True` after a 1.5s delay (same pattern as restart — gives time for HTTP response to be sent).

Sudoers entry added to `setup.sh`:
```
pi ALL=(ALL) NOPASSWD: /usr/sbin/shutdown -h now
```

---

## Frontend: Diagnostics Screen

### New elements (added to existing diagnostics layout)

**Full OBD Test section:**
- "Full OBD Test" button — calls `POST /obd/full-test`
- Stage progress panel (hidden until test starts):
  - 7 rows, each: stage number + name + status dot + one-line detail string
  - Status dot colours: grey (pending), amber (running), green (pass), red (fail), dim (skip)
- On completion: log filename shown below the stage list

**Shutdown section:**
- "Shutdown Pi" button
- Inline confirmation row appears on press: "Shut down now?" + Yes / No buttons
- Yes calls `POST /system/shutdown`; No dismisses

### WebSocket handler addition (`app.js`)

New `case 'obd_test'` in the WS message switch. Updates the stage row matching `data.stage`, or handles the `complete` summary message.

---

## `setup.sh` Changes

Add shutdown sudoers entry alongside existing restart entry:
```bash
echo "$SERVICE_USER ALL=(ALL) NOPASSWD: /usr/sbin/shutdown -h now" >> "$SUDOERS_FILE"
```

---

## Out of Scope

- Log file download via HTTP endpoint (can be added later if needed; `scp` is sufficient)
- Timing sweep (`--timing-sweep` flag from PC version) — terminal-only feature, not needed in UI
- Test cancellation mid-run — adds complexity, stages are short enough that waiting is acceptable
