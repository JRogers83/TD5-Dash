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

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import shared_state

router = APIRouter()

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
    global _launcher_proc, _current_mode, _last_error

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

    # The rest (Spotify pause, Chromium freeze, Popen, watcher) is added in later tasks.
    raise HTTPException(501, {"error": "not_yet_implemented"})
