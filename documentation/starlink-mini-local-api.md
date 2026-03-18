# Starlink Mini — Local API Reference
## For Raspberry Pi Vehicle Display Project

> **Purpose:** This document describes all data accessible from a Raspberry Pi (or any LAN-connected device) via the Starlink Mini's local gRPC API. It is intended as a context document for Claude Code when building a vehicle-mounted display.

---

## Hardware Context: Starlink Mini

The Starlink Mini is an ultra-compact, all-in-one satellite terminal with an **integrated Wi-Fi router built directly into the dish**. Unlike the Standard dish, there is no separate external router unit. Key specs relevant to this project:

| Property | Detail |
|---|---|
| Dimensions | 299 × 260 × 38.5 mm |
| Weight | 1.1 kg |
| Integrated Wi-Fi | Yes — Wi-Fi 5 (802.11 a/b/g/n/ac), 2.4 GHz + 5 GHz |
| Ethernet Port | Single port on the back of the dish |
| Power Input | 12–48 V DC, 60 W peak |
| Idle Power Draw | ~15 W |
| Active Power Draw | ~20–40 W typical |
| IP Rating | IP67 |
| Bypass Mode | Supported — disables integrated Wi-Fi router, Ethernet becomes the sole uplink |

### Network Topology Options

**Option A — Wi-Fi (integrated router active)**
The Pi connects wirelessly or via an external switch to the Mini's built-in Wi-Fi or Ethernet. The dish IP `192.168.100.1` is natively reachable.

**Option B — Ethernet with bypass mode**
The Mini's Ethernet port connects to a third-party router/switch. The Mini acts as a pure modem. The gRPC API at `192.168.100.1` is **still accessible** but requires a static route on the router pointing `192.168.100.0/24` to the WAN interface. Without this, the API will be unreachable.

```
Static route (add to router if using bypass mode):
  Network:   192.168.100.0
  Mask:      255.255.255.0
  Gateway:   192.168.100.1
  Interface: WAN (Ethernet to Mini)
```

> ⚠️ **Important:** If the Pi is directly cabled to the Mini's Ethernet port in bypass mode (no intermediate router), once the Pi obtains a CGNAT IP from Starlink, it loses access to `192.168.100.1` unless a static route is manually added:
> ```bash
> sudo ip route add 192.168.100.0/24 dev eth0
> ```

---

## The Local gRPC API

### Overview

The Starlink Mini (like all Starlink terminals) runs a **gRPC server locally** on the dish hardware. This API is:

- Available at `192.168.100.1`, port `9200` (HTTP/2 gRPC)
- Also available at `192.168.100.1`, port `9201` (gRPC-Web / HTTP/1.1 — used by the mobile app)
- **No authentication required** from the local network
- **Not exposed to the internet** — local LAN only
- Uses Protocol Buffers (protobuf); definitions have been reverse-engineered by the community and are stable enough for production use

### Python Library (Recommended for Pi)

The best-maintained library for Python is [`sparky8512/starlink-grpc-tools`](https://github.com/sparky8512/starlink-grpc-tools):

```bash
pip install starlink-grpc-core
```

Requires Python 3.7+. Works natively on Raspberry Pi OS.

### Quick Test (grpcurl)

```bash
# Install grpcurl
sudo apt install grpcurl   # or download from GitHub releases

# Get current dish status
grpcurl -plaintext -d '{"get_status":{}}' \
  192.168.100.1:9200 SpaceX.API.Device.Device/Handle

# Get history data
grpcurl -plaintext -d '{"get_history":{}}' \
  192.168.100.1:9200 SpaceX.API.Device.Device/Handle
```

---

## Available Data Groups

### 1. Status (`status`)

Real-time snapshot of the dish's current operating state. Poll at 1–5 second intervals for live display.

| Field | Type | Description |
|---|---|---|
| `id` | string | Dish hardware ID / serial |
| `hardware_version` | string | Dish hardware revision |
| `software_version` | string | Current firmware version |
| `state` | enum | Connection state: `UNKNOWN`, `BOOTING`, `SEARCHING`, `CONNECTED`, `OFFLINE`, `SLEEPING` |
| `uptime` | int (seconds) | Seconds since last boot |
| `snr` | float | Signal-to-noise ratio (higher = better) |
| `seconds_to_first_nonempty_slot` | float | Time until next satellite pass (0 when connected) |
| `pop_ping_drop_rate` | float | Fraction of pings dropped (0.0–1.0) |
| `downlink_throughput_bps` | float | Current download speed in bits per second |
| `uplink_throughput_bps` | float | Current upload speed in bits per second |
| `pop_ping_latency_ms` | float | Round-trip latency to Starlink PoP in milliseconds |
| `fraction_obstructed` | float | Fraction of sky view currently obstructed (0.0–1.0) |
| `currently_obstructed` | bool | True if dish is currently obstructed |
| `seconds_obstructed` | float | Cumulative seconds obstructed (rolling window) |
| `country_code` | string | Country code the terminal is registered in |
| `utc_offset_s` | int | UTC offset in seconds |

**Python example:**
```python
import starlink_grpc

context = starlink_grpc.ChannelContext()
status = starlink_grpc.status_data(context)
# status is a dict with all fields above
print(f"State: {status['state']}")
print(f"Download: {status['downlink_throughput_bps'] / 1e6:.1f} Mbps")
print(f"Latency: {status['pop_ping_latency_ms']:.0f} ms")
```

---

### 2. Location / GPS (`location`)

Precise real-time GPS coordinates of the dish. **Disabled by default — must be enabled manually.**

| Field | Type | Description |
|---|---|---|
| `latitude` | float | Latitude in decimal degrees |
| `longitude` | float | Longitude in decimal degrees |
| `altitude` | float | Altitude in metres above sea level |

#### ⚠️ Enabling GPS Access (One-Time Setup)

1. Open the **Starlink mobile app** (iOS or Android — cannot be done via browser)
2. Log in with your Starlink account
3. Go to **Settings → Advanced → Debug Data**
4. Scroll down to the **STARLINK LOCATION** section
5. Toggle **"Allow access on local network"** → ON

This setting persists across reboots. Once enabled, any device on the local network can read the GPS position — ideal for a vehicle install.

**Python example:**
```python
location = starlink_grpc.location_data(context)
lat = location['latitude']
lon = location['longitude']
alt = location['altitude']
print(f"Position: {lat:.6f}, {lon:.6f} @ {alt:.0f}m")
```

> **Note:** On the Mini specifically, GPS data updates approximately every 1 second once a satellite lock is established. Accuracy is within a few metres. This is derived from the terminal's own GPS receiver, not from the satellite signal, so it reflects the vehicle's actual physical location.

---

### 3. Obstruction Detail (`obstruction_detail`)

Detailed sky obstruction data — useful for showing signal quality issues due to terrain, trees, or structures in the vehicle's surroundings.

| Field | Type | Description |
|---|---|---|
| `currently_obstructed` | bool | Whether dish is currently blocked |
| `fraction_obstructed` | float | Overall obstruction fraction (0.0–1.0) |
| `valid_s` | float | Seconds of valid obstruction data collected |
| `wedge_fraction_obstructed[12]` | float array | Obstruction per 30° wedge of sky (12 segments, starting North) |
| `wedge_abs_fraction_obstructed[12]` | float array | Absolute obstruction fraction per wedge |

The 12-wedge array divides the full sky view into 30° segments clockwise from North, enabling a directional obstruction map. This can be rendered as a polar/radar chart on the display.

---

### 4. Alert Detail (`alert_detail`)

Boolean flags for any active alerts or fault conditions on the dish.

| Field | Description |
|---|---|
| `alert_motors_stuck` | Dish motors are stuck/cannot move |
| `alert_thermal_throttle` | Dish is throttling due to overheating |
| `alert_thermal_shutdown` | Dish has shut down due to excess temperature |
| `alert_mast_not_near_vertical` | Mast tilt exceeds tolerance (less relevant for roof mounts) |
| `alert_unexpected_location` | Dish is in an unexpected location vs. service address |
| `alert_slow_ethernet_speeds` | Ethernet performance degraded |
| `alert_roaming` | Terminal is operating in roaming mode |
| `alert_install_pending` | Installation incomplete |
| `alert_is_heating` | Snow/ice heating mode is active |
| `alert_power_supply_thermal_throttle` | PSU thermal throttle active |
| `alert_is_power_save_idle` | Terminal is in power save idle mode |

---

### 5. History — Ping Drop (`ping_drop`)

Computed statistics from the rolling ~12-hour, 1-second-resolution history buffer. Useful for trend charts on the display.

| Field | Description |
|---|---|
| `samples` | Number of samples in the analysis window |
| `end_counter` | Rolling sample counter value at end of window |
| `total_ping_drop` | Total dropped pings (divide by `samples` for loss ratio) |
| `count_full_ping_drop` | Count of samples with 100% ping loss |
| `count_obstructed` | Samples where dish was obstructed |
| `total_obstructed_ping_drop` | Ping drops attributable to obstruction |
| `count_full_obstructed_ping_drop` | Full drops during obstruction |
| `count_unscheduled` | Samples with no scheduled satellite |
| `total_unscheduled_ping_drop` | Drops during unscheduled gaps |

---

### 6. History — Ping Latency (`ping_latency`)

Latency statistics over the history window.

| Field | Description |
|---|---|
| `mean_all_ping_latency` | Mean latency across all samples (ms) |
| `deciles_all_ping_latency[11]` | Latency percentile distribution (0th–100th) |
| `mean_full_ping_latency` | Mean latency on non-dropped samples |
| `deciles_full_ping_latency[11]` | Percentile distribution on non-dropped samples |
| `stdev_full_ping_latency` | Standard deviation of latency |

---

### 7. History — Loaded Latency (`ping_loaded_latency`)

Latency measured under network load — reflects real-world latency when the link is being used.

| Field | Description |
|---|---|
| `load_bucket_samples[15]` | Sample count per load bucket |
| `load_bucket_min_latency[15]` | Minimum latency per load bucket (ms) |
| `load_bucket_median_latency[15]` | Median latency per load bucket (ms) |
| `load_bucket_max_latency[15]` | Maximum latency per load bucket (ms) |

---

### 8. History — Usage (`usage`)

Data throughput totals over the history window.

| Field | Description |
|---|---|
| `download_usage` | Total bytes downloaded in the window |
| `upload_usage` | Total bytes uploaded in the window |

---

### 9. Bulk History (`bulk_history`)

Raw per-second time-series arrays from the history buffer (~43,200 samples = 12 hours at 1 Hz). Each array contains one value per second. Use this for plotting detailed time-series charts.

| Array | Description |
|---|---|
| `samples` | Number of valid samples in buffer |
| `pop_ping_drop_rate[]` | Per-second ping drop rate |
| `pop_ping_latency_ms[]` | Per-second latency (ms), -1 if no sample |
| `downlink_throughput_bps[]` | Per-second download throughput |
| `uplink_throughput_bps[]` | Per-second upload throughput |
| `snr[]` | Per-second SNR values (note: not all firmware versions populate this) |
| `scheduled[]` | bool — whether a satellite was scheduled each second |
| `obstructed[]` | bool — whether dish was obstructed each second |

---

## Python Code Patterns

### Polling Loop (for display updates)

```python
import starlink_grpc
import time

context = starlink_grpc.ChannelContext(target="192.168.100.1:9200")

while True:
    try:
        # Status (real-time)
        status, obstruction, alerts = starlink_grpc.status_data(context)
        
        down_mbps = status['downlink_throughput_bps'] / 1_000_000
        up_mbps   = status['uplink_throughput_bps']   / 1_000_000
        latency   = status['pop_ping_latency_ms']
        state     = status['state']
        obstructed = obstruction['currently_obstructed']

        print(f"↓{down_mbps:.1f} Mbps  ↑{up_mbps:.1f} Mbps  {latency:.0f}ms  [{state}]")
        
        # GPS (if enabled)
        location = starlink_grpc.location_data(context)
        lat, lon, alt = location['latitude'], location['longitude'], location['altitude']
        print(f"GPS: {lat:.6f}, {lon:.6f} @ {alt:.0f}m")

    except starlink_grpc.GrpcError as e:
        print(f"gRPC error: {e}")
    
    time.sleep(2)
```

### Converting Throughput for Display

```python
def format_throughput(bps: float) -> str:
    mbps = bps / 1_000_000
    if mbps >= 1:
        return f"{mbps:.1f} Mbps"
    kbps = bps / 1_000
    return f"{kbps:.0f} Kbps"
```

### Using grpcurl Directly (Shell / Subprocess)

```bash
# Status
grpcurl -plaintext -d '{"get_status":{}}' \
  192.168.100.1:9200 SpaceX.API.Device.Device/Handle

# History
grpcurl -plaintext -d '{"get_history":{}}' \
  192.168.100.1:9200 SpaceX.API.Device.Device/Handle

# Location (requires GPS enabled in app)
# Location is embedded in the get_status response under the 'dish_gps' field
```

---

## Dish Control Commands

The following commands can be issued to the Mini via the gRPC API. Use with care, especially reboot and stow in a moving vehicle.

```python
# Reboot the dish
starlink_grpc.reboot(context)

# Stow (fold/park) the dish
starlink_grpc.dish_stow(context)

# Unstow (deploy) the dish
starlink_grpc.dish_stow(context, unstow=True)
```

Or via grpcurl:
```bash
# Reboot
grpcurl -plaintext -d '{"reboot":{}}' \
  192.168.100.1:9200 SpaceX.API.Device.Device/Handle

# Stow
grpcurl -plaintext -d '{"dish_stow":{}}' \
  192.168.100.1:9200 SpaceX.API.Device.Device/Handle
```

---

## Starlink Mini — Vehicle Install Notes

### Power
The Mini accepts **12–48 V DC** natively, making it well-suited for vehicle installation from a 12 V (or 24 V) supply. The included mains adapter outputs 30 V @ 2 A (60 W). For a vehicle install, a suitable DC–DC step-up converter to 30 V is the cleanest approach.

Typical current draw from a 12 V supply:
- Idle: ~1.25 A (~15 W)
- Active: ~1.7–3.3 A (~20–40 W)
- Peak (boot/search): up to ~5 A

### Networking for the Pi
For a Pi-only setup in the Defender, the simplest topology is:

```
Starlink Mini (integrated Wi-Fi router active)
       │
       │ Wi-Fi or Ethernet
       │
Raspberry Pi
  └── gRPC polling → 192.168.100.1:9200
  └── Dashboard display
```

If a separate router/switch is used in the vehicle:

```
Starlink Mini (Ethernet) ──► Router/Switch ──► Pi (Ethernet)
                                                     │
                                         Static route 192.168.100.0/24
                                         via WAN interface to access gRPC
```

### Connection State Values

When parsing `state` from the status response, expect these values in a mobile/roaming context:

| State | Meaning |
|---|---|
| `CONNECTED` | Actively linked to a satellite — normal operation |
| `SEARCHING` | Scanning for a satellite — brief gaps in coverage |
| `BOOTING` | Terminal starting up (30–90 seconds after power-on) |
| `SLEEPING` | Power-save sleep mode active |
| `OFFLINE` | No connection, not actively searching |
| `UNKNOWN` | State indeterminate |

---

## Reference Links

| Resource | URL |
|---|---|
| sparky8512/starlink-grpc-tools (Python) | https://github.com/sparky8512/starlink-grpc-tools |
| PyPI package (starlink-grpc-core) | https://pypi.org/project/starlink-grpc-core/ |
| Home Assistant Starlink integration | https://www.home-assistant.io/integrations/starlink/ |
| Starlink gRPC Golang (protoset archive) | https://github.com/clarkzjw/starlink-grpc-golang |
| DISHYtech — Bypass mode guide | https://www.dishytech.com/how-to-bypass-the-starlink-router/ |

---

## API Stability Warning

The Starlink gRPC API is **not officially documented or supported by SpaceX**. The protobuf definitions were reverse-engineered from the dish firmware. While the community has maintained these tools reliably for several years and they are stable enough for production use, SpaceX may change the API in a firmware update without notice. Pin your `starlink-grpc-core` package version and test after any Starlink firmware update.

---

*Document prepared for vehicle display project — Starlink Mini on Land Rover Defender TD5.*
*Last updated: March 2026*
