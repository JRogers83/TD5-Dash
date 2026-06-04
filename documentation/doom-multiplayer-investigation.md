# Doom Mode — Multiplayer Investigation

## Context

TD5 Dash is a custom in-vehicle display for a Land Rover Defender TD5, running on a Raspberry Pi 5 with a Waveshare 7.9" bar-format DSI touchscreen at **1280×400 landscape resolution**. The display is physically 191mm × 60mm.

As a "for the lols" feature, we implemented a Doom game mode accessible from the Settings screen. The feature is built on top of a FastAPI backend with a full kiosk Chromium frontend. Single-player Doom works. Two-player split-screen does not yet work.

---

## What Has Been Built (Working)

### Infrastructure
- **`backend/game_service.py`** — FastAPI router exposing `/system/game-mode/start`, `/system/game-mode/stop`, `/system/game-mode/status`. Handles Chromium SIGSTOP/SIGCONT (to freeze the kiosk while Doom runs), Spotify auto-pause/resume, WAD path resolution, and process lifecycle management.
- **`games/doom/launcher.sh`** — Shell script that orchestrates the full session: controller validation, matchbox-window-manager, PulseAudio remap sinks (for stereo L/R split in 2P), chocolate-doom instances, and the GTK overlay.
- **`games/doom/overlay.py`** — Always-on-top GTK touch menu (≡ icon, top-right corner) for Resume/Quit. Uses xdotool to send F2/F3 for Save/Load in single-player. Mode-aware (save/load hidden in netplay modes).
- **`games/doom/joy2key.py`** — Python script using python3-evdev that reads `/dev/input/jsN` directly and injects keyboard events via uinput, bypassing SDL's joystick support entirely.
- **`games/doom/chocolate-doom.cfg`** — Bundled config file passed to chocolate-doom via `-config`. Originally intended to configure SDL joystick support; now largely irrelevant since joy2key bypasses SDL.
- **`frontend/game.html`** — Launcher UI: mode selector (Single/Co-op/Deathmatch), skill selector (1-5), LAUNCH button, GAME RUNNING screen, error toast on abnormal exit.
- **`wads/`** — WAD resolution: checks `wads/doom.wad` (user-supplied, gitignored) first, falls back to `/usr/share/games/doom/freedoom1.wad` (installed via apt).

### Single Player — Working
Single-player launches correctly:
- FreeDoom loads
- One 640×400 window centred on the 1280×400 display (320px offset) with black bars either side
- Controller works via joy2key (one instance per controller)
- Overlay works (quit button confirmed functional)
- Return to dash on exit

### Key Constraints Discovered
- **chocolate-doom installs to `/usr/games/`** — not in the systemd service PATH; absolute paths required
- **SDL joystick support is broken** for generic USB SNES controllers with chocolate-doom on Pi OS Bookworm — confirmed non-functional despite correct config, correct permissions, correct SDL flags
- **joy2key works** — reads `/dev/input/js0` via the legacy joystick interface, injects keyboard events via uinput → X11 routes to focused window. Confirmed working for single-player.
- **`/dev/uinput` permissions** — udev rule (`KERNEL=="uinput", GROUP="input", MODE="0660"`) does not reliably apply on the running system; setup.sh now also does explicit `chown root:input` + `chmod 660` as a fallback
- **WAD**: FreeDoom Phase 1 (`freedoom1.wad`) installed via apt; user can override with `wads/doom.wad` (gitignored, survives OTA)

---

## Two-Player Multiplayer — What We Tried

### Attempt 1: Dedicated Server (`chocolate-doom -dedicated`)

**Approach:**
```sh
/usr/games/chocolate-doom -dedicated -deathmatch 0 -nodes 2 -port 5029 &
sleep 0.3
/usr/games/chocolate-doom [opts] -connect 127.0.0.1:5029 -window -geometry 640x400+0+0 &
/usr/games/chocolate-doom [opts] -connect 127.0.0.1:5029 -window -geometry 640x400+640+0 &
```

**Result:** Failed. Journal showed:
```
The command line parameter '-deathmatch' was specified to a dedicated server.
Dedicated server mode.
```
The `-deathmatch 0` flag caused a warning and the server likely exited immediately. Both clients launched, loaded the WAD, got ALSA audio errors, then exited with no connection established.

**Note:** There is no `chocolate-doom-server` binary in the Debian Bookworm package. Only `/usr/games/chocolate-doom` and `/usr/games/chocolate-doom-setup`.

---

### Attempt 2: P1 Hosts (`-nodes 2`), P2 Connects

**Approach:**
```sh
/usr/games/chocolate-doom [opts] -nodes 2 -port 5029 -window -geometry 640x400+0+0 &
sleep 1.0  # later increased to 3.0
/usr/games/chocolate-doom [opts] -connect 127.0.0.1:5029 -window -geometry 640x400+640+0 &
```

**Result:** P1 launched and showed a window. P2 failed to connect.

Journal from P2:
```
D_InitNetGame: Failed to connect to 127.0.0.1:5029: No response from server
```

Increasing the sleep from 1s to 3s made no difference. Confirmed with `ss -ulnp | grep 5029` while P1 was running — **P1 was not listening on any port**. The `-nodes 2` flag did not cause P1 to bind port 5029.

**Conclusion:** In chocolate-doom 3.1.0 (the version in Debian Bookworm), `-nodes` does not work as documented for same-machine local multiplayer. The network setup may require a different initialisation path, or may only bind the port after a game setup phase that never completes when the display is frozen waiting for P2.

---

## Input Architecture Problem (Independent of Networking)

Even if networking were solved, there is a **fundamental input routing problem** with the current joy2key approach for 2P:

- joy2key creates a uinput virtual keyboard device
- X11 routes keyboard events from this device to the **focused window**
- In 2P mode, only one window can have focus at a time
- Therefore both controllers send input to the same window

**Confirmed working:** `DISPLAY=:0 xdotool keydown Up` causes the player in the focused Doom window to walk forward. So synthetic keyboard injection works correctly — the problem is that both joy2key instances target the same focused window.

**For 2P to work**, each controller's input needs to reach its own Doom instance independently of window focus. Possible solutions include:
- Using SDL joystick directly (each instance uses `-joystick 0` or `-joystick 1`) — bypasses X11 focus entirely, reads from device directly. This is what chocolate-doom was designed to support but SDL joystick proved broken for single-player with these controllers.
- Using xdotool to target a specific window ID (`xdotool key --window <id> keydown Up`) rather than the focused window
- Using two separate keyboard key sets (P1: arrow keys; P2: WASD or similar) — uinput sends both sets globally, but each Doom instance only binds to its own keys

---

## Current State

| Feature | Status |
|---------|--------|
| Single player launch | ✅ Working |
| Single player controller input | ✅ Working (joy2key) |
| Overlay (quit button) | ✅ Working |
| FreeDoom default WAD | ✅ Working |
| OTA updates | ✅ Working |
| Co-op networking | ❌ P1 doesn't bind port |
| Deathmatch networking | ❌ Not tested (same issue expected) |
| 2P controller input routing | ❌ Not solved (both go to focused window) |

---

## Known Constraints / Environment

- **Hardware:** Raspberry Pi 5, Pi OS Bookworm (64-bit)
- **chocolate-doom version:** 3.1.0 (Debian Bookworm package)
- **Display:** 1280×400, X11, matchbox-window-manager (single-window WM, needed for GTK overlay's `_NET_WM_STATE_ABOVE`)
- **Controllers:** Two generic USB SNES-style gamepads at `/dev/input/js0` and `/dev/input/js1`. SDL joystick non-functional; joy2key (evdev → uinput) works for single-player.
- **Audio:** ALSA errors on Doom startup (`Unknown error 524`) — non-fatal, audio simply absent. PulseAudio is present (Raspotify uses it).
- **WAD:** FreeDoom Phase 1 by default. User can drop `wads/doom.wad` for registered Doom 1.
- **No `chocolate-doom-server` binary** in the Bookworm package.

---

## Approaches to Evaluate

### Option A: Fix `-nodes` networking
Investigate why chocolate-doom 3.1.0's `-nodes` flag doesn't bind a port. Possible angles:
- Does P1 need to complete audio/video init before it starts listening?
- Is there a different flag or invocation for same-host netplay in 3.x?
- Does the Bookworm package have networking compiled out or configured differently?
- Check chocolate-doom 3.x changelogs/source for how `-nodes` is supposed to work

### Option B: Use a different Doom source port
Some ports may handle same-machine 2P differently:
- **crispy-doom** — extended chocolate-doom, same network model probably
- **prboom-plus / dsda-doom** — different network stack
- **gzdoom** — supports splitscreen natively without networking, but much heavier

### Option C: Two independent instances (no networking)
Two single-player Doom instances running simultaneously, each in a 640×400 window. No interaction between players — they play independent games side by side.
- Solves the networking problem entirely
- Still requires solving the input routing problem (each controller must target its own window)
- Input solution: map controllers to different keyboard sets; each Doom instance configured with different keybindings

### Option D: Fix SDL joystick and go back to native input
If SDL joystick could be made to work, each Doom instance reads its own joystick device directly (`-joystick 0` and `-joystick 1`), completely bypassing the X11 focus problem. This is the architecturally correct solution.
- Requires diagnosing why SDL joystick doesn't work with these controllers on this platform
- Possible angles: SDL version, evdev vs js interface, SDL_JOYSTICK_DEVICE env var, running `chocolate-doom-setup` to configure joystick interactively

### Option E: Window-targeted xdotool injection
Modify joy2key.py to find each Doom window by ID and send keys directly to it using `xdotool key --window <id>`. This bypasses the focus problem while keeping the evdev approach.
- Each joy2key instance targets its specific window
- Requires finding window IDs after launch (xdotool search)
- Subprocess-per-keypress latency (but likely acceptable for Doom)

---

## Files of Interest

```
games/doom/
├── launcher.sh          # Main orchestration script
├── joy2key.py           # Joystick → keyboard mapper (evdev → uinput)
├── overlay.py           # GTK always-on-top touch menu
└── chocolate-doom.cfg   # Bundled config (joystick settings, largely bypassed)

backend/game_service.py  # FastAPI endpoints + process lifecycle
deploy/setup.sh          # Pi setup — installs all dependencies
```

---

## Questions Worth Answering Before Choosing an Approach

1. Is same-machine networked Doom achievable with chocolate-doom 3.1.0 at all, and if so how?
2. For Option C (two independent instances), how should input routing be solved — window-targeted xdotool, or two distinct key sets?
3. Is the "no interaction between players" tradeoff acceptable for this use case, or is actual co-op/deathmatch important?
4. Would a different source port (gzdoom, prboom) be worth the additional package weight and setup complexity?
