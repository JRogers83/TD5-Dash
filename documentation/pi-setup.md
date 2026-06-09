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
| USB hub (optional) | For game controllers and other HID devices — see note below |

> **USB Hub:** Connect to one of the Pi 5's USB 2.0 ports (the black ports), not a USB 3.0 port (the blue ports). Game controllers and HID devices do not enumerate correctly when connected via USB 3.0 on the Pi 5.

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

Pi OS Lite does not include git by default. Install it first:

```bash
sudo apt install git -y
```

Then clone the repository:

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

> **Important:** MAC addresses must be in colon-separated UPPERCASE format, e.g. `E2:3A:96:CF:21:76`. Without colons (e.g. `E23A96CF2176`), `victron_ble` never matches advertisements and the scanner silently receives no data.

```ini
VICTRON_MOCK=0

VICTRON_SHUNT_MAC=E2:3A:96:CF:21:76
VICTRON_SHUNT_KEY=0123456789abcdef0123456789abcdef

VICTRON_MPPT_MAC=E2:3A:96:CF:21:76
VICTRON_MPPT_KEY=0123456789abcdef0123456789abcdef

VICTRON_ORION_MAC=E2:3A:96:CF:21:76
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

The TD5 uses a proprietary K-Line protocol (not standard OBD-II) — ELM327 adapters will not work. You need a **VAG COM KKL 409.1 cable with a genuine FTDI FT232RL chip**.

**Windows driver requirement:** PyFtdi bypasses the standard FTDI VCP driver and requires libusbK. If the cable isn't detected:

1. Download **Zadig** from [zadig.akeo.ie](https://zadig.akeo.ie)
2. Plug in the KKL cable
3. Options → List All Devices → select the FT232R device
4. Set driver to **libusbK** → Replace Driver

The cable will no longer appear as a COM port — that is expected.

**Desk test (no vehicle needed):**

```bash
python tools/td5_diag.py
```

This runs stages 1–2: USB/FTDI detection, bitbang verification, frame checksum tests, and all 15 seed-key algorithm vectors. No cable or vehicle required for these stages.

**Vehicle test (ignition ON or engine running, cable in OBD port):**

```bash
python tools/td5_diag.py --vehicle --verbose
```

This runs all 7 stages: fast-init, StartCommunication, StartDiagnosticSession, seed-key authentication, PID probe, and continuous polling. Results are logged to a timestamped file in `tools/`.

If the first timing doesn't connect, try a wider sweep:

```bash
python tools/td5_diag.py --vehicle --verbose --timing-sweep
```

**Important notes from vehicle testing:**
- Some PIDs (RPM, battery, throttle) only respond when the **engine is running** — ignition-only will show temps, speed, and MAP but not RPM or battery
- If the tool reports `7F 81 10` (generalReject), the ECU is stuck in a previous session — the tool sends StopCommunication automatically to clear this, but cycling ignition OFF for 10 seconds is the nuclear option
- The OBD connector must be firmly seated — engine vibration can cause intermittent failures on a marginal contact

Once the diagnostic tool passes all stages, enable live OBD data:

```ini
# In .env on the Pi:
TD5_MOCK=0
```

```bash
sudo systemctl restart td5-dash
```

Full protocol details are in `documentation/TD5-ECU-Confirmed-Protocol.md`.

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

GPS data is provided by the dedicated USB GPS receiver — see [Section 12 — GPS Setup](#12-gps-setup-usb-receiver) below. The Starlink view shows the GPS fix status from that receiver, not from the dish.

---

## 12. Display

`setup.sh` configures the display automatically. It writes three entries to `/boot/firmware/config.txt`:

```
dtoverlay=vc4-kms-v3d
dtoverlay=vc4-kms-dsi-waveshare-panel,7_9_inch
dtparam=i2c_arm=on
```

And prepends a video mode parameter to `/boot/firmware/cmdline.txt`:

```
video=DSI-2:400x1280e,rotate=DISPLAY_ROTATION
```

The `video=` rotation affects the framebuffer console and Plymouth splash screen. It has **no effect** on X11/Chromium — that rotation is handled separately by `xrandr` in `deploy/xinitrc`.

**Display rotation** is set via `DISPLAY_ROTATION` in `.env` — valid values are `0`, `90`, `180`, `270`. Default is `270` (landscape, power cable enters from the bottom). The xrandr rotation is read from `.env` on each kiosk session start (just reboot after changing). The `video=` parameter and Plymouth logo are updated when you re-run `setup.sh`.

**Touch input** uses the Goodix capacitive controller via the evdev X11 driver. `setup.sh` writes a coordinate transformation matrix to `/etc/X11/xorg.conf.d/40-touch-rotation.conf` matching the display rotation. Re-run `setup.sh` after changing `DISPLAY_ROTATION` to update the touch mapping.

`i2c_arm=on` is required for the capacitive touchscreen (Goodix I2C controller).

**If the screen is dark after reboot**, verify these lines are present:

```bash
grep -E "vc4-kms|i2c_arm" /boot/firmware/config.txt
grep "video=DSI-2" /boot/firmware/cmdline.txt
```

If either is missing, re-run `sudo ./deploy/setup.sh` and reboot.

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

---

## 12. GPS Setup (USB Receiver)

### Hardware
Connect the u-blox UBX-G7020-KT to any USB port. It appears as `/dev/ttyACM0`.

### Software (handled by setup.sh)
```bash
sudo apt install gpsd gpsd-clients python3-gps
```

`setup.sh` configures `/etc/default/gpsd` automatically. If needed, verify:
```
DEVICES="/dev/ttyACM0"
GPSD_OPTIONS="-n"
START_DAEMON="true"
```

### Verify
```bash
# Check device is recognised
ls /dev/ttyACM*

# Test gpsd is reading data
cgps -s
# Should show fix within ~90 seconds with clear sky view

# Confirm Python library can read
python3 -c "import gps; s = gps.gps(); print(next(s))"
```

### Enable real GPS
Set `GPS_MOCK=0` in `.env` and restart the service.

> **hw-verify:** Device path `/dev/ttyACM0` is standard for this receiver on Bookworm
> but may vary. Run `ls /dev/ttyACM*` after plugging in to confirm. First fix after
> cold boot may take up to 90 seconds outdoors with clear sky view.

---

## Witty Pi 5 HAT+ Setup

### Wiring
```
Leisure battery (+12V)
  ├─→ 12V relay (coil on ignition feed)  →  Witty Pi VIN screw terminal
  └─→ 12V→5V epoxy buck converter (permanent)  →  Witty Pi USB-C
```
Place inline fuse (3A) on the supply to the relay.

### Hardware Installation
1. Fit Witty Pi 5 onto the Pi GPIO header (uses GPIO 4 and 17 internally)
2. Connect Witty Pi VIN screw terminal to the relay switched output
3. Connect the 5V buck converter output to Witty Pi USB-C

### Software Installation

Enable I2C, then install the `wp5` package (two commands — no interactive script):

```bash
sudo raspi-config nonint do_i2c 0
wget https://www.uugear.com/repo/WittyPi5/wp5_latest.deb && sudo apt install ./wp5_latest.deb
```

This installs the `wp5d` daemon (log: `/var/log/wp5d.log`) and the `wp5` CLI tool.

### Shutdown Mechanism

No shell script hook or I2C polling is needed. When wp5d detects VIN loss it calls `shutdown -h now`. systemd sends SIGTERM to the td5-dash service, which triggers the FastAPI lifespan teardown. Pre-shutdown cleanup (unfreeze Chromium if Doom is running, flush SQLite WAL) runs automatically at that point before the service exits.

### Configuration

Set in `.env`:
```
WITTYPI_ENABLED=1
# IGNITION_SENSE_PIN=   ← leave empty when using Witty Pi
```

Configure VIN shutdown threshold using `wp5` to match the relay drop-out voltage on your specific relay.

### Shutdown Handshake (I2C register 71)

The Witty Pi 5 uses I2C register 71 at address 0x51 for shutdown signalling:

| Value | Meaning |
|-------|---------|
| 0 | No action |
| 1 | Witty Pi requests Pi to turn off |
| 2 | Pi acknowledges: shutting down |
| 3 | Pi acknowledges: rebooting |

When wp5d triggers a shutdown, it sets register 71 = 1. The Pi should ideally respond with register 71 = 2 after cleanup. This acknowledgement write is not yet implemented in `wittypi_service.py` — see the TODO comment there for the required `smbus2` call. Verify behaviour with physical hardware first.

### Verify

```bash
# Confirm I2C address 0x51 visible (no conflict with touchscreen at 0x38)
i2cdetect -y 1

# Test pre-shutdown cleanup endpoint manually
curl -X POST http://localhost:8000/system/shutdown-prepare
# Expected: {"ok": true, "cleaned_up": ["db_checkpointed", "shutdown_logged"]}
```

> **hw-verify remaining:** VIN threshold, shutdown delay, beforeShutdown.sh hook path on the emulated USB flash drive, and register-71 handshake behaviour all require the physical unit to confirm.

---

## Doom Mode — Controllers

Doom mode (LZDoom split-screen, accessed from the Settings view) requires two USB game controllers. Connect them via a USB hub attached to one of the Pi 5's **USB 2.0 ports** (black ports — see the USB Hub note in the Hardware section above).

### Confirmed working controllers (tested 2026-06-07)

| Slot | Device | USB ID |
|------|--------|--------|
| js0 | Personal Communication Systems SNES Gamepad | 0810:e501 |
| js1 | DragonRise Inc. PC Twin Shock Gamepad | 0079:0006 |

Both enumerate correctly under the standard Linux HID driver with no additional configuration. The two controllers use different chipsets — this is required for the LZDoom per-device joystick INI configuration (`games/doom/lzdoom-p1.ini` and `games/doom/lzdoom-p2.ini`) to apply independently to each controller.

### Verify controllers are recognised

```bash
ls /dev/input/js*
# Expected: /dev/input/js0  /dev/input/js1

# Check the pi user is in the input group (required for js* access):
id pi | grep input
```

If the `input` group is missing, `deploy/setup.sh` adds it — re-run the script or add manually:

```bash
sudo usermod -aG input pi
# Log out and back in for the change to take effect
```
