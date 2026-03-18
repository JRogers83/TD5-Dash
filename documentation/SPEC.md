# Defender TD5 Dash Display
**Project Specification — Rev 3.0**
Vehicle: Land Rover Defender TD5 (factory)
Date: 18 March 2026
Status: Software complete to Phase 3 with ongoing UI polish — awaiting hardware for vehicle install

> **Note:** This document was written during Phase 0 planning. The software has since progressed through Phases 1–3, plus additional Polish/UI work. Phase 4 (power system + vehicle install) is pending. Phases 4–5 completion awaits hardware. See the build phases table below for current status and see `README.md` for setup and development instructions.

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

The NVMe Base connects to the Pi 5's dedicated PCIe FFC port and is mounted separately within the pod cavity, connected by the 50mm FFC cable. This keeps the Pi's underside clear for pogo pin contact with the display and provides better thermal separation. The Pi mounts directly on the display PCB via pogo pins (carrying 5V power and I2C touch), with the Active Cooler on top, then a GPIO riser for the CarPiHAT.

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

### 3.7 Power System

Deferred to Phase 4 (vehicle install). Bench prototype uses standard USB-C PSU.

**Preferred: CarPiHAT PRO 5** (£110, persistently out of stock — DIY discrete now the more likely path)

TJD CarPiHAT PRO 5 from The Pi Hut. All-in-one: 12V to 5V 5A buck (Pi 5 validated), safe shutdown via GPIO, <1mA off draw, 5 opto-isolated 12V inputs, 2x 12V switched outputs (1A), CAN bus port (not useful for TD5 K-Line), RTC, I2C bus, Burr-Brown DAC with 3.5mm jack, optional cooling fan/shroud. Note: OpenAuto Pro (BlueWave Studio) is defunct. The CarPiHAT is independent hardware by TJD, software-agnostic. Stock has remained unavailable; the DIY discrete fallback is now the more likely path to vehicle install.

**CarPiHAT Mounting**

GPIO riser required with 20mm M2.5 standoffs, 12mm M2.5 screws (top), 8mm M2.5 screws (underside) to accommodate Active Cooler beneath. Stack: Display PCB → pogo pins → Pi 5 → Active Cooler → GPIO Riser → 20mm standoffs → CarPiHAT. NVMe Base mounted separately via 50mm FFC.

**Fallback: DIY discrete (~£25)**

12V-to-5V 5A buck converter, optocouplers, relay, diode OR circuit, inline fuse. Requires separate USB DAC. Pi 5 PMIC may need USB-C PD trigger board for full 5A delivery.

**Override Power**

Leisure battery via Carling Contura V switch, bypassing ignition relay. Software detects override mode, shows status indicator, auto-timeout default 60 min.

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
| 4 — Starlink | Status / obstruction / GPS stat tiles · download / upload / latency / packet loss · uptime · active alerts |
| 5 — Settings | Connectivity tiles (Wi-Fi, BT, override, data feed, Starlink) · day/night brightness bars · system metrics (CPU temp/load, RAM, disk, uptime, throttle) |

### 4.4 Shutdown Sequence

Ignition off → 30s grace period (cancellable) → `shutdown -h now` → CarPiHAT hardware timer (5s) → power relay cuts 12V.

---

## 5. Physical Assembly

Display PCB at front of pod, viewable area aligned with fascia opening via 3D-printed bezel. Pi 5 mounted on display back via pogo pins. Active Cooler on SoC. GPIO Riser. CarPiHAT PRO 5 on riser via 20mm standoffs. NVMe Base mounted separately via 50mm PCIe Pipe FFC. VAG KKL cable exits pod into dash cavity to OBD-II port.

---

## 6. Build Phases

| Phase | Description | Status |
|-------|-------------|--------|
| 0 | **OBD Proof of Concept** — VAG KKL cable + laptop + pyTD5Tester. Verify K-Line handshake and live data. | Complete |
| 1 | **Bench Prototype** — FastAPI backend, WebSocket hub, mock data service, five-view kiosk UI scaffold, Docker dev environment. | Complete |
| 2 | **OBD Integration** — TD5 K-Line service (`backend/obd/`). Fast-init, seed-key auth, cyclic PID polling. | Complete (untested on vehicle) |
| 3 | **Victron, Spotify, Weather, Starlink** — Victron BLE service, Spotify Web API + Connect (Raspotify), Open-Meteo weather, Starlink Mini gRPC, playlist browser. | Complete (plus ongoing UI polish) |
| 4 | **Power System & Vehicle Install** — CarPiHAT PRO 5 (or DIY). Override switch. MUD Mini Pod + 3D-printed bezel. Cable routing. | Pending — Power system hardware TBD: CarPiHAT PRO 5 out of stock, DIY discrete power (buck converter + optocouplers) identified as fallback |
| 5 | **Polish** — Splash screen, gauge calibration, day/night brightness, boot time optimisation, stress testing. | Pending — Plymouth splash screen complete; sidelights auto day/night switch complete |

---

## 7. Open Questions

- ~~Display resolution: confirm 400×1280 vs 400×1480 with seller.~~ RESOLVED: confirmed 1280×400 landscape (dtoverlay rotates panel output).
- KKL cable FTDI authenticity: test with PyFtdi on receipt. (Pending — needs vehicle)
- TD5 PID coverage: empirical testing needed in Phase 2. (Pending — needs vehicle)
- CarPiHAT PRO 5 stock: UNRESOLVED — persistently out of stock at The Pi Hut; DIY discrete power (buck converter + optocouplers) identified as fallback path.
- CarPiHAT I2C address conflicts with display touch controller. (Open — hardware not yet arrived)
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
| CarPiHAT PRO 5 | thepihut.com/products/carpihat-pro-5-car-interface-dac-for-raspberry-pi-5 |
| CarPiHAT GitHub | github.com/gecko242/CarPiHat/wiki |
| pyTD5Tester | github.com/hairyone/pyTD5Tester |
| Ekaitza_Itzali | github.com/EA2EGA/Ekaitza_Itzali |
| td5keygen | github.com/pajacobson/td5keygen |
| victron-ble | github.com/keshavdv/victron-ble |
| Raspotify | github.com/dtcooper/raspotify |
| PyFtdi | github.com/eblot/pyftdi |
| happen-studio schematic | github.com/EA2EGA/Ekaitza_Itzali/issues/4 |
| In-car Pi PSU reference | dontpressthat.wordpress.com/2017/10/13/in-car-raspberry-pi-psu-controller/ |
