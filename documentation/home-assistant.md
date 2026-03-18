# TD5 Dash — Home Assistant Integration

The FastAPI backend exposes a REST state API that Home Assistant can poll to
create sensors from live vehicle data. Combined with a Cloudflare Tunnel, this
works from anywhere — including when the vehicle is away from home.

---

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/state` | All topics — engine, victron, starlink, gps, system, spotify, weather |
| `GET /api/state/{topic}` | Single topic — e.g. `/api/state/victron` |

### Response format

```json
{
  "server_time": "2026-03-18T14:23:01Z",
  "topics": {
    "victron": {
      "data": { "soc_pct": 87, "voltage_v": 13.2, "current_a": -2.1, ... },
      "updated_at": "2026-03-18T14:23:00Z",
      "stale": false
    },
    "engine": {
      "data": { "rpm": 850, "coolant_temp_c": 88, ... },
      "updated_at": "2026-03-18T14:22:58Z",
      "stale": false
    }
  }
}
```

`stale: true` means no data has been received for that topic in the last 30 seconds
— the vehicle is off, the service has crashed, or the connection is lost. HA sensors
will show their last known value; use `stale` to drive availability or alerts.

---

## Cloudflare Access — Service Token Setup

HA needs to authenticate with Cloudflare Access to reach the tunnel without an
interactive login prompt. Use a **Service Token** for machine-to-machine auth.

1. Go to **Zero Trust → Access → Service Auth → Service Tokens**
2. Click **Create Service Token**, give it a name (e.g. `home-assistant`)
3. Copy the **Client ID** and **Client Secret** — you will not see the secret again
4. In your Access policy for the td5-dash application, add a rule:
   - **Service Token** → select the token you just created
5. Add the credentials to HA's `secrets.yaml`:
   ```yaml
   td5dash_cf_client_id: "abc123.access.example.com"
   td5dash_cf_client_secret: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
   ```

HA will include these as `CF-Access-Client-Id` and `CF-Access-Client-Secret`
headers on every request.

---

## Home Assistant Configuration

Add to `configuration.yaml` (or a dedicated `rest.yaml` if you use packages):

```yaml
rest:
  - resource: "https://td5dash.yourdomain.com/api/state"
    scan_interval: 10
    headers:
      CF-Access-Client-Id: !secret td5dash_cf_client_id
      CF-Access-Client-Secret: !secret td5dash_cf_client_secret
    sensor:

      # ── Victron ─────────────────────────────────
      - name: "Defender Battery SoC"
        unique_id: defender_battery_soc
        value_template: "{{ value_json.topics.victron.data.soc_pct }}"
        unit_of_measurement: "%"
        device_class: battery
        state_class: measurement

      - name: "Defender Battery Voltage"
        unique_id: defender_battery_voltage
        value_template: "{{ value_json.topics.victron.data.voltage_v }}"
        unit_of_measurement: "V"
        device_class: voltage
        state_class: measurement

      - name: "Defender Battery Current"
        unique_id: defender_battery_current
        value_template: "{{ value_json.topics.victron.data.current_a }}"
        unit_of_measurement: "A"
        device_class: current
        state_class: measurement

      - name: "Defender Solar Today"
        unique_id: defender_solar_today
        value_template: "{{ value_json.topics.victron.data.solar_yield_wh }}"
        unit_of_measurement: "Wh"
        device_class: energy
        state_class: total_increasing

      - name: "Defender Charge State"
        unique_id: defender_charge_state
        value_template: "{{ value_json.topics.victron.data.charge_state }}"

      # ── Engine ──────────────────────────────────
      - name: "Defender RPM"
        unique_id: defender_rpm
        value_template: "{{ value_json.topics.engine.data.rpm }}"
        unit_of_measurement: "rpm"
        state_class: measurement

      - name: "Defender Coolant Temp"
        unique_id: defender_coolant_temp
        value_template: "{{ value_json.topics.engine.data.coolant_temp_c }}"
        unit_of_measurement: "°C"
        device_class: temperature
        state_class: measurement

      - name: "Defender Battery Voltage (Engine)"
        unique_id: defender_engine_battery_v
        value_template: "{{ value_json.topics.engine.data.battery_v }}"
        unit_of_measurement: "V"
        device_class: voltage
        state_class: measurement

      # ── Starlink ─────────────────────────────────
      - name: "Defender Starlink State"
        unique_id: defender_starlink_state
        value_template: "{{ value_json.topics.starlink.data.state }}"

      - name: "Defender Starlink Download"
        unique_id: defender_starlink_down
        value_template: "{{ value_json.topics.starlink.data.down_mbps }}"
        unit_of_measurement: "Mbit/s"
        state_class: measurement

      - name: "Defender Starlink Upload"
        unique_id: defender_starlink_up
        value_template: "{{ value_json.topics.starlink.data.up_mbps }}"
        unit_of_measurement: "Mbit/s"
        state_class: measurement

      - name: "Defender Starlink Latency"
        unique_id: defender_starlink_latency
        value_template: "{{ value_json.topics.starlink.data.latency_ms }}"
        unit_of_measurement: "ms"
        state_class: measurement

      # ── GPS ──────────────────────────────────────
      - name: "Defender Latitude"
        unique_id: defender_gps_lat
        value_template: "{{ value_json.topics.gps.data.lat }}"
        unit_of_measurement: "°"

      - name: "Defender Longitude"
        unique_id: defender_gps_lon
        value_template: "{{ value_json.topics.gps.data.lon }}"
        unit_of_measurement: "°"

      # ── System ───────────────────────────────────
      - name: "Defender Pi CPU Temp"
        unique_id: defender_pi_cpu_temp
        value_template: "{{ value_json.topics.system.data.cpu_temp_c }}"
        unit_of_measurement: "°C"
        device_class: temperature
        state_class: measurement

      - name: "Defender Pi Uptime"
        unique_id: defender_pi_uptime
        value_template: "{{ value_json.topics.system.data.uptime_s }}"
        unit_of_measurement: "s"
        state_class: total_increasing

      # ── Staleness (availability tracking) ────────
      - name: "Defender Victron Stale"
        unique_id: defender_victron_stale
        value_template: "{{ value_json.topics.victron.stale }}"

      - name: "Defender Engine Stale"
        unique_id: defender_engine_stale
        value_template: "{{ value_json.topics.engine.stale }}"
```

---

## Tracking Vehicle Location in HA

GPS coordinates come from Starlink (when enabled in the Starlink app under
**Settings → Advanced → Debug data → GPS**). Use a `device_tracker` template
to make the vehicle appear on the HA map:

```yaml
template:
  - device_tracker:
      - name: "Defender"
        unique_id: defender_vehicle
        latitude: "{{ states('sensor.defender_latitude') | float(0) }}"
        longitude: "{{ states('sensor.defender_longitude') | float(0) }}"
        icon: mdi:car
```

---

## Dashboard Panel (Iframe Card)

To embed the full TD5 Dash UI in HA:

```yaml
# In a Lovelace dashboard
type: iframe
url: "https://td5dash.yourdomain.com"
aspect_ratio: "320%"  # 1280:400 = 3.2:1
title: Defender Dashboard
```

Cloudflare Access will prompt for authentication the first time; subsequent
visits within the same browser session will be seamless.

---

## Useful Automations

**Alert when battery SoC drops below 20 %:**
```yaml
automation:
  - alias: "Defender Battery Low"
    trigger:
      - platform: numeric_state
        entity_id: sensor.defender_battery_soc
        below: 20
    action:
      - service: notify.mobile_app_your_phone
        data:
          message: "Defender leisure battery at {{ states('sensor.defender_battery_soc') }}%"
```

**Alert when engine coolant is hot:**
```yaml
automation:
  - alias: "Defender Coolant Warning"
    trigger:
      - platform: numeric_state
        entity_id: sensor.defender_coolant_temp
        above: 100
    action:
      - service: notify.mobile_app_your_phone
        data:
          message: "Coolant temp: {{ states('sensor.defender_coolant_temp') }}°C"
```

**Alert when vehicle goes offline (Starlink disconnected or Pi off):**
```yaml
automation:
  - alias: "Defender Went Offline"
    trigger:
      - platform: state
        entity_id: sensor.defender_starlink_stale
        to: "True"
        for: "00:02:00"   # grace period before alerting
    action:
      - service: notify.mobile_app_your_phone
        data:
          message: "Defender is offline"
```
