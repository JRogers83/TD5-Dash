# TD5-Dash — Round 2 Feature/Bug List for Claude Code

Compiled by Jonathan, 31 July 2026. Follows on from the first list (items 1–18), which has now been actioned. This file covers items still outstanding plus new findings from testing the round-1 changes.

---

## 1. Spotify playlist scrolling — still outstanding
The playlist screen itself looks a lot better now, but two things remain unresolved:
- **Entry point:** currently only reachable by pressing the playlist button. Would like scrolling down from the main Spotify screen (like the behaviour on other screens) to also drop into the playlist list — keep the playlist button as well, just add scroll-down as an alternative way in.
- **Scrolling within the playlist list is still broken:** once on the playlist screen, scrolling down doesn't work. Briefly saw a flash of scroll behaviour once, but it immediately snapped back/disappeared, and hasn't reappeared since. Behaves differently from every other scrollable screen in the app — needs investigating properly, not just re-applying the same scroll pattern used elsewhere.

## 2. Trip computer — peak RPM bug + request for synopsis
Peak RPM on the trip computer screen is showing as 60,393 — obviously impossible. Before fixing in isolation: not fully clear on how the trip computer section works end-to-end, so first produce a short synopsis of how this section is built/calculated (data sources, what "peak" values track, reset logic, etc.). Use that as the basis for a follow-up discussion on what would actually be useful to have on the trip computer, alongside fixing the peak RPM bug itself.

## 3. Speed units — global km/h vs mph toggle
Speed is currently shown in km/h everywhere (engine stats, GPS, etc.). Add a toggle on the settings screen to choose km/h or mph, and have that setting apply globally across every screen that displays speed.

## 4. Boot sequence — logo timing, check against feh dependency
Following on from the earlier boot sequence flag: logo appears on power-on, disappears to black, briefly reappears, then fades out into the engine screen. Working theory is this is because the last update was applied without `feh` installed, which is apparently now a requirement as of the latest update — so this may not be a regression at all, just a side effect of a missing dependency on this install. Flagging for Claude Code to check whether this is expected/explained by the feh requirement, rather than assuming it needs fixing as a bug.

## 5. GPS fix state handling
GPS (Starlink → GPS Fix subscreen) works, but loses fix depending on vehicle heading/orientation — worth investigating why. Also unclear what happens to logged data when there's no fix: suspicion is it may be falling back to the hardcoded North Norfolk default (used elsewhere for location-based features) rather than handling "no fix" as its own proper state.

Proposed status model:
- **No fix** — e.g. just after boot.
- **Amber/Green** — once a fix is acquired, amber for 2D fix, green for 3D fix.
- **Red / last known** — when signal drops, hold last known position/speed/etc. rather than updating, and show a red status indicator.
- **Timeout to "no fix"** — if a fix isn't reacquired within roughly 5 minutes of dropping, revert fully to "no fix" rather than holding stale "last known" data indefinitely.

## 6. RPM graph scaling (engine stats screen)
The RPM graph shows almost nothing — just a faint ripple on the baseline — likely a calibration/axis-range issue rather than a data issue. Suggested fix: set the graph's Y-axis to a fixed range of 0 (min) to ~4,000 rpm (max, matching the red-zone threshold from the round-1 RPM research) so the actual RPM trace plots meaningfully within that range.

## 7. Fault code library
Two faults currently showing constantly: **2-5** and **4-6**. Initial research done as a starting point for Claude Code to build on using the existing TD5 documentation:
- **2-5 — Reference Voltage (logged low):** the ECU's 5V reference supply feeding various sensors (MAP, throttle position, etc.). A logged-low fault here usually points to a wiring/connector/earth issue rather than a single sensor failing outright, and can cascade into other sensor readings looking wrong.
- **4-6 — Ambient Air Temperature Circuit (logged high):** sensor lives in the air intake/air cleaner housing. Logged-high faults here are commonly a failing sensor, poor connector, or contamination (e.g. from a blanked-off EGR).
- The "×187" / "×132" figures showing alongside each fault are almost certainly occurrence counters (times logged since faults were last cleared), not measured values or severity indicators.

Feature request: a tappable fault code library anywhere fault codes are displayed, surfacing a plain-English explanation, likely causes, and severity — built from the existing TD5 manuals/documentation plus further research, so this is available on the road rather than needing to look it up separately.

---

*End of list — 7 items total.*
