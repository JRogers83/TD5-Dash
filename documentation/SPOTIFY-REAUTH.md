# Spotify Re-Authorization

## Why this is needed

As of Spotify's **June 2026 policy** (effective **20 July 2026**), a *user*
refresh token — the kind this project uses (Authorization Code flow, with
playback and library scopes) — **expires 6 months after the original
authorization**. Refreshing the access token does **not** reset that timer, and
token rotation does not extend it. When the refresh token expires, Spotify's
token endpoint returns HTTP 400 `{"error": "invalid_grant"}`.

The "Client Credentials flows are not affected" note in Spotify's email does
**not** apply to us — we issue tokens on a user's behalf.

### What the app does automatically
- Detects `invalid_grant`, stops retrying, and sets `spotify_auth.auth_required`.
- Broadcasts an `auth_required` Spotify payload; the UI shows
  **"Spotify Sign-In Required"** instead of a generic error.
- Persists any *rotated* refresh token Spotify returns (DB key
  `spotify_refresh_token`) so it survives restarts. (This is defensive — it
  does not extend the 6-month window.)

### What the app cannot do
This is a headless in-vehicle kiosk. There is **no interactive browser** to
redirect the user through Spotify sign-in. Re-authorization is therefore a
**manual procedure**, performed roughly every 6 months (or whenever the UI
shows "Spotify Sign-In Required").

---

## Manual re-authorization procedure

The Spotify app credentials (`SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET`) are
the same everywhere, so a refresh token obtained on **any** machine works on the
Pi. Easiest is to run the helper on a laptop/desktop with a browser.

1. **Get a fresh refresh token.** On a machine with a browser, in a checkout of
   this repo with `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` set (e.g. in
   `.env`):

   ```sh
   python tools/spotify_auth_setup.py
   ```

   A browser opens the Spotify consent page. Approve it. The script catches the
   `http://127.0.0.1:8888/callback` redirect and prints:

   ```
   SPOTIFY_REFRESH_TOKEN=<new-token>
   ```

   > The redirect URI `http://127.0.0.1:8888/callback` must be registered in the
   > Spotify app settings (Spotify requires `127.0.0.1`, not `localhost`).

2. **Update `.env` on the Pi.** Replace the `SPOTIFY_REFRESH_TOKEN=` line in the
   `.env` file the systemd service reads (`EnvironmentFile=`) with the new value.

3. **Restart the service.** Either:
   - tap **Update / Restart** on the Settings screen (`POST /system/restart`), or
   - `sudo systemctl restart td5-dash`

4. **Verify.** On startup the app sees the new env token differs from the stored
   seed, adopts it (DB keys `spotify_refresh_seed` + `spotify_refresh_token`),
   clears `auth_required`, and the Spotify view returns to normal.

---

## How the env-vs-DB reconciliation works

`backend/spotify_auth.py` keeps two settings-DB keys:

| Key | Meaning |
|-----|---------|
| `spotify_refresh_seed` | The `SPOTIFY_REFRESH_TOKEN` env value last adopted. Used to detect a fresh manual authorization. |
| `spotify_refresh_token` | The current working refresh token (may have been rotated by Spotify since the seed). |

On startup (first token use):
- If `SPOTIFY_REFRESH_TOKEN` (env) **differs** from `spotify_refresh_seed`, it is
  treated as a **new authorization**: both keys are overwritten with it. → this is
  step 2 above.
- Otherwise the **stored** token (possibly rotated) is used.

This is why re-auth is just "update `.env` + restart": no DB editing required.
