import asyncio
import logging
import os
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import carpihat_service
import spotify_service
import system_service
from ws_hub import ConnectionManager
from mock_service import (
    mock_engine_loop,
    mock_victron_loop,
    mock_spotify_loop,
    mock_system_loop,
    mock_starlink_loop,
    mock_weather_loop,
)

# ── Service selection via environment variables ────────────────────────────────
#
#   TD5_MOCK=1      (default)  Engine data from mock_service  — no KKL cable needed
#   TD5_MOCK=0                 Engine data from obd.service   — requires KKL cable + vehicle
#
#   VICTRON_MOCK=1  (default)  Victron data from mock_service — no BLE hardware needed
#   VICTRON_MOCK=0             Victron data from victron.service — requires SmartShunt + MPPT
#
#   WEATHER_MOCK=1  (default)  Weather data from mock_service — no network needed
#   WEATHER_MOCK=0             Weather data from weather_service — fetches Open-Meteo API
#
#   SPOTIFY_MOCK=1  (default)  Spotify data from mock_service  — no credentials needed
#   SPOTIFY_MOCK=0             Spotify data from spotify_service — requires Spotify credentials
#
#   SYSTEM_MOCK=1              System data from mock_service  — static values for Docker UI work
#   SYSTEM_MOCK=0   (default)  System data from system_service — real CPU temp, backlight, Wi-Fi/BT
#
# Docker (development): all mock to 1 except SYSTEM_MOCK (real system data works in Docker too,
#   it just returns -1 for Pi-specific paths, which the frontend handles gracefully).
# Pi systemd service:   TD5_MOCK, VICTRON_MOCK, WEATHER_MOCK, SPOTIFY_MOCK set to 0.

if os.getenv("TD5_MOCK", "1") == "0":
    from obd.service import broadcast_loop as engine_loop
else:
    engine_loop = mock_engine_loop

if os.getenv("VICTRON_MOCK", "1") == "0":
    from victron.service import broadcast_loop as victron_loop
else:
    victron_loop = mock_victron_loop

if os.getenv("STARLINK_MOCK", "1") == "0":
    from starlink_service import broadcast_loop as starlink_loop
else:
    starlink_loop = mock_starlink_loop

if os.getenv("WEATHER_MOCK", "1") == "0":
    from weather_service import broadcast_loop as weather_loop
else:
    weather_loop = mock_weather_loop

if os.getenv("SPOTIFY_MOCK", "1") == "0":
    spotify_loop = spotify_service.broadcast_loop
else:
    spotify_loop = mock_spotify_loop

if os.getenv("SYSTEM_MOCK", "0") == "1":
    system_loop = mock_system_loop
else:
    system_loop = system_service.broadcast_loop

manager  = ConnectionManager()
FRONTEND = Path(__file__).parent.parent / "frontend"
REPO_DIR = Path(__file__).parent.parent
VENV_PIP = REPO_DIR / ".venv" / "bin" / "pip"


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [
        asyncio.create_task(engine_loop(manager)),
        asyncio.create_task(victron_loop(manager)),
        asyncio.create_task(spotify_loop(manager)),
        asyncio.create_task(system_loop(manager)),
        asyncio.create_task(starlink_loop(manager)),
        asyncio.create_task(weather_loop(manager)),
        asyncio.create_task(carpihat_service.monitor_loop()),
    ]
    yield
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="TD5 Dash", lifespan=lifespan)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)


class _SpotifyCmd(BaseModel):
    action: str   # play | pause | next | prev


class _PlayContext(BaseModel):
    context_uri: str
    track_uri: str | None = None


class _LikeTrack(BaseModel):
    track_id: str


class _BrightnessCmd(BaseModel):
    value: int   # 0–255


class _RelayCmd(BaseModel):
    name:  str   # e.g. "amp"
    state: bool


@app.post("/spotify/command")
async def spotify_command(cmd: _SpotifyCmd) -> dict:
    """Forward a playback command to Spotify Web API."""
    ok = await spotify_service.send_command(cmd.action)
    if not ok:
        raise HTTPException(status_code=503, detail="Spotify not available")
    return {"ok": True}


@app.post("/spotify/like")
async def spotify_like(body: _LikeTrack) -> dict:
    """Save a track to the user's Liked Songs."""
    ok = await spotify_service.save_track(body.track_id)
    if not ok:
        raise HTTPException(status_code=503, detail="Spotify not available")
    return {"ok": True}


@app.get("/spotify/playlists")
async def spotify_playlists() -> dict:
    """Return the current user's playlists."""
    playlists = await spotify_service.get_playlists()
    if playlists is None:
        raise HTTPException(status_code=503, detail="Spotify not available")
    return {"playlists": playlists}


@app.get("/spotify/playlist/{playlist_id}/tracks")
async def spotify_tracks(playlist_id: str) -> dict:
    """Return the tracks in a playlist."""
    tracks = await spotify_service.get_playlist_tracks(playlist_id)
    if tracks is None:
        raise HTTPException(status_code=503, detail="Spotify not available")
    return {"tracks": tracks}


@app.post("/spotify/play")
async def spotify_play(cmd: _PlayContext) -> dict:
    """Start playback of a playlist context, optionally from a specific track."""
    ok = await spotify_service.play_context(cmd.context_uri, cmd.track_uri)
    if not ok:
        raise HTTPException(status_code=503, detail="Spotify not available")
    return {"ok": True}


@app.post("/system/brightness")
async def set_brightness(cmd: _BrightnessCmd) -> dict:
    """Set display backlight brightness (0–255). Writes to sysfs on Pi; no-op elsewhere."""
    import glob, pathlib
    value = max(0, min(255, cmd.value))
    paths = glob.glob("/sys/class/backlight/*/brightness")
    if paths:
        try:
            pathlib.Path(paths[0]).write_text(str(value))
        except OSError as exc:
            log.warning("Could not write brightness: %s", exc)
    else:
        log.debug("Brightness write skipped — no backlight device found (Docker/dev)")
    return {"ok": True, "value": value}


@app.post("/system/relay")
async def set_relay(cmd: _RelayCmd) -> dict:
    """Control a named output relay. Writes GPIO pin on Pi; logs only on Docker/dev."""
    carpihat_service.set_relay(cmd.name, cmd.state)
    return {"ok": True, "name": cmd.name, "state": cmd.state}


# ── API: state snapshot ───────────────────────────────────────────────────────

@app.get("/api/state")
async def api_state_all() -> dict:
    """
    Full current state for all topics.

    Each topic entry includes:
      data        — last-broadcast payload
      updated_at  — ISO-8601 UTC timestamp
      stale       — True if no update received in the last 30 s

    Intended for Home Assistant REST sensors and remote dashboards.
    """
    return {
        "server_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "topics": manager.get_state(),
    }


@app.get("/api/state/{topic}")
async def api_state_topic(topic: str) -> dict:
    """Single-topic state snapshot — e.g. GET /api/state/victron"""
    entry = manager.get_state(topic)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Topic '{topic}' not yet received")
    return entry


# ── System: OTA update ────────────────────────────────────────────────────────

async def _delayed_restart() -> None:
    """Wait briefly so the HTTP response is sent, then restart the service."""
    await asyncio.sleep(1.5)
    # In production (Pi): restart via systemd
    # In Docker/dev: systemctl is absent — skip silently
    if subprocess.run(["which", "systemctl"], capture_output=True).returncode == 0:
        subprocess.Popen(["sudo", "systemctl", "restart", "td5-dash"])
    else:
        log.info("Update: systemctl not available (Docker/dev) — restart manually")


@app.post("/system/update")
async def system_update() -> dict:
    """
    Pull latest code from git, update Python dependencies, then restart.

    Returns the git output so the frontend can display what changed.
    The service will restart ~1.5 s after this response is sent —
    callers should expect the connection to drop and handle it gracefully.
    """
    # git pull
    git = subprocess.run(
        ["git", "-C", str(REPO_DIR), "pull"],
        capture_output=True, text=True,
    )
    git_out = git.stdout.strip() or git.stderr.strip() or "No output"

    # pip install (handles requirements changes; quiet to keep output clean)
    if VENV_PIP.exists():
        subprocess.run(
            [str(VENV_PIP), "install", "-q", "-r",
             str(REPO_DIR / "backend" / "requirements.txt")],
            capture_output=True,
        )

    asyncio.create_task(_delayed_restart())

    return {"ok": True, "output": git_out, "restarting": True}


# Static files mount last so /ws, /api/*, /spotify/*, /system/* are matched first.
app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="static")
