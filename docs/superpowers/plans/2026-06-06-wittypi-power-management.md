# Witty Pi 5 HAT+ Power Management — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate the Witty Pi 5 HAT+ as the new shutdown-management hardware, replacing the discrete component design, while retaining the old `ignition_service.py` path behind an env-var toggle.

**Architecture:** `wittypi_service.py` is a lightweight FastAPI router that registers a `/system/shutdown-prepare` endpoint. The UUGear Witty Pi daemon calls `deploy/beforeShutdown.sh` before halting the Pi; that script POSTs to the endpoint triggering ordered cleanup (game mode, DB checkpoint, log). `WITTYPI_ENABLED=1` activates this path; `IGNITION_SENSE_PIN` activates the old discrete path — both can coexist with a logged warning. The `override_mode` check in the endpoint is the architecture hook for a future "stay-on" button.

**Tech Stack:** FastAPI APIRouter, SQLite PRAGMA wal_checkpoint, asyncio, shell script, existing `game_service._stop_internal()`, `shared_state.override_mode`.

**Spec:** `docs/superpowers/specs/2026-06-06-gps-wittypi-integration-design.md` — Power Management section.

---

## Files

| File | Action |
|------|--------|
| `backend/db.py` | Modify — add `wal_checkpoint()` helper |
| `backend/wittypi_service.py` | Create — startup checks + `/system/shutdown-prepare` endpoint |
| `backend/main.py` | Modify — conditional import + register router when WITTYPI_ENABLED=1 |
| `deploy/beforeShutdown.sh` | Create — shell hook called by Witty Pi daemon |
| `deploy/setup.sh` | Modify — add Witty Pi manual install note + beforeShutdown.sh placement |
| `.env.example` | Modify — add WITTYPI_ENABLED=0 |
| `tests/test_wittypi_service.py` | Create — endpoint unit tests |
| `documentation/SPEC.md` | Modify — update power management section |
| `documentation/pi-setup.md` | Modify — add Witty Pi 5 Setup section |

---

### Task 1: Add wal_checkpoint() to db.py

**Files:**
- Modify: `backend/db.py`
- Test: `tests/test_wittypi_service.py` (tested indirectly in Task 2)

- [ ] **Step 1: Read db.py to confirm _DB_PATH location**

Read `backend/db.py`. Confirm `_DB_PATH` is defined as `_DB_DIR / "td5dash.db"` (line ~33). Note it is a `pathlib.Path`.

- [ ] **Step 2: Add wal_checkpoint() function**

Find the end of the public API functions in `db.py` (after `purge_old_history` or similar). Add:
```python
def wal_checkpoint() -> None:
    """Flush the SQLite WAL journal to the main database file.

    Safe to call at any time. Used during graceful shutdown to ensure no
    pending writes are left in the WAL when power is cut.
    Uses a separate connection so it does not interfere with in-flight queries.
    """
    try:
        with sqlite3.connect(str(_DB_PATH)) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        log.debug("WAL checkpoint complete")
    except Exception as exc:
        log.warning("WAL checkpoint failed: %s", exc)
```

- [ ] **Step 3: Verify no tests break**

```bash
docker run --rm \
  -v "C:\code\TD5-Dash\backend:/app/backend" \
  -v "C:\code\TD5-Dash\tests:/app/tests" \
  -v "C:\code\TD5-Dash\pytest.ini:/app/pytest.ini" \
  td5-dash-td5-dash:latest \
  sh -c "cd /app/backend && python -m pytest /app/tests/ -v"
```
Expected: all existing tests pass.

- [ ] **Step 4: Commit**

```bash
git add backend/db.py
git commit -m "Add db.wal_checkpoint() for safe pre-shutdown DB flush"
```

---

### Task 2: Create wittypi_service.py with shutdown-prepare endpoint

**Files:**
- Create: `backend/wittypi_service.py`
- Create: `tests/test_wittypi_service.py`

- [ ] **Step 1: Write the failing tests first**

Create `tests/test_wittypi_service.py`:
```python
"""Tests for wittypi_service /system/shutdown-prepare endpoint."""
import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI


def _make_client(wittypi_enabled: str = "1"):
    """Build a test client with WITTYPI_ENABLED set."""
    os.environ["WITTYPI_ENABLED"] = wittypi_enabled
    # Re-import after env var is set so module-level WITTYPI_ENABLED is correct
    import importlib
    import wittypi_service
    importlib.reload(wittypi_service)

    app = FastAPI()
    app.include_router(wittypi_service.router)
    return TestClient(app)


class TestShutdownPrepareDisabled:
    def test_returns_501_when_disabled(self):
        client = _make_client(wittypi_enabled="0")
        r = client.post("/system/shutdown-prepare")
        assert r.status_code == 501
        assert r.json()["detail"]["error"] == "wittypi_not_enabled"


class TestShutdownPrepareOverrideMode:
    def test_returns_409_when_override_mode_active(self):
        import shared_state
        shared_state.override_mode = True
        try:
            client = _make_client(wittypi_enabled="1")
            r = client.post("/system/shutdown-prepare")
            assert r.status_code == 409
            assert r.json()["detail"]["error"] == "override_active"
        finally:
            shared_state.override_mode = False


class TestShutdownPrepareSuccess:
    def test_returns_200_with_cleaned_up_list(self, monkeypatch):
        import shared_state
        shared_state.override_mode = False

        monkeypatch.setattr("db.wal_checkpoint", lambda: None)

        client = _make_client(wittypi_enabled="1")
        r = client.post("/system/shutdown-prepare")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert isinstance(data["cleaned_up"], list)
        assert "db_checkpointed" in data["cleaned_up"]
        assert "shutdown_logged" in data["cleaned_up"]

    def test_game_mode_cleanup_called_when_active(self, monkeypatch):
        import shared_state
        shared_state.override_mode = False

        stop_called = []

        async def fake_stop():
            stop_called.append(True)

        monkeypatch.setattr("db.wal_checkpoint", lambda: None)

        # Simulate active game mode
        import wittypi_service
        import importlib
        os.environ["WITTYPI_ENABLED"] = "1"
        importlib.reload(wittypi_service)

        with patch("wittypi_service._get_game_service_state",
                   return_value=("running", fake_stop)):
            client = _make_client(wittypi_enabled="1")
            r = client.post("/system/shutdown-prepare")

        assert r.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker run --rm \
  -v "C:\code\TD5-Dash\backend:/app/backend" \
  -v "C:\code\TD5-Dash\tests:/app/tests" \
  -v "C:\code\TD5-Dash\pytest.ini:/app/pytest.ini" \
  td5-dash-td5-dash:latest \
  sh -c "cd /app/backend && python -m pytest /app/tests/test_wittypi_service.py -v"
```
Expected: `ModuleNotFoundError: No module named 'wittypi_service'`

- [ ] **Step 3: Create wittypi_service.py**

Create `backend/wittypi_service.py`:
```python
"""
Witty Pi 5 HAT+ power management service.

Registers the /system/shutdown-prepare REST endpoint which is called by
deploy/beforeShutdown.sh immediately before the Witty Pi daemon halts the Pi.

The endpoint performs ordered cleanup:
  1. Stop game mode if active (unfreezes Chromium)
  2. Flush SQLite WAL journal
  3. Log clean shutdown to journal

Returns HTTP 409 if shared_state.override_mode is True — this signals
beforeShutdown.sh to abort the shutdown (future "stay on" button hook).
Returns HTTP 501 if WITTYPI_ENABLED != "1".

I2C addresses:
  Witty Pi 5 RTC: 0x51
  Waveshare 7.9" touch (Goodix): 0x38
  → No conflict.

# hw-verify: VIN shutdown threshold, shutdown delay, and beforeShutdown.sh
# hook registration must be verified against the physical Witty Pi unit.
# See documentation/pi-setup.md for setup instructions.
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException

import db
import shared_state

log = logging.getLogger(__name__)
router = APIRouter()

WITTYPI_ENABLED: bool = os.getenv("WITTYPI_ENABLED", "0") == "1"


def startup_checks() -> None:
    """Called from main.py lifespan. Logs I2C info and warns on conflicting config."""
    log.info(
        "Witty Pi 5 power management active. "
        "I2C: 0x51 (Witty Pi RTC), 0x38 (Waveshare touch) — no address conflict."
    )
    if os.getenv("IGNITION_SENSE_PIN"):
        log.warning(
            "Both WITTYPI_ENABLED=1 and IGNITION_SENSE_PIN are set. "
            "Both shutdown paths are active simultaneously — this may cause races. "
            "Clear IGNITION_SENSE_PIN when using the Witty Pi."
        )


def _get_game_service_state():
    """
    Returns (status, stop_fn) for the active game session, or (None, None).
    Isolated into a function so tests can monkeypatch without importing game_service
    at module level (game_service has heavy psutil/process dependencies).
    """
    try:
        import game_service
        proc = game_service._launcher_proc
        if proc is not None and proc.poll() is None:
            return "running", game_service._stop_internal
    except Exception:
        pass
    return None, None


@router.post("/system/shutdown-prepare")
async def shutdown_prepare() -> dict:
    """
    Pre-shutdown cleanup hook. Called by deploy/beforeShutdown.sh before halt.

    MUST complete within 8 seconds (beforeShutdown.sh uses a 10s curl timeout).

    Returns:
      200 {"ok": True, "cleaned_up": [...]}  — proceed with shutdown
      409 {"error": "override_active"}       — abort shutdown (override mode on)
      501 {"error": "wittypi_not_enabled"}   — endpoint inactive
    """
    if not WITTYPI_ENABLED:
        raise HTTPException(501, {"error": "wittypi_not_enabled"})

    if shared_state.override_mode:
        log.info("Shutdown aborted: override_mode is active")
        raise HTTPException(409, {"error": "override_active"})

    actions: list[str] = []

    # 1. Stop game mode if active (unfreezes Chromium, kills launcher, cleans PulseAudio)
    status, stop_fn = _get_game_service_state()
    if status == "running" and stop_fn is not None:
        try:
            await stop_fn()
            actions.append("game_mode_stopped")
        except Exception as exc:
            log.warning("Game mode cleanup failed: %s", exc)
            actions.append("game_mode_stop_failed")

    # 2. Flush SQLite WAL journal to main database file
    try:
        db.wal_checkpoint()
        actions.append("db_checkpointed")
    except Exception as exc:
        log.warning("WAL checkpoint failed: %s", exc)
        actions.append("db_checkpoint_failed")

    # 3. Log clean shutdown
    log.info("Witty Pi initiated shutdown — cleanup complete: %s", actions)
    actions.append("shutdown_logged")

    return {"ok": True, "cleaned_up": actions}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker run --rm \
  -v "C:\code\TD5-Dash\backend:/app/backend" \
  -v "C:\code\TD5-Dash\tests:/app/tests" \
  -v "C:\code\TD5-Dash\pytest.ini:/app/pytest.ini" \
  td5-dash-td5-dash:latest \
  sh -c "cd /app/backend && python -m pytest /app/tests/test_wittypi_service.py -v"
```
Expected: all tests pass.

- [ ] **Step 5: Run full test suite**

```bash
docker run --rm \
  -v "C:\code\TD5-Dash\backend:/app/backend" \
  -v "C:\code\TD5-Dash\tests:/app/tests" \
  -v "C:\code\TD5-Dash\pytest.ini:/app/pytest.ini" \
  td5-dash-td5-dash:latest \
  sh -c "cd /app/backend && python -m pytest /app/tests/ -v"
```
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/wittypi_service.py tests/test_wittypi_service.py
git commit -m "Add wittypi_service with /system/shutdown-prepare endpoint and tests"
```

---

### Task 3: Wire wittypi_service into main.py

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 1: Add conditional import and startup call**

Read `backend/main.py`. Find where `game_service` is imported (near top). Add wittypi import below it:
```python
import game_service

# Witty Pi power management — active when WITTYPI_ENABLED=1
if os.getenv("WITTYPI_ENABLED", "0") == "1":
    import wittypi_service
else:
    wittypi_service = None  # type: ignore[assignment]
```

- [ ] **Step 2: Add startup_checks call in lifespan**

Find the `lifespan()` function. After `db.init_db()` and `db.purge_old_history()`, add:
```python
    # Witty Pi startup checks (logs I2C info, warns on conflicting config)
    if wittypi_service is not None:
        wittypi_service.startup_checks()
```

- [ ] **Step 3: Register the router**

Find where `game_service.router` is included (near the bottom of the file, before the static file mount). Add the wittypi router alongside it:
```python
# Game-mode router (before static catch-all).
app.include_router(game_service.router)

# Witty Pi router (registered only when WITTYPI_ENABLED=1)
if wittypi_service is not None:
    app.include_router(wittypi_service.router)
```

- [ ] **Step 4: Run full test suite**

```bash
docker run --rm \
  -v "C:\code\TD5-Dash\backend:/app/backend" \
  -v "C:\code\TD5-Dash\tests:/app/tests" \
  -v "C:\code\TD5-Dash\pytest.ini:/app/pytest.ini" \
  td5-dash-td5-dash:latest \
  sh -c "cd /app/backend && python -m pytest /app/tests/ -v"
```
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/main.py
git commit -m "Wire wittypi_service into main.py with WITTYPI_ENABLED toggle"
```

---

### Task 4: Create deploy/beforeShutdown.sh

**Files:**
- Create: `deploy/beforeShutdown.sh`

- [ ] **Step 1: Create the shell script**

Create `deploy/beforeShutdown.sh`:
```bash
#!/bin/sh
# Witty Pi 5 pre-shutdown hook.
#
# The UUGear Witty Pi daemon calls this script before asking the Pi to halt.
# It gives the TD5 Dash backend up to 10 seconds to perform cleanup
# (unfreeze Chromium, flush DB, log shutdown).
#
# Exit codes:
#   0  — proceed with shutdown
#   1  — abort shutdown (e.g. override_mode is active in the backend)
#
# Installation: copy this file to ~/wittypi/beforeShutdown.sh after running
# the UUGear install script. See documentation/pi-setup.md for details.
#
# hw-verify: confirm hook is called by running `sudo shutdown -h now` with
# Witty Pi installed and observing the journal for cleanup log messages.

result=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST http://localhost:8000/system/shutdown-prepare \
  --max-time 10 2>/dev/null)

if [ "$result" = "409" ]; then
    # Override mode active — abort shutdown
    logger -t td5-dash "Shutdown aborted: override_mode active (Witty Pi hook)"
    exit 1
fi

# 200 or any other response — proceed with halt
exit 0
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x deploy/beforeShutdown.sh
```

- [ ] **Step 3: Commit**

```bash
git add deploy/beforeShutdown.sh
git commit -m "Add deploy/beforeShutdown.sh Witty Pi pre-shutdown hook"
```

---

### Task 5: Update setup.sh and .env.example

**Files:**
- Modify: `deploy/setup.sh`
- Modify: `.env.example`

- [ ] **Step 1: Add WITTYPI_ENABLED to .env.example**

Read `.env.example`. Add a new section after the existing power/ignition entries:
```
# Power management — Witty Pi 5 HAT+
# Set WITTYPI_ENABLED=1 when using the Witty Pi. Clear IGNITION_SENSE_PIN.
WITTYPI_ENABLED=0
```

- [ ] **Step 2: Add Witty Pi install note to setup.sh**

Read `deploy/setup.sh`. Find the end of the main setup steps. Add before the closing summary:
```bash
# ── Witty Pi 5 HAT+ ──────────────────────────────────────────────────────────
# The UUGear install script must be run MANUALLY after first boot.
# It downloads from the internet and requires interactive confirmation.
#
# Step 1: Run the UUGear installer:
#   curl -L https://install.ultronics.co.uk/wittypi5plus.sh | sudo bash
#
# Step 2: Copy the pre-shutdown hook to the Witty Pi scripts directory:
#   cp "$REPO_DIR/deploy/beforeShutdown.sh" ~/wittypi/beforeShutdown.sh
#   chmod +x ~/wittypi/beforeShutdown.sh
#
# Step 3: Set WITTYPI_ENABLED=1 in .env and restart the service.
#
# Step 4: Configure VIN shutdown threshold using the Witty Pi configuration tool.
#
# See documentation/pi-setup.md — Witty Pi 5 Setup section for full details.
# hw-verify: all Witty Pi behaviour requires physical hardware to test.
echo "▸ Witty Pi 5: manual install required — see documentation/pi-setup.md"
```

- [ ] **Step 3: Commit**

```bash
git add deploy/setup.sh .env.example
git commit -m "Add Witty Pi install instructions to setup.sh and WITTYPI_ENABLED to .env.example"
```

---

### Task 6: Update SPEC.md and pi-setup.md

**Files:**
- Modify: `documentation/SPEC.md`
- Modify: `documentation/pi-setup.md`

- [ ] **Step 1: Update SPEC.md power management section**

Read `documentation/SPEC.md`. Find the power management section (3.7 or similar). Replace the discrete component design description with:

```markdown
## Power Management — Witty Pi 5 HAT+

**Hardware:** Witty Pi 5 HAT+ (UUGear). Replaces the earlier discrete component design
(optoisolator + 7805 + relay circuit).

**Wiring topology:**
```
Leisure battery (+12V)
  ├─→ 12V relay (coil on ignition feed) → Witty Pi 5 VIN screw terminal
  │     (running power + shutdown trigger)
  └─→ Epoxy-potted 12V→5V buck converter (permanent standby)
        └─→ Witty Pi 5 USB-C (keeps RTC alive when ignition is off)
```

**Shutdown sequence:**
1. Ignition off → 12V relay drops → Witty Pi detects VIN loss
2. After configured delay → Witty Pi daemon runs `~/wittypi/beforeShutdown.sh`
3. `beforeShutdown.sh` POSTs to `/system/shutdown-prepare` (10s timeout)
4. Backend performs cleanup → returns 200 (proceed) or 409 (override active, abort)
5. Witty Pi daemon calls `shutdown -h now`
6. Pi halts → Witty Pi cuts power after configurable timeout

**I2C addresses:**
| Device | Address |
|--------|---------|
| Witty Pi 5 (RTC) | 0x51 |
| Waveshare 7.9" touch (Goodix) | 0x38 |
No conflict.

**Override mode (future):** `shared_state.override_mode = True` causes
`/system/shutdown-prepare` to return 409, which aborts the Witty Pi's shutdown.
This is the hook for a future "stay on after ignition" UI button.

**Legacy discrete path:** `ignition_service.py` remains in the codebase for the
optoisolator-based ignition detection circuit. Activated by setting `IGNITION_SENSE_PIN`
in `.env`. Do not set both `WITTYPI_ENABLED=1` and `IGNITION_SENSE_PIN` simultaneously.
```

Update the BOM: add `Witty Pi 5 HAT+`. Remove discrete component list items (7805, optoisolator circuit, dual relay items).

- [ ] **Step 2: Add Witty Pi 5 Setup section to pi-setup.md**

Read `documentation/pi-setup.md`. Append a new section:

````markdown
## Witty Pi 5 HAT+ Setup

### Wiring
```
Leisure battery (+12V)
  ├─→ 12V relay (coil on ignition feed)  →  Witty Pi VIN screw terminal
  └─→ 12V→5V epoxy buck converter (permanent)  →  Witty Pi USB-C
```
Place inline fuse (3A) on the supply to the relay.

### Hardware Installation
1. Fit Witty Pi 5 onto the Pi GPIO header (uses GPIO 4 and 17 internally)
2. Connect Witty Pi VIN screw terminal to the relay switched output
3. Connect the 5V buck converter output to Witty Pi USB-C

### Software Installation (manual — requires internet + interactive confirmation)
```bash
curl -L https://install.ultronics.co.uk/wittypi5plus.sh | sudo bash
```

### Pre-shutdown Hook
```bash
cp ~/TD5-Dash/deploy/beforeShutdown.sh ~/wittypi/beforeShutdown.sh
chmod +x ~/wittypi/beforeShutdown.sh
```

### Configuration
Set in `.env`:
```
WITTYPI_ENABLED=1
# IGNITION_SENSE_PIN=   ← leave empty when using Witty Pi
```

Configure VIN shutdown threshold using the Witty Pi configuration tool to match
the relay drop-out voltage on your specific relay.

### Verify
```bash
# Confirm I2C addresses (no conflict with touchscreen at 0x38)
i2cdetect -y 1
# Should show 0x51 (Witty Pi RTC) and 0x38 (Waveshare touch)

# Test pre-shutdown hook manually
curl -X POST http://localhost:8000/system/shutdown-prepare
# Expected: {"ok": true, "cleaned_up": ["db_checkpointed", "shutdown_logged"]}
```

> **hw-verify:** VIN threshold, shutdown delay, and `beforeShutdown.sh` hook
> invocation must all be verified on real hardware. The hook path
> (`~/wittypi/beforeShutdown.sh`) may vary between UUGear firmware versions —
> confirm the correct path after running the install script.
````

- [ ] **Step 3: Run full test suite one final time**

```bash
docker run --rm \
  -v "C:\code\TD5-Dash\backend:/app/backend" \
  -v "C:\code\TD5-Dash\tests:/app/tests" \
  -v "C:\code\TD5-Dash\pytest.ini:/app/pytest.ini" \
  td5-dash-td5-dash:latest \
  sh -c "cd /app/backend && python -m pytest /app/tests/ -v"
```
Expected: all tests pass.

- [ ] **Step 4: Commit and push**

```bash
git add documentation/SPEC.md documentation/pi-setup.md
git commit -m "Update SPEC.md and pi-setup.md for Witty Pi 5 HAT+ power management"
git push origin main
```
