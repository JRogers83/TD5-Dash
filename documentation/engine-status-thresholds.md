# Engine status-dot thresholds

The coloured status dots on the Engine view are driven by the threshold
functions in `frontend/app.js` (`batteryColor`, `coolantColor`, `airTempColor`,
`fuelTempColor`). This document records what each colour means, the exact
thresholds, and the reasoning — update both together.

## Dot colour meaning

| Colour | Class | Meaning |
|--------|-------|---------|
| Blue   | `blue` | Cold / informational — not warmed up, not a fault |
| Green  | `on`   | Normal operating range |
| Amber  | `warn` | Elevated — keep an eye on it |
| Red    | `red`  | Genuinely worth attention |

## Battery / system voltage (V)

| Range | Colour | Notes |
|-------|--------|-------|
| < 12.0 | red | Flat / failing battery |
| 12.0–12.5 | amber | Low at rest |
| 12.5–14.8 | green | 12.6 V at rest → ~13.8–14.4 V while charging — all normal |
| > 14.8 | amber | Over-charging |

## Coolant temperature (°C)

TD5 thermostat opens ~82°C; normal running ~85–95°C; fan/warning territory above ~105°C.

| Range | Colour | Notes |
|-------|--------|-------|
| < 60 | blue | Not yet warmed up |
| 60–95 | green | Normal |
| 95–105 | amber | Hot — working hard / hot day |
| ≥ 105 | red | Overheating — **genuine concern** |

## Inlet air temperature (°C) — charge air, NOT ambient

**Important:** on the TD5 this is the combined MAP/IAT sensor mounted on the inlet
manifold. It reads the **charge-air temperature after the turbo and intercooler**,
not the outside air. It normally sits at ambient + 20–30°C and rises well beyond
that under boost on a warm day. Mid-range readings such as 60°C are **completely
normal and not a fault**.

| Range | Colour | Notes |
|-------|--------|-------|
| < 0 | blue | Sub-zero intake |
| 0–70 | green | Normal, including boost charge temps on a warm day |
| 70–90 | amber | Working hard / very hot ambient |
| ≥ 90 | red | Charge air very hot — an efficiency/power note (poor intercooling), **not an emergency** |

> History: this threshold previously reddened at ≥ 60°C, which lit up during
> ordinary summer driving and prompted this review. Widened July 2026.

Sources: charge-air temperature runs at ambient + 20–30°C and higher under load
(no fixed factory chart — depends on EGR, intercooler, and load):
- <https://www.aulro.com/afvb/discovery-2-a/191442-ambient-intake-temp-td5.html>
- <https://www.landyzone.co.uk/land-rover/td5-engine-sensors-what-does-what.257300/>
- <https://workshop-manuals.com/landrover/defendertd5/engine_management_system/sensor_ambient_air_pressure_and_temperature_(aap)/>

## Fuel temperature (°C)

TD5 fuel is return-fed and warms with use; 40–65°C is common, higher under
sustained load. (Not currently shown as a dot on the Engine view; retained for
the Raw Data layer and future use.)

| Range | Colour | Notes |
|-------|--------|-------|
| < 15 | blue | Cold |
| 15–65 | green | Normal |
| 65–80 | amber | Warm |
| ≥ 80 | red | Very warm |

## RPM gauge zones (for reference)

The RPM radial gauge uses coloured band highlights rather than a dot
(`frontend/app.js`, `rpmGauge` config):

| Range (rpm) | Colour | Notes |
|-------------|--------|-------|
| 0–3000 | green | Normal — low-rev torque band (peak torque ~1,950) + cruising |
| 3000–4000 | amber | Caution |
| ≥ 4000 | red | Approaching the governed/rev-limiter range (~4,200–4,500) |

No factory-marked redline exists on the TD5 tachometer; these are
owner/forum-consensus defaults.
