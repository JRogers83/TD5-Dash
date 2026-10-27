# TD5 Dash — CLAUDE.md

## Project Overview

Custom in-vehicle display for a Land Rover Defender TD5, mounted in the dashboard ashtray opening.
Raspberry Pi 5 running a full-screen web kiosk UI on a Waveshare 7.9" bar-format DSI touchscreen.

Full spec: `documentation/SPEC.md`

---

## Display

- Resolution: **1280×400 landscape** — physically landscape (191mm wide × 60mm tall). The raw panel spec says "400×1280 portrait" but the Pi's dtoverlay rotates the output, so Chromium sees 1280×400. Design and CSS target 1280×400.
- Physical: 191.08mm × 60.40mm viewable area
- Touch: 5-point capacitive, I2C

---

## Tech Stack

| Layer | Choice | Notes |
|-------|--------|-------|
| Backend | FastAPI (Python) | WebSocket server + REST endpoints |
| Frontend | HTML/CSS/JS (vanilla) | Conscious decision — no framework |
| OS target | Pi OS Lite (Bookworm) | No desktop |
| Kiosk | Chromium kiosk mode | Full-screen, no chrome |

### Deferred decisions
- **Audio routing** — BT A2DP vs USB DAC (hardware pending; CarPiHAT DAC dropped with the CarPiHAT)
- **Power system** — resolved: Witty Pi 5 HAT+ (`wittypi_service.py`, `WITTYPI_ENABLED=1`); CarPiHAT PRO 5 dropped

---

## Frontend Design System

Vanilla HTML/CSS/JS — no framework. The design system is a small set of shared CSS classes applied consistently across all views. When adding new UI elements, always reach for these patterns first.

### Design tokens (`style.css` `:root`)

| Token | Value | Use |
|-------|-------|-----|
| `--bg` | `#0d0d0d` | Page / carousel background |
| `--surface` | `#181818` | Tile and card backgrounds |
| `--border` | `#2a2a2a` | Tile borders, dividers |
| `--text` | `#e8e8e8` | Primary text |
| `--text-muted` | `#888` | Labels, secondary text |
| `--text-dim` | `#444` | Inactive dots, placeholder text |
| `--c-green` | `#00e676` | Good / active / connected |
| `--c-amber` | `#ffab40` | Warning / caution |
| `--c-red` | `#ff5252` | Error / critical |
| `--c-blue` | `#40c4ff` | Cold / informational |
| `--c-track` | `#242424` | Gauge tracks and progress bars |

### Core component classes

| Class | Role | Notes |
|-------|------|-------|
| `stat-label` | Field / section heading | 20px, uppercase, `--text-muted` |
| `stat-value` | Data value | **30px**, bold, tabular-nums — **do not override per-view** |
| `stat-tile` | Metric container | `--surface` bg, border, **8px 12px** padding — **do not override per-view** |
| `engine-stat-bottom` | Value + dot row inside a tile | `justify-content: space-between` — value on left, dot on right |
| `status-dot` | Colour indicator | 10px circle default; **20px automatically inside `engine-stat-bottom`** |
| `stat-grid` | 2-column tile grid | `grid-template-columns: 1fr 1fr`, gap 8px; use `repeat(2, minmax(0, Npx))` for fixed-width columns |
| `vdivider` | Vertical column separator | 1px `--border`, `align-self: stretch` |
| `charge-badge` | Large state label | Bordered coloured text (e.g. "Float", "Roaming") |

> **Tile sizing rule:** `stat-tile` padding and `stat-value` font-size are canonical and must not be overridden in per-view CSS. The only exception is the Settings page (`settings-conn-tile` / `settings-sys-tile`) which uses compact sizing (6px 10px / 24px) for its dense control grid — this is intentional and explicit, not a precedent.

### Status dot colours

Apply these classes to a `status-dot` element:

| Class | Colour | Meaning |
|-------|--------|---------|
| `on` | green | Good / active / connected |
| `warn` | amber | Caution / elevated |
| `red` | red | Error / critical |
| `blue` | blue | Cold / informational |
| `off` | dim grey | Inactive / disabled |
| *(none)* | dim grey | Unknown / no data |

### Typical metric tile pattern

```html
<div class="stat-tile">
  <div class="stat-label">Field Name</div>
  <div class="engine-stat-bottom">
    <div class="stat-value" id="txt-field">—</div>
    <div class="status-dot" id="dot-field"></div>
  </div>
</div>
```

The `status-dot` is **automatically 20px** inside `engine-stat-bottom` — no extra CSS needed.
Tiles without a status indicator just omit the `engine-stat-bottom` wrapper and put `stat-value` directly.

### Section / column layout

Each view uses a horizontal flex row (`*-content` class) with `vdivider` elements between columns. Column widths are set with `flex: 0 0 Npx` (fixed) or `flex: 1` (grows to fill). Section headings within a column use `stat-label` directly — no extra heading class.

### What is intentionally different

- **Spotify view** — fully custom design (player, visualiser, browse panel). Does not use `stat-tile` / `stat-label` pattern.
- **Victron SoC arc** — SVG gauge with custom `.g-val` / `.g-unit` / `.g-label` classes.
- **Engine radial gauges** — Canvas Gauges library; the `CLASSIC` config object in `app.js` defines the shared aesthetic.

---

## WebSocket Message Format

Per-topic messages to avoid a monolithic payload and keep views decoupled:

```json
{"type": "engine",   "data": {"rpm": 0, "coolant_temp_c": 0, "boost_bar": 0, "throttle_pct": 0, "throttle_raw_pct": 0, "battery_v": 0, "inlet_air_temp_c": 0, "external_temp_c": 0, "fuel_temp_c": 0, "road_speed_kph": 0, "fault_codes": [{"code": "4-6", "description": "Ambient air temperature circuit (L)", "count": 187, "expected": true}]}}
{"type": "victron",  "data": {"soc_pct": 0, "voltage_v": 0, "current_a": 0, "consumed_ah": null, "time_to_go_min": null, "solar_yield_wh": 0, "solar_power_w": 0, "charge_state": "", "orion_state": "", "orion_input_v": 0, "orion_output_v": 0, "orion_output_a": 0}}
{"type": "spotify",  "data": {"connected": false, "playing": false, "error": false, "auth_required": false, "track": "", "artist": "", "album": "", "album_art_url": null, "progress_s": 0, "duration_s": 0, "device_name": "", "track_id": "", "liked": false}}
{"type": "weather",  "data": {"current": {"temp_c": 0, "humidity_pct": 0, "weather_code": 0, "wind_kph": 0}, "forecast": [], "location": "", "stale": false}}
{"type": "starlink", "data": {"state": "offline", "down_mbps": 0, "up_mbps": 0, "latency_ms": 0, "ping_drop_pct": 0, "obstructed": false, "obstruction_pct": 0, "roaming": false, "uptime_s": 0, "alerts": []}}
{"type": "gps",      "data": {"lat": 0, "lon": 0, "speed_kmh": 0, "heading_deg": 0, "fix": 0, "satellites_used": 0, "satellites_visible": 0, "hdop": null}}
{"type": "system",   "data": {"cpu_temp_c": 0, "cpu_load_pct": 0, "ram_usage_pct": 0, "disk_usage_pct": 0, "uptime_s": 0, "throttled": false, "wifi_connected": false, "bt_connected": false, "override_mode": false, "sidelights": false}}
```

---

## REST Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/ws` | WebSocket upgrade |
| `GET` | `/health` | Health check — per-service status |
| `GET` | `/settings` | All settings key/value pairs |
| `POST` | `/settings` | Write settings — `{"key": "value", ...}` |
| `GET` | `/pages` | Page visibility flags |
| `POST` | `/pages` | Update page visibility — `{"key": 0\|1, ...}` |
| `GET` | `/history` | Engine history — `?time_range=hour\|day\|week\|month\|year\|all` |
| `POST` | `/obd/clear-dtc` | Clear DTCs — **not implemented**: no verified ECU clear frame exists, so it returns an honest "not supported" result and sends nothing |
| `GET` | `/obd/dtc/{code}` | Fault-code library entry (explanation, likely causes, severity) |
| `POST` | `/obd/full-test` | Run the 7-stage K-Line diagnostic (`obd/pi_diag.py`); progress streams over WS as `{"type": "obd_test", ...}` |
| `GET` | `/api/state` | Full state snapshot for all topics (Home Assistant) |
| `GET` | `/api/state/{topic}` | Single-topic state snapshot |
| `POST` | `/spotify/command` | Playback control — `{"action": "play"\|"pause"\|"next"\|"prev"}` |
| `POST` | `/spotify/like` | Save track to Liked Songs — `{"track_id": "..."}` |
| `GET` | `/spotify/playlists` | Current user's playlists |
| `GET` | `/spotify/playlist/{id}/tracks` | Tracks in a playlist |
| `POST` | `/spotify/play` | Play a playlist — `{"context_uri": "...", "track_uri": "..."}` |
| `POST` | `/system/brightness` | Apply backlight brightness — `{"value": 0–255}`; returns `{"ok", "value", "detail"}` with the real outcome (`ok: false` in Docker or if the sysfs write is refused) |
| `POST` | `/system/relay` | Control a relay — `{"name": "amp", "state": bool}` (logs only; no relay hardware wired) |
| `POST` | `/system/update` | OTA update — git pull + pip install + restart |
| `POST` | `/system/restart` | Restart service only (no code pull) |
| `POST` | `/system/rollback` | Roll back to the previous version — undoes the most recent `/system/update` |
| `GET` | `/system/version` | Current commit + rollback availability, for the Settings UI |
| `GET` | `/system/backlight` | Whether backlight control works (device found, writable) — drives the amber note on the Settings screen |
| `POST` | `/system/shutdown` | Clean Pi shutdown (delayed so the response returns first) |
| `GET` | `/system/game-mode/status` | Doom mode state + last error |
| `POST` | `/system/game-mode/start` | Start Doom mode (pauses Spotify, freezes Chromium, launches `games/doom/launcher.sh`) |
| `POST` | `/system/game-mode/stop` | Stop Doom mode and restore Chromium / Spotify |

---

## Development Workflow

1. **Local scaffold** — Windows machine (`C:\code\TD5-Dash`)
2. **Docker (Pi OS)** — primary dev/test environment, mirrors target
3. **Real Pi 5** — final deployment

Do not use Windows-specific paths or tools in any runtime code. Target is Linux/Pi OS Bookworm.

---

## Build Phases

| Phase | Description | Status |
|-------|-------------|--------|
| 0 | OBD Proof of Concept (laptop + KKL cable) | **Complete** |
| 1 | Bench Prototype — FastAPI backend, WebSocket hub, mock data, kiosk UI scaffold | **Complete** |
| 2 | OBD Integration — TD5 K-Line service | **Complete (vehicle-verified 2026-03-21)** |
| 3 | Victron, Spotify, Weather, Starlink | **Complete** |
| 4 | Power System & Vehicle Install | **Complete** — Witty Pi 5 HAT+ power management; installed in the vehicle and in daily use |
| 5 | Polish | **In Progress** — SQLite persistence, 2D navigation, DTC, throttle calibration, diagnostics, history charts, test suite, OTA update complete; Plymouth splash and boot time optimisation complete from earlier work |

---

## Phase 1 Deliverables (complete)

**Backend**
- FastAPI app with WebSocket hub (`ws_hub.py`, `main.py`)
- Mock data broadcaster — engine, spotify, victron, system topics
- Static files served from `/` (WebSocket at `/ws` matched first)

**Frontend** — five swipeable views (later extended with vertical layers — see "Later additions"), touch-only carousel, 1280×400
- Engine: RPM / Boost / Throttle radial gauges (Canvas Gauges), Battery / Coolant / Air Temp / Fuel Temp stat tiles with colour-coded status dots
- Spotify: album art, track / artist / album, progress bar, prev / play-pause / next controls
- Victron: SoC SVG arc gauge, voltage, current, solar yield, charge-state badge
- Starlink: placeholder
- Settings: WiFi / BT / Override Power / Data Feed status dots, CPU temp, brightness
- WebSocket auto-reconnect (3 s)

**Docker**
- `Dockerfile` — `python:3.11-slim-bookworm` base, mirrors Pi OS Bookworm Python
- `docker-compose.yml` — volume mounts + `--reload` for live dev on Windows
- Run: `docker compose up --build` from repo root, open `http://localhost:8000`

**Pi OS deployment** (`deploy/`)
- `setup.sh` — installs packages, creates venv, installs systemd service, configures console autologin, adds kiosk autostart to `~/.bash_profile`, appends display dtoverlay to `/boot/firmware/config.txt`
- `td5-dash.service` — systemd unit for the FastAPI backend (auto-starts on boot)
- `xinitrc` — Chromium kiosk session (blanking off, cursor hidden, waits for backend)
- Deploy: `git clone` repo onto Pi, then `sudo ./deploy/setup.sh && sudo reboot`

---

## Phase 2 Deliverables (complete — vehicle-verified 2026-03-21)

**Backend: `backend/obd/`**
- `connection.py` — PyFtdi K-Line connection wrapper; fast-init (25ms low), configurable FTDI URL; `recv_frame()` reads and verifies ISO 14230 checksum byte
- `protocol.py` — KWP2000 frame builder with ISO 14230 checksums on all frames, TD5 seed-key LFSR algorithm, per-PID constants (`PID_RPM`, `PID_TEMPS`, `PID_MAP_MAF`, `PID_BATTERY`, `PID_SPEED`, `PID_THROTTLE`, `PID_FUELLING`); `SVC_TESTER_PRESENT` for keepalive
- `decoder.py` — per-PID response parsers with verified formulas:
  - RPM: 16-bit raw = RPM (confirmed)
  - Temps: 16-bit Kelvin×10 → `int16/10.0 - 273.2` °C (confirmed, stride 4 bytes per temp)
  - MAP: 16-bit bar×10000 → `int16/10000.0` bar absolute (confirmed)
  - Battery: 16-bit millivolts → `int16/1000.0` V (confirmed)
  - Speed: single byte = kph (confirmed)
  - Throttle: 16-bit P1 voltage ratiometric against supply (confirmed; pct calibration needs vehicle)
- `service.py` — `TD5Session` (StartDiagnosticSession + SecurityAccess), per-PID poll cycle (`read_local_id(pid)` for each parameter group), blocking poll loop in `ThreadPoolExecutor`, broadcasts `{"type": "engine", ...}` to WebSocket hub; negative response (0x7F) error code decoding
- Controlled by `TD5_MOCK` env var; configurable via `TD5_FTDI_URL`, `TD5_POLL_INTERVAL`

**⚠ Protocol correction (March 2026):** All KWP2000 frames REQUIRE an ISO 14230 checksum byte (sum of all preceding bytes mod 256). Prior code omitted checksums — this was the root cause of all failed vehicle communication attempts. Verified frames: `81 13 F7 81 0C` (StartComm), `02 10 A0 B2` (DiagSession), `02 27 01 2A` (RequestSeed). See `documentation/TD5-ECU-Protocol-Technical-Reference.md`.

**⚠ PID mapping note:** The tech reference documents PID `0x01` returning all 22 fuelling parameters in one response. The individual PIDs (0x09, 0x1A, etc.) are also defined. The diagnostic tool (`tools/td5_pc_diag.py`) probes both approaches to determine which this ECU supports.

**Diagnostic tool: `tools/td5_pc_diag.py`**
- Progressive 7-stage verification: USB/FTDI detection → protocol self-test → fast-init → StartComm → DiagSession → SecurityAccess → PID probe → continuous poll
- Run `python td5_pc_diag.py` (software-only) or `python td5_pc_diag.py --vehicle` (full test)
- `--timing-sweep` flag to try a range of fast-init LOW pulse timings
- Logs all TX/RX bytes to timestamped file, `--verbose` for full hex dumps

**Prerequisites for live testing**
- VAG COM KKL 409.1 USB cable with genuine FTDI FT232RL chip connected to vehicle OBD port
- Pi 5 with `pyftdi` able to enumerate the FTDI device
- Vehicle accessible with ignition on

---

## Phase 3 Deliverables (complete)

**Victron BLE — `backend/victron/`**
- `scanner.py` — `VictronScanner` (bleak BLE async scanner), `VictronState` dataclass; decodes SmartShunt (SoC, voltage, current, yield), MPPT 100/30 (charge state, solar yield), Orion XS 12/12-50A (state, input voltage) via `victron-ble` library
- `service.py` — publishes `{"type": "victron", ...}` at 1 Hz; auto-restarts on BLE failure; warns when data is stale (>10s since last BLE advertisement)
- Controlled by `VICTRON_MOCK`; configured via `VICTRON_SHUNT_MAC/KEY`, `VICTRON_MPPT_MAC/KEY`, `VICTRON_ORION_MAC/KEY`

**Spotify — `backend/spotify_service.py` + `backend/spotify_auth.py`**
- `spotify_auth.py` — OAuth2 **Authorization Code** refresh flow (a *user* token with playback/library scopes — NOT client-credentials); access token cached in memory, refreshed on expiry. Refresh token is reconciled env-vs-DB and rotated tokens are persisted to the settings DB. ⚠ Spotify's June 2026 policy expires user refresh tokens 6 months after the original authorization (refreshing does not reset the timer) — on `invalid_grant` the service sets `auth_required` and the UI shows "Spotify Sign-In Required"; recovery is manual re-auth (see `documentation/SPOTIFY-REAUTH.md`)
- `spotify_service.py` — polls `/me/player` at 1 s (playing) or 5 s (idle); broadcasts track, artist, album, art URL, progress, liked status; liked status cached per-track (only calls API on track change)
- **204 handling:** on "no active device" (HTTP 204), re-broadcasts last known payload with `playing: false` so the player stays visible rather than blanking. Falls back to `_DISCONNECTED` only before any track has been received
- **`error` field:** `true` only on genuine auth/network failures (`_ERROR` payload); `false` on normal no-device state (`_DISCONNECTED`). Frontend uses this to show "Spotify Unavailable" vs "No Active Device"
- Playlist browser: `GET /me/playlists`, `GET /playlists/{id}/items` (Spotify Feb 2026 field names: `item` not `track`)
- Like button: `PUT /me/library` with URI param (Spotify Feb 2026 library endpoint)
- `check_track_saved`: `GET /me/library/contains`
- Required scopes: `user-read-playback-state user-modify-playback-state playlist-read-private playlist-read-collaborative user-library-modify user-library-read`
- `tools/spotify_auth_setup.py` — one-time OAuth helper; opens browser, catches callback, prints refresh token; redirect URI must be `http://127.0.0.1:8888/callback` (not localhost — Spotify Feb 2026 requirement)
- Controlled by `SPOTIFY_MOCK`; configured via `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REFRESH_TOKEN`

**Weather — `backend/weather_service.py`**
- Open-Meteo API (free, no key); fetches every 30 min, re-broadcasts cache every 5 s
- Payload: current (temp, humidity, WMO weather code, wind speed) + 4-day forecast
- Stale flag after 5 min without a successful fetch
- Controlled by `WEATHER_MOCK`; configured via `WEATHER_LAT`, `WEATHER_LON`, `WEATHER_LOCATION`

**Starlink — `backend/starlink_service.py`**
- `starlink-grpc-core` library polling `192.168.100.1:9200` (Starlink Mini local API)
- Broadcasts state, throughput, latency, ping drop, obstruction, roaming flag, active alerts
- GPS: originally read from the dish; now superseded by `gps_service.py` (gpsd + USB receiver) — see "Later additions"
- Blocking gRPC I/O in `ThreadPoolExecutor` (same pattern as OBD)
- ⚠ Not yet tested against real Starlink hardware — field names marked `# hw-verify`
- Controlled by `STARLINK_MOCK`; configured via `STARLINK_HOST`, `STARLINK_POLL_INTERVAL`

**Frontend additions**
- Spectrum visualiser: `getUserMedia` → AudioContext → AnalyserNode → 64 log-spaced bars; PulseAudio loopback on Pi (td5_sink), simulation fallback in Docker/dev
- Spotify: playlist browser (card grid → track list), like/heart button with pre-populated liked state, "Play Playlist" button for restricted playlists
- Victron: weather panel embedded in bottom section (WMO emoji icons with color variation selectors, bold temp, wind + humidity)
- Starlink view: three-column layout — (1) state panel with STATUS / OBSTRUCTION / GPS stat tiles + Roaming badge, all top-aligned; (2) "Starlink Stats" section with Download / Upload / Latency / Packet Loss / Obstruction / Uptime tiles; (3) Alerts panel (top-aligned, expands to fill width)

**Deploy additions**
- `setup.sh` — Raspotify install + PulseAudio null-sink loopback (`td5-visualiser.pa`): `module-null-sink` (td5_sink) + `module-loopback` (monitor→real output) + `set-default-source td5_sink.monitor`; sets `LIBRESPOT_BACKEND=pulseaudio` and `LIBRESPOT_PA_SINK=td5_sink` in raspotify conf
- `xinitrc` — `--use-fake-ui-for-media-stream` flag so getUserMedia is auto-granted in kiosk mode without any UI prompt

---

## Phase 3 additions (post-Phase-3 session, March 2026)

**UI fixes**
- Engine stat tiles: status dots now pinned to the right edge of each tile (consistent alignment regardless of value text width)
- Spotify device name: increased from 11px → 15px, brighter color
- Weather location text: increased to 20px to match stat-label style
- Starlink alerts: increased from 13px → 18px; GPS section replaced with a status-dot indicator ("GPS Active" / "No Fix") rather than raw coordinates

**Settings view overhaul** — three columns:
1. Connectivity — 2-col grid of compact stat tiles: Wi-Fi, Bluetooth, Override, Data Feed, Starlink; each with status dot and text value
2. Brightness — "Connectivity" / "Brightness" / "System" section headings in `stat-label` style; Day and Night brightness bars (visual fill, 0–255 range) with +/− buttons; mode toggle. Levels and active mode are stored in the settings DB (`brightness_day` / `brightness_night` / `brightness_mode`); `backlight.apply_on_startup()` sets the real backlight from them at service start, so the screen is correct from boot. Changes are saved via `POST /settings` then applied via `/system/brightness`
3. Pi Health — CPU Temp, CPU Load, RAM, Disk as compact 2-col stat tile grid with colour-coded dots; Uptime and Throttle tiles; Controls section with amplifier relay toggle

**New backend services**
- `system_service.py` — reads real CPU temp (`/sys/class/thermal/thermal_zone0/temp`), Wi-Fi state (`/sys/class/net/wlan0/operstate`), BT state (`rfkill`); falls back gracefully in Docker; includes `override_mode` and `sidelights` from `shared_state`; replaces permanent `mock_system_loop` (toggle via `SYSTEM_MOCK=1` if needed)
- *(removed 2026-08)* `carpihat_service.py` and `ignition_service.py` — dormant, never imported; power is handled by `wittypi_service.py`. See git history for the old CarPiHAT pin map
- `shared_state.py` — module-level shared state between services: `gps_lat`, `gps_lon` and speed/heading/fix (written by `gps_service`); `override_mode`, `sidelights_on` (reserved for the Witty Pi override hook)

**GPS → weather integration**
- `gps_service.py` updates `shared_state.gps_lat/lon` when a GPS fix arrives
- `weather_service.py` uses live GPS coordinates for the next fetch, falling back to `WEATHER_LAT/LON` env vars when no fix is available

**New REST endpoints**
- `POST /system/brightness {"value": 0–255}` — `backlight.apply()` scales to the panel's `max_brightness` and writes `/sys/class/backlight/*/brightness`; needs write permission on the sysfs file (fresh installs get a udev/`video`-group rule from `setup.sh`; an existing Pi that already changes brightness from the UI needs nothing); reports `ok: false` in Docker or when the write is refused. `GET /system/backlight` reports whether control works and drives the amber note on the Settings screen
- `POST /system/relay {"name": "amp", "state": bool}` — self-contained no-op that only logs; no relay hardware is wired

**OBD decoder correction** — complete rewrite of `decoder.py` and `service.py`:
- Confirmed TD5 uses separate per-PID requests (not one frame)
- All formulas corrected per Ekaitza_Itzali / LRDuinoTD5 source verification
- `service.py` now calls `read_local_id(pid)` for each PID group per poll cycle

**Pi kiosk setup (2026-03-22/23)**
- Display: X11 output is `DSI-2` (not DSI-1); xrandr rotation in `deploy/xinitrc` reads `DISPLAY_ROTATION` from `.env`
- Touch: Goodix Capacitive TouchScreen via evdev driver; coordinate transform in `/etc/X11/xorg.conf.d/40-touch-rotation.conf` (option name is `TransformationMatrix`, not `CalibrationMatrix` — evdev, not libinput)
- Cursor: hidden via CSS `cursor: none !important` on universal selector + `unclutter -idle 0 -root`
- Plymouth: retain-splash override keeps logo on screen until X takes over; `prepare_logo.py` pre-rotates logo for unrotated framebuffer; initramfs must be copied to `/boot/firmware/initramfs_2712` after rebuild
- Boot transition: `setterm --foreground black --clear all` in `.bash_profile`; `loglevel=0 logo.nologo vt.global_cursor_default=0` in cmdline.txt; `.hushlogin` suppresses login banner; `xsetroot -solid black` + `--force-dark-mode` for X/Chromium
- systemd service: uses `EnvironmentFile=` to read `.env` (not hardcoded `Environment=` lines)

---

## Next-Phase Implementation (March 2026)

**Configuration architecture**
- SQLite database (`db.py`) with `settings`, `pages`, and `engine_history` tables; REST endpoints (`GET/POST /settings`, `GET/POST /pages`, `GET /history`); `DEV_MODE` support for development overrides

**Two-dimensional navigation**
- Vertical layers within views: Engine (4 layers), Settings (4 layers); axis-locked touch gestures prevent diagonal swipes; `navigateTo()` API for programmatic view/layer transitions

**CarPiHAT deprecation**
- Removed all runtime references to `carpihat_service.py`; the file and the `ignition_service.py` stub were later deleted (2026-08) — power is the Witty Pi

**Test framework**
- 210 pytest tests (run `python -m pytest -q` from the repo root; ~12 s) covering decoder vectors, protocol checksums, WS hub, mock schema, update/rollback, backlight, system service, Victron staleness and the REST endpoints; `pytest.ini` configuration; see `tests/`

**DTC fault codes**
- `dtc_lookup.py` — TD5 DTC fault code dictionary; faults tile replaces fuel temp on Engine layer 0; DTC detail on Engine layer 1 with clear button (`POST /obd/clear-dtc`); Raw Data on Engine layer 3

**Throttle calibration**
- Linear mapping in decoder; Setup wizard with Set Idle / WOT buttons persisted to SQLite settings

**WebSocket reconnect resync**
- Cached state sent on new WebSocket connection so late-joining clients receive current data immediately

**Spotify fixes**
- Backoff broadcasts during HTTP 429 rate limiting; like button 5s hold suppression to prevent duplicate API calls

**Health check**
- `/health` endpoint returns per-service status; `docker-compose.yml` healthcheck configured; Diagnostics screen on Settings layer 3

**Trip computer**
- In-memory session peaks/averages displayed on Engine layer 1

**Coolant trend indicator**
- Dynamic dot/arrow with colour thresholds showing coolant temperature trend direction

**Engine history**
- SQLite `engine_history` table with periodic inserts; `/history` endpoint with `?time_range=hour|day|week|month|year|all`; Canvas line charts on Engine layer 2

**Reverse geocoding** — *not implemented.* An early plan called for Nominatim; no such code exists. The weather location label is a static string (`WEATHER_LOCATION` / settings DB) and does not follow GPS

**OTA update**
- `POST /system/update` (git pull + pip install + restart) and `POST /system/restart` (restart service only) endpoints; frontend Update button on Settings view

---

## Later additions (June–October 2026)

Work done after the original phases. Specs and plans for most of it live in `docs/superpowers/`.

**GPS** — `gps_service.py` reads gpsd (USB receiver) and publishes the `gps` topic; it also writes `shared_state` (lat/lon/speed/heading/fix), which `weather_service` uses for its next fetch. Configured by `GPS_MOCK`, `GPSD_HOST`, `GPSD_PORT`. The Starlink dish no longer supplies GPS. The Starlink view's layer 1 is a GPS diagnostics screen.

**Power** — `wittypi_service.py` (Witty Pi 5 HAT+, `WITTYPI_ENABLED=1`) watches VIN over I2C and requests a clean shutdown when it drops below `WITTYPI_VIN_SHUTDOWN_THRESHOLD_MV`. The `main.py` teardown stops Doom mode first. This replaces the CarPiHAT and the discrete ignition circuit (both deleted).

**Navigation layers** — Engine has 4 layers (live, detail/trip/faults, history charts, raw data). Victron layer 1 is detailed SmartShunt/MPPT/Orion stats. Starlink layer 1 is GPS diagnostics. Settings: 0 = connectivity / brightness / Pi health / speed units, 1 = Software (version, Check for Updates, Roll Back), 2 = page visibility, 3 = setup (throttle calibration), 4 = diagnostics (service health, shutdown). Layers can be hidden via the `pages` table.

**Faults** — tappable fault-code library (`GET /obd/dtc/{code}`). `POST /obd/clear-dtc` is honest about being unimplemented (no verified clear frame exists).

**Speed units** — kph/mph toggle on Settings, stored as the `speed_unit` setting.

**OTA update + rollback** — `update_service.py`: Update = `git pull` + `pip install` + restart. Each update pushes the previous commit hash onto `update_history` (db.py); Rollback runs `git reset --hard` to it. This assumes `origin/main` is never rebased or force-pushed. `backend/requirements.txt` pins minimums only, so a rollback does not restore old dependency versions. An update clears the Chromium profile, so browser-side state (localStorage) resets.

**Brightness** — `backlight.py`: day/night levels and mode live in the settings DB, applied to the real backlight at service start and on change. `GET /system/backlight` reports whether the sysfs write is possible and drives an amber note on Settings when it is not.

**Doom mode** — `game_service.py`, `games/doom/` (lzdoom, `launcher.sh`, touch `overlay.py`), `frontend/game.html`. Starting it pauses Spotify, SIGSTOPs the Chromium tree and launches lzdoom; stopping restores both. Default WAD is FreeDoom; users can supply their own in `wads/`. Tag `v-doom-2p-stable` marks a known-good two-player state.

**Boot** — the logo is held through the black gap between Plymouth and X (see `documentation/boot-splash-investigation.md`).

**Parked: OTA system provisioning** — branch `ota-system-provisioning` (pushed, not merged). A Software → System panel plus a root provisioner, so apt packages and system config could be changed from the UI. Parked because it needs a one-time root action (a sudoers rule) that the Update button cannot perform. Read its design spec (`docs/superpowers/specs/2026-08-01-ota-system-provisioning-design.md` on that branch), especially §12, before merging.

**Hardware / access** — the Pi boots from an **M.2 SSD** (not an SD card). The vehicle Pi has no permanent keyboard or screen; shell access means sitting in the cab with a laptop over SSH, which is deliberately rare. Prefer changes that ship through the Update button.

---

## Project Structure

```
TD5-Dash/
├── CLAUDE.md
├── README.md
├── Dockerfile
├── docker-compose.yml
├── .dockerignore
├── .env                    # local secrets — not committed
├── .env.example            # documented reference for all env vars
├── LR-Logo.png             # Land Rover badge source image (used by Plymouth splash)
├── docs/                   # process artifacts: plans, specs, todo lists, review notes
│   ├── superpowers/specs/  # design specs (doom mode, OTA rollback, GPS/Witty Pi, OBD diag)
│   ├── superpowers/plans/  # matching implementation plans
│   ├── cleanup/            # housekeeping review + removal proposal (completed)
│   └── td5-dash-todo*.md   # driving todo lists, rounds 1 and 2
├── documentation/          # reference: SPEC, pi-setup, protocol docs, Starlink, Home Assistant, Spotify re-auth, driver-profiles proposal
├── backend/
│   ├── main.py             # FastAPI app — lifespan tasks, WS endpoint, REST endpoints
│   ├── ws_hub.py           # WebSocket connection manager
│   ├── db.py               # SQLite settings/pages/engine_history/update_history database
│   ├── mock_service.py     # Static mock data for the mockable topics
│   ├── shared_state.py     # Module-level shared state (GPS fix, override/sidelights state)
│   ├── system_service.py   # Real CPU temp, Wi-Fi/BT monitoring
│   ├── backlight.py        # Backlight sysfs write + boot-time apply of saved brightness
│   ├── update_service.py   # OTA update/rollback orchestration (git + pip + restart)
│   ├── game_service.py     # Doom mode lifecycle + /system/game-mode/* endpoints
│   ├── gps_service.py      # gpsd client — publishes the "gps" topic, feeds shared_state
│   ├── wittypi_service.py  # Witty Pi 5 power management (WITTYPI_ENABLED=1)
│   ├── spotify_auth.py     # OAuth token manager (refresh + in-memory cache)
│   ├── spotify_service.py  # Spotify Web API polling + playlist/command/like
│   ├── weather_service.py  # Open-Meteo polling + stale detection (uses GPS coords)
│   ├── starlink_service.py # Starlink gRPC status polling
│   ├── obd/                # TD5 K-Line OBD service
│   │   ├── connection.py   # PyFtdi K-Line connection + fast-init
│   │   ├── protocol.py     # KWP2000 frame builder + TD5 seed-key algorithm
│   │   ├── decoder.py      # Live data frame parser
│   │   ├── dtc_lookup.py   # TD5 DTC fault code dictionary + fault library
│   │   ├── service.py      # Session management + poll loop
│   │   └── pi_diag.py      # 7-stage on-Pi diagnostic (POST /obd/full-test)
│   ├── victron/            # Victron BLE service
│   │   ├── scanner.py      # BLE scanner + VictronState dataclass
│   │   └── service.py      # Publish loop
│   └── requirements.txt
├── frontend/
│   ├── index.html          # Single-page app — all views and layers
│   ├── game.html           # Doom mode page
│   ├── style.css           # All styles — dark theme, 1280×400 layout
│   ├── app.js              # WS client, view handlers, carousel, visualiser, browse
│   └── lib/
│       └── gauge.min.js    # Canvas Gauges v2.1.7 (local, no CDN)
├── games/doom/             # lzdoom binary + pk3s, launcher.sh, touch overlay, per-player ini (27 MB tracked)
├── wads/                   # user-supplied WADs (FreeDoom is the default)
├── tools/
│   ├── spotify_auth_setup.py  # One-time OAuth helper — run locally
│   └── td5_pc_diag.py         # Laptop-side TD5 ECU diagnostic — progressive K-Line verification
├── data/                   # Runtime SQLite database + logs (gitignored, created at startup)
├── pytest.ini              # Pytest configuration
├── tests/                  # 210 tests — roughly one test_*.py per backend module, plus endpoint tests
└── deploy/
    ├── setup.sh            # Pi first-time setup script (run as root)
    ├── td5-dash.service    # systemd unit template
    ├── xinitrc             # Chromium kiosk X session
    └── plymouth/
        ├── prepare_logo.py     # Logo resize + rotate for Plymouth splash
        └── td5-dash/
            ├── td5-dash.plymouth   # Theme metadata
            └── td5-dash.script     # Splash animation script
```

---

## Key Constraints

- **No OBD-II standard PIDs** — TD5 uses proprietary K-Line protocol (KWP2000 at 10400 baud with ISO 14230 checksums). Confirmed working on vehicle 2026-03-21. See `documentation/TD5-ECU-Confirmed-Protocol.md`
- **No ELM327** — will not work with this ECU; requires VAG COM KKL cable with genuine FTDI FT232RL for bitbang fast-init
- **P3 inter-message gap (55ms)** — mandatory when engine is running; ECU drops requests without it
- **ECU session persistence** — session survives fast-init pulses; must send StopCommunication (0x82) to clear leftover sessions
- **Boot target** — <10 seconds to kiosk live (Phase 5 concern)
- **Pi 5 has no 3.5mm jack** — audio via BT A2DP or a USB DAC
- **Spotify API (Feb 2026)** — library endpoints unified under `/me/library`; redirect URI must use `127.0.0.1` not `localhost`; playlist item field is `item` not `track`
