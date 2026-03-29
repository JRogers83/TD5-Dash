# OBD Session Sharing — Design Spec
**Date:** 2026-03-28

## Problem

The `pi_diag` full OBD test always opens its own `KLineConnection`, which fails when the OBD poll loop is already holding an active KWP2000 session with the TD5 ECU. The ECU only supports one session at a time and rejects `StartCommunication` with `generalReject (0x10)` when a session is already live.

## Solution

When the poll loop has an active authenticated session, `pi_diag` borrows it instead of opening its own connection. A `threading.Lock` mediates access: the poll loop holds it during each PID cycle and releases it during the 1s inter-cycle sleep. `pi_diag` acquires the same lock (waiting at most one cycle duration ~400ms), holds it for the full test, then releases it — the poll loop resumes automatically. Engine data on the UI pauses while the diagnostic runs; this is acceptable since users are not monitoring the engine screen during a diagnostic test.

---

## Files Changed

| Action | File | What changes |
|--------|------|--------------|
| Modify | `backend/obd/service.py` | Add lock + session exposure |
| Modify | `backend/obd/pi_diag.py` | Borrow live session when available |
| Modify | `tests/test_pi_diag.py` | Two new tests |

---

## `service.py` Changes

### Module-level additions

```python
import threading

_obd_lock    = threading.Lock()
_live_session: "TD5Session | None" = None
_live_conn:    "KLineConnection | None" = None
```

### Poll loop inner cycle

Wrap the inner PID poll cycle in `with _obd_lock:` and set/clear the module-level references:

```python
# Before inner while loop:
global _live_session, _live_conn

# Inside inner while loop — replace bare poll block with:
with _obd_lock:
    _live_session = session
    _live_conn    = conn
    # ... all PID polling unchanged ...
time.sleep(POLL_INTERVAL)   # lock released here; pi_diag acquires in this window
```

### Cleanup on disconnect

When the inner loop breaks (KLineError) or the outer try/except catches a connection failure, clear the module-level references before the retry sleep:

```python
_live_session = None
_live_conn    = None
```

---

## `pi_diag.py` Changes

### Session borrowing in `_run_test`

At the top of `_run_test`, before any stages run:

```python
from obd.service import _obd_lock, _live_session, _live_conn as _svc_conn

_borrowing = _live_session is not None
if _borrowing:
    _obd_lock.acquire()   # blocks at most one poll cycle (~400ms)
    borrowed_session = _live_session
    borrowed_conn    = _svc_conn
```

In the `finally` block (alongside existing cleanup):

```python
if _borrowing:
    _obd_lock.release()
```

### Stage behaviour when `_borrowing` is True

| Stage | Behaviour |
|-------|-----------|
| 1 — FTDI Detection | Runs normally (checks USB device) |
| 2 — Protocol Self-Test | Runs normally (software only) |
| 3 — Fast Init | Auto-pass: `"Poll loop paused — active session borrowed"` |
| 4 — Diagnostic Session | Auto-pass: `"Already in diagnostic session"` |
| 5 — Security Access | Auto-pass: `"Already authenticated"` |
| 6 — PID Probe | Uses `borrowed_session.read_local_id_safe()` |
| 7 — Live Data (10s) | Uses `borrowed_session` |

When `_borrowing` is False (mock mode, or OBD not yet connected), all stages behave exactly as before — `pi_diag` opens its own `KLineConnection`.

### `conn` variable in stages 6–7

Current code references `session` which is set in Stage 3. When borrowing:
- Skip the `conn = KLineConnection(...); conn.open()` block in Stage 3
- Set `session = borrowed_session` directly in the Stage 3 block (so stages 6–7 need no changes)
- `conn` is not used after Stage 3 except in the `finally` cleanup — guard it: `if conn: conn.close()`

---

## Tests

Two new tests in `tests/test_pi_diag.py`:

### `test_run_test_uses_live_session_when_available`

Mock `obd.service._live_session` as a non-None `MagicMock`. Verify:
- `_obd_lock` is acquired before Stage 3 executes
- Stages 3, 4, 5 are broadcast as `"pass"` with auto-pass detail strings
- `_obd_lock` is released in the `finally`

### `test_run_test_opens_own_connection_when_no_live_session`

Mock `obd.service._live_session` as None. Verify:
- `_obd_lock` is not acquired
- Stage 3 attempts `KLineConnection.open()` (existing behaviour)

---

## Edge Cases

**Lock already held when diagnostic starts:** Cannot happen — `pi_diag` is the only non-poll-loop acquirer, and `_test_running` prevents concurrent diagnostics.

**Session disconnects mid-diagnostic:** If the ECU drops the session while `pi_diag` holds the lock, the `read_local_id_safe` calls in stages 6–7 will return `None` or raise `KLineError`, which is caught and reported as a stage failure. The lock is released in `finally` regardless.

**Mock mode (TD5_MOCK=1):** `_live_session` is always `None` (mock service never sets it). `pi_diag` falls through to its own connection path, behaviour unchanged from before.
