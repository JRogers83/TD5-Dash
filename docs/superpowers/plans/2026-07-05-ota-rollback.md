# OTA Rollback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Roll Back" action to the Settings → Software panel that returns the
dash to the exact commit it was on immediately before the most recent update, with
up to 5 steps of history.

**Architecture:** All git/subprocess orchestration for update and rollback moves
into a new `backend/update_service.py` module so it's unit-testable without
importing the FastAPI app or touching a real git repo/network in tests. `main.py`'s
`/system/update` handler becomes a thin wrapper around it; two new endpoints
(`/system/rollback`, `/system/version`) are added alongside. A new settings key,
`update_history`, holds a JSON-encoded stack (list) of up to 5 full commit hashes in
the existing SQLite settings table, with helpers added to `db.py`. The frontend gets
a "Roll Back" button mirroring the existing "Check for Updates" button's UX exactly
(no confirmation dialog, disable-then-reconnect pattern).

**Tech Stack:** Python 3.11, FastAPI, SQLite (`db.py`), vanilla JS frontend
(`app.js`), pytest.

## Global Constraints

Copied verbatim from `docs/superpowers/specs/2026-07-03-ota-rollback-design.md`:

- Rollback history is a stack of **up to 5** full 40-character commit hashes,
  stored under settings key `update_history` (JSON array), oldest dropped once the
  array exceeds 5 entries. Hashes are shortened only at display time.
- `POST /system/update` captures `old = git rev-parse HEAD` before pulling and
  `new = git rev-parse HEAD` after; it appends `old` to `update_history` **only if
  `new != old`** (a no-op or failed pull must not record a phantom entry).
- `POST /system/rollback` pops the last entry off `update_history` and
  `git reset --hard <hash>` (branch-preserving, not detached HEAD) so a later
  `git pull` still fast-forwards. Empty stack → `400 {"error": "no_previous_version"}`.
  Both update and rollback run the same pip-install/apt-get dependency reinstall
  step afterward.
- `GET /system/version` returns `current_version`, `rollback_available` (int count),
  and `rollback_target` (short hash + subject, or `null`). Subject-line lookup
  falls back to the bare hash if `git log` fails for any reason — this endpoint
  drives button rendering on every page load and must not 500.
- No confirmation dialog on Roll Back — matches the existing Update button.
- No "redo"/forward stack — out of scope.
- Known, documented (not fixed) limitation: `backend/requirements.txt` pins
  minimum versions only (`>=`), so rollback's dependency reinstall step does not
  guarantee downgraded packages.

---

### Task 1: `update_history` stack helpers in `db.py`

**Files:**
- Modify: `backend/db.py:1-27` (add `import json`), and insert a new section after
  `set_settings` (currently ends at `backend/db.py:220`, immediately before the
  `# ── Pages helpers ──` comment at `backend/db.py:223`)
- Test: `tests/test_db.py` (new file)

**Interfaces:**
- Produces: `db.get_update_history() -> list[str]`,
  `db.push_update_history(commit_hash: str) -> None`,
  `db.pop_update_history() -> str | None` — consumed by `update_service.py` in
  Task 2.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_db.py`:

```python
"""Tests for backend/db.py — update_history rollback stack."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pytest
import db


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point db.py at a fresh SQLite file per test so tests never touch the real DB."""
    monkeypatch.setattr(db, "_DB_DIR", tmp_path)
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "test.db")
    db.init_db()


def test_get_update_history_empty_by_default():
    assert db.get_update_history() == []


def test_push_then_get_returns_pushed_hash():
    db.push_update_history("a" * 40)
    assert db.get_update_history() == ["a" * 40]


def test_push_trims_to_last_five():
    hashes = [str(i) * 40 for i in range(1, 7)]  # 6 distinct hashes
    for h in hashes:
        db.push_update_history(h)
    assert db.get_update_history() == hashes[-5:]


def test_pop_returns_most_recently_pushed():
    db.push_update_history("a" * 40)
    db.push_update_history("b" * 40)
    assert db.pop_update_history() == "b" * 40
    assert db.pop_update_history() == "a" * 40


def test_pop_empty_returns_none():
    assert db.pop_update_history() is None


def test_pop_removes_entry_from_history():
    db.push_update_history("a" * 40)
    db.pop_update_history()
    assert db.get_update_history() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db.py -v`
Expected: FAIL — `AttributeError: module 'db' has no attribute 'get_update_history'`

- [ ] **Step 3: Add `import json` to `db.py`**

In `backend/db.py`, change the import block at the top (currently lines 20-26):

```python
from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional
```

to:

```python
from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional
```

- [ ] **Step 4: Add the update_history helpers**

Insert this new section into `backend/db.py` immediately after the `set_settings`
function (which currently ends right before the `# ── Pages helpers ──` comment):

```python
# ── Update history (OTA rollback) ───────────────────────────────────────────

_UPDATE_HISTORY_KEY = "update_history"
_UPDATE_HISTORY_MAX = 5


def get_update_history() -> list[str]:
    """Return the OTA rollback stack — full commit hashes, oldest first.

    Empty list if no updates have been applied yet, or if the stored value is
    corrupt (defensive — avoids a bad settings row bricking the update UI).
    """
    raw = get_setting(_UPDATE_HISTORY_KEY)
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def push_update_history(commit_hash: str) -> None:
    """Push a commit hash onto the rollback stack, trimming to the last 5 entries."""
    history = get_update_history()
    history.append(commit_hash)
    history = history[-_UPDATE_HISTORY_MAX:]
    set_settings({_UPDATE_HISTORY_KEY: json.dumps(history)})


def pop_update_history() -> Optional[str]:
    """Pop and return the most recent rollback target, or None if the stack is empty."""
    history = get_update_history()
    if not history:
        return None
    commit_hash = history.pop()
    set_settings({_UPDATE_HISTORY_KEY: json.dumps(history)})
    return commit_hash
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/db.py tests/test_db.py
git commit -m "Add update_history rollback stack helpers to db.py"
```

---

### Task 2: `update_service.py` — git/subprocess orchestration

**Files:**
- Create: `backend/update_service.py`
- Test: `tests/test_update_service.py` (new file)

**Interfaces:**
- Consumes: `db.get_update_history()`, `db.push_update_history(str)`,
  `db.pop_update_history() -> str | None` (from Task 1)
- Produces (consumed by `main.py` in Task 3):
  - `update_service.GitError(Exception)` — carries combined stdout/stderr as its
    message
  - `update_service.NoPreviousVersionError(Exception)` — no args
  - `update_service.perform_update() -> dict` — `{"ok": True, "output": str,
    "restarting": True}`; raises `GitError` on pull failure
  - `update_service.perform_rollback() -> dict` — same shape; raises
    `NoPreviousVersionError` if history is empty, `GitError` if reset fails
  - `update_service.get_version_info() -> dict` — `{"current_version": str,
    "rollback_available": int, "rollback_target": str | None}`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_update_service.py`:

```python
"""Tests for backend/update_service.py — OTA update/rollback orchestration.

All subprocess calls are mocked; these tests never touch a real git repo.
"""
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pytest
import update_service as svc


def _git_result(returncode=0, stdout="", stderr=""):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


# ── capture_head / describe_commit ──────────────────────────────────────────

def test_capture_head_returns_stripped_output():
    with patch.object(svc.subprocess, "run", return_value=_git_result(stdout="abc123\n")):
        assert svc.capture_head() == "abc123"


def test_capture_head_raises_git_error_on_failure():
    with patch.object(svc.subprocess, "run", return_value=_git_result(returncode=1, stderr="fatal: no repo")):
        with pytest.raises(svc.GitError):
            svc.capture_head()


def test_describe_commit_falls_back_to_hash_on_git_failure():
    with patch.object(svc.subprocess, "run", return_value=_git_result(returncode=1, stderr="bad object")):
        result = svc.describe_commit("a" * 40)
    assert result == ("a" * 40)[:7]


# ── perform_update ───────────────────────────────────────────────────────────

def test_perform_update_pushes_history_when_head_moves(monkeypatch):
    monkeypatch.setattr(svc.db, "push_update_history", MagicMock())
    monkeypatch.setattr(svc, "reinstall_deps", MagicMock())
    # perform_update() calls capture_head() exactly twice: once before `pull`,
    # once after. Two items only — a third would silently mis-pair the calls.
    heads = iter(["old_hash", "new_hash"])

    def fake_run(*args, **kwargs):
        argv = args[0]
        if argv[:2] == ["git", "-C"] and "rev-parse" in argv:
            return _git_result(stdout=next(heads) + "\n")
        if "pull" in argv:
            return _git_result(stdout="Updating old_hash..new_hash\n")
        raise AssertionError(f"unexpected subprocess call: {argv}")

    with patch.object(svc.subprocess, "run", side_effect=fake_run):
        result = svc.perform_update()

    svc.db.push_update_history.assert_called_once_with("old_hash")
    assert result == {"ok": True, "output": "Updating old_hash..new_hash", "restarting": True}


def test_perform_update_skips_history_when_pull_is_noop(monkeypatch):
    monkeypatch.setattr(svc.db, "push_update_history", MagicMock())
    monkeypatch.setattr(svc, "reinstall_deps", MagicMock())

    def fake_run(*args, **kwargs):
        argv = args[0]
        if "rev-parse" in argv:
            return _git_result(stdout="same_hash\n")
        if "pull" in argv:
            return _git_result(stdout="Already up to date.\n")
        raise AssertionError(f"unexpected subprocess call: {argv}")

    with patch.object(svc.subprocess, "run", side_effect=fake_run):
        svc.perform_update()

    svc.db.push_update_history.assert_not_called()


def test_perform_update_raises_and_skips_history_on_pull_failure(monkeypatch):
    monkeypatch.setattr(svc.db, "push_update_history", MagicMock())
    monkeypatch.setattr(svc, "reinstall_deps", MagicMock())

    def fake_run(*args, **kwargs):
        argv = args[0]
        if "rev-parse" in argv:
            return _git_result(stdout="old_hash\n")
        if "pull" in argv:
            return _git_result(returncode=1, stderr="fatal: unable to access network")
        raise AssertionError(f"unexpected subprocess call: {argv}")

    with patch.object(svc.subprocess, "run", side_effect=fake_run):
        with pytest.raises(svc.GitError):
            svc.perform_update()

    svc.db.push_update_history.assert_not_called()
    svc.reinstall_deps.assert_not_called()


# ── perform_rollback ─────────────────────────────────────────────────────────

def test_perform_rollback_raises_when_history_empty(monkeypatch):
    monkeypatch.setattr(svc.db, "pop_update_history", MagicMock(return_value=None))
    with pytest.raises(svc.NoPreviousVersionError):
        svc.perform_rollback()


def test_perform_rollback_resets_to_popped_hash(monkeypatch):
    monkeypatch.setattr(svc.db, "pop_update_history", MagicMock(return_value="old_hash"))
    monkeypatch.setattr(svc, "reinstall_deps", MagicMock())

    def fake_run(*args, **kwargs):
        argv = args[0]
        if "reset" in argv:
            assert "old_hash" in argv
            return _git_result(stdout="HEAD is now at old_hash\n")
        raise AssertionError(f"unexpected subprocess call: {argv}")

    with patch.object(svc.subprocess, "run", side_effect=fake_run):
        result = svc.perform_rollback()

    assert result == {"ok": True, "output": "HEAD is now at old_hash", "restarting": True}
    svc.reinstall_deps.assert_called_once()


# ── get_version_info ─────────────────────────────────────────────────────────

def test_get_version_info_no_history():
    with patch.object(svc.db, "get_update_history", return_value=[]), \
         patch.object(svc, "capture_head", return_value="head_hash"), \
         patch.object(svc, "describe_commit", return_value="head7 subject line"):
        info = svc.get_version_info()

    assert info == {
        "current_version": "head7 subject line",
        "rollback_available": 0,
        "rollback_target": None,
    }


def test_get_version_info_with_history():
    with patch.object(svc.db, "get_update_history", return_value=["a" * 40, "b" * 40]), \
         patch.object(svc, "capture_head", return_value="head_hash"), \
         patch.object(svc, "describe_commit", side_effect=lambda h: f"described:{h}"):
        info = svc.get_version_info()

    assert info["rollback_available"] == 2
    assert info["rollback_target"] == f"described:{'b' * 40}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_update_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'update_service'`

- [ ] **Step 3: Write `backend/update_service.py`**

```python
"""
OTA update/rollback orchestration.

Kept separate from main.py so the git/subprocess logic is unit-testable
without importing the FastAPI app. See docs/superpowers/specs/2026-07-03-ota-rollback-design.md.

Rollback works by popping a full commit hash off the `update_history` stack
(db.py) and running `git reset --hard <hash>` — this moves the current branch
pointer backwards rather than checking out a detached HEAD, so a later
`git pull` still fast-forwards normally. This assumes upstream history is never
rebased/force-pushed, which holds for this repo's single-maintainer workflow.

Known limitation: backend/requirements.txt pins minimum versions only (>=), so
reinstall_deps() does not guarantee downgraded packages after a rollback — it
reliably reverts code, not necessarily exact dependency versions.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

import db

log = logging.getLogger(__name__)

REPO_DIR = Path(__file__).resolve().parent.parent
VENV_PIP = REPO_DIR / ".venv" / "bin" / "pip"

_APT_PACKAGES = [
    "freedoom", "openbox", "libsamplerate0", "python3-evdev",
    "fonts-noto-color-emoji",
]


class GitError(Exception):
    """Raised when a git operation fails. Message is the combined stdout/stderr."""


class NoPreviousVersionError(Exception):
    """Raised by perform_rollback() when the update_history stack is empty."""


def _run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(REPO_DIR), *args],
        capture_output=True, text=True,
    )
    output = result.stdout.strip() or result.stderr.strip() or "No output"
    if result.returncode != 0:
        raise GitError(output)
    return output


def capture_head() -> str:
    """Return the full 40-character hash of the current HEAD commit."""
    return _run_git("rev-parse", "HEAD")


def describe_commit(commit_hash: str) -> str:
    """Return '<short-hash> <subject>' for a commit.

    Falls back to the bare (shortened) hash if the git log lookup fails for any
    reason — get_version_info() drives button rendering on every page load and
    must not raise.
    """
    try:
        return _run_git("log", "-1", "--format=%h %s", commit_hash)
    except GitError:
        return commit_hash[:7]


def reinstall_deps() -> None:
    """Reinstall Python deps and apt packages for the currently-checked-out commit.

    Best-effort: failures are logged but never raised, so a broken package
    manager never blocks an update/rollback from completing.
    """
    if VENV_PIP.exists():
        pip_result = subprocess.run(
            [str(VENV_PIP), "install", "-q", "-r",
             str(REPO_DIR / "backend" / "requirements.txt")],
            capture_output=True, text=True,
        )
        if pip_result.returncode != 0:
            log.warning("OTA pip install failed (rc=%d): %s", pip_result.returncode,
                        (pip_result.stderr or pip_result.stdout).strip()[:200])

    apt_result = subprocess.run(
        ["sudo", "apt-get", "install", "-y", *_APT_PACKAGES],
        capture_output=True, text=True,
    )
    if apt_result.returncode != 0:
        log.warning("OTA apt-get failed (rc=%d): %s", apt_result.returncode,
                    (apt_result.stderr or apt_result.stdout).strip()[:200])


def perform_update() -> dict:
    """
    Pull latest code, recording the pre-pull commit in the rollback history
    only if the pull actually moved HEAD.

    Raises GitError if `git pull` fails — no history entry is written and
    dependencies are not reinstalled in that case.
    """
    old = capture_head()
    output = _run_git("pull")
    new = capture_head()
    if new != old:
        db.push_update_history(old)
    reinstall_deps()
    return {"ok": True, "output": output, "restarting": True}


def perform_rollback() -> dict:
    """
    Roll back to the most recent entry in the update_history rollback stack.

    Raises NoPreviousVersionError if there is nothing to roll back to, or
    GitError if `git reset --hard` fails.
    """
    target = db.pop_update_history()
    if target is None:
        raise NoPreviousVersionError()
    output = _run_git("reset", "--hard", target)
    reinstall_deps()
    return {"ok": True, "output": output, "restarting": True}


def get_version_info() -> dict:
    """Current commit + rollback availability, for the Settings UI."""
    history = db.get_update_history()
    return {
        "current_version": describe_commit(capture_head()),
        "rollback_available": len(history),
        "rollback_target": describe_commit(history[-1]) if history else None,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_update_service.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/update_service.py tests/test_update_service.py
git commit -m "Add update_service.py — testable OTA update/rollback orchestration"
```

---

### Task 3: Wire `/system/update`, `/system/rollback`, `/system/version` in `main.py`

**Files:**
- Modify: `backend/main.py:96-99` (remove now-unused `REPO_DIR`/`VENV_PIP`)
- Modify: `backend/main.py:482-524` (replace inline `/system/update` body)
- Modify: `backend/main.py` (add `/system/rollback` and `/system/version` after
  the rewritten `/system/update`, before `/system/restart` at `backend/main.py:527`)

**Interfaces:**
- Consumes: `update_service.perform_update()`, `update_service.perform_rollback()`,
  `update_service.get_version_info()`, `update_service.GitError`,
  `update_service.NoPreviousVersionError` (from Task 2)
- Produces: `POST /system/update`, `POST /system/rollback`, `GET /system/version`
  HTTP endpoints — consumed by the frontend in Task 4

- [ ] **Step 1: Add the `update_service` import**

In `backend/main.py`, find the existing imports (near the top, alongside
`import db`, `import game_service`, etc. — `backend/main.py:16-20`):

```python
import db
import game_service
import spotify_service
import system_service
from ws_hub import ConnectionManager
```

Change to:

```python
import db
import game_service
import spotify_service
import system_service
import update_service
from ws_hub import ConnectionManager
```

- [ ] **Step 2: Remove the now-unused `REPO_DIR` / `VENV_PIP` module-level vars**

In `backend/main.py`, this block (`backend/main.py:96-99`):

```python
manager  = ConnectionManager()
FRONTEND = Path(__file__).parent.parent / "frontend"
REPO_DIR = Path(__file__).parent.parent
VENV_PIP = REPO_DIR / ".venv" / "bin" / "pip"
```

becomes:

```python
manager  = ConnectionManager()
FRONTEND = Path(__file__).parent.parent / "frontend"
```

(`update_service.py` now owns its own `REPO_DIR`/`VENV_PIP` constants — see Task 2.)

- [ ] **Step 3: Replace the `/system/update` handler and add the two new endpoints**

In `backend/main.py`, replace the entire existing handler (`backend/main.py:482-524`):

```python
@app.post("/system/update")
async def system_update() -> dict:
    """
    Pull latest code from git, update Python dependencies, then restart.

    Returns the git output so the frontend can display what changed.
    The service will restart ~1.5 s after this response is sent —
    callers should expect the connection to drop and handle it gracefully.
    """
    # git pull — abort if it fails so we don't restart with stale code
    git = subprocess.run(
        ["git", "-C", str(REPO_DIR), "pull"],
        capture_output=True, text=True,
    )
    git_out = git.stdout.strip() or git.stderr.strip() or "No output"
    if git.returncode != 0:
        raise HTTPException(500, {"error": "git_pull_failed", "output": git_out})

    # pip install (handles requirements changes; quiet to keep output clean)
    if VENV_PIP.exists():
        subprocess.run(
            [str(VENV_PIP), "install", "-q", "-r",
             str(REPO_DIR / "backend" / "requirements.txt")],
            capture_output=True,
        )

    # apt install game deps — idempotent; upgrades if newer packages are available.
    # Failure is logged but does not abort the restart — a broken package manager
    # should not prevent code updates from landing.
    apt = subprocess.run(
        ["sudo", "apt-get", "install", "-y",
         "freedoom", "openbox", "libsamplerate0", "python3-evdev",
         "fonts-noto-color-emoji"],
        capture_output=True, text=True,
    )
    if apt.returncode != 0:
        log.warning("OTA apt-get failed (rc=%d): %s", apt.returncode,
                    (apt.stderr or apt.stdout).strip()[:200])

    _clear_chromium_cache()
    asyncio.create_task(_delayed_restart())

    return {"ok": True, "output": git_out, "restarting": True}
```

with:

```python
@app.post("/system/update")
async def system_update() -> dict:
    """
    Pull latest code from git, update Python dependencies, then restart.

    Delegates git/subprocess orchestration to update_service.perform_update(),
    which also records the pre-pull commit in the rollback history (see
    db.push_update_history) so Roll Back can undo this update later.

    The service will restart ~1.5 s after this response is sent —
    callers should expect the connection to drop and handle it gracefully.
    """
    try:
        result = update_service.perform_update()
    except update_service.GitError as exc:
        raise HTTPException(500, {"error": "git_pull_failed", "output": str(exc)})

    _clear_chromium_cache()
    asyncio.create_task(_delayed_restart())
    return result


@app.post("/system/rollback")
async def system_rollback() -> dict:
    """
    Roll back to the commit checked out immediately before the most recent
    update (see update_service.perform_rollback and db.pop_update_history).

    The service will restart ~1.5 s after this response is sent —
    callers should expect the connection to drop and handle it gracefully.
    """
    try:
        result = update_service.perform_rollback()
    except update_service.NoPreviousVersionError:
        raise HTTPException(400, {"error": "no_previous_version"})
    except update_service.GitError as exc:
        raise HTTPException(500, {"error": "git_reset_failed", "output": str(exc)})

    _clear_chromium_cache()
    asyncio.create_task(_delayed_restart())
    return result


@app.get("/system/version")
async def system_version() -> dict:
    """Current commit + rollback availability, for the Settings UI."""
    return update_service.get_version_info()
```

- [ ] **Step 4: Run the full backend test suite**

Run: `pytest tests/ -v`
Expected: PASS — all existing tests plus the new `test_db.py` and
`test_update_service.py` suites, no regressions.

- [ ] **Step 5: Manual smoke test on a dev checkout**

This exercises the real git plumbing end-to-end (Tasks 1-2 only mocked
subprocess). Run from the repo root, with the backend running locally
(`docker compose up --build` or `uvicorn main:app` from `backend/`):

```bash
# Make a throwaway commit so `git pull` (against the local repo itself, using
# `git pull` with no remote configured is fine to test git plumbing failure
# handling) — instead, verify the read-only paths directly:
curl -s http://localhost:8000/system/version | python -m json.tool
```

Expected: `{"current_version": "<hash> <subject of your last commit>",
"rollback_available": 0, "rollback_target": null}`

```bash
curl -s -X POST http://localhost:8000/system/rollback | python -m json.tool
```

Expected: `400` with `{"error": "no_previous_version"}` (no history yet).

Full push/pop round-trip against a real git history is exercised by
`test_update_service.py` (Task 2) with subprocess mocked — a full unmocked
`git pull` + `git reset --hard` cycle is only meaningfully testable on the
actual Pi with a real upstream remote, per the design doc's testing section.

- [ ] **Step 6: Commit**

```bash
git add backend/main.py
git commit -m "Wire /system/rollback and /system/version endpoints, delegate /system/update to update_service"
```

---

### Task 4: Frontend — Roll Back button

**Files:**
- Modify: `frontend/index.html:553-557` (add the Roll Back button)
- Modify: `frontend/style.css` (add disabled-state styling near the existing
  `.relay-btn` rules, `frontend/style.css:438-469`)
- Modify: `frontend/app.js:862-896` (add `refreshVersionInfo()` and
  `triggerRollback()`; wire `refreshVersionInfo()` into the existing startup IIFE
  and into `triggerUpdate()`'s reconnect handler)

**Interfaces:**
- Consumes: `GET /system/version` → `{current_version, rollback_available,
  rollback_target}`, `POST /system/rollback` → `{ok, output, restarting}` or
  `400 {error: "no_previous_version"}` (from Task 3)

- [ ] **Step 1: Add the button markup**

In `frontend/index.html`, this block (`frontend/index.html:553-557`):

```html
              <div class="stat-label settings-lower-label">Software</div>
              <button class="relay-btn" id="btn-update" onclick="triggerUpdate()">
                <span class="relay-btn__label" id="lbl-update">Check for Updates</span>
              </button>
              <div class="update-status" id="update-status"></div>
```

becomes:

```html
              <div class="stat-label settings-lower-label">Software</div>
              <button class="relay-btn" id="btn-update" onclick="triggerUpdate()">
                <span class="relay-btn__label" id="lbl-update">Check for Updates</span>
              </button>
              <button class="relay-btn" id="btn-rollback" onclick="triggerRollback()" disabled>
                <span class="relay-btn__label" id="lbl-rollback">Roll Back</span>
              </button>
              <div class="update-status" id="update-status"></div>
```

- [ ] **Step 2: Add disabled-state styling**

In `frontend/style.css`, immediately after the `.relay-btn` rule
(`frontend/style.css:438-451`), add:

```css
.relay-btn:disabled {
  opacity: 0.3;
  cursor: default;
}
```

- [ ] **Step 3: Add `refreshVersionInfo()` and `triggerRollback()` to `app.js`**

In `frontend/app.js`, immediately after the existing `triggerUpdate()` function
(ends at `frontend/app.js:896`), add:

```javascript
// ── OTA rollback ───────────────────────────────
function _setRollbackButton(target) {
  const btn = document.getElementById('btn-rollback');
  const lbl = document.getElementById('lbl-rollback');
  if (target) {
    btn.disabled    = false;
    lbl.textContent = `Roll Back to ${target}`;
  } else {
    btn.disabled    = true;
    lbl.textContent = 'Roll Back';
  }
}

async function refreshVersionInfo() {
  try {
    const r    = await fetch('/system/version');
    const data = await r.json();
    _setRollbackButton(data.rollback_target);
  } catch (_) {
    // Backend not reachable yet (e.g. mid-restart) — leave button as-is.
  }
}

async function triggerRollback() {
  const btn    = document.getElementById('btn-rollback');
  const lbl    = document.getElementById('lbl-rollback');
  const status = document.getElementById('update-status');

  btn.disabled = true;
  lbl.textContent = 'Rolling back…';
  status.textContent = '';
  status.className = 'update-status';

  try {
    const r    = await fetch('/system/rollback', { method: 'POST' });
    const data = await r.json();

    if (!r.ok) {
      status.textContent = data.error === 'no_previous_version'
        ? 'No previous version to roll back to.'
        : (data.output || 'Rollback failed.');
      status.className = 'update-status';
      refreshVersionInfo();  // re-enable/disable based on actual server state
      return;
    }

    lbl.textContent    = 'Restarting…';
    status.textContent = data.output;
    status.className   = 'update-status update-status--ok';
  } catch (_) {
    // Expected — service restarted before response completed
    lbl.textContent    = 'Restarting…';
    status.textContent = 'Reconnecting…';
    status.className   = 'update-status update-status--ok';
  }

  // Re-enable once the WS reconnects (service is back up)
  const _resetBtn = () => {
    btn.disabled = false;
    refreshVersionInfo();
  };
  document.addEventListener('td5-ws-connected', _resetBtn, { once: true });
  // Fallback: reset after 30 s if reconnect event never fires
  setTimeout(_resetBtn, 30_000);
}
```

- [ ] **Step 4: Fetch version info on page load and after an update completes**

In `frontend/app.js`, find the existing startup IIFE that runs at script load
(`frontend/app.js:860-861`):

```javascript
  _applyRelayUI('amp');
}());
```

Change to:

```javascript
  _applyRelayUI('amp');
  refreshVersionInfo();
}());
```

Then, in `triggerUpdate()`'s reconnect handler (`frontend/app.js:888-892`):

```javascript
  const _resetBtn = () => {
    btn.disabled    = false;
    lbl.textContent = 'Check for Updates';
    // Leave status text visible so user can see what changed
  };
```

change to:

```javascript
  const _resetBtn = () => {
    btn.disabled    = false;
    lbl.textContent = 'Check for Updates';
    // Leave status text visible so user can see what changed
    refreshVersionInfo();  // update just moved HEAD — refresh the rollback target
  };
```

- [ ] **Step 5: Manual browser verification**

Start the dev stack (`docker compose up --build`) and open
`http://localhost:8000`, navigate to Settings → Software:

1. Confirm the "Roll Back" button renders **disabled** (dimmed via the new
   `:disabled` style) on load, since `update_history` is empty on a fresh DB.
2. Open devtools Network tab, confirm a `GET /system/version` request fires on
   page load and returns `rollback_target: null`.
3. Click "Check for Updates" (this runs a real `git pull` against whatever
   remote is configured — safe to run against this repo). Confirm the button
   goes through "Checking…" → "Restarting…" → re-enables as "Check for
   Updates" after the WebSocket reconnects.
4. After the update completes, reload the page and re-check
   `GET /system/version` — if the pull actually moved HEAD, `rollback_target`
   should now be populated and the "Roll Back" button should be enabled with a
   label like "Roll Back to `<hash>` `<subject>`".
5. Click "Roll Back" and confirm it goes through the same
   disable → "Rolling back…" → "Restarting…" → re-enable cycle, and that the
   repo's `git log -1` afterward matches the hash that was shown on the button.

- [ ] **Step 6: Commit**

```bash
git add frontend/index.html frontend/style.css frontend/app.js
git commit -m "Add Roll Back button to Settings — mirrors Check for Updates UX"
```

---

## Post-implementation checklist

- [ ] `pytest tests/ -v` passes with no regressions
- [ ] Manual browser verification (Task 4, Step 5) completed
- [ ] `docs/superpowers/specs/2026-07-03-ota-rollback-design.md`'s "Testing"
      section requirements are all covered:
  - `update_history` push/trim logic — `test_db.py` (Task 1)
  - No-op/failed-pull dedup guard — `test_update_service.py::test_perform_update_skips_history_when_pull_is_noop` and `::test_perform_update_raises_and_skips_history_on_pull_failure` (Task 2)
  - Empty-history 400 path — `test_update_service.py::test_perform_rollback_raises_when_history_empty` (Task 2), confirmed end-to-end via curl in Task 3, Step 5
