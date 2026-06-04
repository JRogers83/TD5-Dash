# LZDoom Splitscreen — Research Notes for TD5 Dash Implementation

## Purpose

This document captures community knowledge about running LZDoom in two-instance "fake splitscreen" mode on Raspberry Pi hardware. It is intended as context for a coding agent implementing the Doom multiplayer mode in the TD5 Dash project.

---

## What LZDoom Splitscreen Actually Is

LZDoom's "splitscreen" is not a single-binary native splitscreen. It is two separate instances of LZDoom running simultaneously on the same machine, each in its own window, connected via LZDoom's built-in network multiplayer (`-host`/`-client`). The windows are positioned side by side to simulate a split-screen experience.

This is confirmed by LZDoom's maintainer (drfrag):

> "It's running several instances of the engine from different folders (a network game on the same machine), the other ones run in the background and must use a game controller."

Source: [ZDoom Forums – Multiplayer Doom on Raspberry Pi](https://forum.zdoom.org/viewtopic.php?t=68130)

---

## The Key Feature: `joy_background`

The reason LZDoom is preferred over GZDoom for this approach is the `joy_background` cvar. In standard GZDoom, joystick/gamepad input is only read by the focused window. LZDoom added a background joystick input mode specifically to support fake splitscreen.

From drfrag (LZDoom changelog and forum posts):

> "Fake splitscreen support: you need to enable the `vid_activeinbackground`, `i_soundinbackground` and the new `joy_background` cvars."

> "In LZDoom now the `joy_background` cvar defaults to true so no need to change that. The key is to set axes to none for each controller in the other instances (and you need to configure the buttons of course)."

Source: [ZDoom Forums – How to splitscreen/couch coop? (page 3)](https://forum.zdoom.org/viewtopic.php?p=1144417)

**Critical detail on axis configuration:** Each LZDoom instance reads ALL connected DirectInput/SDL joysticks. To prevent both instances responding to both controllers, you must configure each instance's INI so that the joystick assigned to the OTHER player has all axes set to `None`. Button bindings still need to be set per-instance.

---

## Controller Input Architecture

LZDoom uses SDL for joystick input on Linux. The `joy_background` feature sets `SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS=1` internally (the SDL hint), allowing joystick events to be read even when the window is not focused.

From the source PR discussion:

> "what about adding `SDL_SetHint("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")` before `if(SDL_InitSubSystem(SDL_INIT_JOYSTICK) >= 0)`?"

Source: [ZDoom Forums – How to splitscreen/couch coop? (page 2)](https://forum.zdoom.org/viewtopic.php?f=4&t=63710&start=15)

This is fundamentally different from the existing TD5 Dash `joy2key.py` approach (evdev → uinput → X11 focus chain). With LZDoom's native SDL joystick reading, the X11 focus problem does not apply — each instance reads directly from the SDL joystick layer regardless of which window has focus.

**Implication for TD5 Dash:** The existing `joy2key.py` should NOT be used with LZDoom. LZDoom's native gamepad support replaces it entirely. The generic USB SNES controllers at `/dev/input/js0` and `/dev/input/js1` are accessed via SDL's joystick interface (the legacy `jsX` interface), which LZDoom should handle natively.

---

## Instance Configuration: Separate INI Files

Each LZDoom instance must run with its own config file to prevent settings conflicts. The `-config` flag specifies the INI path.

The pattern confirmed to work (from LZSplitDoom's implementation approach):

> "Player 1's INI is copied to P2-4 at every load to avoid conflicting settings, then P2-4's INIs are edited in place before executing LZDoom to assign a different color to them and a name of Player_2, Player_3 or Player_4, swapping Gamepad IDs so any connected controllers don't interfere with each other, muting music on all clients except Player 1 and setting save directory paths."

Source: [LZSplitDoom – GitHub](https://github.com/SliverXReal/LZSplitDoom)

Key per-instance INI settings to configure:
- `joy_guid` or joystick device index — assign js0 to P1, js1 to P2
- Axes for the OTHER player's joystick set to `None` (e.g. in P1's INI, all axes for joystick index 1 = None)
- `i_soundinbackground = true` — prevent audio from stopping in background instance
- `vid_activeinbackground = true` — prevent video/game from pausing in background instance
- `joy_background = true` — enable background joystick polling (defaults to true in recent LZDoom)
- Music muted on all clients except P1 (to avoid doubled audio)
- Separate save directories per instance

---

## Networking: `-host` / `-client`

LZDoom uses GZDoom's network stack, not chocolate-doom's. The same-machine two-instance networking uses:

```
Instance 1 (P1): lzdoom [opts] -host 2 ...
Instance 2 (P2): lzdoom [opts] -connect 127.0.0.1 ...
```

The `-host 2` flag means "host a game, wait for 2 players total". P1 binds a port (default 5029) and waits. P2 connects to it. Unlike chocolate-doom 3.1.0's broken `-nodes` flag, this approach is confirmed working for same-machine local netplay.

This has been confirmed working on Linux by multiple users, including on Raspberry Pi hardware.

From user testing on Pi:

> "I just compiled LZDoom 3.85 and set up the 'splitscreen' multiplayer. That is some duct tape and bailing wire stuff right there, man! I love it. It works great! Still trying to figure out how to configure my xbox controllers for it."

Source: [ZDoom Forums – Multiplayer Doom on Raspberry Pi](https://forum.zdoom.org/viewtopic.php?t=68130)

---

## Raspberry Pi Build: Confirmed Viable

LZDoom compiles on ARM/aarch64. Confirmed by:

- Multiple community members compiling on Pi 2/3/4 without architecture-specific changes
- AUR package notes: "It works on aarch64 so please add it to architecture list"
- `christianhaitian/lzdoom` fork exists specifically for easier ARM builds

Source: [AUR – lzdoom](https://aur.archlinux.org/packages/lzdoom)

Pi 5 specifics: The Pi 5 has a VideoCore VII GPU with OpenGL ES 3.1 support. LZDoom targets OpenGL 2 / GL2 hardware and can also use a software renderer (SoftPoly). Either path works fine on Pi 5 for FreeDoom at 640×400 resolution.

---

## Build Dependencies (Debian Bookworm / Pi OS)

Based on GZDoom Linux build documentation and confirmed community builds:

```bash
sudo apt-get install \
  build-essential cmake git \
  libsdl2-dev \
  libopenal-dev \
  libmpg123-dev \
  libsndfile1-dev \
  libgtk-3-dev \
  zlib1g-dev \
  libbz2-dev \
  libjpeg-dev \
  libfluidsynth-dev \
  libgme-dev \
  libvpx-dev
```

ZMusic must be built and installed first (it is a separate library that LZDoom depends on):

```bash
git clone https://github.com/ZDoom/ZMusic.git
mkdir -p ZMusic/build && cd ZMusic/build
cmake .. -DCMAKE_BUILD_TYPE=Release
make -j4
sudo make install
cd ../..
```

Then build LZDoom:

```bash
git clone https://github.com/drfrag666/lzdoom.git
cd lzdoom
mkdir build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
make -j4
# Binary is at: lzdoom/build/lzdoom
```

**Note on cmake flags:** One Pi community member noted that for GZDoom-family ports on Pi 5, `cmake ..` (without `-DCMAKE_BUILD_TYPE=Release`) may give better optimisation because the CMakeLists.txt already auto-detects O2 for the Pi build environment. Worth trying both.

Source: [Raspberry Pi Forums – GZDoom on Pi 4](https://forums.raspberrypi.com/viewtopic.php?t=354301)

**Binary storage recommendation:** Once compiled, store the binary in the repo or as a release artifact. Do not rely on recompiling at deploy time. The binary is self-contained and will continue to work on a fixed Pi OS Bookworm image indefinitely.

---

## LZSplitDoom: Reference Implementation

A Windows launcher called **LZSplitDoom** (by SliverXReal, released May 2025) automates all of the above — INI management, window positioning, gamepad assignment, save paths — as a GUI wrapper around exactly this approach. It is Windows-only but is an excellent reference for the logic that needs to be ported to `launcher.sh`.

Key techniques it uses, relevant to the TD5 Dash Linux implementation:
- Copies P1's INI to P2–P4, then edits P2–P4 INIs in-place before launch (to swap joystick IDs, mute music, set save dirs)
- Strips window borders programmatically after launch
- Calculates display dimensions and resizes/positions windows based on screen size
- Binds the quit/menu action only to P1; when P1 quits, kills all other instances

Source: [LZSplitDoom – GitHub](https://github.com/SliverXReal/LZSplitDoom) | [Doomworld thread](https://www.doomworld.com/forum/topic/133012-lzsplitdoom/)

---

## Known Issues / Gotchas

### Joystick axes conflict
The most commonly reported problem is both controllers controlling both instances. Root cause: both instances see all joysticks. Fix: in each instance's INI, set all axis bindings for the OTHER joystick index to `None`. Button bindings are set independently per-instance.

### DirectInput vs SDL joystick on Linux
LZDoom on Linux uses SDL for joystick input (not DirectInput, which is Windows-only). The generic USB SNES controllers at `/dev/input/js0` and `/dev/input/js1` are exposed via the legacy joystick API, which SDL supports via `SDL_JOYSTICK_DEVICE` or by index. SDL joystick index 0 = `js0`, index 1 = `js1`.

This is different from the current TD5 Dash approach where SDL joystick was found broken for chocolate-doom. LZDoom uses a newer SDL stack and different SDL init path — worth testing from scratch rather than assuming it's broken.

### Instance startup sequencing
P1 must have time to bind its port before P2 tries to connect. A `sleep 1.5` or `sleep 2` between launching P1 and P2 is the standard approach. Some users needed up to 3 seconds on slower hardware, but Pi 5 should be faster.

### Audio doubling
Both instances will attempt to play music and sound effects. Standard mitigation: mute music on all instances except P1 via INI cvar. Sound effects from both instances playing simultaneously is expected and fine (they're in the same game world anyway in co-op).

### Window management with matchbox-wm
The TD5 Dash kiosk uses matchbox-window-manager (single-window WM). Running two LZDoom windows simultaneously means matchbox will only show one at a time — which is the opposite of what you want. **The two-instance approach likely requires switching to a different WM (e.g. openbox) for the Doom session, or using matchbox in a mode that allows multiple windows, or bypassing it entirely.**

The existing `launcher.sh` already handles WM switching (it starts matchbox specifically for the Doom session). This will need to be changed to a multi-window WM for 2P mode. `openbox` is a lightweight option available via apt.

---

## Suggested Implementation Approach for TD5 Dash

Based on all of the above, the recommended launcher flow for 2P mode:

1. **Build phase (setup.sh):** Compile LZDoom binary, store at `games/doom/lzdoom`. Build ZMusic first.

2. **INI generation:** At launch time, generate two INI files:
   - `games/doom/lzdoom-p1.ini` — joystick 0 active, joystick 1 axes all `None`, music on
   - `games/doom/lzdoom-p2.ini` — joystick 1 active, joystick 0 axes all `None`, music muted
   - Both with `vid_activeinbackground=true`, `i_soundinbackground=true`, `joy_background=true`

3. **WM:** For 2P mode, use `openbox` (or another multi-window WM) instead of `matchbox`.

4. **Launch sequence:**
   ```bash
   # P1 hosts, left half of screen
   games/doom/lzdoom \
     -config games/doom/lzdoom-p1.ini \
     -iwad <wad_path> \
     -host 2 \
     +vid_activeinbackground 1 \
     +i_soundinbackground 1 \
     +joy_background 1 \
     -window -geometry 640x400+0+0 &
   P1_PID=$!

   sleep 2.0

   # P2 connects, right half of screen
   games/doom/lzdoom \
     -config games/doom/lzdoom-p2.ini \
     -iwad <wad_path> \
     -connect 127.0.0.1 \
     +vid_activeinbackground 1 \
     +i_soundinbackground 1 \
     +joy_background 1 \
     -window -geometry 640x400+640+0 &
   P2_PID=$!
   ```

5. **Cleanup:** When either instance exits, kill the other. Restore matchbox for the GTK overlay / return to kiosk.

---

## LZDoom Status (June 2025)

LZDoom was officially discontinued by its maintainer (drfrag). The ZDoom forums now redirect LZDoom bug reports to GZDoom with the GLES renderer. However:

- Last release (4.11.4) was March 2025 — recently maintained
- The `joy_background` feature is stable and complete
- No security or compatibility concerns for a fixed embedded deployment
- The binary, once compiled, is fully self-contained

For the TD5 Dash use case (fixed hardware, fixed OS, joke feature playing FreeDoom), discontinued status is inconsequential.

---

## Source References

| Source | URL |
|--------|-----|
| ZDoom Forums – Multiplayer Doom on Pi | https://forum.zdoom.org/viewtopic.php?t=68130 |
| ZDoom Forums – How to splitscreen/couch coop? | https://forum.zdoom.org/viewtopic.php?t=63710 |
| LZDoom 3.85 release thread (splitscreen changelog) | https://forum.zdoom.org/viewtopic.php?t=67672 |
| LZDoom 4.11.4 release thread | https://forum.zdoom.org/viewtopic.php?f=231&t=62157 |
| LZSplitDoom – GitHub | https://github.com/SliverXReal/LZSplitDoom |
| LZSplitDoom – Doomworld thread | https://www.doomworld.com/forum/topic/133012-lzsplitdoom/ |
| LZDoom – GitHub (drfrag666) | https://github.com/drfrag666/lzdoom |
| christianhaitian/lzdoom (ARM build fork) | https://github.com/christianhaitian/lzdoom |
| ZDoom Wiki – Compile GZDoom on Linux | https://zdoom.org/w/index.php?title=Compile_GZDoom_on_Linux |
| RPi Forums – GZDoom on Pi 4 (Bookworm build notes) | https://forums.raspberrypi.com/viewtopic.php?t=354301 |
| AUR – lzdoom (aarch64 confirmation) | https://aur.archlinux.org/packages/lzdoom |
