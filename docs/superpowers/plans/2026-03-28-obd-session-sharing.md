# OBD Session Sharing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow `pi_diag` to borrow the poll loop's active KWP2000 session instead of opening a competing connection, so the Full OBD Test works when the OBD service is live.

**Architecture:** A `threading.Lock` in `service.py` wraps each poll cycle; `pi_diag` acquires it to pause polling and borrow the authenticated session. Stages 3–5 auto-pass when a session is borrowed (no new connection needed). The lock is released in `_run_test`'s `finally` block.

**Tech Stack:** Python 3.11, threading.Lock, existing KLineConnection / TD5Session classes

---

## File Map

| Action | File | What changes |
|--------|------|--------------|
| Modify | `backend/obd/service.py` | Add lock + module-level session vars; wrap poll cycle |
| Modify | `backend/obd/pi_diag.py` | Borrow live session when available; auto-pass stages 3–5 |
| Modify | `tests/test_pi_diag.py` | Two new tests |

---

## Task 1: Add lock and session exposure to `service.py`

**Files:**
- Modify: `backend/obd/service.py`
- Test: `tests/test_pi_diag.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pi_diag.py` after the existing tests:

```python
# ── Session borrowing ─────────────────────────────────────────────────────────

def test_run_test_uses_live_session_when_available(tmp_path):
    """When _live_session is set, _run_test borrows it and stages 3-5 auto-pass."""
    import threading
    import obd.service as svc

    mock_session = MagicMock()
    mock_session.read_local_id_safe.return_value = None  # PIDs return no response

    svc._live_session = mock_session
    svc._obd_lock = threading.Lock()

    loop = asyncio.new_event_loop()
    manager = _make_manager()
    log_path = str(tmp_path / "obd_borrow_test.log")

    try:
        pi_diag._run_test(manager, loop, log_path)
    finally:
        svc._live_session = None

    content = Path(log_path).read_text()
    assert "Poll loop paused" in content
    assert "Already in diagnostic session" in content
    assert "Already authenticated" in content

    # Lock must be released after test completes
    assert svc._obd_lock.acquire(blocking=False)
    svc._obd_lock.release()

    loop.run_until_complete(asyncio.sleep(0))
    loop.close()


def test_run_test_opens_own_connection_when_no_live_session(tmp_path):
    """When _live_session is None, _run_test opens its own KLineConnection."""
    import obd.service as svc
    svc._live_session = None

    loop = asyncio.new_event_loop()
    manager = _make_manager()
    log_path = str(tmp_path / "obd_own_conn_test.log")

    with patch("obd.pi_diag.KLineConnection") as mock_conn_cls:
        mock_conn_cls.return_value.open = MagicMock(side_effect=Exception("no hardware"))
        pi_diag._run_test(manager, loop, log_path)

    mock_conn_cls.return_value.open.assert_called_once()

    loop.run_until_complete(asyncio.sleep(0))
    loop.close()
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
cd /c/code/TD5-Dash/backend && python -m pytest ../tests/test_pi_diag.py::test_run_test_uses_live_session_when_available ../tests/test_pi_diag.py::test_run_test_opens_own_connection_when_no_live_session -v
```

Expected: both fail — `AttributeError: module 'obd.service' has no attribute '_obd_lock'`

- [ ] **Step 3: Add lock and module-level session vars to `service.py`**

At the top of `service.py`, after `import time`, add:

```python
import threading
```

After the module-level constants (`FTDI_URL`, `POLL_INTERVAL`, etc.), add:

```python
# ── Diagnostic session sharing ─────────────────────────────────────────────────
# Allows pi_diag to borrow the active session instead of opening a competing one.
# The lock wraps each poll cycle; pi_diag acquires it to pause polling.
_obd_lock:     threading.Lock        = threading.Lock()
_live_session: "TD5Session | None"   = None
_live_conn:    "KLineConnection | None" = None
```

- [ ] **Step 4: Restructure the inner poll loop to use the lock**

In `_poll_loop`, add `global _live_session, _live_conn` at the very start of the function (before the outer `while True:`).

Then restructure the inner `while True:` loop. The current loop (lines ~217–301) is:

```python
                while True:
                    try:
                        # ── Always-available PIDs ────────────────────────
                        temps = session.read_local_id(P.PID_TEMPS)
                        ...
                        time.sleep(POLL_INTERVAL)

                    except KLineError as exc:
                        log.warning("K-Line read error: %s — reconnecting", exc)
                        break

                    # If all PIDs timed out...
                    if time.monotonic() - last_successful_read > 3.0:
                        session.send_tester_present()
```

Replace it with:

```python
                while True:
                    with _obd_lock:
                        _live_session = session
                        _live_conn    = conn
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
                            throttle_raw = D.decode_throttle_raw(thr_payload) if thr_payload else None

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
                                        "throttle_raw_pct": throttle_raw,
                                        "battery_v":        battery if battery is not None else 0.0,
                                        "road_speed_kph":   round(speed) if speed is not None else 0,
                                        "fuel_temp_c":      fuel_temp,
                                        "fault_codes":      fault_codes,
                                    },
                                }),
                                loop,
                            )

                            # ── Periodic history write (~10s cadence) ──────
                            if time.monotonic() - last_history_write >= HISTORY_WRITE_INTERVAL_S:
                                try:
                                    db.insert_history({
                                        "rpm":              rpm or 0,
                                        "road_speed_kph":   speed or 0,
                                        "coolant_temp_c":   coolant,
                                        "boost_bar":        boost,
                                        "throttle_pct":     throttle or 0,
                                        "battery_v":        battery or 0,
                                        "fuel_temp_c":      fuel_temp,
                                    })
                                    last_history_write = time.monotonic()
                                except Exception:
                                    log.debug("Failed to write engine history row")

                        except KLineError as exc:
                            _live_session = None
                            _live_conn    = None
                            log.warning("K-Line read error: %s — reconnecting", exc)
                            break

                        # If all PIDs timed out (engine off, ECU sluggish), send
                        # a keepalive to prevent the session from expiring.
                        if time.monotonic() - last_successful_read > 3.0:
                            session.send_tester_present()

                    time.sleep(POLL_INTERVAL)   # lock released here — pi_diag can acquire
```

- [ ] **Step 5: Clear session vars on outer reconnect**

The outer `try/except` (around the `with KLineConnection(...)` block) currently ends with:

```python
        except KLineError as exc:
            log.error("K-Line connection failed: %s — retrying in %.0f s", exc, RETRY_DELAY_S)
        except Exception:
            log.exception("Unexpected error in OBD poll loop — retrying in %.0f s", RETRY_DELAY_S)

        time.sleep(RETRY_DELAY_S)
```

Add a `finally:` clause to clear the session on any disconnect:

```python
        except KLineError as exc:
            log.error("K-Line connection failed: %s — retrying in %.0f s", exc, RETRY_DELAY_S)
        except Exception:
            log.exception("Unexpected error in OBD poll loop — retrying in %.0f s", RETRY_DELAY_S)
        finally:
            _live_session = None
            _live_conn    = None

        time.sleep(RETRY_DELAY_S)
```

- [ ] **Step 6: Verify import is clean**

```bash
cd /c/code/TD5-Dash/backend && python -c "import obd.service as s; print('lock:', s._obd_lock); print('session:', s._live_session)"
```

Expected:
```
lock: <unlocked _thread.lock object at 0x...>
session: None
```

- [ ] **Step 7: Run existing tests to confirm nothing broken**

```bash
cd /c/code/TD5-Dash/backend && python -m pytest ../tests/ -v -q 2>&1 | tail -5
```

Expected: `103 passed` (new tests still fail — that's fine, they need `pi_diag.py` changes).

- [ ] **Step 8: Commit**

```bash
git add backend/obd/service.py
git commit -m "Add _obd_lock and live session exposure to OBD service for diagnostic handoff"
```

---

## Task 2: Update `pi_diag.py` to borrow the live session

**Files:**
- Modify: `backend/obd/pi_diag.py`

- [ ] **Step 1: Add session-borrowing logic at the top of `_run_test`**

In `_run_test`, after the log setup section (after `file_handler.close()` is set up, before the `try:` block that contains stage 1), add the following. Find the line:

```python
    passed  = 0
    failed  = 0
    skipped = 0
    skip_from: int | None = None   # if set, skip all stages >= this number

    conn    = None
    session = None
```

Replace it with:

```python
    passed  = 0
    failed  = 0
    skipped = 0
    skip_from: int | None = None   # if set, skip all stages >= this number

    conn     = None
    session  = None
    _borrowing = False   # True when using the poll loop's active session

    # Check if the OBD poll loop has an active authenticated session to borrow.
    # If so, acquire the lock (pausing the poll loop) and reuse its session
    # instead of opening a competing connection (ECU only supports one session).
    from .service import _obd_lock, _live_session
    if _live_session is not None:
        _obd_lock.acquire()   # blocks at most one poll cycle (~400ms)
        _borrowing = True
        session    = _live_session
        log.info("Session borrowed from active poll loop — poll paused for duration of test")
    else:
        log.info("No active session — will open own connection. FTDI URL: %s", FTDI_URL)
```

- [ ] **Step 2: Update the `finally` block to release the lock**

Find the `finally:` block in `_run_test`:

```python
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
```

Replace with:

```python
    finally:
        if _borrowing:
            _obd_lock.release()
            log.info("Poll loop lock released — polling will resume")
        elif conn:
            try:
                conn.close()
            except Exception:
                pass
```

- [ ] **Step 3: Make stages 3, 4, 5 auto-pass when borrowing**

Find the Stage 3 block:

```python
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
```

Replace with:

```python
        # ── Stage 3: Fast Init + StartCommunication ───────────────────────────
        if skip_from and skip_from <= 3:
            emit(3, "skip"); skipped += 1
        elif _borrowing:
            emit(3, "pass", "Poll loop paused — active session borrowed")
            passed += 1
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
```

Find the Stage 4 block:

```python
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
```

Replace with:

```python
        # ── Stage 4: Diagnostic Session ───────────────────────────────────────
        if skip_from and skip_from <= 4:
            emit(4, "skip"); skipped += 1
        elif _borrowing:
            emit(4, "pass", "Already in diagnostic session")
            passed += 1
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
```

Find the Stage 5 block:

```python
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
```

Replace with:

```python
        # ── Stage 5: Security Access ──────────────────────────────────────────
        if skip_from and skip_from <= 5:
            emit(5, "skip"); skipped += 1
        elif _borrowing:
            emit(5, "pass", "Already authenticated")
            passed += 1
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
```

- [ ] **Step 4: Run all tests**

```bash
cd /c/code/TD5-Dash/backend && python -m pytest ../tests/test_pi_diag.py -v
```

Expected: all 6 tests pass:
```
test_run_full_test_rejects_when_already_running PASSED
test_broadcast_stage_message_format PASSED
test_run_test_creates_log_file PASSED
test_protocol_self_test_vectors PASSED
test_run_test_uses_live_session_when_available PASSED
test_run_test_opens_own_connection_when_no_live_session PASSED
```

- [ ] **Step 5: Run full suite**

```bash
cd /c/code/TD5-Dash/backend && python -m pytest ../tests/ -q 2>&1 | tail -5
```

Expected: `105 passed` (103 existing + 2 new).

- [ ] **Step 6: Commit**

```bash
git add backend/obd/pi_diag.py tests/test_pi_diag.py
git commit -m "pi_diag borrows live OBD session when poll loop is active"
```

---

## Task 3: Push and verify

- [ ] **Step 1: Push**

```bash
git push origin main
```

- [ ] **Step 2: Verify app still imports cleanly**

```bash
cd /c/code/TD5-Dash/backend && python -c "import main; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Confirm git log**

```bash
git log --oneline -4
```

Expected: two new commits on top of the previous HEAD.
