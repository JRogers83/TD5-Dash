"""
Doom mode service — exposes /system/game-mode/{status,start,stop} endpoints.

Lifecycle:
  start  → validate inputs → (eventually) pause Spotify, SIGSTOP Chromium tree,
           spawn launcher.sh as a new-session subprocess
  stop   → kill launcher process group, SIGCONT Chromium, resume Spotify
  watcher task captures launcher exit code → maps to user-facing last_error

See docs/superpowers/specs/2026-05-23-doom-mode-design.md for the full design.
"""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from pathlib import Path

import psutil

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import shared_state

import logging
log = logging.getLogger(__name__)

router = APIRouter()

# os.killpg is POSIX-only; provide a shim so tests can monkeypatch it on
# Windows and so the real Pi path calls the genuine syscall.
if hasattr(os, "killpg"):
    def _killpg(pgid: int, sig: int) -> None:  # pragma: no cover
        os.killpg(pgid, sig)
else:
    def _killpg(pgid: int, sig: int) -> None:  # pragma: no cover (Windows dev)
        log.warning("_killpg called on Windows (pgid=%d, sig=%d) — no-op", pgid, sig)

_REPO_DIR     = Path(__file__).resolve().parent.parent
_WAD_PATH     = _REPO_DIR / "wads" / "doom.wad"
_LAUNCHER     = _REPO_DIR / "games" / "doom" / "launcher.sh"
_VALID_MODES  = {"single", "coop", "deathmatch"}
_VALID_SKILLS = {1, 2, 3, 4, 5}

# Module state
_launcher_proc:  subprocess.Popen | None = None
_current_mode:   str | None = None
_spotify_was_playing: bool = False
_watcher_task:   asyncio.Task | None = None
_last_error:     str | None = None
_stop_lock = asyncio.Lock()

# Launcher exit code → user-facing message
_EXIT_CODE_MESSAGES = {
    2: "Controllers required for this mode",
    3: "Window manager failed; check journalctl",
    4: "Doom failed to start; check journalctl",
}


class StartRequest(BaseModel):
    mode:  str
    skill: int


@router.get("/system/game-mode/status")
def status() -> dict:
    running = _launcher_proc is not None and _launcher_proc.poll() is None
    return {
        "running":    running,
        "mode":       _current_mode if running else None,
        "last_error": _last_error,
    }


@router.post("/system/game-mode/start")
async def start(req: StartRequest) -> dict:
    global _launcher_proc, _current_mode, _last_error, _watcher_task, _spotify_was_playing

    # Distinguish dev environment from production misconfiguration
    if os.environ.get("DEV_MODE") == "1":
        raise HTTPException(501, {"error": "game_mode_not_available_in_dev"})
    if not os.environ.get("DISPLAY"):
        raise HTTPException(500, {"error": "display_not_available"})

    # Idempotency
    if _launcher_proc is not None and _launcher_proc.poll() is None:
        raise HTTPException(409, {"error": "already_running"})

    # Validation
    if req.mode not in _VALID_MODES:
        raise HTTPException(400, {"error": "invalid_mode"})
    if req.skill not in _VALID_SKILLS:
        raise HTTPException(400, {"error": "invalid_skill"})
    if not _WAD_PATH.is_file():
        raise HTTPException(500, {"error": "wad_missing"})

    # Clear stale error from previous run
    _last_error = None

    # Pause Spotify if playing (so its audio doesn't fight with Doom)
    _spotify_was_playing = await _pause_spotify_if_playing()

    # Freeze the Chromium kiosk tree
    _freeze_chromium_tree()

    # Launch
    # NOTE: start_new_session=True makes the launcher a session leader, so
    # PID == PGID. This is what makes os.killpg(proc.pid, ...) work in stop().
    # Do not remove without rewriting the cleanup path.
    env = {
        **os.environ,
        "MODE":  req.mode,
        "WAD":   str(_WAD_PATH),
        "SKILL": str(req.skill),
    }
    _launcher_proc = subprocess.Popen(
        [str(_LAUNCHER)],
        env=env,
        start_new_session=True,
    )
    _current_mode = req.mode

    # Watcher captures the launcher exit code → last_error
    _watcher_task = asyncio.create_task(_watch_for_exit())

    return {"ok": True}


@router.post("/system/game-mode/stop")
async def stop_endpoint() -> dict:
    await _stop_internal()
    return {"ok": True}


async def _watch_for_exit() -> None:
    """Block until launcher exits, record exit code, then run teardown."""
    global _last_error
    if _launcher_proc is None:
        return
    rc = await asyncio.to_thread(_launcher_proc.wait)
    if rc != 0:
        _last_error = _EXIT_CODE_MESSAGES.get(rc, "Game exited unexpectedly")
    await _stop_internal()


async def _stop_internal() -> None:
    """Idempotent teardown — guarded against concurrent calls from /stop + watcher."""
    global _launcher_proc, _current_mode, _watcher_task, _spotify_was_playing

    async with _stop_lock:
        proc = _launcher_proc
        _launcher_proc = None
        _current_mode  = None

        if proc is not None and proc.poll() is None:
            try:
                _killpg(proc.pid, signal.SIGTERM)
                try:
                    await asyncio.to_thread(proc.wait, timeout=3)
                except subprocess.TimeoutExpired:
                    _killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        _unfreeze_chromium_tree()

        if _spotify_was_playing:
            try:
                await _resume_spotify()
            finally:
                _spotify_was_playing = False

        if _watcher_task is not None and not _watcher_task.done():
            _watcher_task.cancel()
        _watcher_task = None


def _freeze_chromium_tree() -> None:
    """SIGSTOP the kiosk Chromium process and all its children.

    Tree-walking is unconditional: Chromium's renderer/GPU/utility children
    sometimes don't fully idle on parent-only SIGSTOP. Suspending each is safe
    and avoids that ambiguity.
    """
    pid = shared_state.chromium_pid
    if pid is None:
        return
    try:
        parent = psutil.Process(pid)
        for proc in [parent] + parent.children(recursive=True):
            try:
                proc.suspend()
            except psutil.NoSuchProcess:
                pass
    except psutil.NoSuchProcess:
        pass


def _unfreeze_chromium_tree() -> None:
    """SIGCONT the kiosk Chromium process and all its children.

    Mirror of _freeze_chromium_tree(). Called from _stop_internal() once
    the launcher process group has been killed and Doom has exited.
    """
    pid = shared_state.chromium_pid
    if pid is None:
        return
    try:
        parent = psutil.Process(pid)
        for proc in [parent] + parent.children(recursive=True):
            try:
                proc.resume()
            except psutil.NoSuchProcess:
                pass
    except psutil.NoSuchProcess:
        pass


async def _pause_spotify_if_playing() -> bool:
    """Pause Spotify if currently playing. Returns True only if we actually paused it."""
    import spotify_service
    state = spotify_service.current_state()
    if state and state.get("playing"):
        ok = await spotify_service.send_command("pause")
        return ok
    return False


async def _resume_spotify() -> None:
    """Resume Spotify playback. Caller is responsible for checking _spotify_was_playing."""
    import spotify_service
    ok = await spotify_service.send_command("play")
    if not ok:
        log.warning("Spotify resume command failed")
