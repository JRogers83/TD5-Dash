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
    GitError if `git reset --hard` fails. The history entry is only removed
    from the stack after a successful reset, so a failed reset leaves the
    rollback target intact for a retry.
    """
    history = db.get_update_history()
    if not history:
        raise NoPreviousVersionError()
    target = history[-1]
    output = _run_git("reset", "--hard", target)
    db.pop_update_history()
    reinstall_deps()
    return {"ok": True, "output": output, "restarting": True}


def get_version_info() -> dict:
    """Current commit + rollback availability, for the Settings UI.

    Must never raise — this drives button rendering on every page load. If
    even `git rev-parse HEAD` fails, current_version falls back to a safe
    placeholder; rollback_available/rollback_target are independent of
    capture_head() and are unaffected.
    """
    history = db.get_update_history()
    try:
        current_version = describe_commit(capture_head())
    except GitError:
        current_version = "unknown"
    return {
        "current_version": current_version,
        "rollback_available": len(history),
        "rollback_target": describe_commit(history[-1]) if history else None,
    }
