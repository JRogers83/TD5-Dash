# Starlink Mini — Setup Guide for TD5 Dash

This guide covers everything needed to connect a Starlink Mini to the TD5 Dash and get live connectivity stats, obstruction data, and GPS location feeding through to the display.

---

## What you get

Once set up, the Starlink view on the display shows:

- **Connection state** — Online / Offline / Searching / Booting
- **Throughput** — real-time download and upload in Mbps
- **Latency** — round-trip to Starlink PoP in milliseconds
- **Packet loss** — ping drop percentage
- **Obstruction** — historical sky obstruction percentage (amber if >0%)
- **Uptime** — time since last dish boot
- **Alerts** — active hardware alerts (thermal, motors, etc.)
- **GPS** — live coordinates fed to the weather panel for location-accurate forecasts

### What is NOT available

The local API only exposes hardware-level data. Some alerts in the Starlink app come from SpaceX's cloud servers and are not accessible locally:

| | Available |
|---|---|
| Obstruction %, throughput, latency | ✅ |
| Hardware alerts (thermal, motors stuck, etc.) | ✅ |
| GPS coordinates | ✅ (requires one-time opt-in — see below) |
| "Misaligned" / "Low speed plan" / service alerts | ❌ Cloud only |

---

## Requirements

- **Starlink Mini** — the integrated Wi-Fi router must be active (not bypass mode), or see Ethernet section below
- **Raspberry Pi 5** running TD5 Dash
- **Starlink mobile app** (iOS or Android) — needed once to enable GPS

---

## Step 1 — Connect the Pi to the Starlink Mini

### Option A: Wi-Fi (simplest for a vehicle install)

Connect the Pi's Wi-Fi to the Starlink Mini's built-in Wi-Fi network.

```bash
# On the Pi, use nmcli or raspi-config to connect
sudo nmcli device wifi connect "YourStarlinkSSID" password "YourPassword"
```

The Mini's management API is at `192.168.100.1:9200` and is natively reachable once you're on its Wi-Fi. No additional routing needed.

> **Note:** When the Pi is on Starlink's Wi-Fi it uses Starlink for internet access. This is exactly what you want — the weather service will also work via Starlink.

### Option B: Ethernet (with integrated router active)

Connect the Pi's Ethernet port to the Mini's Ethernet port (or through a switch on the same network). The `192.168.100.1` address is still reachable.

### Option C: Bypass mode (Ethernet modem-only)

With bypass mode enabled, the Mini acts as a pure modem — its integrated router is disabled and the Ethernet port provides a direct WAN connection. `192.168.100.1` is still accessible but requires a static route:

```bash
# Add on the Pi (or your router) to reach the dish management IP
sudo ip route add 192.168.100.0/24 dev eth0

# To make it persist across reboots, add to /etc/rc.local or a systemd service
```

---

## Step 2 — Verify connectivity

Before touching any config, confirm the Pi can reach the Starlink API:

```bash
# Install grpcurl if needed
sudo apt install -y grpcurl

# Test the dish API
grpcurl -plaintext -d '{"get_status":{}}' \
  192.168.100.1:9200 SpaceX.API.Device.Device/Handle
```

You should see a JSON response with `state`, throughput values, and other status fields. If this times out, the routing isn't working — revisit Step 1.

Alternatively with Python (the library used by TD5 Dash):

```bash
cd ~/TD5-Dash
.venv/bin/python3 -c "
import starlink_grpc
ctx = starlink_grpc.ChannelContext(target='192.168.100.1:9200')
s, o, a = starlink_grpc.status_data(ctx)
print('State:', s.get('state'))
print('Down:', round(s.get('downlink_throughput_bps', 0) / 1e6, 1), 'Mbps')
print('Obstruction:', round(s.get('fraction_obstructed', 0) * 100, 1), '%')
"
```

---

## Step 3 — Enable GPS (one-time, requires the Starlink app)

The Starlink Mini has a built-in GPS receiver. GPS data is disabled by default and must be opted in via the app — this only needs to be done once, and the setting persists across reboots. Below is details of what I did, I believe this has changed in the past so it may change again. A bit of hunting in the app and you should find the debug data screen.

Make sure your phone is connected to the Starlink Mini's Wi-Fi, then follow these steps:

1. Open the **Starlink app**
2. Tap the **hamburger menu** (☰) in the top-left corner to open the full menu
3. In the bottom-right of that screen, tap the **eye icon** (ⓘ) for information
4. At the bottom of that screen, tap **Debug Data**
5. Scroll to the bottom — the **STARLINK LOCATION** section is second from the bottom
6. Toggle **"Allow access on local network"** → ON

You can also confirm the setting is active here — the current latitude and longitude reported by the dish are shown directly beneath the toggle.

Verify it's working:

```bash
cd ~/TD5-Dash
.venv/bin/python3 -c "
import starlink_grpc
ctx = starlink_grpc.ChannelContext(target='192.168.100.1:9200')
try:
    loc = starlink_grpc.location_data(ctx)
    print('GPS:', loc)
except Exception as e:
    print('GPS not available:', e)
"
```

You should see latitude, longitude, and altitude. If you get an error, GPS sharing is not yet enabled in the app.

Once GPS is active, TD5 Dash automatically:
- Displays GPS status on the Starlink view
- Uses live coordinates for the weather panel (updates every 5 minutes via Nominatim reverse geocoding)
- Updates `weather_lat`, `weather_lon`, and `weather_location` in the settings database

---

## Step 4 — Configure TD5 Dash

Edit `~/TD5-Dash/.env` and set:

```bash
STARLINK_MOCK=0
WEATHER_MOCK=0

# Optional — override the default Starlink API address
# STARLINK_HOST=192.168.100.1:9200

# Optional — fallback weather location if GPS is not enabled
# WEATHER_LAT=51.5074
# WEATHER_LON=-0.1278
# WEATHER_LOCATION=London, UK
```

> **`WEATHER_LAT` / `WEATHER_LON` / `WEATHER_LOCATION`** are the fallback coordinates used before GPS has provided a fix. Once the Starlink GPS is active, the live coordinates take over automatically.

Restart the service to pick up the changes:

```bash
sudo systemctl restart td5-dash
```

---

## Step 5 — Enable the Starlink view on the display

The Starlink page visibility can be toggled from the Settings screen:

1. Navigate to **Settings → Page Visibility**
2. Toggle **Starlink** on
3. Press **Restart Now** — the display reloads and the Starlink view becomes accessible by swiping

---

## Behaviour notes

### Obstruction display

The obstruction tile shows the **historical sky obstruction fraction** — the percentage of the sky view that has been blocked over the observation window (typically the last few hours). This matches the obstruction map in the Starlink app.

- **Green + "Clear"** — no recorded obstruction
- **Amber + "X%"** — historical obstruction (matches app map)
- **Red + "X%"** — dish is actively blocked right now

A vehicle mount will almost always show some obstruction percentage because trees, buildings, and terrain block parts of the sky during travel. Values under ~10% generally have no meaningful impact on performance.

### Weather loading at boot

The weather service retries every 30 seconds on startup until it gets a successful response from Open-Meteo. If Starlink takes a moment to acquire a satellite lock after boot, the weather panel will load automatically once connectivity is established — typically within 30–60 seconds of boot completing.

### Alert types

Hardware alerts (thermal throttle, motors stuck, dish heating, etc.) appear in the Alerts panel when active. Alerts shown in the Starlink mobile app that relate to your account or service plan (e.g. low speed plan, misaligned location) come from SpaceX's cloud servers and are not accessible via the local API — they will not appear on the TD5 Dash display.

---

## Troubleshooting

### Starlink view shows "Offline"

The dish is unreachable. Check:

```bash
ping 192.168.100.1
```

If this fails, the Pi is not on the Starlink network. Reconnect to the Starlink Wi-Fi or check the static route (bypass mode).

### GPS shows "No Fix"

Either GPS sharing is not enabled in the app (see Step 3), or the dish hasn't yet established a satellite lock (wait ~60 seconds after powering on).

### Weather panel loading slowly

Normal at first boot — the weather service starts fetching immediately but Open-Meteo requires internet access. If Starlink takes time to acquire a satellite on boot, the first fetch will be delayed. After the first success, data is cached and displayed within 5 seconds of connecting.

### Obstruction shows 0% but the app shows obstructions

Ensure you have run `git pull` and restarted the service after April 2026. Earlier versions read the obstruction value from the wrong API field and always returned 0%.

---

## Technical reference

- **API address:** `192.168.100.1:9200` (gRPC, HTTP/2, no authentication)
- **Python library:** `starlink-grpc-core` (PyPI) — `sparky8512/starlink-grpc-tools` on GitHub
- **Poll interval:** 2 seconds (configurable via `STARLINK_POLL_INTERVAL` in `.env`)
- **GPS poll interval:** every 2 seconds (same poll cycle)
- **Reverse geocode interval:** every 5 minutes maximum (Nominatim rate limit)

For the full API reference including all available data fields, see `documentation/starlink-mini-local-api.md`.

---

*Tested on Starlink Mini, firmware 2026.03.15, with Raspberry Pi 5 running Pi OS Bookworm.*
*Last updated: April 2026*
