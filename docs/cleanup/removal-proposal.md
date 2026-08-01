# Removal Proposal — housekeeping review

**Branch:** `housekeeping-review` (NOT merged to main). **Date:** 2026-08-01.
**Nothing has been deleted.** This is a proposal for your approval.

How to use: for each item, tick the box if you want it removed, or edit/annotate.
I'll only act on the items you approve. Items are grouped by confidence.

---

## A. Safe stray artifacts — recommend remove (empty, untracked, junk)

These are empty directories, untracked by git, created 3–6 June (not from recent
work). Almost certainly a Windows path-handling artifact: a command with a
semicolon-joined path whose second element began with `C:\` (e.g. a
`PYTHONPATH=backend;C:\...`-style value) — Windows treats the `:` as an
alternate-data-stream separator and truncated it, leaving an empty `<name>;C` dir.
They are local-only (never committed/pushed).

- [ ] `backend;C/`   (empty dir)
- [ ] `pytest.ini;C/` (empty dir)
- [ ] `tests;C/`     (empty dir)

Removal is a local `rmdir` (not a git change) — zero effect on `main` or the repo.

---

## B. Dead reference code — CarPiHAT (see code review for full evaluation)

`backend/carpihat_service.py` (167 lines) is **100% dormant** — never imported,
never called, zero runtime cost. Superseded by `ignition_service.py` →
`wittypi_service.py` (the latter is the only power-hardware module actually wired
in). SPEC.md itself states CarPiHAT was dropped in favour of the Witty Pi 5.

- [ ] Remove `backend/carpihat_service.py`
- [ ] Also remove the related dormant stub `backend/ignition_service.py` (129 lines)
      — successor to CarPiHAT, also never imported; the current path is
      `wittypi_service.py`. **Nuance:** design docs describe it as the "legacy
      discrete power path" behind an `IGNITION_SENSE_PIN` env toggle. Removing it
      is safe today (nothing imports it) but drops that fallback option.

If removed, no imports/endpoints/UI change (the `/system/relay` endpoint and amp
button are already self-contained). `shared_state.override_mode` / `sidelights_on`
should **stay** (read by system_service + frontend; reserved for the WittyPi
override hook). See the code review for pros/cons.

Note: `backend/__pycache__/carpihat_service.cpython-311.pyc` is a stale compiled
artifact — already gitignored, will disappear on its own; no action needed.

---

## C. Untracked working files — YOUR call (I won't delete these)

These are untracked files you created as inputs/notes. Listed for awareness, not
recommended for deletion — decide whether to commit, keep local, or gitignore.

- [ ] `docs/td5-dash-todo.md` — round-1 todo (your input). Commit for history, or keep local.
- [ ] `docs/td5-dash-todo-round2.md` — round-2 todo (your input). Same choice.
- [ ] `docs/OTA System Provisioning — Design.md` — **untracked**, your working copy
      of the provisioning design (em-dash name, off the `docs/superpowers/specs/…`
      convention). My committed version of that spec lives on the
      `ota-system-provisioning` branch (not merged). Suggest: when you pick up the
      provisioning work, consolidate to one copy at
      `docs/superpowers/specs/2026-08-01-ota-system-provisioning-design.md` and
      delete this loose one. Not now.

---

## D. Not removals — doc drift to correct (flagged here, fixed only on approval)

- [ ] `CLAUDE.md:307` — stale: claims `POST /system/relay` "calls
      `carpihat_service.set_relay()`". The actual endpoint is a self-contained
      no-op log. Correct the wording (independent of whether CarPiHAT is removed).
- [ ] `documentation/SPEC.md` (≈251, 265) — references CarPiHAT pin allocation as
      "documented in carpihat_service.py". If that file is removed, re-point or
      prune these lines.

---

## What is explicitly NOT proposed for removal

- Everything in `frontend/` — no orphans (all of index.html, game.html, app.js,
  style.css, lib/gauge.min.js are referenced).
- `data/` (runtime SQLite + logs), `__pycache__`, `.env`, `.vscode`,
  `.pytest_cache` — all correctly gitignored already; leave as-is.
- All tracked docs under `documentation/` and `docs/superpowers/` — normal history.
- The two doc roots (`docs/` = process artifacts, `documentation/` = reference)
  coexist intentionally; not an error.
