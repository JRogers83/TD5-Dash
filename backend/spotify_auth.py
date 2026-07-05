"""
Spotify OAuth token manager (Authorization Code flow).

This is the **Authorization Code** flow — a long-lived *user* refresh token
issued on a user's behalf via tools/spotify_auth_setup.py, carrying user
scopes (playback state, library, etc.). It is NOT the Client Credentials
flow. This distinction matters: Spotify's June 2026 policy expires user
refresh tokens 6 months after the original authorization (the Client
Credentials exemption does not apply to us).

Stores the refresh token and handles automatic access token refresh. Access
tokens expire after 3600 seconds; this module refreshes proactively.

Refresh token lifecycle (per Spotify docs, June 2026):
  - A refresh token is valid for 6 months from the user's ORIGINAL
    authorization. Refreshing the access token does NOT reset this timer.
  - A refresh response MAY include a new refresh_token (rotation). When it
    does, we persist it (DB) and use it going forward; the old one can be
    invalidated. Rotation does not extend the 6-month window.
  - When the refresh token is expired/revoked, the token endpoint returns
    HTTP 400 with {"error": "invalid_grant"}. We do NOT retry: we set the
    module-level `auth_required` flag so the UI can prompt for re-auth, and
    the user must re-run spotify_auth_setup.py (see SPOTIFY-REAUTH.md).

Active-token resolution (env vs DB):
  The current refresh token is stored in the settings DB so a rotated token
  survives restarts. The SPOTIFY_REFRESH_TOKEN env var is treated as the
  "seed" from the last manual authorization: when it differs from the seed
  recorded in the DB, it is taken as a fresh authorization and adopted
  (overwriting any stale/rotated DB token). This makes re-auth simply
  "update .env + restart".

All public functions are async and safe to call from multiple coroutines —
an asyncio.Lock ensures only one refresh runs at a time.

Configuration:
  SPOTIFY_CLIENT_ID      Spotify Developer App client ID
  SPOTIFY_CLIENT_SECRET  Spotify Developer App client secret
  SPOTIFY_REFRESH_TOKEN  Long-lived refresh token from spotify_auth_setup.py
"""

from __future__ import annotations

import logging
import os
import time

import httpx

log = logging.getLogger(__name__)

CLIENT_ID     = os.getenv("SPOTIFY_CLIENT_ID",     "")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET",  "")
REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN",  "")

_TOKEN_URL = "https://accounts.spotify.com/api/token"

# Settings DB keys
_K_SEED   = "spotify_refresh_seed"    # env token last adopted (re-auth detector)
_K_ACTIVE = "spotify_refresh_token"   # current working token (may be rotated)

_access_token = ""
_expiry       = 0.0   # monotonic time when current token expires

# Set True when the refresh token is expired/revoked (invalid_grant). The
# Spotify service reads this to surface a "re-authorization required" UI state
# distinct from a transient network error. Reset to False on a good refresh.
auth_required = False

# Resolved current refresh token (env reconciled against DB). Lazily computed
# on first use and cached; updated in place on rotation.
_active_token: str | None = None
_resolved     = False


def _resolve_active_token() -> str:
    """
    Return the refresh token to use, reconciling the env seed against the DB.

    A rotated token is persisted in the DB so it survives restarts. The env
    var SPOTIFY_REFRESH_TOKEN is the seed from the last manual authorization:
    if it differs from the seed recorded in the DB, treat it as a fresh
    authorization and adopt it (this is the re-auth path). Result is cached.
    """
    global _active_token, _resolved
    if _resolved:
        return _active_token or ""

    try:
        import db
        seed   = db.get_setting(_K_SEED)
        stored = db.get_setting(_K_ACTIVE)
        if REFRESH_TOKEN and REFRESH_TOKEN != seed:
            # New manual authorization via .env — adopt it, discard stale token.
            db.set_settings({_K_SEED: REFRESH_TOKEN, _K_ACTIVE: REFRESH_TOKEN})
            _active_token = REFRESH_TOKEN
            log.info("Spotify: adopted new refresh token from environment")
        else:
            _active_token = stored or REFRESH_TOKEN
    except Exception as exc:
        # DB unavailable (e.g. tests, early startup) — fall back to env token.
        log.warning("Spotify token DB reconcile failed, using env token: %s", exc)
        _active_token = REFRESH_TOKEN

    _resolved = True
    return _active_token or ""


def _persist_rotated_token(new_token: str) -> None:
    """Persist a rotated refresh token and use it going forward."""
    global _active_token
    _active_token = new_token
    try:
        import db
        db.set_settings({_K_ACTIVE: new_token})
        log.info("Spotify: persisted rotated refresh token")
    except Exception as exc:
        log.error("Spotify: failed to persist rotated refresh token: %s", exc)


def configured() -> bool:
    """Return True if client credentials and a refresh token are available."""
    return bool(CLIENT_ID and CLIENT_SECRET and _resolve_active_token())


async def get_token() -> str | None:
    """
    Return a valid access token, refreshing if necessary.
    Returns None if credentials are not configured or refresh fails.
    """
    import asyncio

    if not configured():
        return None

    global _access_token, _expiry

    # Token still valid with 30s headroom
    if _access_token and time.monotonic() < _expiry - 30:
        return _access_token

    # Refresh — use a module-level lock to prevent concurrent refreshes
    async with _get_lock():
        # Re-check after acquiring lock (another coroutine may have refreshed)
        if _access_token and time.monotonic() < _expiry - 30:
            return _access_token

        global auth_required
        token = _resolve_active_token()
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    _TOKEN_URL,
                    data={
                        "grant_type":    "refresh_token",
                        "refresh_token": token,
                    },
                    auth=(CLIENT_ID, CLIENT_SECRET),
                    timeout=10,
                )

                # Expired/revoked refresh token — do NOT retry. Flag for re-auth.
                if r.status_code == 400:
                    err = ""
                    try:
                        err = (r.json() or {}).get("error", "")
                    except Exception:
                        pass
                    if err == "invalid_grant":
                        auth_required = True
                        log.error(
                            "Spotify refresh token expired or revoked "
                            "(invalid_grant) — re-authorization required. "
                            "See SPOTIFY-REAUTH.md."
                        )
                        return None

                r.raise_for_status()
                j = r.json()
                _access_token = j["access_token"]
                _expiry       = time.monotonic() + j["expires_in"]
                auth_required = False

                # Token rotation: a new refresh_token may be returned. When it
                # is, persist and use it (the old one may be invalidated).
                new_rt = j.get("refresh_token")
                if new_rt and new_rt != token:
                    _persist_rotated_token(new_rt)

                log.info("Spotify token refreshed (expires in %ds)", j["expires_in"])
                return _access_token

        except Exception as exc:
            log.error("Spotify token refresh failed: %s", exc)
            return None


# ── Lock helper ────────────────────────────────────────────────────────────────
# asyncio.Lock must be created inside a running event loop in Python <3.10.
# We create it lazily on first use to stay compatible.

_lock: object = None   # type: ignore[assignment]

def _get_lock():
    import asyncio
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock
