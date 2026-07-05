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
forward flow: it pushes whatever commit is currently checked out (i.e. A, the
commit just rolled back to) onto the history, then pulls forward again — so a
second Roll Back would return to A once more, not to E.

No "redo" / forward stack — out of scope, not requested.

## Data model

New settings key, `update_history`, stored as a JSON array of full 40-character
commit hashes in the existing key/value settings table (`db.py`). Hashes are
shortened (`hash[:7]`) only at display time (the `/system/version` response and
any status text) — the stored value is always the full hash so `git reset --hard`
is unambiguous. It acts as a stack of up to 5 entries, oldest dropped once the
array exceeds 5.

- **On `POST /system/update`:** capture `old = git rev-parse HEAD`, run `git pull`,
  then capture `new = git rev-parse HEAD`. Only append `old` to `update_history`
  (trim to the last 5 entries) **if `new != old`** — i.e. the pull actually moved
  HEAD. A no-op pull (already up to date) or a failed pull must not push a phantom
  entry, since a stack of identical hashes would make Roll Back silently do
  nothing. The write to `update_history` and the `git pull` are treated as one
  transaction: skip the DB write entirely unless the pull both succeeded and
  changed HEAD.
- **On `POST /system/rollback`:** pop the last entry off `update_history`. If the
  array is empty, return `400 {"error": "no_previous_version"}`. Otherwise:
  `git reset --hard <hash>` (not a detached-HEAD checkout — the branch pointer
  moves back so a subsequent `git pull` fast-forwards normally, **assuming
  upstream history is never rebased/force-pushed** — a reasonable assumption for
  this repo's single-maintainer workflow, but worth noting since a force-push
  would break the fast-forward), then run the same pip install / apt-get /
  clear-cache / delayed-restart steps `/system/update` already runs.

**Assumption — clean working tree:** `git reset --hard` discards any uncommitted
changes to tracked files. The only runtime-written file is the SQLite settings DB
(`data/td5dash.db`), which is already `.gitignore`d and therefore untracked —
`reset --hard` does not touch it. No other tracked file is written to at runtime.
This design assumes that remains true; if a future change starts writing to a
tracked file on the Pi, that file must either be added to `.gitignore` or excluded
from rollback's blast radius explicitly.

**Known limitation — unpinned dependencies:** `backend/requirements.txt` pins
minimum versions only (`>=`), not exact versions. `pip install -r requirements.txt`
after a rollback will happily leave newer, already-installed package versions in
place if they still satisfy the `>=` constraint — it only reinstalls to satisfy
the constraint, it does not downgrade to match what the older commit was
originally tested against. This means rollback reliably reverts *code* but does
not guarantee dependency versions match what shipped with that commit. Full
correctness would require pinning exact versions (`==`) or committing a lockfile,
which is a larger change out of scope here — noting it as a known gap rather than
fixing it in this pass.

## New/changed endpoints

- `POST /system/update` — captures HEAD before pulling, and appends it to
  `update_history` after a successful pull that moved HEAD (see Data model —
  `old`/`new` capture and `new != old` guard; a no-op or failed pull writes
  nothing).
- `POST /system/rollback` — new. Pops `update_history`, resets, reinstalls deps,
  restarts. Mirrors `/system/update`'s response shape:
  `{"ok": true, "output": ..., "restarting": true}` on success,
  `400 {"error": "no_previous_version"}` when history is empty.
- `GET /system/version` — new. Returns:
  - `current_version` — current short commit hash + subject line
    (`git log -1 --format="%h %s"`)
  - `rollback_available` — length of `update_history`
  - `rollback_target` — short hash + subject line of the top of the
    `update_history` stack (`null` when the stack is empty)

  The frontend needs `rollback_target` to render the "Roll Back to \<hash\>" label
  on page load, before any update/rollback has happened in the current session —
  `rollback_available` alone (a bare count) can't supply the hash the button label
  requires. Subject-line lookup (`git log -1 --format="%h %s" <hash>`) is expected
  to always succeed since stored hashes are branch ancestors and stay reachable,
  but since this endpoint drives button rendering on every page load, if the
  subject lookup fails for any reason, fall back to showing the hash alone
  (`git log -1 --format="%h" <hash>` or the stored hash directly) rather than
  erroring the whole endpoint.

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
- Unit test the no-op/failed-pull dedup guard: given `old == new` (or a failed
  pull), `update_history` must be unchanged after `/system/update`.
- Unit/integration test `POST /system/rollback` against an empty `update_history`
  returns `400 {"error": "no_previous_version"}` and does not touch git or restart
  the service.
- Manual verification on a dev checkout: perform a few no-op commits, call
  `/system/update` and `/system/rollback` in sequence via curl, confirm `git log`
  matches expectations at each step (cannot fully verify restart-via-systemd
  outside the Pi).
