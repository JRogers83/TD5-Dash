# GPS Receiver + Witty Pi 5 HAT+ Integration — Design Spec

**Date:** 2026-06-06
**Status:** Approved

---

## Overview

Two independent hardware integrations:

1. **GPS** — Replace the defunct Starlink GPS API with a u-blox UBX-G7020-KT USB GPS receiver via gpsd. Restores lat/lon feed to the weather service; full NMEA payload (including speed and heading) broadcast via WebSocket for future use.

2. **Power Management** — Replace the discrete component shutdown circuit (optoisolator + 7805 + relay) with a Witty Pi 5 HAT+. The existing `ignition_service.py` (discrete path) is retained behind a toggle so neither codebase nor hardware investment is wasted.

---

## Architecture

```
GPS:
  GPS_MOCK=0  →  gps_service.py  →  gpsd socket (GPSD_HOST:GPSD_PORT)
                                  →  shared_state.gps_lat/lon (→ weather_service)
                                  →  broadcast {"type":"gps", "data":{...}}

  GPS_MOCK=1  →  mock_service.mock_gps_loop  →  same broadcast path

Power:
  WITTYPI_ENABLED=1  →  wittypi_service.py registers /system/shutdown-prepare endpoint
  WITTYPI_ENABLED=0  →  ignition_service.py (unchanged; active when IGNITION_SENSE_PIN set)

  Runtime guard: warn to journal if both WITTYPI_ENABLED=1 AND IGNITION_SENSE_PIN is set.

  Shutdown sequence:
    Witty Pi daemon detects VIN drop
      → runs beforeShutdown.sh
        → POST /system/shutdown-prepare (10s timeout)
          → cleanup (game mode, DB, logs)
          → 200 OK  → daemon calls `shutdown -h now`
          → 409     → daemon aborts shutdown (future: override_mode active)
```

---

## New Files

| File | Purpose |
|------|---------|
| `backend/gps_service.py` | gpsd polling loop, WebSocket broadcast, shared_state update |
| `backend/wittypi_service.py` | pre-shutdown cleanup coordinator, /system/shutdown-prepare endpoint |
| `deploy/beforeShutdown.sh` | Called by Witty Pi daemon before halt; HTTP callback to backend |

---

## Modified Files

| File | Change |
|------|--------|
| `backend/main.py` | Conditional import + start of gps_service and wittypi_service |
| `backend/mock_service.py` | Add `mock_gps_loop` |
| `backend/shared_state.py` | Add `gps_speed_kmh`, `gps_heading_deg`, `gps_fix` |
| `deploy/setup.sh` | gpsd packages; Witty Pi manual install note; I2C address docs |
| `.env.example` | GPS_MOCK, GPSD_HOST, GPSD_PORT, WITTYPI_ENABLED; remove STARLINK_GPS_POLL_INTERVAL |
| `documentation/SPEC.md` | GPS + power management hardware sections updated; BOM updated |
| `documentation/pi-setup.md` | New GPS Setup and Witty Pi 5 Setup sections |

---

## GPS Service Detail

### `backend/gps_service.py`

- **Library:** `gps` Python package (from `python3-gps` / `gpsd-clients`)
- **Connection:** gpsd JSON protocol socket at `GPSD_HOST:GPSD_PORT` (default `localhost:2947`)
- **Poll interval:** 1 second
- **Fix quality:** broadcast only when `fix >= 2` (2D or 3D fix). On fix loss, broadcast no-fix payload.
- **Retry on failure:** 10s exponential backoff capped at 60s; DEBUG-level log after first failure to avoid journal noise (same pattern as OBD service)
- **Controlled by:** `GPS_MOCK` env var (default `1` = mock)

**WebSocket message — fix acquired:**
```json
{"type": "gps", "data": {
  "lat": 52.6309,
  "lon": 1.2974,
  "speed_kmh": 0.0,
  "heading_deg": 0.0,
  "fix": 3
}}
```

**WebSocket message — no fix:**
```json
{"type": "gps", "data": {
  "lat": null, "lon": null,
  "speed_kmh": null, "heading_deg": null,
  "fix": 0
}}
```

**shared_state updates (on valid fix only):**
```python
shared_state.gps_lat        = lat       # existing — feeds weather_service
shared_state.gps_lon        = lon       # existing — feeds weather_service
shared_state.gps_speed_kmh  = speed     # new — available for future navigation
shared_state.gps_heading_deg = heading  # new — available for future navigation
shared_state.gps_fix         = fix      # new — 0/2/3
```

### `backend/mock_service.py`

New `mock_gps_loop` — broadcasts static fix-3 payload using `WEATHER_LAT`/`WEATHER_LON` env var values. No fake movement. Interval: 1s (matching real service).

### `backend/shared_state.py` additions

```python
gps_speed_kmh:   float | None = None
gps_heading_deg: float | None = None
gps_fix:         int = 0
```

### `setup.sh` additions

```bash
apt-get install -y gpsd gpsd-clients python3-gps
```

Configure gpsd to auto-start with the u-blox device:
```bash
# /etc/default/gpsd
DEVICES="/dev/ttyACM0"
GPSD_OPTIONS="-n"
START_DAEMON="true"
```

> **# hw-verify** — `/dev/ttyACM0` is the expected device path for the u-blox UBX-G7020-KT on Bookworm. Confirm with `ls /dev/ttyACM*` after plugging in. Also verify that gpsd auto-detects the receiver and `cgps -s` shows a fix before relying on the service.

### `.env.example` additions

```
GPS_MOCK=1
GPSD_HOST=localhost
GPSD_PORT=2947
```

Remove: `STARLINK_GPS_POLL_INTERVAL` (obsolete).

---

## Power Management Detail

### Wiring Topology

```
Leisure battery (+12V)
  ├─→ 12V relay coil (switched by ignition feed)
  │     └─→ Witty Pi 5 VIN screw terminal  ← running power + boot/shutdown trigger
  └─→ Epoxy-potted 12V→5V buck converter (permanent)
        └─→ Witty Pi 5 USB-C               ← standby power (keeps RTC alive)
```

Witty Pi 5 monitors VIN. When VIN drops (ignition off), it triggers a graceful shutdown after a configurable delay, then cuts power after the Pi halts.

### I2C Addresses

| Device | Address | Conflict? |
|--------|---------|-----------|
| Witty Pi 5 (RTC) | 0x51 | — |
| Waveshare 7.9" touch (Goodix) | 0x38 | No conflict |

### `backend/wittypi_service.py`

Lightweight module. On startup (when `WITTYPI_ENABLED=1`):
- Logs I2C address confirmation and no-conflict note
- Logs warning if `IGNITION_SENSE_PIN` is also set
- Registers the `/system/shutdown-prepare` endpoint (see below)

No GPIO polling. The UUGear daemon handles shutdown detection.

### New REST Endpoint: `POST /system/shutdown-prepare`

Called by `deploy/beforeShutdown.sh`. Performs cleanup in order:

1. If game mode active: call `game_service._stop_internal()` (unfreezes Chromium, kills launcher, cleans PulseAudio sinks)
2. Flush pending engine history writes to SQLite
3. Close DB connections cleanly (`db.close()`)
4. Log `"Witty Pi initiated shutdown — cleanup complete"` to journal
5. Return `{"ok": True, "cleaned_up": [...list of actions taken...]}`

**Timeout:** entire cleanup must complete within 8s (leaves 2s margin for the 10s curl timeout in `beforeShutdown.sh`).

**Future override hook:** if `shared_state.override_mode is True`, return HTTP 409 `{"ok": False, "reason": "override_active"}`. The `beforeShutdown.sh` script detects this and exits non-zero, causing the UUGear daemon to abort the shutdown. This is the clean architecture hook for a future "stay on after ignition off" UI button — that feature only needs to toggle `shared_state.override_mode`.

**Guard:** endpoint only active when `WITTYPI_ENABLED=1`. Returns 501 otherwise.

### `deploy/beforeShutdown.sh`

```bash
#!/bin/sh
# Witty Pi pre-shutdown hook.
# Called by the UUGear daemon before halting the Pi.
# Gives the TD5 Dash backend 10 seconds to clean up.
# Exit 1 to abort shutdown (e.g. override mode active), exit 0 to proceed.
result=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST http://localhost:8000/system/shutdown-prepare \
  --max-time 10 2>/dev/null)
[ "$result" = "409" ] && exit 1  # override active — abort
exit 0                            # proceed with halt
```

Placed in the Witty Pi scripts directory (typically `~/wittypi/`) during setup. Flagged in setup.sh as a manual step.

### Toggle Behaviour

| `WITTYPI_ENABLED` | `IGNITION_SENSE_PIN` | Result |
|-------------------|----------------------|--------|
| `0` (default) | empty | Neither service active — no software shutdown handling |
| `0` | set | Discrete component path (ignition_service.py) active |
| `1` | empty | Witty Pi path active |
| `1` | set | Both configured — **warning logged**, Witty Pi takes precedence |

### `setup.sh` additions

```bash
# ── Witty Pi 5 HAT+ ──────────────────────────────────────────────────
# NOTE: The UUGear install script must be run manually after first boot.
# It downloads from GitHub and requires interactive confirmation.
# Run: curl -L https://install.ultronics.co.uk/wittypi5plus.sh | sudo bash
# Then copy deploy/beforeShutdown.sh to ~/wittypi/beforeShutdown.sh
echo "▸ Witty Pi 5: run UUGear install script manually — see documentation/pi-setup.md"
```

> **# hw-verify** — VIN shutdown voltage threshold, shutdown delay, and the `beforeShutdown.sh` hook path must all be verified against the physical unit. The UUGear daemon config file location may vary between firmware versions.

---

## Documentation Changes

### `documentation/SPEC.md`

- **GPS section**: u-blox UBX-G7020-KT via gpsd replaces Starlink GPS. WebSocket format updated with `speed_kmh`, `heading_deg`, `fix` fields. Note: coordinates feed weather; full payload available for future navigation.
- **Power management section**: Witty Pi 5 HAT+ replaces discrete component design. Wiring topology documented. Old discrete path retained in code behind `IGNITION_SENSE_PIN`. I2C addresses confirmed.
- **BOM**: Add Witty Pi 5 HAT+, u-blox UBX-G7020-KT. Remove 7805 regulator, dual optoisolator circuit items.

### `documentation/pi-setup.md`

Two new sections appended:

**GPS Setup**
- `apt install gpsd gpsd-clients python3-gps`
- Configure `/etc/default/gpsd` with `DEVICES="/dev/ttyACM0"`
- Test: `cgps -s` should show fix within ~90s with clear sky view
- Set `GPS_MOCK=0` in `.env`
- `# hw-verify`: device path, auto-detection, first-fix behaviour

**Witty Pi 5 Setup**
- Hardware installation (GPIO header, standby USB-C, VIN screw terminal wiring)
- Run UUGear install script
- Copy `deploy/beforeShutdown.sh` to `~/wittypi/`
- Set `WITTYPI_ENABLED=1` in `.env`, clear `IGNITION_SENSE_PIN`
- Configure VIN threshold via Witty Pi tool
- ASCII wiring diagram (topology above)
- `# hw-verify`: VIN threshold, shutdown delay, beforeShutdown.sh hook registration

---

## What Requires Hardware Testing

All the following are marked `# hw-verify` in code and documentation:

| Item | Why |
|------|-----|
| `/dev/ttyACM0` device path | May vary; confirm with `ls /dev/ttyACM*` after plugging in |
| gpsd auto-detection of u-blox | Some receivers need explicit `DEVICES=` in gpsd config |
| GPS first-fix time | ~90s cold, faster warm; test in open sky |
| Witty Pi VIN threshold | Configure to match relay drop-out voltage |
| Witty Pi shutdown delay | Configure based on acceptable run-on time |
| `beforeShutdown.sh` hook path | Verify correct directory in UUGear daemon config |
| I2C address 0x51 (Witty Pi RTC) | Confirm with `i2cdetect -y 1` on installed hardware |
| Override mode abort behaviour | Requires Witty Pi present to test 409 → abort path |

---

## Out of Scope (Future Work)

- Navigation view using GPS speed/heading
- "Stay on after ignition" UI button (architecture hook is in place via `override_mode` + 409 response)
- NMEA sentence logging / trip recording
