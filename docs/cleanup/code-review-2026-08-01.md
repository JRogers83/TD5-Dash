# Code Review — TD5-Dash housekeeping

**Branch:** `housekeeping-review` (NOT merged to main). **Date:** 2026-08-01.
**No code was changed.** Findings only — apply on your approval.

Scope: full-codebase tidy-up review (backend services, `frontend/app.js`,
supporting files) plus the CarPiHAT evaluation you asked for. Findings are
prioritised by impact with `file:line` and a suggested direction.

---

## CarPiHAT evaluation (your specific question)

**How much code:** one file, `backend/carpihat_service.py` — **167 lines**, 100%
CarPiHAT-specific. Plus ~19 incidental doc/comment lines (CLAUDE.md, SPEC.md,
one comment in `shared_state.py:29`). The successor stub `ignition_service.py`
(129 lines) is dormant too.

**Does any of it run?** No. Grep of the whole tree: no `import carpihat_service`
anywhere; `monitor_loop` is never scheduled; `set_relay()` is never called (the
live `POST /system/relay` at `main.py:350-354` is its own no-op handler). It is
never loaded into the running process.

**Performance impact of leaving it:** none. Not imported → not parsed, not in
memory, no tasks. Zero runtime cost. (The `.pyc` is a few KB of gitignored disk.)

**Maintenance impact of leaving it:** low but negative. It's dead code that has
already caused doc drift — `CLAUDE.md:307` claims the relay endpoint calls it,
which is false. It documents a pin map for hardware `SPEC.md` says was dropped,
and it creates a confusing three-way overlap (carpihat → ignition_service →
wittypi_service) for anyone reading the power code.

**Pros of removing:**
- Removes 167 lines of dead, misleading reference code (and clears the doc drift).
- One clear power path (`wittypi_service.py`) instead of three overlapping ones.
- Nothing depends on it — deletion is a one-file change; no imports/endpoints/UI
  to touch.

**Cons of removing:**
- Loses the documented CarPiHAT pin map (recoverable from git history if ever
  needed).
- If you ever did fit a CarPiHAT, you'd rebuild the GPIO skeleton — but the
  WittyPi is the chosen path, so this is unlikely.

**Recommendation:** remove `carpihat_service.py`; keep `shared_state.override_mode`
/ `sidelights_on` (reserved for the WittyPi override hook). Treat
`ignition_service.py` as a separate decision (it's the "legacy discrete" fallback).
Removal candidates are listed in `removal-proposal.md` §B.

---

## HIGH IMPACT

**1. `POST /obd/clear-dtc` falsely reports success — `backend/main.py:359-374`**
Returns `{"ok": true, "detail": "Clear request queued"}` but sends nothing to the
ECU. `SVC_CLEAR_DTC = 0x14` (`obd/protocol.py:35`) is referenced only in a
docstring — never used (verified). The frontend (`app.js:1694-1706`) reads `ok`
and shows "Cleared", so the driver is told faults were cleared when they weren't.
Direction: either wire a real clear through the existing `_obd_lock`/`_live_session`
path (as `pi_diag` does), or return an honest "not implemented" until built.

**2. Spectrum visualiser rAF loop runs forever, off-screen — `frontend/app.js:468, 509`**
`_frame` calls `requestAnimationFrame(_frame)` unconditionally (no
`cancelAnimationFrame`, no `document.hidden` / view check — verified). It clears a
1280×400 canvas and runs two 64-bar loops every frame even when Spotify is paused
and the Spotify view isn't visible. Continuous CPU/GPU cost on the Pi.
Direction: gate the loop on `NAV.currentView === 1 && !document.hidden`, and stop
once decayed to zero.

**3. WS hot-path handlers use unguarded `getElementById().prop` — `frontend/app.js:280-304, 579-606, 762-815, 921-967`**
`handleEngine`/`handleVictron`/`handleStarlink`/`handleSystem` dereference DOM
directly with no null check, so one missing id aborts the rest of that tick's
updates. Other renderers (`_renderGps`, `_renderVictronDetail`, `_updateRawData`,
`_trip.render`) already use the defensive `const el=…; if(el)` helper. Direction:
route these through the same `setTxt`/`setDot` helpers for consistency + resilience.

---

## MEDIUM IMPACT

**4. Victron staleness warning spams the log every 1s when Orion isn't fitted — `victron/service.py:96-102`, `scanner.py:103-110`**
`is_fresh()` requires all three devices; `orion_updated` stays `0.0` if no Orion
MAC is configured, so the multi-line "may be stale" warning logs every second
forever. Direction: only include configured devices (MAC present) in the check.

**5. Temps broadcast as `null` at session start → "null °C" + false blue dot — `obd/service.py:299-302` + `app.js:286-287`**
TEMPS are read every 5th cycle, so for ~1s `coolant/air/ext/fuel` are `None`,
broadcast as JSON null; `handleEngine` renders `${d.coolant_temp_c} °C` → "null °C"
and `_setCoolantIndicator(null)` shows a spurious cold/blue dot. Direction:
null-guard like the Raw Data view already does.

**6. Full engine payload + fault list re-rendered every poll cycle (~4–5 Hz) — `obd/service.py:317-336` + `app.js:299-300`**
`fault_codes` changes only every 30s but ships every tick, and `handleEngine`
does a full `innerHTML` rebuild of the DTC list and raw grid each tick. Continuous
DOM churn for rarely-changing data. Direction: rebuild the DTC list only when the
fault set changes (signature compare); update the raw grid only when its layer is
visible.

**7. Weather loop: dead double-assignment + deprecated loop call — `weather_service.py:145,150,123`**
`last_fetch = now` (145) is immediately overwritten (150); line 123 uses
deprecated `asyncio.get_event_loop().time()`. Direction: collapse to one explicit
"retry in 5 min" and use `get_running_loop()`.

**8. `system` payload broadcasts `brightness` the frontend never reads — `system_service.py:199`**
`_read_brightness()` does a glob + file read every 2s; the field is never consumed
(brightness UI is localStorage-driven). Dead field + pointless per-tick I/O.
Direction: drop it, or actually reflect real backlight state in the sliders.

---

## LOW IMPACT

- **9. `_read_cpu_load` docstring contradicts behaviour on first call — `system_service.py:59-73`** (returns cumulative load, not the documented -1.0). Fix code or docstring.
- **10. `/system/relay` + amp toggle are a convincing no-op — `main.py:350-354`, `app.js:1044-1053`** (known pending-hardware stub; the button looks functional).
- **11. `_updateIndicator()` is an empty no-op still called 3× — `app.js:1424-1426` (calls at 1249,1346,1391)**. Delete function + calls.
- **12. Unused `import time as _time` — `spotify_service.py:359`**. Dead line.
- **13. `reimportWeatherLocation` is a placeholder on a live button — `app.js:1909-1915`, `index.html:842`** (just `alert()`s). Hide or finish.
- **14. Engine-off battery shows "0.0 V" red, not "—" — `obd/service.py:329`, `app.js:285`** (`last_battery or 0.0` coerces unavailable → 0). Send null + guard, like the temps.
- **15. Spotify progress written by both the WS tick and the interval — `app.js:322-331, 386-389`**. Harmless duplication; let the tick own interpolation.
- **16. Mock Spotify payload missing `track_id`/`liked`/`error`/`auth_required` — `mock_service.py:33-43`**. Mock/live drift; keep mock field-complete so mock mode exercises the same paths.

---

## Checked and OK (non-findings)
- Division-by-zero paths guarded (`_read_cpu_load`, `decode_throttle_raw`, `_drawLineChart`).
- `VictronState` numeric fields default to `0.0`, so `handleVictron` `.toFixed()` won't throw.
- The new GPS no-fix/last-known state machine (`app.js:826-908`) is clean, incl. the 20s re-evaluation.
- Spotify 429/401/error handling is thorough.
- CSS spot-check: sampled selectors all present in both HTML/JS and CSS — no obvious dead selectors.

---

## Suggested fix order (if/when you want them applied — on this branch)
1. **#1 clear-DTC** (trust/correctness) and **#2 visualiser loop** (Pi power drain).
2. **#4** log spam, **#5** null temps, **#8** dead brightness read (cheap, clear wins).
3. **#3** defensive handlers, **#6** DOM churn (resilience/perf).
4. Low-impact tidy-ups (#11, #12, #9, #14) — quick and safe.

Say which you'd like me to implement here (I'll keep it on `housekeeping-review`,
off `main`, so it doesn't collide with your on-vehicle testing of the round-2 work).
