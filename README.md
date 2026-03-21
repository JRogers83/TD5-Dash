# TD5 Dash

A custom in-vehicle display for a Land Rover Defender TD5, mounted in the dashboard ashtray opening. Raspberry Pi 5 running a full-screen Chromium kiosk on a Waveshare 7.9" bar-format DSI touchscreen (1280×400 landscape).

Five swipeable views: engine gauges, Spotify, Victron battery/solar, Starlink connectivity, and system settings.

For full hardware specification, mounting details, and vehicle context see [`documentation/SPEC.md`](documentation/SPEC.md).

---

## Views

| # | View | Content |
|---|------|---------|
| 1 | **Engine** | RPM, boost, throttle gauges · battery voltage, coolant, air and fuel temps |
| 2 | **Spotify** | Album art · track / artist / album · progress bar · prev/play/next · playlist browser |
| 3 | **Victron** | Battery SoC arc · voltage, current, solar yield, DC-DC charger · weather panel |
| 4 | **Starlink** | Status / obstruction / GPS tiles · download / upload / latency / packet loss · alerts |
| 5 | **Settings** | Connectivity status · day/night brightness · CPU temp / load / RAM / disk · uptime |

Navigate by swiping left/right. Wraps continuously.

---

## Prerequisites

- **Docker Desktop** — for local development (no Pi needed)
- **Python 3.10+** — only needed to run the one-time Spotify auth tool
- **Git**

---

## Quick Start (Docker)

```bash
git clone <repo-url>
cd TD5-Dash
cp .env.example .env
docker compose up --build
```

Open `http://localhost:8000`. All views run on mock data by default — no hardware or credentials required.

To stop:
```bash
docker compose down
```

Changes to `backend/` and `frontend/` files are picked up live without a rebuild (volume-mounted + `--reload`).

---

## Spotify Setup

Spotify requires real credentials even in Docker because the Spotify Web API has no sandbox mode. Everything else can stay mocked while you work on Spotify.

### Step 1 — Create a Spotify Developer app

1. Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) and log in
2. Click **Create app**
3. Give it any name and description
4. Under **Redirect URIs**, add: `http://127.0.0.1:8888/callback`
5. Save. You will land on the app overview page.

### Step 2 — Copy Client ID and Client Secret

From the app overview page, copy:
- **Client ID** — visible immediately
- **Client Secret** — click **View client secret**

Paste both into `.env`:

```
SPOTIFY_CLIENT_ID=your_client_id_here
SPOTIFY_CLIENT_SECRET=your_client_secret_here
```

### Step 3 — Obtain the refresh token

Run the one-time auth helper **on your local machine** (not inside Docker). It opens a browser window.

```bash
# Install httpx if you don't already have it
pip install httpx

# Windows
set SPOTIFY_CLIENT_ID=your_client_id
set SPOTIFY_CLIENT_SECRET=your_client_secret
python tools/spotify_auth_setup.py

# macOS / Linux
SPOTIFY_CLIENT_ID=your_client_id \
SPOTIFY_CLIENT_SECRET=your_client_secret \
python tools/spotify_auth_setup.py
```

A browser window opens at the Spotify login/authorisation page. Log in with the account you want the dashboard to control and click **Agree**.

The script catches the redirect and prints:

```
======================================================
SUCCESS — add this to deploy/td5-dash.service:

  SPOTIFY_REFRESH_TOKEN=AQD...long_token_here...

======================================================
```

Paste the token into `.env`:

```
SPOTIFY_REFRESH_TOKEN=AQD...long_token_here...
```

### Step 4 — Enable live Spotify in Docker

In `.env`, set:

```
SPOTIFY_MOCK=0
```

### Step 5 — Start playing something

Start playback on any Spotify device (phone, laptop, etc.). Then:

```bash
docker compose up --build
```

Navigate to the Spotify view. Within a few seconds the track name, artist, album art, and progress bar should appear. The prev/play/next buttons and the playlist browser are live.

> **If the Spotify view shows "No Active Device":** nothing is actively playing on your Spotify account. Press play on your phone and the dashboard will pick it up within 5 seconds. If it shows "Spotify Unavailable", check your credentials in `.env`.

---

## Pi Deployment

### Hardware required

- Raspberry Pi 5 (4 GB recommended)
- Waveshare 7.9" DSI touchscreen — `dtoverlay=vc4-kms-dsi-waveshare-panel,7_9_inch`
- VAG COM KKL 409.1 USB cable with genuine FTDI chip (for OBD / engine data)
- microSD card or NVMe SSD for boot

See [`documentation/SPEC.md`](documentation/SPEC.md) for the full hardware list, sourcing, and mounting instructions.

### 1 — Flash Pi OS Lite (Bookworm, 64-bit)

Use the Raspberry Pi Imager. Select **Raspberry Pi OS Lite (64-bit)**. Under advanced settings:
- Set a hostname, e.g. `td5dash`
- Enable SSH with a password or public key
- Configure Wi-Fi if needed

Write the card/SSD and boot the Pi.

### 2 — Clone the repository

SSH into the Pi:

```bash
ssh pi@td5dash.local   # or whatever hostname / IP you set
```

Then:

```bash
git clone <repo-url> /home/pi/TD5-Dash
cd /home/pi/TD5-Dash
```

### 3 — Run the setup script

```bash
sudo ./deploy/setup.sh
```

This script:
- Installs system packages (`chromium-browser`, `xinit`, `unclutter`, etc.)
- Creates a Python virtualenv at `.venv/` and installs `backend/requirements.txt`
- Installs and enables the `td5-dash` systemd service
- Configures Chromium kiosk autostart on tty1 login
- Enables console autologin for the service user
- Adds the Waveshare display `dtoverlay` to `/boot/firmware/config.txt`
- Installs and configures Raspotify (Spotify Connect audio device, named **Defender**)
- Creates a PulseAudio null sink (`td5_sink`) and loopback so the spectrum visualiser can capture Raspotify's audio output via `getUserMedia`

To use a different device name for Raspotify:

```bash
DEFENDER_DEVICE_NAME="Land Rover" sudo ./deploy/setup.sh
```

### 4 — Configure credentials

Edit the systemd service file to add your credentials:

```bash
sudo nano /etc/systemd/system/td5-dash.service
```

Fill in the blank `Environment=` lines:

```ini
Environment=SPOTIFY_CLIENT_ID=your_client_id
Environment=SPOTIFY_CLIENT_SECRET=your_client_secret
Environment=SPOTIFY_REFRESH_TOKEN=your_refresh_token

Environment=WEATHER_LAT=52.6309
Environment=WEATHER_LON=1.2974
Environment=WEATHER_LOCATION=Norwich, UK

# Victron BLE — MAC address and encryption key for each device
# Leave blank to skip that device
Environment=VICTRON_SHUNT_MAC=aa:bb:cc:dd:ee:ff
Environment=VICTRON_SHUNT_KEY=0123456789abcdef0123456789abcdef
```

Then reload and restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart td5-dash
```

Check the service is running:

```bash
sudo systemctl status td5-dash
sudo journalctl -u td5-dash -f
```

### 5 — Reboot

```bash
sudo reboot
```

On boot, the Pi logs in automatically, waits for the backend service to start, then opens Chromium in kiosk mode. The dashboard should appear within 10 seconds of power-on.

---

## Victron BLE Setup

Finding your device keys:

1. Open the **VictronConnect** app on your phone
2. Connect to each device (SmartShunt, MPPT, Orion XS)
3. Tap the three-dot menu → **Product info**
4. If **Encryption key** is not visible, enable **Instant readout** first, then come back
5. Copy the MAC address and 32-character hex encryption key for each device

Add them to the service file (or `.env` for Docker) as described above.

---

## Starlink Integration

The dashboard reads live stats from a Starlink Mini via its local gRPC API (`192.168.100.1:9200`). No authentication required on LAN.

**Network assumption:** the Pi connects directly to the Starlink Wi-Fi. If the Pi is on a different network, the gRPC call will time out and the Starlink view will show offline data.

GPS data from Starlink appears in the Starlink view once enabled in the Starlink app (**Settings → Advanced → Debug data → GPS**).

To disable Starlink polling and use mock data:
```
STARLINK_MOCK=1
```

---

## OBD / Engine Data

The TD5 ECU uses a proprietary K-Line protocol (ISO 14230 / KWP2000 at 10400 baud) — not standard OBD-II. ELM327 adapters will not work.

### Hardware

A **VAG COM KKL 409.1 USB cable** with a genuine FTDI FT232RL chip is required. The cable's built-in level shifter converts between the FTDI's TTL and the K-Line's 12V signalling. PyFtdi's bitbang mode drives the fast-init pulse — ordinary serial libraries cannot do this.

**Windows prerequisite:** PyFtdi requires the libusbK driver. Use [Zadig](https://zadig.akeo.ie) to replace the default FTDI VCP driver (Options > List All Devices > select FT232R > set driver to libusbK > Replace Driver). The cable will no longer appear as a COM port — this is expected.

### Diagnostic tool

A standalone diagnostic tool verifies the full communication chain without running the backend:

```bash
# Software-only (no cable needed) — verifies protocol code
python tools/td5_diag.py

# Full vehicle test (ignition ON or engine running, cable connected)
python tools/td5_diag.py --vehicle --verbose

# Try a range of fast-init timings if the default doesn't connect
python tools/td5_diag.py --vehicle --verbose --timing-sweep
```

The tool runs 7 progressive stages: USB/FTDI detection, protocol self-test, fast-init + StartCommunication, StartDiagnosticSession, seed-key authentication, PID probe, and continuous polling. Results are logged to a timestamped text file in `tools/`.

### Protocol documentation

Full confirmed protocol details (frame format, checksums, timing, PID decoding, session trace) are in [`documentation/TD5-ECU-Confirmed-Protocol.md`](documentation/TD5-ECU-Confirmed-Protocol.md). The broader technical reference from open-source research is in [`documentation/TD5-ECU-Protocol-Technical-Reference.md`](documentation/TD5-ECU-Protocol-Technical-Reference.md).

---

## Configuration Reference

All variables are documented in [`.env.example`](.env.example). The table below summarises the mock toggles, which control whether each service uses real hardware/credentials or static placeholder data.

| Variable | Default | Description |
|----------|---------|-------------|
| `TD5_MOCK` | `1` | `0` = poll engine via KKL cable; `1` = mock gauges |
| `VICTRON_MOCK` | `1` | `0` = scan Victron BLE devices; `1` = mock Victron |
| `STARLINK_MOCK` | `1` | `0` = read Starlink gRPC; `1` = mock Starlink |
| `WEATHER_MOCK` | `1` | `0` = fetch Open-Meteo API; `1` = mock weather |
| `SPOTIFY_MOCK` | `1` | `0` = poll Spotify Web API; `1` = mock player |

The Pi systemd service sets all of these to `0`. Docker defaults all to `1` so development works without any hardware or credentials. Override individual variables in `.env` to bring up one real service at a time.

---

## Project Structure

```
TD5-Dash/
├── .env                    # Local secrets — never committed (copy from .env.example)
├── .env.example            # Documented reference for all environment variables
├── .gitignore
├── docker-compose.yml      # Development environment
├── Dockerfile
│
├── backend/
│   ├── main.py             # FastAPI app — WebSocket hub + REST endpoints
│   ├── ws_hub.py           # WebSocket connection manager
│   ├── mock_service.py     # Static mock data for all topics
│   ├── spotify_auth.py     # OAuth token manager (refresh + cache)
│   ├── spotify_service.py  # Spotify Web API polling + playlist/command functions
│   ├── weather_service.py  # Open-Meteo polling service
│   ├── starlink_service.py # Starlink gRPC polling service
│   ├── obd/                # TD5 K-Line OBD service
│   │   ├── service.py
│   │   ├── protocol.py
│   │   ├── decoder.py
│   │   └── connection.py
│   ├── victron/            # Victron BLE service
│   │   ├── service.py
│   │   └── scanner.py
│   └── requirements.txt
│
├── frontend/
│   ├── index.html          # Single-page app — all five views
│   ├── style.css           # All styles — dark theme, 1280×400 layout
│   ├── app.js              # WebSocket client, view handlers, carousel, browse
│   └── lib/
│       └── gauge.min.js    # Canvas Gauges library (engine view)
│
├── tools/
│   ├── spotify_auth_setup.py  # One-time OAuth helper — run locally to get refresh token
│   └── td5_diag.py            # TD5 ECU diagnostic tool — progressive K-Line verification
│
├── deploy/
│   ├── setup.sh            # Pi first-time setup script (run as root)
│   ├── td5-dash.service    # systemd service template
│   └── xinitrc             # Chromium kiosk X session script
│
└── documentation/
    ├── SPEC.md                                  # Full hardware specification and project planning
    ├── TD5-ECU-Protocol-Technical-Reference.md   # Protocol research from open-source TD5 projects
    └── TD5-ECU-Confirmed-Protocol.md             # Vehicle-verified protocol, PIDs, and session trace
```

---

## WebSocket Message Format

The backend broadcasts per-topic JSON messages. Each view subscribes to one or more topics.

```jsonc
{ "type": "engine",   "data": { "rpm": 850, "coolant_temp_c": 88, "boost_bar": 0.0, ... } }
{ "type": "spotify",  "data": { "connected": true, "playing": true, "track": "...", ... } }
{ "type": "victron",  "data": { "soc_pct": 87, "voltage_v": 13.2, ... } }
{ "type": "weather",  "data": { "current": { "temp_c": 9, "weather_code": 61, ... }, ... } }
{ "type": "starlink", "data": { "state": "connected", "down_mbps": 187, ... } }
{ "type": "gps",      "data": { "lat": 52.4862, "lon": -1.8904, "alt": 142 } }
{ "type": "system",   "data": { "brightness": 180, "cpu_temp_c": 45, ... } }
```

## REST Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/ws` | WebSocket upgrade |
| `POST` | `/spotify/command` | Playback control — `{"action": "play"\|"pause"\|"next"\|"prev"}` |
| `POST` | `/spotify/like` | Save track to Liked Songs — `{"track_id": "..."}` |
| `GET` | `/spotify/playlists` | Current user's playlists |
| `GET` | `/spotify/playlist/{id}/tracks` | Tracks in a playlist |
| `POST` | `/spotify/play` | Play a playlist from a specific track — `{"context_uri": "...", "track_uri": "..."}` |

Interactive API docs available at `http://localhost:8000/docs` when running.

---

## Development Notes

**Adding a new data source**

1. Create `backend/your_service.py` with a `broadcast_loop(manager)` async function
2. Add a mock entry in `mock_service.py`
3. Add the mock toggle in `main.py` following the existing `TD5_MOCK` pattern
4. Add a `case 'your_topic':` handler in the `ws.onmessage` switch in `app.js`

**Updating the Pi without a full reinstall**

```bash
cd /home/pi/TD5-Dash
git pull
.venv/bin/pip install -r backend/requirements.txt
sudo systemctl restart td5-dash
```

The kiosk refreshes automatically when the backend restarts (WebSocket reconnect with 3-second backoff).

---

## Troubleshooting

**Dashboard shows "Offline" in settings**
The WebSocket connection to the backend failed. Check `sudo systemctl status td5-dash` on the Pi. On Docker, check `docker compose logs`.

**Spotify view shows "No Active Device"**
Nothing is actively playing on your Spotify account. Press play on any device and the dashboard will pick it up within 5 seconds. The last track will remain visible in a paused state until you play again.

**Spotify view shows "Spotify Unavailable"**
Credentials are wrong or the API can't be reached. Check `docker compose logs` for `Spotify token refresh failed` or `Spotify poll error`.

**Playlist browser shows "Could not load playlists"**
Same credential issue, or your Spotify account has no playlists. Verify the refresh token is correct and not expired.

**Victron data not appearing (Pi)**
Confirm the Pi's Bluetooth adapter can see the device: `bluetoothctl scan on`. Verify the MAC address and encryption key are correct in the service file.

**Starlink view shows offline**
The Pi cannot reach `192.168.100.1:9200`. Confirm the Pi is connected to the Starlink Wi-Fi network, not another network.

**OBD — ECU not responding (all timings silent)**
Confirm ignition is ON (not just accessory). Re-seat the OBD connector firmly. On Windows, confirm Zadig has swapped the FTDI driver to libusbK. Try cycling ignition OFF for 10+ seconds then ON again — the ECU may be in a security lockout from previous failed attempts.

**OBD — ECU rejects StartCommunication (`7F 81 10`)**
The ECU is stuck in a session from a previous run. The diagnostic tool sends StopCommunication automatically to clear this. If it persists, cycle ignition OFF for 10 seconds.

**OBD — SecurityAccess or PID reads time out with engine running**
The P3 inter-message delay (55ms between ECU response and next request) is required when the engine is running. This is enforced in the current code. If using older code, update from the repository.

**Display not detected at boot**
Confirm the dtoverlay line is in `/boot/firmware/config.txt`:
```
dtoverlay=vc4-kms-dsi-waveshare-panel,7_9_inch
```

---

## Licence

Private project. Not for distribution.
