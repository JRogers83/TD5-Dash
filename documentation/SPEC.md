# Defender TD5 Dash Display
**Project Specification — Rev 3.1**
Vehicle: Land Rover Defender TD5 (factory)
Date: 25 March 2026
Status: Phases 1–3 complete, Phase 5 in progress — awaiting hardware for vehicle install

> **Note:** This document was written during Phase 0 planning. The software has since progressed through Phases 1–3 plus extensive Phase 5 work (2D navigation, SQLite persistence, DTC fault codes, diagnostics, engine history, test suite, OTA updates). Phase 4 (power system + vehicle install) is pending hardware. See the build phases table below for current status and see `README.md` for setup and development instructions.

---

## 1. Project Overview

A custom in-vehicle information display for a Land Rover Defender TD5, mounted in the dashboard ashtray opening using a MUD Mini Pod. The system provides live engine gauges via the TD5's proprietary K-Line diagnostic interface, Spotify playback control, Victron leisure battery and solar monitoring, and automated power management tied to the vehicle's ignition circuit.

The display uses a Raspberry Pi 5 running a minimal Linux installation with a full-screen web-based kiosk UI, connected to a Waveshare 7.9" bar-format DSI touchscreen. The interface provides five swipeable views: engine gauges, Spotify, Victron status, Starlink, and settings/diagnostics.

---

## 2. Vehicle Context

The Defender TD5 uses a Lucas/MEMS engine management ECU. Critically, this ECU is **NOT OBD-II compliant**. Although it has a standard 16-pin OBD-II physical connector (located by the centre cubby on factory TD5 Defenders), it communicates using a proprietary protocol over the ISO 9141-2 K-Line at 10,400 baud with non-standard command codes and a seed-key authentication handshake.

Standard ELM327 OBD adapters will not work. The ECU requires a specific initialisation sequence (25ms K-Line low fast-init), proprietary diagnostic start requests, and a cryptographic seed-key exchange before it will respond to data requests. This protocol has been reverse-engineered by the open-source community, and multiple working implementations exist.

The vehicle has a Victron leisure electrical system: SmartShunt 500A for battery monitoring, MPPT 100/30 solar charge controller, Renogy Core Mini 12.8V 300Ah LiFePO4 battery, and Victron Orion XS 12/12-50A DC-DC charger. Both the SmartShunt and MPPT broadcast data via Bluetooth LE.

---

## 3. Hardware Specification

### 3.1 Display

| Parameter | Value |
|-----------|-------|
| Model | Waveshare 7.9" DSI IPS Capacitive Touchscreen LCD |
| Resolution | 400×1280 (confirm 1280 vs 1480 with seller before purchase) |
| Connection | DSI ribbon cable (video) + pogo pins or 4-pin header (5V, GND, SDA, SCL for I2C touch) |
| Touch | 5-point capacitive |
| Glass | Toughened, 6H hardness |
| Power | 5V at 600mA via pogo pins or wired header |
| Operating temp | 0°C to 60°C |
| Viewable area | 191.08mm × 60.40mm |
| Overall glass | 207.78mm × 69.80mm |
| Wiki | www.waveshare.com/wiki/7.9inch_DSI_LCD |
| Config | `dtoverlay=vc4-kms-dsi-waveshare-panel,7_9_inch` |
| Brightness | `echo X \| sudo tee /sys/class/backlight/*/brightness` (0–255) |

### 3.2 Mounting

| Parameter | Value |
|-----------|-------|
| Pod | MUD Mini Pod (MUD-0005) — £33.33 from mudstuff.co.uk |
| Fascia opening | 195mm × 62mm (recessed) |
| External | 228mm(w) × 104mm(d) × 84mm(h) |
| Fit | Display viewable area fits with 3.92mm horizontal and 1.60mm vertical clearance. 3D-printed bezel required to centre display in opening. |

No-drill mount into Defender ashtray opening. Pod is open-backed into dash cavity, providing natural convection for cooling.

### 3.3 Compute

| Parameter | Value |
|-----------|-------|
| Board | Raspberry Pi 5 (4GB) |
| Storage | Samsung PM991 256GB M.2 2230 NVMe SSD (used, eBay) |
| NVMe HAT | Pimoroni NVMe Base (bottom-mount) |
| NVMe FFC | Pimoroni 50mm PCIe Pipe cable (replaces standard 35mm, allows NVMe Base to be mounted separately from Pi inside pod) |
| Cooling | Pi 5 Active Cooler (clip-on heatsink + fan) |

The NVMe Base connects to the Pi 5's dedicated PCIe FFC port and is mounted separately within the pod cavity, connected by the 50mm FFC cable. This keeps the Pi's underside clear for pogo pin contact with the display and provides better thermal separation. The Pi mounts directly on the display PCB via pogo pins (carrying 5V power and I2C touch), with the Active Cooler on top, then the Witty Pi 5 HAT+ on the GPIO header.

> **USB hub note:** A USB hub must be connected to one of the Pi 5's USB 2.0 ports (black ports). HID devices such as game controllers and keyboards do not enumerate reliably on USB 3.0 (blue ports) on the Pi 5.

### 3.4 OBD / Engine Data Interface

> **IMPORTANT:** The TD5 ECU does NOT speak standard OBD-II. A standard ELM327 adapter will not work.

**Protocol**

| Parameter | Value |
|-----------|-------|
| Physical layer | ISO 9141-2, K-Line (OBD pin 7) |
| Baud rate | 10,400 baud (non-standard) |
| Command set | Proprietary (not standard OBD-II PIDs) |
| Authentication | Seed-key handshake required before any data requests |
| Init sequence | Fast-init (25ms K-Line low), init frame, diagnostics start request, seed request, key response |

**Hardware**

Interface cable: VAG COM KKL 409.1 USB cable with genuine FTDI FT232RL chip — Amazon UK, approx. £19.95.

The KKL cable contains a built-in K-Line level shifter (TTL to 12V) and connects directly from the Pi's USB port to the vehicle's OBD-II connector. No separate level-shifting circuit, CP2102 adapter, or baud rate reprogramming is required. The cable must use a genuine FTDI chip (not CH340 clone) as the pyTD5Tester software uses the PyFtdi library which communicates directly with FTDI hardware.

**Software**

| Codebase | Details |
|----------|---------|
| Primary | pyTD5Tester (github.com/hairyone/pyTD5Tester) — Python, proven on TD5 |
| Reference | Ekaitza_Itzali (github.com/EA2EGA/Ekaitza_Itzali) — comprehensive TD5 diagnostic tool |
| Auth algorithm | td5keygen (github.com/pajacobson/td5keygen) |
| Library | PyFtdi for FTDI USB communication |

**Available Data (once authenticated)**

RPM, coolant temperature, inlet air temperature, manifold absolute pressure (boost), ambient pressure, throttle position (pedal pots 1 and 2), battery voltage, mass airflow, road speed, idle speed error, fuel temperature, EGR modulator duty, wastegate duty, injector data. Fault code read/clear is also supported.

**Prior Art**

This approach has been validated by at least two independent builders. GitHub user happen-studio designed a custom PCB for the CP2102-to-K-Line interface and confirmed live data reception. GitHub user hairyone (pyTD5Tester author) has a working TD5 diagnostic system on a Raspberry Pi Zero with a custom dashboard and the same Waveshare bar display family (11" variant), including a custom PCB for safe shutdown on ignition-off. The protocol reverse engineering is mature and well-documented.

### 3.5 Victron Integration

| Parameter | Value |
|-----------|-------|
| Method | Bluetooth LE (not VE.Direct USB cables) |
| Library | victron-ble (github.com/keshavdv/victron-ble) — Python |
| Devices | SmartShunt 500A + MPPT 100/30 |

Both Victron devices broadcast data at approximately 1Hz via BLE. The Pi 5 has onboard Bluetooth 5.0. BLE encryption keys are extracted from the VictronConnect app. This approach saves two USB ports and approximately £56 in VE.Direct cables compared to the wired alternative.

### 3.6 Audio

| Parameter | Value |
|-----------|-------|
| Spotify | Raspotify (librespot wrapper) — Pi becomes a Spotify Connect device |
| Audio output | Bluetooth A2DP to head unit (preferred), or CarPiHAT PRO 5 built-in DAC, or USB DAC as fallback |

The Pi 5 has no 3.5mm audio jack. If the CarPiHAT PRO 5 is used, its built-in 192kHz/24-bit Burr-Brown DAC with 3.5mm jack provides high-quality analogue output directly to the head unit. Otherwise, a USB DAC (e.g. Sabrent AU-MMSA, approx. £7) provides the same function.

Head unit — Current: Pioneer DEH-1320MP (front 3.5mm aux only, no Bluetooth). Replacement recommended: any cheap Bluetooth single-DIN mechless unit. Pioneer MVH-S320BT identified as strong candidate. Head unit decision decoupled from project.

### 3.7 GPS

| Parameter | Value |
|-----------|-------|
| Receiver | u-blox UBX-G7020-KT USB GPS Receiver |
| Interface | USB (appears as `/dev/ttyACM0`) |
| Protocol | NMEA 0183 via gpsd |
| Library | `gps` (Python gpsd client) |

The GPS receiver connects to any USB port on the Pi. `gpsd` manages the device and provides a standard socket interface consumed by the GPS backend service. The GPS WebSocket message format is `{"type": "gps", "data": {"lat": 0, "lon": 0, "speed_kmh": 0, "heading_deg": 0, "fix": 3}}` — `fix` is the gpsd fix type (0 = no fix, 2 = 2D, 3 = 3D). Note: `alt` was present in an earlier Starlink-sourced GPS format but has been removed; NMEA altitude data will be added in future if needed.

GPS data is used by the weather service for live location-based forecasts (falling back to `WEATHER_LAT/LON` env vars when no fix is available).

### 3.8 Power Management — Witty Pi 5 HAT+

**Hardware:** Witty Pi 5 HAT+ (UUGear). Replaces the earlier discrete component design (optoisolator + 7805 + relay circuit).

**Wiring topology:**
```
Leisure battery (+12V)
  ├─→ 12V relay (coil on ignition feed) → Witty Pi 5 VIN screw terminal
  │     (running power + shutdown trigger)
  └─→ Epoxy-potted 12V→5V buck converter (permanent standby)
        └─→ Witty Pi 5 USB-C (keeps RTC alive when ignition is off)
```

**Shutdown sequence:**
1. Ignition off → 12V relay drops → Witty Pi detects VIN loss
2. After configured delay → Witty Pi daemon runs `~/wittypi/beforeShutdown.sh`
3. `beforeShutdown.sh` POSTs to `/system/shutdown-prepare` (10s timeout)
4. Backend performs cleanup → returns 200 (proceed) or 409 (override active, abort)
5. Witty Pi daemon calls `shutdown -h now`
6. Pi halts → Witty Pi cuts power after configurable timeout

**I2C addresses:**
| Device | Address |
|--------|---------|
| Witty Pi 5 (RTC) | 0x51 |
| Waveshare 7.9" touch (Goodix) | 0x38 |
No conflict.

**Override mode (future):** `shared_state.override_mode = True` causes `/system/shutdown-prepare` to return 409, aborting the Witty Pi's shutdown. This is the hook for a future "stay on after ignition" UI button.

**Legacy discrete path:** `ignition_service.py` remains in the codebase for the optoisolator-based ignition detection circuit. Activated by setting `IGNITION_SENSE_PIN` in `.env`. Do not set both `WITTYPI_ENABLED=1` and `IGNITION_SENSE_PIN` simultaneously.

---

## 4. Software Architecture

### 4.1 Stack

| Component | Detail |
|-----------|--------|
| OS | Pi OS Lite (Bookworm) — no desktop environment |
| Backend | FastAPI (Python) — WebSocket server feeding live data to frontend |
| Frontend | Single-page HTML/CSS/JS web app in Chromium kiosk mode |
| Boot target | <10 seconds. Plymouth splash at ~2s, services at ~4s, kiosk live at ~8–10s. NVMe boot critical. |

### 4.2 Backend Services (systemd)

**OBD Service**

Custom Python service adapted from pyTD5Tester and Ekaitza_Itzali. Handles K-Line fast-init, seed-key authentication, cyclic PID polling at ~1Hz. Publishes engine data via WebSocket. NOT based on python-obd.

**Victron BLE Service**

victron-ble library. Passive BLE listener for SmartShunt and MPPT broadcasts. Publishes SoC, voltage, current, solar yield, charge state via WebSocket.

**Audio Service**

Raspotify (librespot). Spotify Connect device. Audio routed to head unit via BT A2DP, CarPiHAT DAC, or USB DAC.

**System Service**

GPIO/CarPiHAT input monitoring (ignition, sidelights), brightness control, shutdown management (30s grace period), SoC temp monitoring, override mode detection.

### 4.3 Frontend UI

Five horizontally-swipeable views at display resolution (1280×400 landscape). The raw panel spec says "400×1280 portrait" but the Pi's dtoverlay rotates the output, so Chromium sees 1280×400.

| View | Content |
|------|---------|
| 1 — Engine Gauges | RPM, boost, throttle radial gauges · battery voltage, coolant, air and fuel temp stat tiles |
| 2 — Spotify | Album art · track/artist/album · progress bar · prev/play-pause/next · like button · playlist browser · spectrum visualiser (real audio via PulseAudio loopback / getUserMedia, simulation fallback) |
| 3 — Victron | Battery SoC arc gauge · voltage, current, solar yield, DC-DC charger state · embedded weather panel (Open-Meteo, WMO icon, temp, wind, humidity) |
| 4 — Starlink | Status / obstruction / GPS stat tiles · download / upload / latency / packet loss · uptime · active alerts · GPS fix status (from u-blox UBX-G7020-KT via gpsd) |
| 5 — Settings | Connectivity tiles (Wi-Fi, BT, override, data feed, Starlink) · day/night brightness bars · system metrics (CPU temp/load, RAM, disk, uptime, throttle) |

### 4.4 Shutdown Sequence

Ignition off → 12V relay drops → Witty Pi detects VIN loss → configured delay → `beforeShutdown.sh` POSTs to `/system/shutdown-prepare` → backend returns 200 (proceed) or 409 (override active, abort) → `shutdown -h now` → Witty Pi cuts power after configurable timeout.

---

## 5. Physical Assembly

Display PCB at front of pod, viewable area aligned with fascia opening via 3D-printed bezel. Pi 5 mounted on display back via pogo pins. Active Cooler on SoC. Witty Pi 5 HAT+ on GPIO header. NVMe Base mounted separately via 50mm PCIe Pipe FFC. VAG KKL cable exits pod into dash cavity to OBD-II port. 12V relay and epoxy-potted 12V→5V buck converter mounted in dash cavity; relay coil on ignition feed, switched output to Witty Pi VIN; buck output (5V permanent) to Witty Pi USB-C.

---

## 6. Build Phases

| Phase | Description | Status |
|-------|-------------|--------|
| 0 | **OBD Proof of Concept** — VAG KKL cable + laptop + pyTD5Tester. Verify K-Line handshake and live data. | Complete |
| 1 | **Bench Prototype** — FastAPI backend, WebSocket hub, mock data service, five-view kiosk UI scaffold, Docker dev environment. | Complete |
| 2 | **OBD Integration** — TD5 K-Line service (`backend/obd/`). Fast-init, seed-key auth, cyclic PID polling. | Complete (vehicle-verified 2026-03-21) |
| 3 | **Victron, Spotify, Weather, Starlink** — Victron BLE service, Spotify Web API + Connect (Raspotify), Open-Meteo weather, Starlink Mini gRPC, playlist browser. | Complete (plus ongoing UI polish) |
| 4 | **Power System & Vehicle Install** — Witty Pi 5 HAT+. 12V relay + permanent 5V standby buck. Override switch (future). MUD Mini Pod + 3D-printed bezel. Cable routing. | Pending — Power system architecture resolved: Witty Pi 5 HAT+ with 12V relay on ignition feed and epoxy-potted 12V→5V buck for permanent standby |
| 5 | **Polish** — Splash screen, gauge calibration, day/night brightness, boot time optimisation, stress testing. | In Progress — Plymouth splash, sidelights auto day/night, 2D navigation (4 engine layers + 4 settings layers), SQLite persistence (settings/pages/history), DTC fault codes + lookup, throttle calibration wizard, diagnostics screen, engine history charts, trip computer, coolant trend indicator, test suite (99 tests), reverse geocoding, OTA update, WebSocket reconnect resync, health check endpoint |

---

## 7. Open Questions

- ~~Display resolution: confirm 400×1280 vs 400×1480 with seller.~~ RESOLVED: confirmed 1280×400 landscape (dtoverlay rotates panel output).
- ~~KKL cable FTDI authenticity: test with PyFtdi on receipt.~~ RESOLVED: confirmed working on vehicle 2026-03-21 with genuine FTDI FT232RL chip.
- ~~TD5 PID coverage: empirical testing needed in Phase 2.~~ RESOLVED: all PIDs confirmed on vehicle. See `documentation/TD5-ECU-Confirmed-Protocol.md`.
- ~~CarPiHAT PRO 5 stock: UNRESOLVED.~~ RESOLVED: CarPiHAT dropped in favour of Witty Pi 5 HAT+. Power architecture: 12V relay on ignition feed to Witty Pi VIN + epoxy-potted 12V→5V buck to Witty Pi USB-C for permanent standby. No I2C conflict (Witty Pi RTC at 0x51; Waveshare touch at 0x38).
- Head unit replacement model and BT dual-pairing confirmation. (Open)
- ~~GPIO pin allocation: finalise during Phase 1.~~ RESOLVED: documented in carpihat_service.py; can be adapted for any GPIO circuit.
- BLE coexistence: two Victron BLE + A2DP audio simultaneously. (Pending — needs hardware)
- ~~Splash screen: Land Rover logo at display resolution.~~ RESOLVED: Plymouth theme with Land Rover badge + shimmer effect complete.

---

## 8. Reference Links

| Resource | URL |
|----------|-----|
| Waveshare 7.9" DSI Wiki | www.waveshare.com/wiki/7.9inch_DSI_LCD |
| MUD Mini Pod | www.mudstuff.co.uk/products/mini-mud-pod |
| Witty Pi 5 HAT+ | uugear.com/product/witty-pi-5-hat-plus/ |
| Witty Pi 5 GitHub | github.com/uugear/Witty-Pi-5 |
| CarPiHAT PRO 5 (legacy) | thepihut.com/products/carpihat-pro-5-car-interface-dac-for-raspberry-pi-5 |
| pyTD5Tester | github.com/hairyone/pyTD5Tester |
| Ekaitza_Itzali | github.com/EA2EGA/Ekaitza_Itzali |
| td5keygen | github.com/pajacobson/td5keygen |
| victron-ble | github.com/keshavdv/victron-ble |
| Raspotify | github.com/dtcooper/raspotify |
| PyFtdi | github.com/eblot/pyftdi |
| happen-studio schematic | github.com/EA2EGA/Ekaitza_Itzali/issues/4 |
| In-car Pi PSU reference | dontpressthat.wordpress.com/2017/10/13/in-car-raspberry-pi-psu-controller/ |
