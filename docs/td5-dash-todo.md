# TD5-Dash — Feature/Bug List for Claude Code

Compiled by Jonathan, 30 July 2026. To be worked through in a Claude Code session.

---

## 1. RPM gauge position — swap with throttle gauge
Not just a reposition — swap the RPM gauge with the throttle gauge. Throttle is basically a pointless gauge to have in that prime position; RPM should take its place on the right-hand side, closer to the driver, and throttle can move to where RPM currently sits.

## 2. Status dot thresholds (investigate)
The small coloured status dots next to battery, coolant, and air temperature have unclear thresholds. Air temperature went red unexpectedly with no explanation of why. Need to document/expose what the colour thresholds actually are, and confirm whether red is something to genuinely worry about. There's a good amount of vehicle documentation already available — use that as well as researching online to establish sensible thresholds.

## 3. Driver profiles — needs a proposal first (separate piece of work)
This is probably a bigger project than the rest of this list. Rather than building it directly, get Claude Code to come up with a proposal first for how driver profiles/toggle and stat-tracking would work — treat this as a separate, standalone piece of work rather than folding it into this list.

## 4. GPS diagnostics screen
GPS is stuck showing North Norfolk as the location and reporting "GPS stage offline." There are also no GPS-related settings visible in the connectivity pane. Add a dedicated GPS diagnostic screen with better real-time feedback — fix status, satellite count, last known good position, etc.

## 5. Spotify playlist scrolling
On the Spotify playlist selection screen, the list doesn't scroll — only the playlists that fit on the first screen are visible/selectable. Needs scroll support.

## 6. Brightness / day-night default on restart
Brightness level does persist correctly across a service restart (e.g. after "check for updates"), but the day/night mode defaults to night automatically after a restart. Doesn't need to remember the previous day/night state — but if there's a hardcoded fallback default, it should default to **day**, not night.

## 7. Engine stats screen — initial load
Navigating to the engine stats screen shows no stats/graphs on first entry. Only appears after manually switching the time range (e.g. away from "hours today") to force a refresh. Should auto-load data on first entry without needing a manual trigger.

## 8. Settings screen — contrast/visibility
Most of the settings screen still uses grey text where it should be white — hard to read even in day mode at full brightness. Review contrast throughout the settings screen generally.

## 9. Pi Health section — layout
The Pi Health section in settings currently sits slightly off-screen. Needs reworking, and probably relocating elsewhere in the UI rather than staying where it is.

## 10. Victron stats — text colour + solar value bug
- Same grey-text-should-be-white issue as elsewhere in this section.
- Solar generation figure is wrong: displaying as "160,000 Wh today" when the Victron app itself shows **160 Wh** for the same day — three extra trailing zeros with no business being there. This isn't just a unit-formatting issue (Wh vs kWh), it's an actual data bug. Needs code review to trace where the value is read from and where the ×1000 error is introduced, then fix at the source.

## 11. Victron detailed stats screen
Add a second, more detailed Victron screen. There's a lot more data available across the three Victron units in the vehicle (SmartSolar MPPT, SmartShunt, Orion XS DC-DC charger) than is currently surfaced — display the fuller range of available data here.

## 12. RPM red zone visibility
Make the red zone on the RPM gauge more visually prominent/obvious while driving.

## 13. RPM gauge zone thresholds (research complete)
Never actually been into the red zone, so researched sensible thresholds for a TD5:
- **Green (normal):** idle up to ~3,000 rpm — covers the TD5's low-rev torque band (peak torque ~1,950 rpm) and typical cruising range.
- **Amber (caution):** ~3,000–4,000 rpm.
- **Red:** ~4,000 rpm and above — approaching the governed/rev-limiter range generally cited around 4,200–4,500 rpm.

No factory-marked redline exists on the TD5 tachometer, so these are sensible defaults based on owner/forum consensus rather than an official spec — worth sanity-checking against whatever the dash currently uses before changing it.

## 14. Boot sequence — logo screen too brief
On boot: screen is black for a while, the Land Rover logo briefly appears, then goes black again, then finally loads into the expected view. Would expect the logo to stay on screen for more of the loading process rather than dropping back to black.

**Caution:** we've had boot-sequence issues before that caused real problems. Investigate carefully — this is not worth breaking to fix. Go slowly, understand root cause before touching anything.

## 15. Engine management screen — data lag on boot
The engine management screen (RPM dial etc.) doesn't pick up live data immediately on boot — it used to. It does eventually populate, but can take a few minutes, and this feels like a regression from a few versions back. Suspect this may relate to a backoff/retry timing setting on the ECU connection that's changed at some point. Investigate the connection/retry timing on first launch specifically.

## 16. Rollback/update button — visibility + move to sub-screen
The rollback button (and related Controls/Software controls) is so faint it's barely visible on the settings screen — needs much better contrast/visibility.

Additionally: consider moving Update & Rollback off the main settings screen entirely, onto a separate screen you scroll/navigate down to. This would:
- Free up real estate on the main settings screen (currently in high demand).
- Allow the rollback control to be made clearer with better UX — labelling, instructions, etc.
- Leave room to add more detail later (e.g. release notes) — not needed now, just worth designing for.

---

*End of list — 16 items total.*
