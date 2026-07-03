# OTA Rollback — Design

**Date:** 2026-07-03
**Status:** Approved

## Problem

The Settings view has a "Check for Updates" button (`POST /system/update`) that does
`git pull` + `pip install` + restart. There is no way to undo an update once applied —
if a pulled version turns out to be broken, the only recovery is manually SSHing into
the Pi and running git commands by hand, which isn't practical while the dash is
installed in the vehicle.

## Requirement

Add a "Roll Back" action that returns the dash to the exact commit it was on
immediately before the most recent update — not simply "one commit back" in git log
terms. Example: dash is on commit A ("v8"); several commits land upstream; hitting
Update moves it to commit E ("v12"); hitting Roll Back must return to commit A, no
matter how many commits separate A and E.

Additionally, keep a short rollback history (up to 5 steps) rather than a single
slot, since the marginal implementation cost is small once the data is persisted in
the database anyway. Hitting Update again after a rollback resumes the normal
forward flow (pulls latest, and the version just rolled back from becomes rollback
history if you update forward and then want to reverse that too).

## Data model

New settings key, `update_history`, stored as a JSON array in the existing
key/value settings table (`db.py`). It acts as a stack of up to 5 previous commit
hashes, oldest dropped once the array exceeds 5 entries.

- **On `POST /system/update`:** before `git pull`, capture `git rev-parse HEAD` and
  append it to `update_history` (trim to the last 5 entries), persist to the
  settings DB. Then proceed with the existing pull / pip install / apt-get /
  restart flow, unchanged.
- **On `POST /system/rollback`:** pop the last entry off `update_history`. If the
  array is empty, return `400 {"error": "no_previous_version"}`. Otherwise:
  `git reset --hard <hash>` (not a detached-HEAD checkout — the branch pointer
  moves back so a subsequent `git pull` fast-forwards normally), then run the same
  pip install / apt-get / clear-cache / delayed-restart steps `/system/update`
  already runs (an older commit may need older dependencies, not just newer ones).

No "redo" / forward stack — out of scope, not requested.

## New/changed endpoints

- `POST /system/update` — unchanged behavior, plus pushes current HEAD onto
  `update_history` before pulling.
- `POST /system/rollback` — new. Pops `update_history`, resets, reinstalls deps,
  restarts. Mirrors `/system/update`'s response shape:
  `{"ok": true, "output": ..., "restarting": true}` on success,
  `400 {"error": "no_previous_version"}` when history is empty.
- `GET /system/version` — new. Returns current short commit hash + subject line
  (`git log -1 --format="%h %s"`) and `rollback_available` (length of
  `update_history`). Used by the frontend to render button state and label without
  requiring an update/rollback to have just happened.

## Frontend (Settings → Software section)

- New "Roll Back" button placed next to the existing "Check for Updates" button.
- On page load (and after any update/rollback completes + WS reconnects), fetch
  `GET /system/version` and:
  - Disable the Roll Back button when `rollback_available == 0`.
  - Label it using the target hash, e.g. `Roll Back to a3f9c21`.
- Clicking Roll Back follows the same UX pattern `triggerUpdate()` already uses:
  disable button, show "Restarting…", status text from the response, re-enable via
  the `td5-ws-connected` event (30s fallback timeout). No confirmation dialog —
  matches the existing Update button's lack of a confirmation step, keeping the two
  actions symmetric.

## Error handling

- Empty history → button is disabled client-side; if somehow clicked (race), server
  400 is surfaced in the status text area, same styling path as a failed update.
- `git reset --hard` failure (e.g. corrupted repo) → same treatment as a failed
  `git pull` today: non-zero return code raises `500` with output, no restart is
  triggered, button re-enables.

## Testing

- Unit test `update_history` push/trim logic (cap at 5, FIFO eviction) in isolation
  from git/subprocess calls.
- Manual verification on a dev checkout: perform a few no-op commits, call
  `/system/update` and `/system/rollback` in sequence via curl, confirm `git log`
  matches expectations at each step (cannot fully verify restart-via-systemd
  outside the Pi).
