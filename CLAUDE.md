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
- **Audio routing** — BT A2DP vs CarPiHAT DAC vs USB DAC (hardware pending)
- **Power system** — CarPiHAT PRO 5 (preferred, out of stock) vs DIY discrete

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
{"type": "engine",   "data": {"rpm": 0, "coolant_temp_c": 0, "boost_bar": 0, "throttle_pct": 0, "battery_v": 0, "inlet_air_temp_c": 0, "fuel_temp_c": 0, "road_speed_kph": 0}}
{"type": "victron",  "data": {"soc_pct": 0, "voltage_v": 0, "current_a": 0, "solar_yield_wh": 0, "charge_state": "", "orion_state": "", "orion_input_v": 0}}
{"type": "spotify",  "data": {"connected": false, "playing": false, "error": false, "track": "", "artist": "", "album": "", "album_art_url": null, "progress_s": 0, "duration_s": 0, "device_name": "", "track_id": "", "liked": false}}
{"type": "weather",  "data": {"current": {"temp_c": 0, "humidity_pct": 0, "weather_code": 0, "wind_kph": 0}, "forecast": [], "location": "", "stale": false}}
{"type": "starlink", "data": {"state": "offline", "down_mbps": 0, "up_mbps": 0, "latency_ms": 0, "ping_drop_pct": 0, "obstructed": false, "obstruction_pct": 0, "roaming": false, "uptime_s": 0, "alerts": []}}
{"type": "gps",      "data": {"lat": 0, "lon": 0, "alt": 0}}
{"type": "system",   "data": {"brightness": 0, "cpu_temp_c": 0, "cpu_load_pct": 0, "ram_usage_pct": 0, "disk_usage_pct": 0, "uptime_s": 0, "throttled": false, "wifi_connected": false, "bt_connected": false, "override_mode": false}}
```

---

## REST Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/ws` | WebSocket upgrade |
| `POST` | `/spotify/command` | Playback control — `{"action": "play"\|"pause"\|"next"\|"prev"}` |
| `POST` | `/spotify/like` | Save track to Liked Songs — `{"track_id": "..."}` |
| `GET` | `/spotify/playlists` | Current user's playlists |
| `GET` | `/spotify/playlist/{id}/tracks` | Tracks in a playlist |
| `POST` | `/spotify/play` | Play a playlist — `{"context_uri": "...", "track_uri": "..."}` |
| `POST` | `/system/brightness` | Set backlight brightness — `{"value": 0–255}` (writes sysfs on Pi) |
| `POST` | `/system/relay` | Control a relay — `{"name": "amp", "state": bool}` (GPIO pending CarPiHAT) |

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
| 2 | OBD Integration — TD5 K-Line service | **Complete (untested on vehicle)** |
| 3 | Victron, Spotify, Weather, Starlink | **Complete** |
| 4 | Power System & Vehicle Install | Pending |
| 5 | Polish | Pending |

---

## Phase 1 Deliverables (complete)

**Backend**
- FastAPI app with WebSocket hub (`ws_hub.py`, `main.py`)
- Mock data broadcaster — engine, spotify, victron, system topics
- Static files served from `/` (WebSocket at `/ws` matched first)

**Frontend** — five swipeable views, touch-only carousel, 1280×400
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

## Phase 2 Deliverables (complete — untested on vehicle)

**Backend: `backend/obd/`**
- `connection.py` — PyFtdi K-Line connection wrapper; fast-init (25ms low), configurable FTDI URL
- `protocol.py` — KWP2000 frame builder, TD5 seed-key LFSR algorithm, per-PID constants (`PID_RPM`, `PID_TEMPS`, `PID_MAP_MAF`, `PID_BATTERY`, `PID_SPEED`, `PID_THROTTLE`)
- `decoder.py` — per-PID response parsers with verified formulas:
  - RPM: 16-bit raw = RPM (confirmed)
  - Temps: 16-bit Kelvin×10 → `int16/10.0 - 273.2` °C (confirmed, stride 4 bytes per temp)
  - MAP: 16-bit bar×10000 → `int16/10000.0` bar absolute (confirmed)
  - Battery: 16-bit millivolts → `int16/1000.0` V (confirmed)
  - Speed: single byte = kph (confirmed)
  - Throttle: 16-bit P1 voltage ratiometric against supply (confirmed; pct calibration needs vehicle)
- `service.py` — `TD5Session` (StartDiagnosticSession + SecurityAccess), per-PID poll cycle (`read_local_id(pid)` for each parameter group), blocking poll loop in `ThreadPoolExecutor`, broadcasts `{"type": "engine", ...}` to WebSocket hub
- Controlled by `TD5_MOCK` env var; configurable via `TD5_FTDI_URL`, `TD5_POLL_INTERVAL`

**⚠ Critical structural note:** The TD5 ECU does NOT return all data in one frame. Each parameter group (RPM, temperatures, MAP, battery, speed, throttle) requires a separate ReadDataByLocalIdentifier (0x21) request with its own sub-identifier. Verified against Ekaitza_Itzali, pyTD5Tester, LRDuinoTD5.

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
- `spotify_auth.py` — OAuth2 client-credentials refresh flow; token cached in memory, refreshed on expiry
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
- GPS polling: publishes `{"type": "gps", ...}` when GPS is enabled in Starlink app and has a fix
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
2. Brightness — "Connectivity" / "Brightness" / "System" section headings in `stat-label` style; Day and Night brightness bars (visual fill, 0–255 range) with +/− buttons; mode toggle; active mode POSTed to `/system/brightness`
3. Pi Health — CPU Temp, CPU Load, RAM, Disk as compact 2-col stat tile grid with colour-coded dots; Uptime and Throttle tiles; Controls section with amplifier relay toggle

**New backend services**
- `system_service.py` — reads real CPU temp (`/sys/class/thermal/thermal_zone0/temp`), backlight brightness (`/sys/class/backlight/*/brightness`), Wi-Fi state (`/sys/class/net/wlan0/operstate`), BT state (`rfkill`); falls back gracefully in Docker; includes `override_mode` and `sidelights` from `shared_state`; replaces permanent `mock_system_loop` (toggle via `SYSTEM_MOCK=1` if needed)
- `carpihat_service.py` — GPIO skeleton for CarPiHAT PRO 5; documents pin assignments (IN1=ignition, IN2=override, IN3=sidelights, OUT1=amp relay); implements ignition-off → 30s grace → `systemctl poweroff`; updates `shared_state`; `set_relay(name, state)` called by `/system/relay` endpoint; RPi.GPIO optional (stub mode in Docker)
- `shared_state.py` — module-level shared state between services: `gps_lat`, `gps_lon` (from Starlink GPS); `override_mode`, `sidelights_on` (from CarPiHAT)

**GPS → weather integration**
- `starlink_service.py` updates `shared_state.gps_lat/lon` when a GPS fix arrives
- `weather_service.py` uses live GPS coordinates for the next fetch, falling back to `WEATHER_LAT/LON` env vars when no fix is available

**New REST endpoints**
- `POST /system/brightness {"value": 0–255}` — writes `/sys/class/backlight/*/brightness` on Pi; no-op in Docker
- `POST /system/relay {"name": "amp", "state": bool}` — calls `carpihat_service.set_relay()`; GPIO pending CarPiHAT

**OBD decoder correction** — complete rewrite of `decoder.py` and `service.py`:
- Confirmed TD5 uses separate per-PID requests (not one frame)
- All formulas corrected per Ekaitza_Itzali / LRDuinoTD5 source verification
- `service.py` now calls `read_local_id(pid)` for each PID group per poll cycle

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
├── documentation/
│   ├── SPEC.md
│   └── starlink-mini-local-api.md
├── backend/
│   ├── main.py             # FastAPI app — lifespan tasks, WS endpoint, REST endpoints
│   ├── ws_hub.py           # WebSocket connection manager
│   ├── mock_service.py     # Static mock data for all six topics
│   ├── shared_state.py     # Module-level shared state (GPS coords, CarPiHAT state)
│   ├── system_service.py   # Real CPU temp, backlight, Wi-Fi/BT monitoring
│   ├── carpihat_service.py # GPIO skeleton — ignition/shutdown/relay (CarPiHAT PRO 5)
│   ├── spotify_auth.py     # OAuth token manager (refresh + in-memory cache)
│   ├── spotify_service.py  # Spotify Web API polling + playlist/command/like
│   ├── weather_service.py  # Open-Meteo polling + stale detection (uses GPS coords)
│   ├── starlink_service.py # Starlink gRPC polling + GPS (updates shared_state)
│   ├── obd/                # TD5 K-Line OBD service
│   │   ├── __init__.py
│   │   ├── connection.py   # PyFtdi K-Line connection + fast-init
│   │   ├── protocol.py     # KWP2000 frame builder + TD5 seed-key algorithm
│   │   ├── decoder.py      # Live data frame parser
│   │   └── service.py      # Session management + poll loop
│   ├── victron/            # Victron BLE service
│   │   ├── __init__.py
│   │   ├── scanner.py      # BLE scanner + VictronState dataclass
│   │   └── service.py      # Publish loop
│   └── requirements.txt
├── frontend/
│   ├── index.html          # Single-page app — all five views
│   ├── style.css           # All styles — dark theme, 1280×400 layout
│   ├── app.js              # WS client, view handlers, carousel, visualiser, browse
│   └── lib/
│       └── gauge.min.js    # Canvas Gauges v2.1.7 (local, no CDN)
├── tools/
│   └── spotify_auth_setup.py  # One-time OAuth helper — run locally
└── deploy/
    ├── setup.sh            # Pi first-time setup script (run as root)
    ├── td5-dash.service    # systemd unit template
    └── xinitrc             # Chromium kiosk X session
```

---

## Key Constraints

- **No OBD-II standard PIDs** — TD5 uses proprietary K-Line protocol (pyTD5Tester)
- **No ELM327** — will not work with this ECU
- **Boot target** — <10 seconds to kiosk live (Phase 5 concern)
- **Pi 5 has no 3.5mm jack** — audio via CarPiHAT DAC, BT A2DP, or USB DAC
- **Spotify API (Feb 2026)** — library endpoints unified under `/me/library`; redirect URI must use `127.0.0.1` not `localhost`; playlist item field is `item` not `track`
