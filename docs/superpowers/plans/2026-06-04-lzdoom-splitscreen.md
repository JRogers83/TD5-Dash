# LZDoom Split-Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace chocolate-doom with LZDoom as the Doom engine for both single-player and split-screen 2P modes, using LZDoom's native `joy_background` cvar for reliable background joystick input.

**Architecture:** A pre-compiled aarch64 LZDoom binary is committed to the repo so OTA updates deliver engine updates via `git pull`. Per-player INI files configure joystick assignment and background rendering. Single-player uses one LZDoom instance under openbox; 2P uses two networked instances (`-host`/`-connect`) side by side. Phase 0 is a spike that must pass GO/NO-GO before any code is committed.

**Tech Stack:** LZDoom 4.11.4 (drfrag666/lzdoom or christianhaitian/lzdoom ARM fork), ZMusic (statically linked via `-DDYN_ZMUSIC=OFF`), openbox WM, python3-evdev (removed in cleanup), SDL2 joystick (native LZDoom), FastAPI backend unchanged.

---

## Fallback

If Phase 0 returns NO-GO (LZDoom won't compile or SDL joystick fails), the fallback is Option C: two independent single-player instances with xdotool window-targeted input (`xdotool key --window <id>`). Do not begin Option C unless Phase 0 explicitly fails.

---

## Files

| File | Action | Purpose |
|------|--------|---------|
| `games/doom/lzdoom` | Create (built on Pi) | Compiled aarch64 LZDoom binary |
| `games/doom/lzdoom-p1.ini` | Create | P1 config — Joy1 active, Joy2 axes unbound, music on |
| `games/doom/lzdoom-p2.ini` | Create | P2 config — Joy2 active, Joy1 axes unbound, music muted |
| `.gitattributes` | Create/modify | Mark `games/doom/lzdoom` as binary |
| `games/doom/launcher.sh` | Rewrite | LZDoom launch logic, openbox, SDL_VIDEO_WINDOW_POS |
| `games/doom/overlay.py` | Modify | Remove matchbox dependency, works under openbox |
| `backend/game_service.py` | Modify | Add `_LZDOOM` path, `lzdoom_missing` error, simplify exit codes |
| `tests/test_game_service.py` | Modify | Update mocked paths, add lzdoom_missing test |
| `deploy/setup.sh` | Modify | Add LZDoom runtime deps + openbox, remove joy2key uinput rule |
| `backend/main.py` | Modify | Add LZDoom runtime deps to OTA apt install |
| `games/doom/joy2key.py` | Delete (Phase 4) | Replaced by LZDoom native SDL joystick |
| `games/doom/chocolate-doom.cfg` | Delete (Phase 4) | Replaced by lzdoom-p1/p2.ini |

---

## Phase 0: Spike — Validate All Assumptions on Pi (SSH Only, Nothing Committed)

**This phase is a gate. Do not proceed to Phase 1 unless all checks pass.**

Run everything over SSH. The working single-player chocolate-doom is untouched throughout this phase.

### Task 0.1: Install Build Dependencies on Pi

- [ ] SSH into Pi
- [ ] Install build deps:
```bash
sudo apt-get install -y \
  build-essential cmake git \
  libsdl2-dev libopenal-dev libmpg123-dev \
  libsndfile1-dev libgtk-3-dev zlib1g-dev \
  libbz2-dev libjpeg-dev libfluidsynth-dev \
  libgme-dev libvpx-dev
```
- [ ] Verify cmake version is 3.x+:
```bash
cmake --version
```
Expected: `cmake version 3.x.x`

---

### Task 0.2: Build ZMusic (LZDoom Dependency)

- [ ] Clone and build ZMusic with static output:
```bash
cd ~
git clone https://github.com/ZDoom/ZMusic.git
mkdir -p ZMusic/build && cd ZMusic/build
cmake .. -DCMAKE_BUILD_TYPE=Release
make -j4
sudo make install
sudo ldconfig
```
- [ ] Verify library installed:
```bash
ls /usr/local/lib/libzmusic*
```
Expected: at least `libzmusic.a` or `libzmusic.so*`

---

### Task 0.3: Build LZDoom Binary

Try the christianhaitian ARM fork first (more recently maintained for ARM). Fall back to drfrag666 if it fails to build.

- [ ] Clone and build:
```bash
cd ~
git clone https://github.com/christianhaitian/lzdoom.git
cd lzdoom
mkdir build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release -DDYN_ZMUSIC=OFF
make -j4
```
Expected: build completes with binary at `~/lzdoom/build/lzdoom`

- [ ] If christianhaitian fails, try drfrag666:
```bash
cd ~
git clone https://github.com/drfrag666/lzdoom.git lzdoom-drfrag
cd lzdoom-drfrag
mkdir build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release -DDYN_ZMUSIC=OFF
make -j4
```

- [ ] Verify binary runs:
```bash
~/lzdoom/build/lzdoom --version
```
Expected output contains: `LZDoom` and a version number (e.g. `LZDoom 4.x.x`)

---

### Task 0.4: Test Single-Instance Launch and Controller Input

This is the most critical spike test. LZDoom must respond to the SNES controller natively via SDL.

- [ ] Run a single LZDoom instance with DISPLAY:
```bash
DISPLAY=:0 SDL_VIDEO_WINDOW_POS="320,0" \
  ~/lzdoom/build/lzdoom \
  -iwad /usr/share/games/doom/freedoom1.wad \
  -window -width 640 -height 400 \
  +joy_background 1 \
  +vid_activeinbackground 1 \
  +i_soundinbackground 1
```
- [ ] **Critical test:** Press D-pad and face buttons on the controller. The player should move and fire.
  - If YES → SDL joystick works. **Proceed.**
  - If NO → SDL joystick does not work with LZDoom on this hardware. **STOP. Activate Option C fallback.**
- [ ] Note what window title LZDoom uses (needed for overlay.py):
```bash
DISPLAY=:0 xdotool search --name ".*" getwindowname
```

---

### Task 0.5: Test Network Port Binding

- [ ] In one SSH session, launch P1 as host:
```bash
DISPLAY=:0 SDL_VIDEO_WINDOW_POS="0,0" \
  ~/lzdoom/build/lzdoom \
  -iwad /usr/share/games/doom/freedoom1.wad \
  -window -width 640 -height 400 \
  -host 2 -port 5029 &
sleep 3
```
- [ ] In the same session, check if port is bound:
```bash
ss -ulnp | grep 5029
```
Expected: a line showing `lzdoom` bound to UDP port 5029. If empty → `-host` doesn't bind as expected, abort and reassess.

- [ ] In a second SSH session, launch P2:
```bash
DISPLAY=:0 SDL_VIDEO_WINDOW_POS="640,0" \
  ~/lzdoom/build/lzdoom \
  -iwad /usr/share/games/doom/freedoom1.wad \
  -window -width 640 -height 400 \
  -connect 127.0.0.1:5029
```
- [ ] Verify both windows appear and both controllers independently control their own player. **If both windows appear and controllers are independent → 2P works. Proceed.**

---

### Task 0.6: Generate Reference INI and Record Format

This step captures the exact cvar names needed for Phase 2 INI templates.

- [ ] Launch LZDoom once to generate default INI:
```bash
DISPLAY=:0 ~/lzdoom/build/lzdoom \
  -iwad /usr/share/games/doom/freedoom1.wad \
  -window -width 640 -height 400 &
sleep 5 && kill %1
```
- [ ] Find and read the generated INI:
```bash
find ~/.config ~/.local ~ -name "*.ini" 2>/dev/null | grep -i zdoom
cat <found-ini-path>
```
- [ ] Record:
  - The INI file path (needed for `-config` flag)
  - The section name for key bindings (e.g. `[Doom.Bindings]` or `[GlobalSettings]`)
  - The joystick axis cvar names (e.g. `Joy1up`, `Joy1YAxis`, etc.)
  - The joystick button cvar names (e.g. `Joy1+1`, `Joy1Button0`, etc.)
  - Any existing Joy2 entries (to know what to clear for cross-player isolation)
- [ ] **Save a copy to the repo location for reference:**
```bash
cp <ini-path> ~/TD5-Dash/games/doom/lzdoom-reference.ini
```

---

### Task 0.7: Check Runtime Dependencies

- [ ] Check what libraries the binary needs at runtime:
```bash
ldd ~/lzdoom/build/lzdoom
```
- [ ] Note any non-standard libraries (i.e. not `libc`, `libm`, `libpthread`, `libdl`).
- [ ] For each non-standard lib, find the apt package:
```bash
dpkg -S <lib-path>
```
Record the package names — these go into `setup.sh` as runtime deps.

---

### Task 0.8: GO / NO-GO Decision

- [ ] All of the following must be true to proceed:
  - [ ] LZDoom binary built and runs
  - [ ] Single-instance SDL joystick responds to SNES controller
  - [ ] `-host 2` binds UDP port 5029
  - [ ] P2 `-connect` joins P1 and both windows appear
  - [ ] INI format documented
  - [ ] Runtime deps identified

If all pass: **GO — proceed to Phase 1.**
If any fail: **NO-GO — activate Option C fallback (xdotool window-targeted input).**

---

## Phase 1: Binary and Runtime Infrastructure

### Task 1.1: Add .gitattributes Entry

- [ ] Check if `.gitattributes` exists:
```bash
ls C:\code\TD5-Dash\.gitattributes
```
- [ ] Create or append:
```
games/doom/lzdoom binary
```
Full file content if creating new:
```
games/doom/lzdoom binary
```
- [ ] Commit:
```bash
git add .gitattributes
git commit -m "Mark games/doom/lzdoom as binary in gitattributes"
```

---

### Task 1.2: Copy Binary From Pi and Commit

- [ ] On Windows, copy binary from Pi via SCP:
```
scp pi@<pi-ip>:~/lzdoom/build/lzdoom C:\code\TD5-Dash\games\doom\lzdoom
```
- [ ] Verify file is present and is aarch64:
```bash
# On Pi:
file ~/lzdoom/build/lzdoom
```
Expected: `ELF 64-bit LSB executable, ARM aarch64`

- [ ] Commit binary (this will be ~15-20MB):
```bash
git add games/doom/lzdoom
git commit -m "Add compiled aarch64 LZDoom binary"
git push origin main
```

---

### Task 1.3: Update setup.sh with Runtime Deps and openbox

Use the runtime dep list from Task 0.7. At minimum the list below covers what LZDoom needs on a clean Bookworm install (adjust based on `ldd` output from spike):

- [ ] Add to the apt-get install block in `deploy/setup.sh`:
```bash
    openbox \
    libopenal1 \
    libmpg123-0 \
    libsndfile1 \
    libfluidsynth3 \
    libgme0 \
```
Also remove the uinput udev rule block (joy2key replacement):
- [ ] Remove this block from `deploy/setup.sh`:
```bash
# ── uinput access for joy2key joystick mapper ──────────────────────────────────
echo "▸ Configuring uinput access for joystick mapper..."
echo 'KERNEL=="uinput", GROUP="input", MODE="0660"' > /etc/udev/rules.d/99-td5-uinput.rules
modprobe uinput 2>/dev/null || true
udevadm control --reload-rules && udevadm trigger 2>/dev/null || true
# Set permissions directly in case the udev rule hasn't taken effect yet
chown root:input /dev/uinput 2>/dev/null || true
chmod 660 /dev/uinput 2>/dev/null || true
```
- [ ] Commit:
```bash
git add deploy/setup.sh
git commit -m "Add LZDoom runtime deps and openbox to setup.sh, remove uinput rule"
```

---

### Task 1.4: Update OTA apt Install in main.py and sudoers

- [ ] In `backend/main.py`, update the apt install line:
```python
    # apt install game deps — idempotent; upgrades if newer packages are available
    subprocess.run(
        ["sudo", "apt-get", "install", "-y",
         "freedoom", "python3-evdev", "openbox",
         "libopenal1", "libmpg123-0", "libsndfile1",
         "libfluidsynth3", "libgme0"],
        capture_output=True,
    )
```
- [ ] In `deploy/setup.sh`, update the sudoers rule to match:
```bash
$SERVICE_USER ALL=(ALL) NOPASSWD: /usr/bin/apt-get install -y freedoom python3-evdev openbox libopenal1 libmpg123-0 libsndfile1 libfluidsynth3 libgme0
```
- [ ] Commit:
```bash
git add backend/main.py deploy/setup.sh
git commit -m "Add LZDoom runtime deps to OTA apt install and sudoers rule"
```

---

## Phase 2: Single-Player LZDoom

### Task 2.1: Create P1 INI Template

Using the INI format discovered in Task 0.6, create the P1 config. The values below use the standard GZDoom/LZDoom cvar names — **adjust based on actual reference INI from spike**.

- [ ] Create `games/doom/lzdoom-p1.ini`:
```ini
[GlobalSettings]
vid_activeinbackground=true
i_soundinbackground=true
joy_background=true

[Doom.ConsoleVariables]
snd_musicvolume=0.5

[Doom.Bindings]
Joy1up=+forward
Joy1down=+back
Joy1left=+turnleft
Joy1right=+turnright
Joy1+1=+attack
Joy1+2=+use
Joy1+4=+speed
Joy1+5=+strafe
Joy1+3=invprev
Joy1+0=invnext
Joy1+9=menu_main
Joy1+8=automap

; Disable all Joy2 axes so P2's controller does not affect P1
Joy2up=
Joy2down=
Joy2left=
Joy2right=
```

**Note:** Button indices here (0-9) correspond to the SNES controller mapping confirmed in single-player testing (A=1, B=2, X=0, Y=3, L=4, R=5, Select=8, Start=9). Adjust to match reference INI format exactly — the key names `Joy1+0` vs `Joy1Button0` may differ based on spike output.

- [ ] Commit:
```bash
git add games/doom/lzdoom-p1.ini
git commit -m "Add LZDoom P1 INI config with joystick bindings"
```

---

### Task 2.2: Update game_service.py for LZDoom

- [ ] Write failing tests first. In `tests/test_game_service.py`, add a test class:
```python
class TestLZDoomValidation:
    def test_start_lzdoom_missing_returns_500(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(game_service, "_LZDOOM", tmp_path / "lzdoom")
        monkeypatch.setattr(game_service, "_WAD_OVERRIDE", tmp_path / "nope.wad")
        monkeypatch.setattr(game_service, "_WAD_FREEDOOM", tmp_path / "nope2.wad")
        r = client.post("/system/game-mode/start",
                        json={"mode": "single", "skill": 3})
        assert r.status_code == 500
        assert r.json()["detail"] == {"error": "lzdoom_missing"}
```
- [ ] Run test to verify it fails:
```bash
docker run --rm \
  -v "C:\code\TD5-Dash\backend:/app/backend" \
  -v "C:\code\TD5-Dash\tests:/app/tests" \
  -v "C:\code\TD5-Dash\pytest.ini:/app/pytest.ini" \
  td5-dash-td5-dash:latest \
  sh -c "cd /app/backend && python -m pytest /app/tests/test_game_service.py::TestLZDoomValidation -v"
```
Expected: `FAILED` — `AttributeError: module 'game_service' has no attribute '_LZDOOM'`

- [ ] Update `backend/game_service.py`. Change the constants block:
```python
_REPO_DIR      = Path(__file__).resolve().parent.parent
_WAD_OVERRIDE  = _REPO_DIR / "wads" / "doom.wad"
_WAD_FREEDOOM  = Path("/usr/share/games/doom/freedoom1.wad")
_LZDOOM        = _REPO_DIR / "games" / "doom" / "lzdoom"
_LAUNCHER      = _REPO_DIR / "games" / "doom" / "launcher.sh"
```

- [ ] Add `lzdoom_missing` check in the `start` endpoint, after the WAD check:
```python
    wad_path = _WAD_OVERRIDE if _WAD_OVERRIDE.is_file() else _WAD_FREEDOOM
    if not wad_path.is_file():
        raise HTTPException(500, {"error": "wad_missing"})
    if not _LZDOOM.is_file():
        raise HTTPException(500, {"error": "lzdoom_missing"})
```

- [ ] Simplify exit code messages (LZDoom codes differ from chocolate-doom):
```python
_EXIT_CODE_MESSAGES = {
    1: "LZDoom exited with an error — check journalctl for details",
}
```

- [ ] Run tests:
```bash
docker run --rm \
  -v "C:\code\TD5-Dash\backend:/app/backend" \
  -v "C:\code\TD5-Dash\tests:/app/tests" \
  -v "C:\code\TD5-Dash\pytest.ini:/app/pytest.ini" \
  td5-dash-td5-dash:latest \
  sh -c "cd /app/backend && python -m pytest /app/tests/test_game_service.py -v"
```
Expected: all pass including `TestLZDoomValidation`

- [ ] Commit:
```bash
git add backend/game_service.py tests/test_game_service.py
git commit -m "Add LZDoom binary path validation and simplify exit code map"
```

---

### Task 2.3: Rewrite launcher.sh for Single-Player LZDoom

- [ ] Rewrite `games/doom/launcher.sh` for single-player. This replaces the entire file:
```bash
#!/bin/sh
# Doom Mode launcher — orchestrates openbox + LZDoom instance(s) + overlay.
# Env in: MODE (single|coop|deathmatch), WAD (full path), SKILL (1-5), LZDOOM (binary path)
# Caller invokes with start_new_session=True so this becomes session leader.
#
# Exit codes:
#   0  clean exit
#   1  LZDoom failed to start

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

cleanup() {
    pkill -P $$ 2>/dev/null || true
    wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# ── Controller validation (2P only) ───────────────────────────────────
if [ "$MODE" != "single" ]; then
    JS_COUNT=$(ls /dev/input/js* 2>/dev/null | wc -l)
    if [ "$JS_COUNT" -lt 2 ]; then
        echo "ERROR: 2P mode needs 2 controllers, found $JS_COUNT" >&2
        exit 2
    fi
fi

# ── Window manager ────────────────────────────────────────────────────
openbox --sm-disable &
sleep 0.3
xsetroot -solid black 2>/dev/null || true

# ── Common LZDoom flags ───────────────────────────────────────────────
COMMON_OPTS="-iwad $WAD -skill $SKILL +vid_activeinbackground 1 +i_soundinbackground 1 +joy_background 1"

case "$MODE" in
    single)
        SDL_VIDEO_WINDOW_POS="320,0" \
        "$LZDOOM" $COMMON_OPTS \
            -config "$SCRIPT_DIR/lzdoom-p1.ini" \
            -window -width 640 -height 400 &
        ;;
    coop)
        SDL_VIDEO_WINDOW_POS="0,0" \
        "$LZDOOM" $COMMON_OPTS \
            -config "$SCRIPT_DIR/lzdoom-p1.ini" \
            -window -width 640 -height 400 \
            -host 2 -port 5029 &
        sleep 2.5
        SDL_VIDEO_WINDOW_POS="640,0" \
        "$LZDOOM" $COMMON_OPTS \
            -config "$SCRIPT_DIR/lzdoom-p2.ini" \
            -window -width 640 -height 400 \
            -connect 127.0.0.1:5029 &
        ;;
    deathmatch)
        SDL_VIDEO_WINDOW_POS="0,0" \
        "$LZDOOM" $COMMON_OPTS \
            -config "$SCRIPT_DIR/lzdoom-p1.ini" \
            -window -width 640 -height 400 \
            -deathmatch -host 2 -port 5029 &
        sleep 2.5
        SDL_VIDEO_WINDOW_POS="640,0" \
        "$LZDOOM" $COMMON_OPTS \
            -config "$SCRIPT_DIR/lzdoom-p2.ini" \
            -window -width 640 -height 400 \
            -connect 127.0.0.1:5029 &
        ;;
esac

# Detect immediate LZDoom failure
sleep 1.0
if ! pgrep -f lzdoom >/dev/null 2>&1; then
    echo "ERROR: lzdoom failed to launch" >&2
    exit 1
fi

# ── Overlay ────────────────────────────────────────────────────────────
MODE="$MODE" python3 "$SCRIPT_DIR/overlay.py" &

# ── Wait for all LZDoom instances to exit ─────────────────────────────
while pgrep -f lzdoom >/dev/null 2>&1; do
    sleep 0.5
done

exit 0
```

- [ ] Pass `LZDOOM` path from `game_service.py`. In `backend/game_service.py`, update the `env` dict in `start()`:
```python
    env = {
        **os.environ,
        "MODE":   req.mode,
        "WAD":    str(wad_path),
        "SKILL":  str(req.skill),
        "LZDOOM": str(_LZDOOM),
    }
```

- [ ] Run tests:
```bash
docker run --rm \
  -v "C:\code\TD5-Dash\backend:/app/backend" \
  -v "C:\code\TD5-Dash\tests:/app/tests" \
  -v "C:\code\TD5-Dash\pytest.ini:/app/pytest.ini" \
  td5-dash-td5-dash:latest \
  sh -c "cd /app/backend && python -m pytest /app/tests/test_game_service.py -v"
```
Expected: all pass

- [ ] Commit:
```bash
git add games/doom/launcher.sh backend/game_service.py
git commit -m "Rewrite launcher for LZDoom, pass LZDOOM binary path from game_service"
```

---

### Task 2.4: Update overlay.py for openbox

The overlay runs under openbox instead of matchbox. openbox supports `_NET_WM_STATE_ABOVE` so the GTK overlay code requires no functional changes. However, verify the window title for xdotool F2/F3 commands now targets LZDoom's window title (discovered in Task 0.4).

- [ ] Check current xdotool calls in `games/doom/overlay.py`:
```bash
grep -n "xdotool" games/doom/overlay.py
```
- [ ] If xdotool calls reference `--name` with a window title, update to match LZDoom's window title from spike Task 0.4. LZDoom typically titles windows as `LZDoom <version>` or the IWAD name.
- [ ] Commit if changed:
```bash
git add games/doom/overlay.py
git commit -m "Update overlay xdotool window title for LZDoom"
```

---

### Task 2.5: Test Single-Player End-to-End on Pi

This is a Pi-side validation, not a unit test.

- [ ] Push latest changes and hit Update in Settings UI
- [ ] Launch single-player Doom from the Settings tile
- [ ] Verify:
  - [ ] LZDoom window appears centred on display
  - [ ] SNES controller moves player (D-pad), fires (A), uses (B)
  - [ ] Overlay (≡) appears top-right
  - [ ] Quit button returns to Settings
- [ ] Check journal for any errors:
```bash
journalctl -u td5-dash -n 200 | grep -v "ftdi\|pyftdi\|UsbTools\|obd\|httpx" | grep -i "error\|warn\|lzdoom\|doom"
```
- [ ] **Gate:** Do not proceed to Phase 3 until single-player works end-to-end.

---

## Phase 3: Two-Player Split-Screen

### Task 3.1: Create P2 INI Template

- [ ] Create `games/doom/lzdoom-p2.ini`. This is P1's INI with joystick indices swapped and music muted. Using the exact cvar names from spike Task 0.6:
```ini
[GlobalSettings]
vid_activeinbackground=true
i_soundinbackground=true
joy_background=true

[Doom.ConsoleVariables]
snd_musicvolume=0

[Doom.Bindings]
; P2 uses Joy2 (js1 = second SNES controller)
Joy2up=+forward
Joy2down=+back
Joy2left=+turnleft
Joy2right=+turnright
Joy2+1=+attack
Joy2+2=+use
Joy2+4=+speed
Joy2+5=+strafe
Joy2+3=invprev
Joy2+0=invnext
Joy2+9=menu_main
Joy2+8=automap

; Disable all Joy1 axes so P1's controller does not affect P2
Joy1up=
Joy1down=
Joy1left=
Joy1right=
```

**Note:** Adjust Joy2 cvar names to match actual LZDoom INI format from spike.

- [ ] Commit:
```bash
git add games/doom/lzdoom-p2.ini
git commit -m "Add LZDoom P2 INI config with Joy2 bindings and music muted"
```

---

### Task 3.2: Test 2P End-to-End on Pi

- [ ] Push and update via UI
- [ ] Launch Co-op mode from the game launcher
- [ ] Verify:
  - [ ] Both LZDoom windows appear (left 640px / right 640px)
  - [ ] P1's controller controls the left window only
  - [ ] P2's controller controls the right window only
  - [ ] Overlay appears and Quit closes both instances
- [ ] Test deathmatch mode same way
- [ ] Check journal for errors:
```bash
journalctl -u td5-dash -n 200 | grep -v "ftdi\|pyftdi\|UsbTools\|obd\|httpx" | grep -i "lzdoom\|error\|connect\|host"
```

---

## Phase 4: Cleanup — Remove Old Stack

**Only execute this phase after Phase 3 is passing end-to-end.**

### Task 4.1: Remove joy2key.py and chocolate-doom.cfg

- [ ] Delete files:
```bash
git rm games/doom/joy2key.py
git rm games/doom/chocolate-doom.cfg
git rm games/doom/lzdoom-reference.ini  # spike artefact, not needed in repo
```
- [ ] Commit:
```bash
git commit -m "Remove joy2key.py and chocolate-doom.cfg — replaced by LZDoom native input"
```

---

### Task 4.2: Remove chocolate-doom from setup.sh

- [ ] In `deploy/setup.sh`, remove from the apt-get install block:
```
    chocolate-doom \
```
- [ ] Remove `python3-evdev` from the apt-get install block (no longer needed):
```
    python3-evdev \
```
- [ ] Commit:
```bash
git add deploy/setup.sh
git commit -m "Remove chocolate-doom and python3-evdev from setup.sh"
```

---

### Task 4.3: Remove python3-evdev from OTA and sudoers

- [ ] In `backend/main.py`, remove `"python3-evdev"` from the apt install list:
```python
    subprocess.run(
        ["sudo", "apt-get", "install", "-y",
         "freedoom", "openbox",
         "libopenal1", "libmpg123-0", "libsndfile1",
         "libfluidsynth3", "libgme0"],
        capture_output=True,
    )
```
- [ ] In `deploy/setup.sh`, update the sudoers rule to match (remove `python3-evdev`):
```bash
$SERVICE_USER ALL=(ALL) NOPASSWD: /usr/bin/apt-get install -y freedoom openbox libopenal1 libmpg123-0 libsndfile1 libfluidsynth3 libgme0
```
- [ ] Run full test suite:
```bash
docker run --rm \
  -v "C:\code\TD5-Dash\backend:/app/backend" \
  -v "C:\code\TD5-Dash\tests:/app/tests" \
  -v "C:\code\TD5-Dash\pytest.ini:/app/pytest.ini" \
  td5-dash-td5-dash:latest \
  sh -c "cd /app/backend && python -m pytest /app/tests/ -v"
```
Expected: all pass

- [ ] Commit:
```bash
git add backend/main.py deploy/setup.sh
git commit -m "Remove python3-evdev from OTA and sudoers — LZDoom uses native SDL joystick"
```

---

### Task 4.4: Final verification and push

- [ ] Run full test suite one more time to confirm clean state
- [ ] Push to main:
```bash
git push origin main
```
- [ ] On Pi: hit Update, launch single-player, confirm working
- [ ] On Pi: launch co-op, confirm working
- [ ] Update `documentation/doom-mode-test-plan.md` to reflect LZDoom and remove references to joy2key and chocolate-doom
- [ ] Commit docs update:
```bash
git add documentation/doom-mode-test-plan.md
git commit -m "Update doom test plan for LZDoom implementation"
git push origin main
```

---

## Self-Review

**Spec coverage:**
- ✅ LZDoom binary built and committed to repo
- ✅ OTA delivers binary via git pull (no recompile on Pi)
- ✅ Single-player migrated to LZDoom
- ✅ 2P co-op and deathmatch via `-host`/`-connect`
- ✅ Native SDL joystick (joy_background) replaces joy2key
- ✅ openbox replaces matchbox for Doom sessions
- ✅ Per-player INI files for joystick isolation
- ✅ Cleanup of old stack in final phase
- ✅ Spike gate before any committed changes
- ✅ Runtime deps in setup.sh and OTA
- ✅ Fallback (Option C) named if spike fails

**Placeholder check:**
- INI cvar names are marked as "adjust based on spike" — this is intentional and unavoidable without running the spike first. All other steps have exact content.

**Type consistency:**
- `_LZDOOM` defined in Task 2.2, used in Task 2.3 ✅
- `LZDOOM` env var set in Task 2.3, read in launcher.sh Task 2.3 ✅
- `lzdoom_missing` error code defined in Task 2.2 ✅
