# OTA System Provisioning — Design

**Date:** 2026-08-01
**Status:** approved design, pre-implementation
**Related:** `docs/superpowers/specs/2026-07-03-ota-rollback-design.md` (code update/rollback)

## Problem

OTA update (`POST /system/update`) does `git pull` + pip reinstall + `sudo apt-get
install -y <fixed list>` + restart. Three gaps:

1. **The apt list is hardcoded and drifts.** `update_service._APT_PACKAGES` and the
   sudoers rule (`deploy/setup.sh`) each pin an explicit package set. They already
   disagree with `setup.sh`'s install list (missing `gpsd`, `gpsd-clients`,
   `python3-gps`, and now `feh`).
2. **The sudoers rule pins exact package arguments.** sudo matches arguments
   exactly, so OTA can never install a package that isn't already in that pinned
   line — and changing the line needs root on the Pi (a manual `setup.sh` re-run).
   OTA therefore cannot extend its own system dependencies. **This is the core gap.**
3. **No path for removals or other system steps.** Nothing can purge an obsolete
   package or delete a stale file as the app evolves.

Additionally, system changes are **not cleanly reversible** (a purge or `rm` can't
be undone by a code rollback), so bundling them into the reversible update/rollback
flow is misleading.

## Goals

- Let the update process install / remove / update system packages **and** run
  arbitrary idempotent system steps, driven by **version-controlled** desired state
  (no more hardcoded drift).
- Keep it **decoupled** from code update/rollback: system changes are forward-only,
  user-initiated, and explicit, with UI visibility and consent.
- Close the sudoers gap **once** so future system changes are pure repo edits.
- Add cheap, public-repo-appropriate **governance** so the newly root-capable files
  can't be changed without the owner's review.

## Non-goals

- Rollback / undo of system changes (forward-only by design).
- Auto-applying system changes during a code update (explicitly rejected — see UX).
- CVE / dependency scanning (GitHub's free scanners don't address the abuse vector;
  human review does).

---

## 1. Concept — two decoupled maintenance actions

On the Settings → Software layer:

- **Code** (existing): Update / Rollback. `git pull` / `git reset --hard` + pip.
  Reversible. Unchanged except that the apt step is removed from it (see §5).
- **System** (new): reconcile the live OS to the repo's declared desired state —
  install missing packages, purge unwanted ones, run cleanup steps. Forward-only,
  user-initiated.

Rollback never touches the system. After a code update that needs new system deps,
the System panel simply shows them as **pending**; nothing irreversible runs until
the user presses **Apply**.

## 2. Desired state in the repo

New directory `deploy/system/`:

- `packages.txt` — packages that should be present (one per line, `#` comments,
  blank lines ignored). Single source of truth for runtime/feature packages.
- `remove.txt` — packages that should be absent (purged if installed).
- `cleanup.sh` — optional, idempotent hook for arbitrary root steps (stale-file
  `rm -f`, config/systemd rewrites). Runs last during apply. **Highest-risk
  surface** — gated by CODEOWNERS (§8). May be absent.

`deploy/ota-provision.sh` — the root apply script:

```
ota-provision.sh apply
  1. apt-get install -y  $(grep -v '^#' packages.txt)     # install/upgrade
  2. for pkg in remove.txt: if dpkg -s pkg installed -> apt-get purge -y pkg
  3. if [ -x deploy/system/cleanup.sh ]: run it
  # emits a line per action; exits non-zero only on a hard failure
```

The script is intentionally "dumb": it derives actions from the same manifests the
backend reads, so status and apply agree. `apt-get install` of an already-present
package is a no-op, so the script is safe to re-run (idempotent convergence).

## 3. The one-time enabler (sudoers)

`deploy/setup.sh` replaces the pinned apt line:

```
# OLD:
<user> ALL=(ALL) NOPASSWD: /usr/bin/apt-get install -y freedoom openbox libsamplerate0 python3-evdev fonts-noto-color-emoji
# NEW:
<user> ALL=(ALL) NOPASSWD: <REPO>/deploy/ota-provision.sh apply
```

(`systemctl restart` and `shutdown` rules unchanged.) After this one change, every
future system change is a repo edit — sudoers is never touched again.

**Rollout note:** existing Pis carry the OLD sudoers line. OTA cannot rewrite
sudoers (chicken-and-egg — it lacks permission), so each existing install needs a
**one-time `sudo ./deploy/setup.sh` re-run** to pick up the new rule. New installs
get it automatically. Document this in the update UI's first-run message and README.

## 4. Backend — `provision_service.py`

New module (mirrors `update_service.py`; unit-testable, no FastAPI import):

- `read_manifest(path) -> list[str]` — parse a manifest (strip comments/blanks).
- `_is_installed(pkg) -> bool` — `dpkg -s <pkg>` returns installed status.
- `get_status() -> dict` — diff desired vs actual, **no root needed**:
  ```json
  {"up_to_date": false,
   "pending_install": ["feh"],
   "pending_remove": ["oldpkg"],
   "cleanup_present": true}
  ```
- `apply() -> dict` — run `sudo <REPO>/deploy/ota-provision.sh apply`, capture
  output; best-effort, never raises. Returns `{"ok": bool, "output": str}`.

Endpoints in `main.py` (before the static mount):

| Method | Path | Purpose |
|--------|------|---------|
| GET  | `/system/provision` | status/diff (no root) |
| POST | `/system/provision/apply` | run the provisioner (root) |

## 5. Changes to `update_service.py`

- Delete `_APT_PACKAGES` and the `sudo apt-get install` call in `reinstall_deps()`.
  Pip reinstall stays (per-version, reversible). System packages now come **only**
  from the provisioning manifest.
- Consequence: updating to a version that needs a new package no longer installs it
  silently; the System panel surfaces it as pending for the user to apply. This is
  the intended decoupling.

## 6. Frontend — System panel on the Software layer

The Software layer becomes **two columns** (uses the 1280×400 geometry rather than
stacking):

- **Left — Code:** current version, Update, Rollback, status (as today).
- **Right — System:** a status line — `System up to date` or
  `2 changes pending: +feh  −oldpkg` — an **Apply Changes** button (disabled when
  `up_to_date`), and a feedback area for per-step output. Fully separate from
  Update/Rollback.

Behaviour:
- On entering the layer (and after a code update completes), fetch
  `GET /system/provision` and render pending changes.
- **Apply Changes** → `POST /system/provision/apply`; show live/return output, then
  re-fetch status. Because apply may install packages it does **not** restart the
  service by default (no code changed); if a cleanup step needs a restart, it's the
  user's explicit follow-up via the existing Restart control.
- A subtle indicator (e.g. amber dot on the System heading) when changes are pending,
  so the user knows to look after an update.

Styling reuses `stat-tile` / `relay-btn` / `update-status` patterns. Column layout
mirrors the existing multi-column views (`flex`, `vdivider`).

## 7. Update / rollback interaction

- Code update: unchanged flow minus apt. Afterwards the UI shows any new pending
  system changes.
- Rollback: reverts code only; never runs the provisioner. If the older version's
  manifest differs, the System panel may show pending changes, but nothing runs
  without Apply. Extra packages left installed after a rollback are harmless.

## 8. Security & governance

The provisioner grants the git remote effective **root** on every vehicle that
applies it. Inherent to "OTA installs system deps"; acceptable for the current
single-maintainer / single-vehicle context, but the repo is public and may gain
contributors, so:

- **`.github/CODEOWNERS`** — require the owner's review on `deploy/**` **and
  `.github/**`** (so the protections themselves, and the workflow, can't be changed
  without owner review). CODEOWNERS only *enforces* once branch protection requires
  code-owner review — see below.
- **PR-flagging Action** (`.github/workflows/flag-sensitive-changes.yml`) — on PRs
  touching `deploy/system/**`, `deploy/ota-provision.sh`, `deploy/setup.sh`, or
  `.github/**`, add a `security-sensitive` label and a warning comment. Free on
  public repos; ensures a root-capable change is never invisible.
- **Branch protection (documented, not enabled now):** in `README`/this spec, record
  the exact settings to switch on when collaborators are added — require PR + 1
  review + **Require review from Code Owners** on `main`, admin-bypass optional.
  Not enabled today so the solo push workflow is unaffected; currently the fact that
  only the owner can merge to `main` is the de-facto protection.
- **Design-level mitigations already present:** sudoers scoped to exactly one
  script+arg; consent-to-apply UI (no silent auto-root on update).
- **Highest-risk surface:** `deploy/system/cleanup.sh` (arbitrary root shell). Kept
  for flexibility per decision, but explicitly the thing CODEOWNERS/flagging exist
  to watch.

Free GitHub scanners (CodeQL, Dependabot, secret-scanning) are **not** part of the
defense here — they catch known CVEs / leaked secrets, not a malicious package in a
manifest. Human review on the sensitive paths is the control.

## 9. Testing

- `tests/test_provision_service.py`:
  - `read_manifest` — comments, blanks, whitespace.
  - `get_status` diff — mock `_is_installed`: pending_install when desired-and-absent;
    pending_remove when unwanted-and-present; `up_to_date` when aligned;
    `cleanup_present` reflects file existence.
  - `apply` — mock subprocess: returns ok/output; never raises on non-zero.
- Keep the shell `ota-provision.sh` minimal (logic lives in testable Python) — smoke
  it manually / in Docker; not unit-tested.
- Existing `update_service` tests updated for the removed apt step.

## 10. Rollout / migration

1. Merge to `main`.
2. On each existing Pi: `sudo ./deploy/setup.sh` once (new sudoers rule). Until then,
   `Apply Changes` will fail on permission — the UI reports this clearly.
3. Thereafter, all system changes are repo edits to `deploy/system/*`, applied from
   the vehicle's System panel.
4. First concrete use: add `feh` to `deploy/system/packages.txt` so the boot-splash
   holding image (todo #14) reaches existing vehicles via Apply.

## 11. Out of scope / future

- System-change rollback / snapshotting.
- Auto-apply / scheduled provisioning.
- Signed manifests / release pinning (revisit if the contributor base grows).
