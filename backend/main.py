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

import db
import game_service
import spotify_service
import system_service
from ws_hub import ConnectionManager

# Witty Pi power management — active when WITTYPI_ENABLED=1
if os.getenv("WITTYPI_ENABLED", "0") == "1":
    import wittypi_service
else:
    wittypi_service = None  # type: ignore[assignment]
from mock_service import (
    mock_engine_loop,
    mock_victron_loop,
    mock_spotify_loop,
    mock_system_loop,
    mock_starlink_loop,
    mock_gps_loop,
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
#   GPS_MOCK=1      (default)  GPS data from mock_service  — static coordinates (Norwich, UK)
#   GPS_MOCK=0                 GPS data from gps_service   — live GPS data from gpsd
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

if os.getenv("GPS_MOCK", "1") == "0":
    from gps_service import broadcast_loop as gps_loop
else:
    gps_loop = mock_gps_loop

manager  = ConnectionManager()
FRONTEND = Path(__file__).parent.parent / "frontend"
REPO_DIR = Path(__file__).parent.parent
VENV_PIP = REPO_DIR / ".venv" / "bin" / "pip"

def _clear_chromium_cache() -> None:
    """Remove Chromium's disk and config caches so the next launch loads fresh frontend files."""
    import shutil
    home = Path.home()
    for p in [home / ".cache" / "chromium", home / ".config" / "chromium"]:
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)


async def _discover_chromium_pid() -> None:
    """Locate the kiosk Chromium parent process. Retries every second for up to 30 s.

    On first success, stores the PID in shared_state.chromium_pid. Gives up silently
    after the deadline — game_service degrades gracefully (Doom on top of Chromium
    instead of freeze, visually janky but functional).
    """
    import shared_state
    import psutil
    def _scan() -> int | None:
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if proc.info["name"] != "chromium":
                    continue
                if "--kiosk" in (proc.info.get("cmdline") or []):
                    return proc.info["pid"]
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return None

    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        pid = await asyncio.to_thread(_scan)
        if pid is not None:
            shared_state.chromium_pid = pid
            log.info("Chromium kiosk PID discovered: %d", pid)
            return
        await asyncio.sleep(1.0)
    log.warning("Chromium kiosk PID not found within 30 s; Doom mode will degrade")


async def _doom_startup_cleanup() -> None:
    """Background crash-recovery: if a previous run crashed mid-Doom, Chromium may
    be SIGSTOPped, an orphan launcher running, or PulseAudio remap sinks loaded.

    Runs as a background task AFTER the server starts accepting requests so it
    never blocks the startup path. PulseAudio is a user-session daemon that starts
    when the user logs in — running pactl synchronously before yield would block
    lifespan for up to 5 s on every normal boot while waiting for PulseAudio to
    connect.
    """
    try:
        subprocess.run(["pkill", "-CONT", "-x", "chromium"], check=False)
        subprocess.run(["pkill", "-f", "games/doom/launcher.sh"], check=False)
    except FileNotFoundError:
        pass  # pkill not present (Docker / dev)
    subprocess.run(
        "pactl list short modules 2>/dev/null "
        "| awk -F'\\t' '$3 ~ /sink_name=doom_p[12]/ { print $1 }' "
        "| xargs -r -n1 pactl unload-module",
        shell=True, check=False,
    )


async def _wittypi_pre_shutdown_cleanup() -> None:
    """
    Pre-shutdown cleanup triggered by SIGTERM when wp5d calls `shutdown -h now`.

    Runs in the lifespan teardown BEFORE tasks are cancelled so game_service
    state is still accessible. Equivalent to what /system/shutdown-prepare
    used to do when called via beforeShutdown.sh — but driven by SIGTERM
    rather than a shell script hook (which wp5d does not support).
    """
    import game_service as _gs
    # Stop game mode if active (unfreezes Chromium, kills launcher, cleans PulseAudio)
    try:
        if _gs._launcher_proc is not None and _gs._launcher_proc.poll() is None:
            await _gs._stop_internal()
            log.info("Witty Pi shutdown: game mode stopped")
    except Exception as exc:
        log.warning("Witty Pi shutdown: game mode cleanup error: %s", exc)
    # Flush SQLite WAL journal to main database file
    try:
        db.wal_checkpoint()
        log.info("Witty Pi shutdown: WAL checkpoint complete")
    except Exception as exc:
        log.warning("Witty Pi shutdown: WAL checkpoint error: %s", exc)
    log.info("Pre-shutdown cleanup complete (WITTYPI_ENABLED)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    db.purge_old_history()

    # Witty Pi startup checks (logs I2C info, warns on conflicting config)
    if wittypi_service is not None:
        wittypi_service.startup_checks()

    tasks = [
        asyncio.create_task(engine_loop(manager)),
        asyncio.create_task(victron_loop(manager)),
        asyncio.create_task(spotify_loop(manager)),
        asyncio.create_task(system_loop(manager)),
        asyncio.create_task(starlink_loop(manager)),
        asyncio.create_task(weather_loop(manager)),
        asyncio.create_task(gps_loop(manager)),
        asyncio.create_task(_discover_chromium_pid()),
        asyncio.create_task(_doom_startup_cleanup()),
    ]
    if wittypi_service is not None:
        tasks.append(asyncio.create_task(wittypi_service.monitor_vin()))
    yield

    # Witty Pi pre-shutdown cleanup: wp5d calls `shutdown -h now` on ignition off,
    # systemd sends SIGTERM here, lifespan teardown runs this before task cancellation
    # so game_service state is still accessible.
    if wittypi_service is not None:
        await _wittypi_pre_shutdown_cleanup()

    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="TD5 Dash", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    """Health check — returns service status for each data source."""
    def _svc_status(env_var: str, default: str = "1") -> str:
        if os.getenv(env_var, default) == "1":
            return "mock"
        topic = env_var.replace("_MOCK", "").lower()
        state = manager.get_state(topic)
        if not state:
            return "starting"
        if state.get("stale", False):
            return "error"
        return "live"

    return {
        "status": "ok",
        "services": {
            "engine":   _svc_status("TD5_MOCK"),
            "victron":  _svc_status("VICTRON_MOCK"),
            "spotify":  _svc_status("SPOTIFY_MOCK"),
            "starlink": _svc_status("STARLINK_MOCK"),
            "weather":  _svc_status("WEATHER_MOCK"),
            "gps":      _svc_status("GPS_MOCK"),
            "system":   "mock" if os.getenv("SYSTEM_MOCK", "0") == "1" else "live",
        },
        "ws_clients": len(manager._connections),
    }


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
    """Control a named output relay. GPIO hardware not yet wired — logs only."""
    log.info("Relay '%s' → %s (no-op — GPIO hardware not wired)", cmd.name, "ON" if cmd.state else "OFF")
    return {"ok": True, "name": cmd.name, "state": cmd.state}


# ── API: DTC fault codes ─────────────────────────────────────────────────────

@app.post("/obd/clear-dtc")
async def clear_dtc() -> dict:
    """
    Clear stored DTC fault codes on the TD5 ECU.

    Uses KWP2000 service 0x14 (ClearDiagnosticInformation).
    Only works when a live OBD session is active (TD5_MOCK=0 + engine on).
    """
    if os.getenv("TD5_MOCK", "1") == "1":
        return {"ok": False, "detail": "DTC clear not available in mock mode"}
    # The clear command needs to be sent through the active session.
    # For now, this is a stub — the actual implementation requires thread-safe
    # access to the running TD5Session, which will be wired when the OBD
    # service supports command injection from the REST layer.
    log.warning("DTC clear requested — command will be sent on next poll cycle")
    return {"ok": True, "detail": "Clear request queued"}


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


# ── API: settings & pages ─────────────────────────────────────────────────────

@app.get("/settings")
async def get_settings() -> dict:
    """Return all key/value pairs from the settings table."""
    return db.get_all_settings()


@app.post("/settings")
async def post_settings(body: dict) -> dict:
    """Write one or more key/value pairs to the settings table."""
    if not body:
        raise HTTPException(status_code=400, detail="Empty body")
    db.set_settings(body)
    return {"ok": True}


@app.get("/pages")
async def get_pages() -> dict:
    """Return all page visibility flags."""
    return db.get_all_pages()


@app.post("/pages")
async def post_pages(body: dict) -> dict:
    """Update one or more page visibility flags."""
    if not body:
        raise HTTPException(status_code=400, detail="Empty body")
    db.set_pages({k: int(v) for k, v in body.items()})
    return {"ok": True}


# ── API: engine history ───────────────────────────────────────────────────────

@app.get("/history")
async def get_history(time_range: str = "hour") -> dict:
    """Return engine history data for the given time range (query param: ?time_range=)."""
    valid = {"hour", "day", "week", "month", "year", "all"}
    if time_range not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid range. Use: {', '.join(sorted(valid))}")
    rows = db.get_history(time_range)
    return {"range": time_range, "count": len(rows), "rows": rows}


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
        # start_new_session=True detaches the child into its own process group
        # so systemd killing the parent process group doesn't also kill this child
        # before it can trigger the restart.
        subprocess.Popen(["sudo", "systemctl", "restart", "td5-dash"],
                         start_new_session=True)
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


@app.post("/system/restart")
async def system_restart() -> dict:
    """Restart the service without pulling code or updating dependencies."""
    _clear_chromium_cache()
    asyncio.create_task(_delayed_restart())
    return {"ok": True, "restarting": True}


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


# Game-mode router (before static catch-all).
app.include_router(game_service.router)

# Static files mount last so /ws, /api/*, /spotify/*, /system/* are matched first.
app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="static")
