# GPS USB Receiver Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the defunct Starlink GPS API with a u-blox UBX-G7020-KT USB receiver via gpsd, restoring lat/lon to the weather service and broadcasting full NMEA data for future use.

**Architecture:** A new `gps_service.py` follows the existing broadcast-loop pattern (ThreadPoolExecutor for blocking gpsd I/O). `_parse_tpv()` is extracted as a pure function for testability. `GPS_MOCK` env var selects between real gpsd and a static mock (consistent with TD5_MOCK, STARLINK_MOCK etc.). `shared_state` gains three new fields; `weather_service` continues to use `gps_lat`/`gps_lon` unchanged.

**Tech Stack:** Python `gps` library (package `python3-gps`), gpsd daemon, FastAPI/asyncio, existing ws_hub broadcast pattern.

**Spec:** `docs/superpowers/specs/2026-06-06-gps-wittypi-integration-design.md` — GPS section.

---

## Files

| File | Action |
|------|--------|
| `backend/shared_state.py` | Modify — add `gps_speed_kmh`, `gps_heading_deg`, `gps_fix` |
| `backend/gps_service.py` | Create — gpsd poll loop, `_parse_tpv`, `broadcast_loop` |
| `backend/mock_service.py` | Modify — add `mock_gps_loop` |
| `backend/main.py` | Modify — add GPS_MOCK service selection, add task to lifespan |
| `tests/test_gps_service.py` | Create — unit tests for `_parse_tpv` and `_NO_FIX_DATA` |
| `tests/test_mock_service.py` | Modify — add GPS schema back (with new fields) |
| `deploy/setup.sh` | Modify — add gpsd packages + `/etc/default/gpsd` config |
| `.env.example` | Modify — add GPS_MOCK, GPSD_HOST, GPSD_PORT; remove STARLINK_GPS_POLL_INTERVAL |
| `documentation/SPEC.md` | Modify — update GPS section |
| `documentation/pi-setup.md` | Modify — add GPS Setup section |

---

### Task 1: Add GPS fields to shared_state

**Files:**
- Modify: `backend/shared_state.py`

- [ ] **Step 1: Read the current file**

Read `backend/shared_state.py` and note the existing `gps_lat`/`gps_lon` fields.

- [ ] **Step 2: Add the new fields**

Add three fields after `gps_lon`:
```python
# GPS (set by gps_service when a fix is available)
gps_lat:          float | None = None
gps_lon:          float | None = None
gps_speed_kmh:    float | None = None   # new
gps_heading_deg:  float | None = None   # new
gps_fix:          int = 0               # new: 0=no data, 2=2D fix, 3=3D fix
```

- [ ] **Step 3: Verify no tests break**

```bash
docker run --rm \
  -v "C:\code\TD5-Dash\backend:/app/backend" \
  -v "C:\code\TD5-Dash\tests:/app/tests" \
  -v "C:\code\TD5-Dash\pytest.ini:/app/pytest.ini" \
  td5-dash-td5-dash:latest \
  sh -c "cd /app/backend && python -m pytest /app/tests/ -v"
```
Expected: all existing tests pass.

- [ ] **Step 4: Commit**

```bash
git add backend/shared_state.py
git commit -m "Add gps_speed_kmh, gps_heading_deg, gps_fix to shared_state"
```

---

### Task 2: Create gps_service.py with _parse_tpv

**Files:**
- Create: `backend/gps_service.py`
- Create: `tests/test_gps_service.py`

- [ ] **Step 1: Write the failing tests first**

Create `tests/test_gps_service.py`:
```python
"""Tests for gps_service._parse_tpv — pure function, no gpsd required."""
import pytest


class FakeTPVReport:
    """Duck-type for a gpsd TPV report object."""
    def __init__(self, mode=3, lat=52.6309, lon=1.2974, speed=13.89, track=180.5):
        self.mode = mode
        self.lat = lat
        self.lon = lon
        self.speed = speed   # m/s from gpsd
        self.track = track   # degrees true

    def get(self, key, default=None):
        return getattr(self, key, default)


class TestParseTpv:
    def test_3d_fix_returns_correct_fields(self):
        from gps_service import _parse_tpv
        result = _parse_tpv(FakeTPVReport(mode=3, lat=52.6309, lon=1.2974,
                                          speed=13.89, track=180.5))
        assert result is not None
        assert result["fix"] == 3
        assert result["lat"] == 52.6309
        assert result["lon"] == 1.2974
        assert result["speed_kmh"] == pytest.approx(50.0, abs=0.2)
        assert result["heading_deg"] == 180.5

    def test_2d_fix_returns_data(self):
        from gps_service import _parse_tpv
        result = _parse_tpv(FakeTPVReport(mode=2))
        assert result is not None
        assert result["fix"] == 2

    def test_mode_0_returns_none(self):
        from gps_service import _parse_tpv
        assert _parse_tpv(FakeTPVReport(mode=0)) is None

    def test_mode_1_returns_none(self):
        from gps_service import _parse_tpv
        assert _parse_tpv(FakeTPVReport(mode=1)) is None

    def test_speed_converted_ms_to_kmh(self):
        from gps_service import _parse_tpv
        result = _parse_tpv(FakeTPVReport(speed=1.0))
        assert result["speed_kmh"] == pytest.approx(3.6, abs=0.01)

    def test_missing_attributes_default_to_zero(self):
        from gps_service import _parse_tpv

        class MinimalReport:
            mode = 3
            def get(self, k, d=None): return getattr(self, k, d)

        result = _parse_tpv(MinimalReport())
        assert result["lat"] == 0.0
        assert result["lon"] == 0.0
        assert result["speed_kmh"] == 0.0
        assert result["heading_deg"] == 0.0

    def test_none_attribute_values_treated_as_zero(self):
        from gps_service import _parse_tpv

        class NoneReport:
            mode = 3
            lat = None; lon = None; speed = None; track = None
            def get(self, k, d=None): return getattr(self, k, d)

        result = _parse_tpv(NoneReport())
        assert result["lat"] == 0.0

    def test_coordinates_rounded_to_6dp(self):
        from gps_service import _parse_tpv
        result = _parse_tpv(FakeTPVReport(lat=52.630912345678))
        assert result["lat"] == 52.630912

    def test_speed_rounded_to_1dp(self):
        from gps_service import _parse_tpv
        result = _parse_tpv(FakeTPVReport(speed=13.8888))
        assert result["speed_kmh"] == round(13.8888 * 3.6, 1)


class TestNoFixData:
    def test_all_numeric_fields_are_none(self):
        from gps_service import _NO_FIX_DATA
        assert _NO_FIX_DATA["lat"] is None
        assert _NO_FIX_DATA["lon"] is None
        assert _NO_FIX_DATA["speed_kmh"] is None
        assert _NO_FIX_DATA["heading_deg"] is None

    def test_fix_is_zero(self):
        from gps_service import _NO_FIX_DATA
        assert _NO_FIX_DATA["fix"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker run --rm \
  -v "C:\code\TD5-Dash\backend:/app/backend" \
  -v "C:\code\TD5-Dash\tests:/app/tests" \
  -v "C:\code\TD5-Dash\pytest.ini:/app/pytest.ini" \
  td5-dash-td5-dash:latest \
  sh -c "cd /app/backend && python -m pytest /app/tests/test_gps_service.py -v"
```
Expected: `ModuleNotFoundError: No module named 'gps_service'`

- [ ] **Step 3: Create gps_service.py**

Create `backend/gps_service.py`:
```python
"""
GPS service — polls gpsd for NMEA fix data and broadcasts via WebSocket.

Reads from gpsd using the Python gps library (python3-gps package).
Controlled by GPS_MOCK env var (default 1 = mock, matches other service toggles).
Falls back gracefully when gpsd is unavailable (retries with exponential backoff).

Configuration:
  GPS_MOCK=0       Use real gpsd (requires gpsd running + GPS receiver)
  GPSD_HOST        gpsd hostname (default: localhost)
  GPSD_PORT        gpsd port (default: 2947)

WebSocket message published when fix available:
  {"type": "gps", "data": {"lat": 52.6309, "lon": 1.2974,
                            "speed_kmh": 0.0, "heading_deg": 0.0, "fix": 3}}

WebSocket message when no fix:
  {"type": "gps", "data": {"lat": null, "lon": null,
                            "speed_kmh": null, "heading_deg": null, "fix": 0}}

# hw-verify: /dev/ttyACM0 device path and gpsd auto-detection of u-blox
# UBX-G7020-KT must be confirmed on real hardware (see documentation/pi-setup.md).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

import shared_state
from ws_hub import ConnectionManager

log = logging.getLogger(__name__)

GPSD_HOST    = os.getenv("GPSD_HOST", "localhost")
GPSD_PORT    = int(os.getenv("GPSD_PORT", "2947"))
_RETRY_MIN_S = 5.0
_RETRY_MAX_S = 60.0

_NO_FIX_DATA: dict = {
    "lat":         None,
    "lon":         None,
    "speed_kmh":   None,
    "heading_deg": None,
    "fix":         0,
}


def _parse_tpv(report) -> dict | None:
    """
    Parse a gpsd TPV report object into our GPS data dict.

    Returns None when mode < 2 (no usable position fix).
    Accepts any object with .mode, .lat, .lon, .speed, .track attributes —
    duck-typing makes this unit-testable without a real gpsd connection.

    mode: 0=no data, 1=no fix, 2=2D fix, 3=3D fix
    speed: m/s from gpsd — converted to km/h here
    track: degrees true from north (heading)
    """
    mode = int(getattr(report, 'mode', 0) or 0)
    if mode < 2:
        return None
    return {
        "lat":         round(float(getattr(report, 'lat',   0.0) or 0.0), 6),
        "lon":         round(float(getattr(report, 'lon',   0.0) or 0.0), 6),
        "speed_kmh":   round(float(getattr(report, 'speed', 0.0) or 0.0) * 3.6, 1),
        "heading_deg": round(float(getattr(report, 'track', 0.0) or 0.0), 1),
        "fix":         mode,
    }


def _poll_loop(manager: ConnectionManager, loop: asyncio.AbstractEventLoop) -> None:
    """Blocking gpsd poll loop — runs in a dedicated ThreadPoolExecutor thread."""
    import gps as gpsd  # imported here so dev/Docker environments don't error on import

    def _broadcast(data: dict) -> None:
        asyncio.run_coroutine_threadsafe(
            manager.broadcast({"type": "gps", "data": data}), loop
        )

    retry_delay = _RETRY_MIN_S

    while True:
        log.info("Connecting to gpsd at %s:%d …", GPSD_HOST, GPSD_PORT)
        try:
            session = gpsd.gps(
                host=GPSD_HOST,
                port=str(GPSD_PORT),
                mode=gpsd.WATCH_ENABLE | gpsd.WATCH_NEWSTYLE,
            )
            log.info("gpsd connected. Polling for GPS fix.")
            retry_delay = _RETRY_MIN_S

            for report in session:
                if report.get('class') != 'TPV':
                    continue

                data = _parse_tpv(report)
                if data is None:
                    _broadcast(_NO_FIX_DATA)
                    shared_state.gps_lat         = None
                    shared_state.gps_lon         = None
                    shared_state.gps_speed_kmh   = None
                    shared_state.gps_heading_deg = None
                    shared_state.gps_fix         = 0
                else:
                    _broadcast(data)
                    shared_state.gps_lat         = data["lat"]
                    shared_state.gps_lon         = data["lon"]
                    shared_state.gps_speed_kmh   = data["speed_kmh"]
                    shared_state.gps_heading_deg = data["heading_deg"]
                    shared_state.gps_fix         = data["fix"]

        except Exception:
            # Log full traceback on first failure; subsequent failures are warnings only
            if retry_delay == _RETRY_MIN_S:
                log.exception("gpsd connection error — retrying in %.0f s", retry_delay)
            else:
                log.warning("gpsd unavailable — retrying in %.0f s", retry_delay)
            _broadcast(_NO_FIX_DATA)
            shared_state.gps_fix = 0
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, _RETRY_MAX_S)


async def broadcast_loop(manager: ConnectionManager) -> None:
    """Async entry point — called from main.py lifespan when GPS_MOCK=0."""
    loop     = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gps")
    await loop.run_in_executor(executor, _poll_loop, manager, loop)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker run --rm \
  -v "C:\code\TD5-Dash\backend:/app/backend" \
  -v "C:\code\TD5-Dash\tests:/app/tests" \
  -v "C:\code\TD5-Dash\pytest.ini:/app/pytest.ini" \
  td5-dash-td5-dash:latest \
  sh -c "cd /app/backend && python -m pytest /app/tests/test_gps_service.py -v"
```
Expected: 11 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/gps_service.py tests/test_gps_service.py
git commit -m "Add gps_service.py with gpsd integration and _parse_tpv unit tests"
```

---

### Task 3: Add mock_gps_loop to mock_service and restore GPS schema test

**Files:**
- Modify: `backend/mock_service.py`
- Modify: `tests/test_mock_service.py`

- [ ] **Step 1: Add mock_gps_loop to mock_service.py**

Read `backend/mock_service.py`. Find the `_MOCK` dict and the other mock loop functions.

Add the GPS mock data entry to `_MOCK` (after the `"starlink"` entry):
```python
    "gps": {
        "lat":         52.6309,
        "lon":         1.2974,
        "speed_kmh":   0.0,
        "heading_deg": 0.0,
        "fix":         3,
    },
```

Add the mock loop function (after `mock_starlink_loop`):
```python
async def mock_gps_loop(manager: ConnectionManager, interval_s: float = 1.0) -> None:
    """Broadcasts a static GPS fix (parked at mock location)."""
    while True:
        await manager.broadcast({"type": "gps", "data": _MOCK["gps"]})
        await asyncio.sleep(interval_s)
```

- [ ] **Step 2: Restore GPS schema test in test_mock_service.py**

Read `tests/test_mock_service.py`. Find `EXPECTED_SCHEMAS` dict. Add the GPS entry (the old one was removed when GPS was stripped from the mock):

```python
    "gps": {
        "lat":         (int, float),
        "lon":         (int, float),
        "speed_kmh":   (int, float),
        "heading_deg": (int, float),
        "fix":         int,
    },
```

Also add a `TestGPSFields` class after `TestStarlinkFields`:
```python
class TestGPSFields:
    def test_has_all_required_fields(self):
        for field in EXPECTED_SCHEMAS["gps"]:
            assert field in _MOCK["gps"], f"gps missing field: {field}"

    def test_field_types(self):
        for field, expected_type in EXPECTED_SCHEMAS["gps"].items():
            assert isinstance(_MOCK["gps"][field], expected_type), \
                f"gps.{field} has wrong type: {type(_MOCK['gps'][field])}"
```

Also add a sanity check to `TestMockValueSanity`:
```python
    def test_gps_fix_is_valid(self):
        assert _MOCK["gps"]["fix"] in (0, 2, 3)

    def test_gps_lat_lon_range(self):
        assert -90  <= _MOCK["gps"]["lat"] <= 90
        assert -180 <= _MOCK["gps"]["lon"] <= 180
```

- [ ] **Step 3: Run full test suite**

```bash
docker run --rm \
  -v "C:\code\TD5-Dash\backend:/app/backend" \
  -v "C:\code\TD5-Dash\tests:/app/tests" \
  -v "C:\code\TD5-Dash\pytest.ini:/app/pytest.ini" \
  td5-dash-td5-dash:latest \
  sh -c "cd /app/backend && python -m pytest /app/tests/ -v"
```
Expected: all tests pass including new GPS mock tests.

- [ ] **Step 4: Commit**

```bash
git add backend/mock_service.py tests/test_mock_service.py
git commit -m "Add mock_gps_loop and restore GPS schema tests with new speed/heading fields"
```

---

### Task 4: Wire GPS service into main.py

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 1: Add mock_gps_loop to the mock_service import**

Find the `from mock_service import (...)` block in `backend/main.py`. Add `mock_gps_loop` to it:
```python
from mock_service import (
    mock_engine_loop,
    mock_victron_loop,
    mock_spotify_loop,
    mock_system_loop,
    mock_starlink_loop,
    mock_weather_loop,
    mock_gps_loop,         # ← add this
)
```

- [ ] **Step 2: Add GPS service selection**

After the `SYSTEM_MOCK` selection block, add:
```python
if os.getenv("GPS_MOCK", "1") == "0":
    from gps_service import broadcast_loop as gps_loop
else:
    gps_loop = mock_gps_loop
```

- [ ] **Step 3: Add gps_loop to the lifespan tasks**

Find the `tasks = [...]` list in `lifespan()`. Add the GPS task:
```python
    tasks = [
        asyncio.create_task(engine_loop(manager)),
        asyncio.create_task(victron_loop(manager)),
        asyncio.create_task(spotify_loop(manager)),
        asyncio.create_task(system_loop(manager)),
        asyncio.create_task(starlink_loop(manager)),
        asyncio.create_task(weather_loop(manager)),
        asyncio.create_task(gps_loop(manager)),        # ← add this
        asyncio.create_task(_discover_chromium_pid()),
        asyncio.create_task(_doom_startup_cleanup()),
    ]
```

- [ ] **Step 4: Run full test suite**

```bash
docker run --rm \
  -v "C:\code\TD5-Dash\backend:/app/backend" \
  -v "C:\code\TD5-Dash\tests:/app/tests" \
  -v "C:\code\TD5-Dash\pytest.ini:/app/pytest.ini" \
  td5-dash-td5-dash:latest \
  sh -c "cd /app/backend && python -m pytest /app/tests/ -v"
```
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/main.py
git commit -m "Wire GPS service into main.py with GPS_MOCK toggle (default mock)"
```

---

### Task 5: Update setup.sh and .env.example

**Files:**
- Modify: `deploy/setup.sh`
- Modify: `.env.example`

- [ ] **Step 1: Add gpsd packages to setup.sh**

Read `deploy/setup.sh`. Find the `apt-get install` block. Add gpsd packages to it:
```bash
    gpsd \
    gpsd-clients \
    python3-gps \
```

After the apt block, add a gpsd configuration section:
```bash
# ── GPS (USB receiver via gpsd) ────────────────────────────────────────────────
echo "▸ Configuring gpsd for u-blox USB GPS receiver..."
GPSD_DEFAULT="/etc/default/gpsd"
if [ -f "$GPSD_DEFAULT" ]; then
    # Set device path; -n means don't wait for a client before polling
    sed -i 's|^DEVICES=.*|DEVICES="/dev/ttyACM0"|'       "$GPSD_DEFAULT" 2>/dev/null || true
    sed -i 's|^GPSD_OPTIONS=.*|GPSD_OPTIONS="-n"|'       "$GPSD_DEFAULT" 2>/dev/null || true
    sed -i 's|^START_DAEMON=.*|START_DAEMON="true"|'      "$GPSD_DEFAULT" 2>/dev/null || true
    echo "  gpsd configured for /dev/ttyACM0 (hw-verify: confirm device path after plugging in)"
fi
systemctl enable gpsd 2>/dev/null || true
```

- [ ] **Step 2: Update .env.example**

Read `.env.example`. Make these changes:

Add a GPS section (after the Starlink section):
```
# GPS (USB receiver via gpsd — python3-gps)
# GPS_MOCK=1 uses static coordinates from WEATHER_LAT/LON above
GPS_MOCK=1
GPSD_HOST=localhost
GPSD_PORT=2947
```

Remove the now-obsolete entry:
```
STARLINK_GPS_POLL_INTERVAL=300
```

- [ ] **Step 3: Commit**

```bash
git add deploy/setup.sh .env.example
git commit -m "Add gpsd packages and config to setup.sh; add GPS_MOCK env vars to .env.example"
```

---

### Task 6: Update SPEC.md and pi-setup.md

**Files:**
- Modify: `documentation/SPEC.md`
- Modify: `documentation/pi-setup.md`

- [ ] **Step 1: Update SPEC.md GPS section**

Read `documentation/SPEC.md`. Find the GPS/Starlink GPS section. Replace it with:

```markdown
**GPS** — u-blox UBX-G7020-KT USB GPS receiver, connected via `/dev/ttyACM0`.
Read by `gps_service.py` via the `gpsd` daemon (Python `gps` library).

WebSocket topic `gps`:
```json
{"type": "gps", "data": {"lat": 52.6309, "lon": 1.2974,
                          "speed_kmh": 0.0, "heading_deg": 0.0, "fix": 3}}
```
`fix`: 0=no data, 2=2D fix, 3=3D fix. All numeric fields `null` when `fix=0`.

`gps_lat`/`gps_lon` update `shared_state` and feed `weather_service` for location-aware
forecasts. `speed_kmh`/`heading_deg`/`fix` are available in `shared_state` for future
navigation features. The Starlink GPS API was removed in 2026 (endpoint closed by Starlink).
```

Also update the BOM: replace Starlink GPS entry with `u-blox UBX-G7020-KT USB GPS Receiver`.

- [ ] **Step 2: Add GPS Setup section to pi-setup.md**

Read `documentation/pi-setup.md`. Append a new section at the end:

````markdown
## GPS Setup (USB Receiver)

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
````

- [ ] **Step 3: Run full test suite one last time**

```bash
docker run --rm \
  -v "C:\code\TD5-Dash\backend:/app/backend" \
  -v "C:\code\TD5-Dash\tests:/app/tests" \
  -v "C:\code\TD5-Dash\pytest.ini:/app/pytest.ini" \
  td5-dash-td5-dash:latest \
  sh -c "cd /app/backend && python -m pytest /app/tests/ -v"
```
Expected: all tests pass.

- [ ] **Step 4: Commit and push**

```bash
git add documentation/SPEC.md documentation/pi-setup.md
git commit -m "Update SPEC.md and pi-setup.md for USB GPS receiver via gpsd"
git push origin main
```
