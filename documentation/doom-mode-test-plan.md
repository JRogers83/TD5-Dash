# Doom Mode — Manual Test Plan

This is the manual verification checklist for the Doom mode feature. It is intended to be run on the actual Pi (not in Docker) after `deploy/setup.sh` has been re-run. See `docs/superpowers/specs/2026-05-23-doom-mode-design.md` for the full design.

## Prerequisites

- `wads/doom.wad` is in place (registered Doom 1).
- Two USB controllers are plugged in for the 2P tests.
- The Pi has booted to kiosk normally (Settings → engine data updating).

## Phase 3.5 — Controller mapping + stereo audio routing

- [ ] `id pi` (or whichever user the service runs as) shows membership of the `input` group.
- [ ] `ls /dev/input/js*` shows `/dev/input/js0` and `/dev/input/js1` when both controllers are plugged in.
- [ ] Run launcher manually: `MODE=single WAD=$PWD/wads/doom.wad SKILL=3 games/doom/launcher.sh`
       Expected: one chocolate-doom window centered at (320, 0) on a 1280x400 screen, black bars left and right. Controller A controls the player.
- [ ] Stop the manual run (close the window). Now `MODE=coop WAD=$PWD/wads/doom.wad SKILL=3 games/doom/launcher.sh` with two controllers.
       Expected: two chocolate-doom windows at (0,0) and (640,0). Each controller drives its own window. Audio from Player 1 plays from the left speaker only, Player 2 from the right.
- [ ] If L and R are reversed compared to seating, swap which `master_channel_map=front-*` value goes into the P1 vs P2 `pactl load-module` calls in `games/doom/launcher.sh`.
- [ ] `pactl list short modules` after a clean exit shows no `doom_p1` or `doom_p2` sinks remaining.

## Phase 4 — Overlay

- [ ] During gameplay, the "≡" icon is visible in the top-right corner.
- [ ] Tap the icon → the menu panel appears with the correct buttons:
       Single: Resume / Save / Load / Quit
       Co-op or Deathmatch: Resume / Quit only
- [ ] In single player, Save → Doom's save dialog appears in the chocolate-doom window.
- [ ] In single player, Load → Doom's load dialog appears.
- [ ] Quit → all Doom-mode processes terminate, Chromium returns, dashboard resumes.

## Phase 5a — Chromium freeze

- [ ] With Doom running, in another shell: `top -p $(pgrep -d, chromium)` shows all Chromium PIDs at near-zero CPU.
- [ ] After Quit: Chromium resumes, no visible artifacts beyond a brief X Expose repaint.

## Phase 5b — Launcher lifecycle & error paths

- [ ] Tap Game Mode tile → launcher loads with Single + Hurt Me Plenty preselected.
- [ ] Tap LAUNCH with `wads/doom.wad` removed → page reverts to launcher with red "wad_missing" banner.
- [ ] Put `wads/doom.wad` back, unplug both controllers, tap LAUNCH with Co-op selected → after a few seconds, browser is back on Settings with toast "Controllers required for this mode".
- [ ] Quit via Doom's own in-game menu (rather than the overlay) → same return path.

## Phase 5c — Spotify integration

- [ ] Start Spotify playing on the dashboard. Launch Doom. Spotify pauses.
- [ ] Quit Doom. Spotify resumes from where it paused.
- [ ] Pause Spotify manually first. Launch Doom. Spotify stays paused.
- [ ] Quit Doom. Spotify does not auto-play.

## Recovery / robustness

- [ ] `sudo systemctl restart td5-dash` mid-game → Doom keeps running. Backend startup kills the orphan launcher, SIGCONTs Chromium, unloads orphan remap sinks. Dashboard returns clean.
- [ ] Power-cut + reboot → fresh kiosk boot, no stale state.

## Known cosmetics (not bugs)

- Brief X Expose handling artifacts on Doom exit before Chromium repaints are expected.
- Two physically-identical controllers may enumerate in non-deterministic order; if encountered, add a udev rule keyed on USB port.
