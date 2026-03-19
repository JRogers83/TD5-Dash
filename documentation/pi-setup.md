# TD5 Dash — Raspberry Pi Setup Guide

This guide takes you from a blank Pi 5 to a fully running kiosk.
Follow the sections in order — each step depends on the previous one.

---

## Hardware Required

**Core (required):**

| Item | Notes |
|------|-------|
| Raspberry Pi 5 | 4 GB or 8 GB |
| Waveshare 7.9" DSI display | 1280×400, connects via ribbon cable to DSI port |
| USB-C power supply | Official Pi 5 27W PSU recommended |
| VAG-COM KKL 409.1 cable | Must have genuine FTDI FT232RL chip — clones will not work |
| Your development machine | To flash storage and SSH in for initial setup |

**Storage — choose one:**

| Option | What you need | Notes |
|--------|--------------|-------|
| microSD card | 16 GB minimum, 32 GB recommended, Class 10 / A1 | Simplest to get started |
| M.2 NVMe SSD + HAT | M.2 HAT for Pi 5, any M.2 NVMe SSD (2230 or 2242 recommended for size) | More durable for in-vehicle use; faster boot; recommended for permanent install |

> **Why SSD for a vehicle install?** microSD cards have a finite write cycle life and are vulnerable to corruption from power loss (e.g. the vehicle being switched off mid-write). An NVMe SSD is significantly more robust for an always-on embedded system.

---

## 1. Flash Pi OS

Use **Raspberry Pi Imager** (download from raspberrypi.com/software).

In Imager, click the **gear icon** (advanced options / OS customisation) and configure these settings **before** writing — they are the same regardless of storage:

- Hostname: `td5dash`
- Enable SSH: **Use password authentication**
- Username: `pi` (or your preferred username — the setup script adapts to whoever runs it)
- Password: something you will remember
- WiFi: your home network SSID and password

Choose **OS → Raspberry Pi OS (other) → Raspberry Pi OS Lite (64-bit)**
(Lite = no desktop — the kiosk launches Chromium directly from the console.)

---

### 1a. SD card

Select your SD card as the storage target and click **Write**. Skip to [Section 2](#2-first-boot).

---

### 1b. M.2 NVMe SSD

There are two ways to flash the SSD depending on whether you have a USB-to-M.2 adapter.

**Option A — flash directly with a USB adapter (simplest)**

A USB-to-M.2 NVMe adapter costs a few pounds and makes the SSD appear as a USB drive on your computer, so you can flash it exactly like an SD card.

1. Insert the SSD into the USB adapter and connect it to your development machine
2. In Raspberry Pi Imager, select the SSD as the storage target and click **Write**
3. Fit the SSD to the M.2 HAT and attach the HAT to the Pi
4. Continue to [Section 2](#2-first-boot) — after first boot you will need to enable NVMe booting (covered below)

**Option B — bootstrap via SD card (no adapter needed)**

1. Flash Pi OS to an SD card as in [Section 1a](#1a-sd-card) and boot the Pi
2. Attach the M.2 HAT with SSD to the running Pi
3. SSH in and flash the SSD from the Pi itself:
   ```bash
   # Download and run Pi Imager in server mode, or use dd to clone the SD card:
   sudo dd if=/dev/mmcblk0 of=/dev/nvme0n1 bs=4M status=progress conv=fsync
   # Then grow the partition on the SSD to fill the drive:
   sudo growpart /dev/nvme0n1 2
   sudo resize2fs /dev/nvme0n1p2
   ```
   Alternatively, install `rpi-imager` on the Pi and use its GUI over VNC to write a fresh image to the SSD.

**Enabling NVMe boot (required for both Option A and B)**

The Pi 5 bootloader needs to be told to look for NVMe. SSH in (booted from SD card if using Option B) and run:

```bash
# Ensure the bootloader is up to date
sudo apt update && sudo apt full-upgrade -y
sudo rpi-eeprom-update -a
sudo reboot
```

After the reboot, SSH back in and set the boot order:

```bash
sudo raspi-config
```

Navigate to **Advanced Options → Boot Order → NVMe/USB Boot** and confirm. This sets NVMe as the first boot device with SD card as fallback — useful if you ever need to recover.

Reboot. The Pi should now boot from the SSD. You can confirm with:

```bash
findmnt / | grep nvme   # should show /dev/nvme0n1p2 as the root device
```

Once booting from SSD, the SD card can be removed (or left in as a recovery fallback).

---

## 2. First Boot

Connect the display ribbon cable to the Pi's DSI port and power on.

Wait about 60 seconds for first-boot setup to complete, then SSH in:

```bash
ssh pi@td5dash.local
```

If `td5dash.local` doesn't resolve, check your router's DHCP client list for the Pi's IP address.

---

## 3. Clone the Repository

```bash
cd ~
git clone https://github.com/JRogers83/TD5-Dash.git
cd TD5-Dash
```

---

## 4. Create Your `.env` File

The `.env` file holds all your credentials and service configuration. Copy the example and fill it in:

```bash
cp .env.example .env
nano .env
```

Set the mock toggles first. Start with everything mocked so you can verify the display and UI before adding real credentials one at a time:

```ini
TD5_MOCK=1
VICTRON_MOCK=1
STARLINK_MOCK=1
WEATHER_MOCK=0     # Weather needs no credentials — safe to enable now
SPOTIFY_MOCK=0     # Enable if you have Spotify credentials ready
```

Fill in the sections that apply. See `.env.example` for full documentation of every variable. The Victron and Spotify sections below explain how to obtain the values you need.

Save and exit: `Ctrl+X → Y → Enter`

---

## 5. Run the Setup Script

The setup script installs all system packages, creates the Python virtualenv, installs and configures Raspotify, sets up PulseAudio for the spectrum visualiser, configures console autologin, adds the display dtoverlay, installs Plymouth splash, and enables the systemd service.

```bash
sudo ./deploy/setup.sh
```

The script is idempotent — safe to re-run if anything needs correcting.

When it finishes you should see:

```
╔══════════════════════════════════╗
║  Setup complete — reboot to go.  ║
╚══════════════════════════════════╝
```

Then reboot:

```bash
sudo reboot
```

After the reboot the Pi will:
- Show the Land Rover Plymouth splash screen during boot
- Auto-login to the console
- Launch Chromium in kiosk mode
- Start the FastAPI backend as a systemd service

The dashboard should appear on the display within about 10–15 seconds of the splash screen.

---

## 6. Verify the Backend Service

SSH back in and check the service is running:

```bash
systemctl status td5-dash
```

To watch live logs:

```bash
journalctl -fu td5-dash
```

The backend is also reachable from your development machine at `http://td5dash.local:8000`.

---

## 7. Victron BLE Setup

You need the MAC address and encryption key for each Victron device.

**In the Victron Connect app (on your phone):**
1. Connect to the device
2. Tap the **three-dot menu → Product info**
3. Note the **MAC address**
4. Tap **Encryption key** — if none is shown, tap to generate one first

Add the values to `.env`:

```ini
VICTRON_MOCK=0

VICTRON_SHUNT_MAC=aa:bb:cc:dd:ee:ff
VICTRON_SHUNT_KEY=0123456789abcdef0123456789abcdef

VICTRON_MPPT_MAC=aa:bb:cc:dd:ee:ff
VICTRON_MPPT_KEY=0123456789abcdef0123456789abcdef

VICTRON_ORION_MAC=aa:bb:cc:dd:ee:ff
VICTRON_ORION_KEY=0123456789abcdef0123456789abcdef
```

Leave a MAC/KEY pair blank to skip that device.

The Pi needs Bluetooth to reach the devices. Confirm BT is enabled:

```bash
rfkill list bluetooth
# Should show: Soft blocked: no
```

Then restart the service to pick up the new credentials:

```bash
sudo systemctl restart td5-dash
```

---

## 8. Spotify Setup

Spotify requires a one-time OAuth flow. Run this on your **development machine** (not the Pi) — it opens a browser window.

**Step 1 — Create a Spotify Developer app:**
1. Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
2. Create an app (name and description can be anything)
3. Add redirect URI: `http://127.0.0.1:8888/callback` (must be exactly this — not localhost)
4. Note your **Client ID** and **Client Secret**

**Step 2 — Run the auth helper:**

```bash
# On your dev machine, from the repo root:
SPOTIFY_CLIENT_ID=your_id SPOTIFY_CLIENT_SECRET=your_secret python tools/spotify_auth_setup.py
```

A browser window opens. Log in and approve. The script prints your refresh token.

**Step 3 — Add to `.env` on the Pi:**

```ini
SPOTIFY_MOCK=0
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
SPOTIFY_REFRESH_TOKEN=your_refresh_token
```

Restart the service:

```bash
sudo systemctl restart td5-dash
```

The Pi will now appear as **Defender** in your Spotify device picker (via Raspotify). Playing to it will show the track on the dashboard and drive the spectrum visualiser.

---

## 9. OBD / K-Line Setup

The KKL cable connects to the vehicle's OBD-II port. Before testing in the vehicle, verify the cable works at your desk using the test tool:

```bash
# On your dev machine (Windows), from the repo root:
python tools/td5_obd_test.py
```

It will ask whether you are in the vehicle. Say no for the desk test — this runs stages 1–7 (USB detection, PyFtdi, bitbang mode, protocol and decoder self-tests) without needing the car.

**Windows driver requirement:** PyFtdi bypasses the standard FTDI VCP driver and requires libusbK instead. If stage 3 fails:

1. Download **Zadig** from [zadig.akeo.ie](https://zadig.akeo.ie)
2. Plug in the KKL cable
3. Options → List All Devices → select the FT232R device
4. Set driver to **libusbK** → Replace Driver

After the driver swap, re-run the test. The cable will no longer appear as a COM port — that is expected.

When in the vehicle with the Pi connected to the OBD port:

```bash
python tools/td5_obd_test.py   # answer yes to "in vehicle"
```

Once all 11 stages pass, enable live OBD data:

```ini
# In .env on the Pi:
TD5_MOCK=0
```

```bash
sudo systemctl restart td5-dash
```

---

## 10. Weather Location

Weather uses the free Open-Meteo API — no account or key required. Set your coordinates and a display name:

```ini
WEATHER_MOCK=0
WEATHER_LAT=52.6309
WEATHER_LON=1.2974
WEATHER_LOCATION=Norwich, UK
```

Decimal degrees: positive = North/East, negative = South/West.

---

## 11. Starlink

Starlink polls the dish's local gRPC API at `192.168.100.1` — this only works when the Pi is connected to the Starlink Mini's WiFi network. No credentials needed.

```ini
STARLINK_MOCK=0
```

GPS data (shown on the Starlink view and used by the weather service for location) requires enabling it in the Starlink app:
**Settings → Advanced → Debug data → GPS**

---

## 12. Display Orientation

The display is set up by `setup.sh` via a dtoverlay in `/boot/firmware/config.txt`:

```
dtoverlay=vc4-kms-dsi-waveshare-panel,7_9_inch
```

The Pi outputs 1280×400 landscape. If the image appears rotated, check that this line is present and reboot.

---

## Useful Commands

| Command | What it does |
|---------|-------------|
| `systemctl status td5-dash` | Check if the backend is running |
| `journalctl -fu td5-dash` | Follow live backend logs |
| `sudo systemctl restart td5-dash` | Restart the backend (picks up `.env` changes) |
| `sudo systemctl stop td5-dash` | Stop the backend |
| `rfkill list` | Check WiFi and Bluetooth are unblocked |
| `vcgencmd measure_temp` | Read Pi CPU temperature |
| `systemd-analyze blame` | Identify slow boot units |

---

## Updating

From the dashboard Settings page, tap the **Update** button. This runs `git pull`, reinstalls any new Python dependencies, and restarts the service automatically. The dashboard will reconnect within a few seconds.

Alternatively via SSH:

```bash
cd ~/TD5-Dash
git pull
.venv/bin/pip install -q -r backend/requirements.txt
sudo systemctl restart td5-dash
```
