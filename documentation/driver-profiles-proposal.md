# Driver Profiles — Design Proposal

**Status:** proposal (not implemented). Requested in `docs/td5-dash-todo.md` #3 as a
standalone piece of work to be scoped before any code is written.

**Author:** Claude Code session, 30 Jul 2026.

---

## 1. What problem are we solving?

Two related ideas:

1. **Profiles / toggle** — the vehicle may be driven by more than one person, each
   wanting their own preferences (brightness, calibration, which pages show, units,
   etc.) without reconfiguring the dash each time.
2. **Stat tracking** — accumulate driving statistics *per driver* over time (not just
   the current in-memory trip), e.g. distance, hours, peak/again values, so each
   driver has their own running record.

The current dash has no concept of "who is driving". Everything is global:
- Brightness day/night levels live in `localStorage` + the `settings` DB table.
- Throttle calibration (`throttle_idle`/`throttle_wot`) is global in `settings`.
- Page visibility is global in the `pages` table.
- The trip computer (`_trip` in `app.js`) is in-memory only and resets every reload.

## 2. What is a "profile"?

A named identity with its own bundle of **preferences** and its own **lifetime stats**.
Exactly one profile is *active* at a time. A built-in **"Default"** profile always
exists and cannot be deleted (covers the "just get in and drive" case and first boot).

Suggested cap: a small fixed number (e.g. up to 4–5 profiles) — this is a dashboard,
not a user-management system.

### Preferences owned by a profile
Candidates (all currently global — moving them into a profile is the main work):

| Preference | Today | Notes |
|-----------|-------|-------|
| Brightness day level | `settings.brightness_day` | Per-driver eye comfort |
| Brightness night level | `settings.brightness_night` | |
| Default day/night mode | (frontend localStorage) | ties into todo #6 |
| Throttle calibration idle/WOT | `settings.throttle_*` | Arguably vehicle-global, not per-driver — see §6 open question |
| Page visibility | `pages` table | Driver A wants Spotify, Driver B doesn't |
| Units (future) | — | mph/kph, °C/°F if ever added |
| Default view on boot (future) | — | e.g. start on Engine vs Spotify |

### Stats owned by a profile
Lifetime, persisted, per driver — a superset of today's in-memory trip computer:

- Distance driven (from road speed integrated over time, or GPS)
- Engine hours / time with ignition on
- Peak RPM, peak boost, peak coolant, max speed (all-time for that driver)
- Averages: average speed while moving, average trip length
- Trip count / last-driven timestamp
- (Optional) a rolling "last N trips" list

The existing `_trip` object stays as the **current-trip** view; when a trip ends it
"rolls up" into the active profile's lifetime stats (see §4).

## 3. Data model

Add two tables to `db.py` (alongside `settings`, `pages`, `engine_history`):

```sql
CREATE TABLE IF NOT EXISTS profiles (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    is_active    INTEGER NOT NULL DEFAULT 0,   -- exactly one row = 1
    created_at   TEXT NOT NULL,
    -- preferences (nullable → fall back to global/default when unset)
    brightness_day    INTEGER,
    brightness_night  INTEGER,
    default_mode      TEXT,      -- 'day' | 'night'
    -- stats (lifetime, for this profile)
    distance_km       REAL NOT NULL DEFAULT 0,
    engine_seconds    INTEGER NOT NULL DEFAULT 0,
    peak_rpm          INTEGER NOT NULL DEFAULT 0,
    peak_boost_bar    REAL NOT NULL DEFAULT 0,
    peak_coolant_c    REAL NOT NULL DEFAULT 0,
    max_speed_kph     INTEGER NOT NULL DEFAULT 0,
    trip_count        INTEGER NOT NULL DEFAULT 0,
    last_driven_at    TEXT
);

CREATE TABLE IF NOT EXISTS profile_pages (   -- per-profile page visibility
    profile_id  INTEGER NOT NULL,
    page_key    TEXT NOT NULL,
    visible     INTEGER NOT NULL,
    PRIMARY KEY (profile_id, page_key)
);
```

A single **`Default`** profile is seeded with `is_active = 1` on first run; existing
global `settings`/`pages` values migrate into it so behaviour is unchanged on upgrade.

Design choice: keep preference columns **nullable** so "unset" means "inherit the
global/hardcoded default" — this keeps the Default profile behaving exactly like today
and avoids forcing every driver to configure everything.

## 4. Where stats come from (backend)

The OBD poll loop (`backend/obd/service.py`) already has live `rpm`, `road_speed_kph`,
`boost_bar`, `coolant_temp_c` every cycle and already writes `engine_history` every
~10 s. Add a lightweight accumulator alongside that write:

- Integrate `road_speed_kph` over elapsed time → distance for the **active** profile.
- Track peaks/max and increment `engine_seconds`.
- Define a **trip boundary**: a trip ends after ignition-off / N seconds of no ECU
  session (we already detect session drop). On trip end: `trip_count += 1`,
  `last_driven_at = now`, reset the in-memory current-trip view.

This keeps all accumulation server-side (survives a UI reload) and reuses data we
already poll — no new ECU traffic.

## 5. UX / where it lives on the dash

Given the 1280×400 layout and that Settings real-estate is already tight (todo #9/#16),
propose a **dedicated Profiles layer** under the Settings view (a new vertical layer,
same mechanism as Pages/Wizard/Diagnostics), containing:

- A row of profile "chips" (Default, Jonathan, …) — tap to switch active profile.
  Active chip highlighted (reuse `charge-badge`/active-button styling).
- Selected profile's **lifetime stats** shown as a `stat-tile` grid (distance, hours,
  peak RPM/boost, max speed, trips, last driven).
- Add / rename / reset-stats / delete controls (Default not deletable).

Switching profile:
- Applies that profile's brightness + mode immediately (POST `/system/brightness`).
- Applies its page-visibility set.
- Points stat accumulation at the new profile.

Optionally surface the **active profile name** subtly on the main Settings screen or
as a small indicator, so it's obvious who's active without opening the layer.

### New REST endpoints (sketch)
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/profiles` | List profiles + which is active |
| POST | `/profiles` | Create `{name}` |
| POST | `/profiles/{id}/activate` | Switch active profile |
| PATCH | `/profiles/{id}` | Rename / update preferences |
| POST | `/profiles/{id}/reset-stats` | Zero the lifetime stats |
| DELETE | `/profiles/{id}` | Delete (not Default) |
| GET | `/profiles/{id}/stats` | Lifetime stats (or fold into GET `/profiles`) |

WebSocket: optionally broadcast `{"type":"profile", "data":{...}}` on switch so all
views react (e.g. brightness), or keep it REST-driven from the Settings UI.

## 6. Open questions for Jonathan (decide before build)

1. **Is throttle calibration per-driver or per-vehicle?** It calibrates the *pedal
   sensor*, so it's arguably vehicle-global and should **not** move into profiles.
   Recommend leaving it global.
2. **How is a profile selected in practice?** Manual tap each drive? Or is there any
   automatic signal (e.g. a future key fob / phone BT presence)? Manual-only is far
   simpler and probably fine.
3. **Distance source** — integrate ECU road speed (always available when driving) vs
   GPS (only with a fix). Recommend ECU road speed as primary, it's simpler and always
   present when the engine's running.
4. **Scope for v1** — smallest useful version is: profiles + brightness/pages
   preferences + distance/hours/peaks stats + a Settings layer to switch and view.
   Units, boot-view, per-trip history list can be phase 2.
5. **Privacy/'"reset"'** — should deleting a profile wipe its stats immediately? (Yes,
   recommended — no reason to retain.)

## 7. Suggested phasing

- **Phase A** — data model + Default profile migration + backend stat accumulator
  (no visible change; stats start recording against Default).
- **Phase B** — Profiles Settings layer: switch/create/rename, apply brightness +
  pages on switch, show lifetime stats.
- **Phase C** — polish: active-profile indicator, reset-stats, optional units/boot-view.

Nothing here is built yet — this document is the proposal to react to.
