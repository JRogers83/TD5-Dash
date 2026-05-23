# Doom Mode — Design Spec
**Date:** 2026-05-23
**Revision:** 2 — post-review (Claude Opus + user Q&A)

## Problem

The TD5 Dash display is 1280×400 — exactly 2× the native resolution of original Doom (320×200) along both axes when split in half. The aspect ratio of each half (1.6:1) matches DOS Doom natively. This is a near-perfect physical fit for split-screen Doom.

The user wants the ability to plug in two USB controllers and play Doom in split-screen from the driver's seat (parked), as a deliberately silly feature. The feature must coexist with the existing dashboard kiosk without disrupting backend services (OBD, Victron, Spotify, Starlink, Weather) or requiring a reboot to return to normal operation.

## Solution

Add a "Game Mode" tile to the Settings view. Tapping it loads a launcher page where the user picks game mode (Single Player / Co-op / Deathmatch) and skill, then taps Launch. The frontend repaints itself as a black "GAME RUNNING" screen, then issues the start request. The backend `SIGSTOP`s the Chromium kiosk process tree, launches `matchbox-window-manager` and one or two `chocolate-doom` instances side-by-side at 640×400 each (single player is centered with black bars), plus a small always-on-top touch overlay for in-game menu access. In 2P modes Player 1's audio is routed to the left channel only and Player 2's to the right via PulseAudio remap sinks.

On exit the launcher tears everything down and `SIGCONT`s Chromium back to life; the frontend's status poll catches the transition and navigates back to Settings.

Two networked `chocolate-doom` instances (deathmatch/co-op over loopback) are used rather than a single splitscreen-aware port because (a) `chocolate-doom` is in the Pi OS apt repo with no compilation needed, (b) it stays faithful to original Doom behaviour, (c) controller-to-instance mapping is trivial via SDL2 `-joystick N`, and (d) the technique is well-documented and robust.

Backend services keep running throughout. The WebSocket reconnects on Chromium resume.

**Explicit non-goals:**
- No speed gating. The sole driver/owner has decided manually-enforced safety is sufficient and a software speed check is a failure-prone surface area.
- No support for Doom engines other than `chocolate-doom` in v1.
- No WAD selection UI in v1 — single hardcoded IWAD (`wads/doom.wad`, registered Doom 1). v2 can reintroduce a selector without architectural cost.
- No mod/PWAD support in v1.
- No "Restart Map" button in the overlay in v1 — `chocolate-doom` has no clean native trigger for it.
- No support for >2 players (the display is too wide for 2, too narrow for 4).
- No save-slot management UI — relies on Doom's native save menus via simulated F2/F3 (single-player only).

---

## Files Changed

| Action | File | What changes |
|--------|------|--------------|
| Add | `backend/game_service.py` | Endpoint handlers, process management, Spotify pause/resume, Chromium tree freeze, last-error tracking |
| Modify | `backend/main.py` | Mount `game_service` routes, Chromium PID discovery with retry, orphan launcher cleanup, defensive PulseAudio cleanup |
| Modify | `backend/shared_state.py` | Add `chromium_pid: int \| None` |
| Modify | `backend/requirements.txt` | Add `psutil` (Chromium PID discovery + tree freeze) |
| Add | `frontend/game.html` | Pre-game launcher page (mode + skill picker), "GAME RUNNING" overlay state, error revert path |
| Modify | `frontend/index.html` | Add Game Mode tile to Settings view |
| Modify | `frontend/app.js` | Wire Game Mode tile click → navigate to `/game.html` |
| Add | `games/doom/launcher.sh` | Orchestrate matchbox + chocolate-doom + overlay process group, per-player PulseAudio remap sinks, structured exit codes |
| Add | `games/doom/overlay.py` | Always-on-top GTK touch overlay (Resume/Save/Load/Quit), mode-aware button set |
| Add | `wads/README.md` | Explains where to drop `doom.wad`; directory itself gitignored |
| Modify | `.gitignore` | Add `wads/*.wad` |
| Modify | `deploy/setup.sh` | apt install `chocolate-doom xdotool python3-gi gir1.2-gtk-3.0 matchbox-window-manager`; `usermod -aG input "${SERVICE_USER}"`; render systemd unit with `DISPLAY=:0` |
| Modify | `deploy/td5-dash.service` | Add `Environment="DISPLAY=:0"` |
| Add | `tests/test_game_service.py` | Unit tests for endpoint behaviour, process lifecycle, error mapping |
| Add | `documentation/doom-mode-test-plan.md` | Manual test checklist for Pi integration |

---

## Architecture

### Process model

Three independent pieces glued by a launcher script:

1. **Existing kiosk** — Chromium + FastAPI backend, unchanged structurally.
2. **One or two `chocolate-doom` instances** — each in a 640×400 SDL window. Single player: one window centered at `(320,0)`. Two player: at `(0,0)` and `(640,0)`, networked over loopback via Doom's built-in `-server` / `-connect`.
3. **`matchbox-window-manager`** — runs only during Doom mode. Required because GTK's `set_keep_above(True)` is a no-op without a window manager honouring `_NET_WM_STATE_ABOVE`, and the overlay's z-order against the `chocolate-doom` windows would be undefined.
4. **Overlay app** — Python + GTK3 always-on-top window. Translucent menu icon visible during play; tap opens a Resume/(Save/Load if single-player)/Quit panel.

### Mode-switch flow

```
Settings → tap "Game Mode" tile
  → Chromium navigates to /game.html (still inside kiosk)
    → user picks mode + skill → tap Launch
      → JS replaces page body with centred "GAME RUNNING" on solid black
      → JS issues POST /system/game-mode/start {mode, skill}
        → backend validates; on 4xx/5xx, JS restores launcher with inline error banner
        → backend pauses Spotify if playing (records state)
        → backend SIGSTOPs Chromium parent + all children (psutil tree walk)
        → backend Popen launcher.sh with env (MODE, SKILL, DISPLAY)
          → launcher: setsid → matchbox-window-manager → load PulseAudio remap sinks (2P only)
                   → chocolate-doom-server (if 2P) → N×chocolate-doom → overlay.py
                   → wait for chocolate-doom clients to all exit
        → backend returns {ok: true}
  ... gameplay ...
Quit (overlay or Doom's own menu)
  → launcher's wait loop ends; trap fires
    → unload PulseAudio remap sinks
    → SIGTERM remaining children (matchbox, overlay, server)
  → backend watcher task observes launcher exit
    → captures exit code → stores last_error if non-zero
    → SIGCONTs Chromium tree
    → resumes Spotify if it was playing
  → Chromium resumes the GAME RUNNING page → poll catches running=false
    → if last_error set, show toast → navigate to /#settings
```

### Audio

**Single player.** Doom routes through PulseAudio's existing `td5_sink` (the same path Raspotify uses) → loopback → real default output → BT/head unit. No new audio configuration. Doom's internal positional audio is preserved.

**Two player (coop or deathmatch).** Each `chocolate-doom` instance is routed to its own remap sink:

| Sink | `master_channel_map` | Effect |
|---|---|---|
| `doom_p1` | `front-left,front-left` | P1 audio (both L+R) routes only to head unit's left channel |
| `doom_p2` | `front-right,front-right` | P2 audio (both L+R) routes only to head unit's right channel |

Within-player positional audio collapses to mono per player; channel volume is dynamic with each player's in-game activity. Accepted tradeoff.

Both instances are launched with `PULSE_SINK=doom_pN` env var. SDL2 honours this natively — no `chocolate-doom` modification needed.

**Failure handling.** If either remap sink fails to create, both instances fall back to the default sink. The game still launches; doubled audio in that path is acceptable. Audio routing must never block game launch.

**Cleanup.** Launcher's exit trap calls `pactl unload-module` on any successfully-loaded sink modules. Backend additionally performs defensive cleanup on startup:

```sh
pactl list short modules | awk -F'\t' '$3 ~ /sink_name=doom_p[12]/ { print $1 }' | xargs -r -n1 pactl unload-module
```

This belt-and-braces approach covers both clean shutdown and post-crash startup.

**Spotify.** Auto-paused on launch if playing, resumed on exit. State recorded in `game_service` module memory; not persisted across backend restart.

### Window manager (Doom-mode only)

`matchbox-window-manager -use_titlebar no -use_cursor no` is launched by `launcher.sh` before `chocolate-doom`. It dies with the rest of the process group on Doom exit. The kiosk continues to run without any window manager under normal operation; matchbox only exists during gameplay.

### Display environment

The systemd unit currently has no `DISPLAY` set, only `EnvironmentFile=.env` (which contains rotation but no display number). With no `DISPLAY` exported, every X-aware child spawned by the backend (chocolate-doom, xdotool, matchbox, the GTK overlay) would fail to open the display.

`deploy/td5-dash.service` adds:

```ini
Environment="DISPLAY=:0"
```

`setup.sh` templates this into the rendered unit file alongside the existing user substitution.

### Resource use

| Resource | Cost |
|---|---|
| CPU | ~10% of one core per `chocolate-doom` instance on Pi 5 |
| RAM | ~50 MB for 2× Doom + overlay; ~1 MB matchbox |
| GPU | Software rendering; Chromium freeze releases the compositor |
| Audio | PulseAudio mixer + 2 remap sinks; negligible CPU |
| Network | Loopback only |

---

## `backend/game_service.py`

### Module state

```python
import os
import signal
import subprocess
import asyncio
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import psutil

from . import shared_state

router = APIRouter()

_REPO_DIR     = Path(__file__).resolve().parent.parent
_WAD_PATH     = _REPO_DIR / "wads" / "doom.wad"
_LAUNCHER     = _REPO_DIR / "games" / "doom" / "launcher.sh"
_VALID_MODES  = {"single", "coop", "deathmatch"}
_VALID_SKILLS = {1, 2, 3, 4, 5}

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
```

### Endpoints

```python
class StartRequest(BaseModel):
    mode:  str    # "single" | "coop" | "deathmatch"
    skill: int    # 1..5

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
    global _launcher_proc, _current_mode, _spotify_was_playing, _watcher_task, _last_error

    # Environment guards — distinguish dev from production misconfiguration
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

    # Pre-launch: pause Spotify if playing
    _spotify_was_playing = await _pause_spotify_if_playing()

    # Pre-launch: freeze Chromium (parent + all children)
    _freeze_chromium_tree()

    # Launch
    # NOTE: start_new_session=True makes the launcher a session leader, so
    # PID == PGID. This is what makes os.killpg(proc.pid, ...) work in stop().
    # Do not remove this without rewriting the cleanup path.
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

    # Watcher: when launcher exits, capture exit code and run cleanup
    _watcher_task = asyncio.create_task(_watch_for_exit())

    return {"ok": True}

@router.post("/system/game-mode/stop")
async def stop() -> dict:
    await _stop_internal()
    return {"ok": True}
```

### Internal helpers

```python
async def _watch_for_exit() -> None:
    """Block until launcher exits, record exit code, then run teardown."""
    global _launcher_proc, _last_error
    if _launcher_proc is None:
        return
    rc = await asyncio.to_thread(_launcher_proc.wait)
    if rc != 0:
        _last_error = _EXIT_CODE_MESSAGES.get(rc, "Game exited unexpectedly")
    await _stop_internal()

async def _stop_internal() -> None:
    """Idempotent teardown — guarded against concurrent calls from /stop + watcher."""
    global _launcher_proc, _current_mode, _spotify_was_playing, _watcher_task

    async with _stop_lock:
        proc = _launcher_proc
        _launcher_proc = None
        _current_mode  = None

        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    await asyncio.to_thread(proc.wait, timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        _unfreeze_chromium_tree()

        if _spotify_was_playing:
            await _resume_spotify()
            _spotify_was_playing = False

        if _watcher_task and not _watcher_task.done():
            _watcher_task.cancel()
        _watcher_task = None

def _freeze_chromium_tree() -> None:
    """SIGSTOP the kiosk Chromium process and all its children.

    Whether parent-only SIGSTOP fully idles the renderer/GPU/utility children
    depends on Chromium's IPC behaviour (children typically block waiting for
    parent → idle). Tree-walking is unconditional here for safety and is
    verified during Phase 5a.
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
    from . import spotify_service
    state = spotify_service.current_state()
    if state and state.get("playing"):
        await spotify_service.command("pause")
        return True
    return False

async def _resume_spotify() -> None:
    from . import spotify_service
    await spotify_service.command("play")
```

### Chromium PID discovery (in `main.py` lifespan)

Discovery runs as a background task that retries for up to 30 seconds, since Chromium starts from `xinitrc` after the backend and may not be present at first lookup.

```python
async def _discover_chromium_pid() -> None:
    """Find the kiosk Chromium process. Retries for 30 s, gives up silently."""
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if proc.info["name"] != "chromium":
                    continue
                cmd = proc.info.get("cmdline") or []
                if "--kiosk" in cmd:
                    shared_state.chromium_pid = proc.info["pid"]
                    return
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        await asyncio.sleep(1.0)
    # No log spam — feature still works without freeze, just visually janky
```

The binary is `chromium` on this system (verified from `deploy/xinitrc:49`), not `chromium-browser`. No `chromium-browser` matching is needed.

### Crash recovery on backend restart

In `main.py` startup, before discovery:

```python
# Defensive SIGCONT — if backend crashed mid-game, Chromium tree may be SIGSTOPped.
# Match by binary name (-x chromium) so the parent AND all children are caught;
# matching only on '--kiosk' cmdline would miss the renderer/GPU/utility children
# (they have comm=chromium but no --kiosk in their cmdline) and leave the tree
# half-frozen. SIGCONT on an already-running process is a harmless no-op.
subprocess.run(["pkill", "-CONT", "-x", "chromium"], check=False)

# Kill any orphan launcher.sh from a previous backend run.
subprocess.run(["pkill", "-f", "games/doom/launcher.sh"], check=False)

# Unload orphan PulseAudio remap sinks.
subprocess.run(
    "pactl list short modules | awk -F'\\t' '$3 ~ /sink_name=doom_p[12]/ { print $1 }' "
    "| xargs -r -n1 pactl unload-module",
    shell=True, check=False,
)
```

---

## `games/doom/launcher.sh`

```sh
#!/bin/sh
# Doom Mode launcher — orchestrates matchbox + chocolate-doom + overlay.
# Env in: MODE (single|coop|deathmatch), WAD (full path), SKILL (1-5)
# Lives in its own process group (caller uses start_new_session=True).
# Exit codes:
#   0  clean exit (game quit normally)
#   2  controllers missing for 2P mode
#   3  matchbox-window-manager failed to start
#   4  chocolate-doom failed to launch

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

SINK_P1_MOD=""
SINK_P2_MOD=""

cleanup() {
    # Unload PulseAudio remap sinks first (before killing apps that may still hold them)
    [ -n "$SINK_P1_MOD" ] && pactl unload-module "$SINK_P1_MOD" 2>/dev/null || true
    [ -n "$SINK_P2_MOD" ] && pactl unload-module "$SINK_P2_MOD" 2>/dev/null || true
    # Kill everything in this process group except the shell itself
    pkill -P $$ 2>/dev/null || true
    wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# ── Controller validation ─────────────────────────────────────────────
if [ "$MODE" != "single" ]; then
    JS_COUNT=$(ls /dev/input/js* 2>/dev/null | wc -l)
    if [ "$JS_COUNT" -lt 2 ]; then
        echo "ERROR: 2P mode needs 2 controllers, found $JS_COUNT" >&2
        exit 2
    fi
fi

# ── Window manager ────────────────────────────────────────────────────
matchbox-window-manager -use_titlebar no -use_cursor no &
MATCHBOX_PID=$!
sleep 0.2
if ! kill -0 "$MATCHBOX_PID" 2>/dev/null; then
    echo "ERROR: matchbox-window-manager failed to start" >&2
    exit 3
fi

# Paint background black either side of single-player window
xsetroot -solid black 2>/dev/null || true

# ── Audio: per-player L/R remap sinks for 2P modes ────────────────────
P1_PULSE_PREFIX=""
P2_PULSE_PREFIX=""
if [ "$MODE" != "single" ]; then
    DEFAULT_SINK=$(pactl get-default-sink 2>/dev/null || echo "")
    if [ -n "$DEFAULT_SINK" ]; then
        SINK_P1_MOD=$(pactl load-module module-remap-sink \
            sink_name=doom_p1 \
            master="$DEFAULT_SINK" \
            channels=2 \
            master_channel_map=front-left,front-left \
            channel_map=front-left,front-right \
            remix=no 2>/dev/null) || SINK_P1_MOD=""

        SINK_P2_MOD=$(pactl load-module module-remap-sink \
            sink_name=doom_p2 \
            master="$DEFAULT_SINK" \
            channels=2 \
            master_channel_map=front-right,front-right \
            channel_map=front-left,front-right \
            remix=no 2>/dev/null) || SINK_P2_MOD=""

        if [ -n "$SINK_P1_MOD" ] && [ -n "$SINK_P2_MOD" ]; then
            P1_PULSE_PREFIX="env PULSE_SINK=doom_p1"
            P2_PULSE_PREFIX="env PULSE_SINK=doom_p2"
        else
            echo "WARN: per-player remap sinks failed; falling back to default sink" >&2
        fi
    fi
fi

# ── Launch chocolate-doom ─────────────────────────────────────────────
COMMON_OPTS="-iwad $WAD -nograbmouse -skill $SKILL"

case "$MODE" in
    single)
        $P1_PULSE_PREFIX chocolate-doom $COMMON_OPTS \
            -window -geometry 640x400+320+0 \
            -joystick 0 &
        ;;
    coop)
        chocolate-doom-server -deathmatch 0 -nodes 2 -port 5029 &
        sleep 0.3
        $P1_PULSE_PREFIX chocolate-doom $COMMON_OPTS -connect 127.0.0.1:5029 \
            -window -geometry 640x400+0+0 -joystick 0 &
        $P2_PULSE_PREFIX chocolate-doom $COMMON_OPTS -connect 127.0.0.1:5029 \
            -window -geometry 640x400+640+0 -joystick 1 &
        ;;
    deathmatch)
        chocolate-doom-server -deathmatch 1 -nodes 2 -port 5029 &
        sleep 0.3
        $P1_PULSE_PREFIX chocolate-doom $COMMON_OPTS -connect 127.0.0.1:5029 \
            -window -geometry 640x400+0+0 -joystick 0 &
        $P2_PULSE_PREFIX chocolate-doom $COMMON_OPTS -connect 127.0.0.1:5029 \
            -window -geometry 640x400+640+0 -joystick 1 &
        ;;
esac

# Detect immediate chocolate-doom failure (within ~0.5s of launch)
sleep 0.5
if ! pgrep -x chocolate-doom >/dev/null 2>&1; then
    echo "ERROR: chocolate-doom failed to launch" >&2
    exit 4
fi

# ── Overlay ────────────────────────────────────────────────────────────
MODE="$MODE" python3 "$SCRIPT_DIR/overlay.py" &

# ── Wait for all chocolate-doom clients to exit ───────────────────────
while pgrep -x chocolate-doom >/dev/null 2>&1; do
    sleep 0.5
done

exit 0
```

---

## `games/doom/overlay.py`

```python
#!/usr/bin/env python3
"""TD5 Dash Doom mode — touch overlay for in-game menu access."""
import os
import subprocess
import urllib.request

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

MODE = os.environ.get("MODE", "single")
IS_SINGLE = (MODE == "single")


class Overlay(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.set_app_paintable(True)
        self.set_decorated(False)
        self.set_keep_above(True)        # Honoured by matchbox via _NET_WM_STATE_ABOVE
        self.set_accept_focus(False)
        self.set_skip_taskbar_hint(True)
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)

        self.menu_open = False
        self._build_compact()
        self.connect("button-press-event", self._on_click)

    def _build_compact(self):
        # 60×60 icon at top-right (1210, 10) on 1280×400 display
        self.move(1210, 10)
        self.resize(60, 60)
        label = Gtk.Label()
        label.set_markup('<span size="36000" color="#e8e8e8">≡</span>')
        if self.get_child():
            self.remove(self.get_child())
        self.add(label)
        self.show_all()

    def _build_expanded(self):
        # Panel at top-right; height depends on button count
        buttons = [("Resume", self._resume)]
        if IS_SINGLE:
            buttons.append(("Save", self._save))
            buttons.append(("Load", self._load))
        buttons.append(("Quit", self._quit))

        panel_h = 24 + len(buttons) * 72   # margin + per-button height
        self.move(1030, 10)
        self.resize(240, panel_h)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(8); box.set_margin_bottom(8)
        box.set_margin_start(8); box.set_margin_end(8)
        for label, action in buttons:
            btn = Gtk.Button(label=label)
            btn.set_size_request(-1, 64)
            btn.connect("clicked", lambda _w, fn=action: fn())
            box.pack_start(btn, True, True, 0)
        if self.get_child():
            self.remove(self.get_child())
        self.add(box)
        self.show_all()

    def _on_click(self, _w, _evt):
        self.menu_open = not self.menu_open
        if self.menu_open:
            self._build_expanded()
        else:
            self._build_compact()

    def _resume(self):
        self.menu_open = False
        self._build_compact()

    def _save(self):
        self._send_key("F2")
        self._resume()

    def _load(self):
        self._send_key("F3")
        self._resume()

    def _quit(self):
        try:
            urllib.request.urlopen(
                "http://localhost:8000/system/game-mode/stop",
                data=b"",
                timeout=2,
            )
        except Exception:
            pass
        Gtk.main_quit()

    def _send_key(self, key: str):
        subprocess.run(
            ["xdotool", "search", "--name", "Chocolate Doom",
             "windowactivate", "--sync", "key", key],
            check=False,
        )


def main():
    win = Overlay()
    win.connect("destroy", Gtk.main_quit)
    Gtk.main()


if __name__ == "__main__":
    main()
```

Save/Load buttons are hidden in 2P modes because `chocolate-doom` does not support save/load in netplay sessions.

---

## `frontend/game.html`

Served automatically by the existing `StaticFiles` mount in `backend/main.py:426` (`app.mount("/", StaticFiles(directory=FRONTEND, html=True))`). Dropping `game.html` into `frontend/` is sufficient; no route registration required.

A standalone 1280×400 page styled with the existing design system. Two columns and a launch tile:

| Column | Content |
|---|---|
| Left (480px) | Mode picker — three `stat-tile` elements: SINGLE PLAYER / CO-OP / DEATHMATCH. Selected tile has `--c-green` border. |
| Middle (480px) | Skill picker — five tiles: TOO YOUNG TO DIE / HEY NOT TOO ROUGH / HURT ME PLENTY / ULTRA-VIOLENCE / NIGHTMARE. |
| Right (320px) | Big LAUNCH tile (`--c-green` background) and Back tile (returns to `/`). |

(No WAD picker. The WAD is hardcoded to `wads/doom.wad`. If it's missing, `/start` returns 500 `wad_missing` and the launcher shows that error inline.)

### Flow

1. On load: defaults — mode = `single`, skill = 3 (Hurt Me Plenty).
2. User adjusts mode and skill.
3. Tap LAUNCH:
   - JS immediately replaces the page body with a centred "GAME RUNNING" message on solid black.
   - JS then issues `POST /system/game-mode/start {mode, skill}`.
   - **On 200:** start polling `GET /system/game-mode/status` every 1 s. When `running: false`, read `last_error`; if set, show a transient toast on `/#settings` before navigating; navigate to `/#settings`.
   - **On 4xx/5xx:** restore the launcher state (mode + skill selectors with prior values) and show a red error banner above LAUNCH containing the error message from the response body. User can correct and re-launch without a page reload.

This sequencing means the "GAME RUNNING" screen is what's painted on the framebuffer when Chromium gets `SIGSTOP`ped — no race between the POST returning and the freeze.

---

## `frontend/index.html` + `app.js`

One new tile in the Settings view's Controls column:

```html
<div class="stat-tile" id="tile-gamemode">
  <div class="stat-label">Game Mode</div>
  <div class="engine-stat-bottom">
    <div class="stat-value">DOOM</div>
    <div class="status-dot off" id="dot-gamemode"></div>
  </div>
</div>
```

In `app.js`:

```js
document.getElementById("tile-gamemode").addEventListener("click", () => {
    window.location.href = "/game.html";
});
```

The tile is a launcher, not a state indicator. The `off` class is purely cosmetic.

---

## `deploy/setup.sh` additions

In the system packages block:

```sh
apt-get install -y --no-install-recommends \
    ... existing list ... \
    chocolate-doom \
    xdotool \
    python3-gi \
    gir1.2-gtk-3.0 \
    matchbox-window-manager
```

Add `psutil` to `backend/requirements.txt`.

Add the service-user `input` group membership (idempotent):

```sh
usermod -aG input "${SERVICE_USER}"
```

Render the systemd unit with `DISPLAY=:0`:

```sh
sed \
    -e "s|__USER__|$SERVICE_USER|g" \
    -e "s|__REPO_DIR__|$REPO_DIR|g" \
    "$SCRIPT_DIR/td5-dash.service" > /etc/systemd/system/td5-dash.service
```

The template `deploy/td5-dash.service` gains:

```ini
Environment="DISPLAY=:0"
```

---

## Tests

### `tests/test_game_service.py`

All tests mock `subprocess.Popen`, `psutil.Process`, `shared_state.chromium_pid`, and the Spotify service. No real Doom launched.

| Test | What it covers |
|---|---|
| `test_start_invalid_mode` | Returns 400 `{error: "invalid_mode"}` |
| `test_start_invalid_skill` | Skill 0 and 6 both rejected |
| `test_start_wad_missing` | When `wads/doom.wad` doesn't exist, returns 500 `{error: "wad_missing"}` |
| `test_start_dev_mode_blocked` | With `DEV_MODE=1`, returns 501 `game_mode_not_available_in_dev` |
| `test_start_display_unset_in_prod` | With `DEV_MODE` unset and `DISPLAY` unset, returns 500 `display_not_available` (distinct from dev) |
| `test_start_idempotent` | Second start while running returns 409 |
| `test_start_freezes_chromium_tree` | Verifies parent.suspend() AND each child.suspend() called |
| `test_chromium_pid_none_skips_sigstop` | When PID discovery hasn't succeeded, the freeze path is a no-op |
| `test_start_pauses_spotify_if_playing` | Spotify pause called only when state.playing is True |
| `test_start_no_spotify_pause_when_paused` | Spotify pause not called when already paused |
| `test_stop_unfreezes_chromium_tree` | After stop, parent.resume() AND children.resume() called |
| `test_stop_resumes_spotify_only_if_was_playing` | Resume called iff start had paused |
| `test_stop_when_not_running` | Safe no-op, returns 200 |
| `test_stop_lock_serialises_concurrent_calls` | Two concurrent _stop_internal calls don't double-clean |
| `test_status_running` | `/status` reflects start → running, stop → not running |
| `test_status_last_error_present` | After non-zero exit, `last_error` is set to mapped message |
| `test_status_last_error_cleared_on_next_start` | Successful next start clears prior `last_error` |
| `test_watcher_maps_exit_codes` | Exit codes 2, 3, 4 → respective messages; others → "Game exited unexpectedly" |
| `test_watcher_runs_cleanup_on_natural_exit` | When mock Popen exits, cleanup runs without explicit /stop |
| `test_orphan_launcher_killed_on_startup` | On backend restart, prior launcher PIDs cleaned up |

### `documentation/doom-mode-test-plan.md`

Manual checklist for Pi integration testing:

1. Fresh boot → kiosk loads → tap Settings → Game Mode tile present and tappable
2. Launcher page shows mode + skill selectors with defaults (single, Hurt Me Plenty); no WAD picker
3. With `wads/doom.wad` missing → tap LAUNCH → page reverts to launcher with red banner "wad_missing"
4. With `wads/doom.wad` present, no controllers + 2P selected → "Controllers required for this mode" toast on returning to Settings
5. 1 controller + single player → Doom launches centered, black bars left and right, save/load buttons visible in overlay
6. 2 controllers + co-op → both halves render, both controllers respond, Player 1 audio left-only, Player 2 audio right-only on head unit
7. 2 controllers + deathmatch → confirm frag count works between halves; stereo separation as above
8. Overlay icon visible top-right during play; tap → menu opens
9. In 2P, Save and Load buttons are absent from the overlay menu
10. In 1P, Save via overlay → Doom save menu appears; Load → load menu appears
11. Quit via overlay → all processes (matchbox + chocolate-doom + overlay) terminate, Chromium snaps back, WebSocket reconnects, engine data fresh
12. Quit via Doom's own in-game menu → same outcome (watcher catches exit)
13. Spotify playing → launch Doom → paused → quit Doom → resumed
14. Spotify paused → launch Doom → still paused → quit Doom → still paused
15. `sudo systemctl restart td5-dash` mid-game → Doom keeps running → backend startup kills launcher → Chromium resumed (no orphan SIGSTOP), orphan remap sinks unloaded
16. Power cut + reboot → fresh kiosk boot, no stale state
17. `pactl list short modules` after a clean quit shows no `doom_p1` or `doom_p2` sinks remaining
18. Brief X Expose handling artifacts on Doom exit before Chromium repaints are cosmetic; do not treat as a bug

---

## Risks & open questions

| Risk | Mitigation |
|---|---|
| Chromium subprocess tree may not fully idle on parent-only `SIGSTOP` | Implementation unconditionally walks the tree with psutil; verified in Phase 5a |
| Chromium PID discovery races backend startup (Chromium not running yet) | Discovery task retries every 1 s for up to 30 s; if still not found, feature degrades silently to "Doom on top of Chromium" — visually janky but functional |
| Overlay GTK app captures touch events meant for Doom | Overlay window is sized exactly to the icon (60×60) when compact; touches outside that area pass through. Matchbox provides proper z-order semantics so the icon stays on top reliably. |
| `xdotool key F2` targets the wrong Doom window | Only relevant in single-player (save/load hidden in 2P). Single-player has only one Doom window so the issue does not occur. |
| Pioneer DEH-1320MP head unit applies stereo widening / mono-downmix DSP that defeats L/R separation | Graceful fallback to default sink already in launcher; document head-unit setting if applicable; verify during Phase 3.5 |
| PulseAudio remap sinks orphaned on crash → accumulate across runs | `pactl unload-module` in launcher exit trap; defensive awk-based cleanup on backend startup |
| L/R-to-player audio mapping inverted relative to seating arrangement | One-line swap in `launcher.sh` master_channel_map values; verify during Phase 3.5 |
| chocolate-doom does not support save/load in netplay | Save/Load buttons hidden when MODE != single |
| `/dev/input/js*` permissions on non-default service user | `usermod -aG input "${SERVICE_USER}"` in `setup.sh` (idempotent) |
| Brief X Expose handling artifacts on Doom exit before Chromium repaints | Cosmetic; documented in test plan so it isn't mistaken for a bug |
| Two physically-identical controllers may enumerate in non-deterministic order | Deferred — handle at first manifestation by adding a udev rule keyed on USB port |
| `_stop_internal` called concurrently by `/stop` endpoint and watcher task | `asyncio.Lock` serialises both paths |
| Launcher fails silently after backend returns 200 from `/start` | Exit code captured by watcher; mapped to `last_error`; surfaced via `/status` and toasted in frontend on return to Settings |
| Backend service has no graceful shutdown for in-progress Doom | Acceptable — SIGTERM the backend, watcher task cancellation triggers `_stop_internal`. If backend SIGKILLed, orphan launcher gets `pkill`ed on next backend startup. |
| Backend running without `DISPLAY` set fails silently | `DISPLAY=:0` mandated in systemd unit; explicit check in `/start` returns 500 `display_not_available` for diagnosability |

---

## Pi-side verification during build

These items require empirical observation on the Pi during implementation. None block the spec being finalised; all are folded into the phased build plan.

1. **Chromium freeze sufficiency** (Phase 5a) — verify with `top` that all Chromium PIDs are at ~0% CPU during Doom mode. Tree-walking is unconditional in the spec; this verifies it works in practice.
2. **Audio stack identity** (Phase 3.5) — `pactl info | grep "Server Name"` to record PulseAudio vs PipeWire-pulse. Remap sink approach works on both; just for documentation.
3. **`pi` user group membership** (Phase 1 deploy) — `id pi` to confirm `input` group is present after `setup.sh` runs.
4. **Head-unit DSP behaviour** (Phase 3.5) — verify Pioneer DEH-1320MP passes stereo through without widening or downmix.

---

## Build phases

| Phase | Scope | Verification |
|---|---|---|
| 1 | Backend skeleton — endpoints, validation, mocked `Popen`, distinct dev/display guards, `last_error` plumbing | `pytest tests/test_game_service.py` passes |
| 2 | Frontend — Game Mode tile, `game.html` launcher (mode + skill only), GAME RUNNING black screen, error revert path | Visual check in Docker — page loads, selectors work, mock 501 in dev shows the inline error banner |
| 3 | Pi integration: install `chocolate-doom`, `matchbox-window-manager`, `xdotool`; manually run `launcher.sh` to confirm 2 instances + loopback netplay; verify `usermod -aG input` took effect | Manual on Pi with controllers + `wads/doom.wad` |
| 3.5 | **Controller mapping + stereo audio routing** — verify joystick 0/1 → Player A/B, verify L/R remap sinks produce per-player channel separation on the head unit, verify L/R-to-seat mapping matches intent | Manual on Pi with 2 controllers + audio on head unit |
| 4 | Overlay GTK app — verify icon visible, menu opens, save/load work in 1P, save/load hidden in 2P, quit works, matchbox z-order honours `_NET_WM_STATE_ABOVE` | Manual on Pi |
| 5a | **Chromium freeze/unfreeze only** — PID discovery (30 s retry), tree-walking suspend/resume | `top` shows all Chromium PIDs at ~0% CPU during Doom mode; resume produces no artifacts beyond brief X Expose |
| 5b | **Watcher task + launcher lifecycle orchestration (no Spotify)** — clean exit, overlay-quit, in-game-quit, controller-missing exit (code 2), matchbox-failed exit (code 3), chocolate-doom-failed exit (code 4); `last_error` populated correctly | Manual; each exit path observed |
| 5c | **Spotify pause/resume integration** — paused on launch if playing, resumed on exit; not touched if already paused | Manual |
| 6 | Polish — error toasts on launcher, GTK styling if needed, full test plan run-through | Sign-off |

---

## Discipline reminder

The non-goals listed in the Solution section are sound. Resist scope creep during implementation: no PWAD support, no four-player, no mod menus, no gamepad rebinding UI, no level select beyond `chocolate-doom`'s native command-line options, no Restart Map button. Anything that tries to grow during the build goes into a v2 design document rather than swelling v1.
