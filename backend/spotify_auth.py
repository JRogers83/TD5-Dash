"""
Spotify OAuth token manager.

Stores the refresh token (from SPOTIFY_REFRESH_TOKEN env var) and handles
automatic access token refresh. Access tokens expire after 3600 seconds;
this module refreshes proactively at 3570s to avoid mid-request expiry.

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

_access_token = ""
_expiry       = 0.0   # monotonic time when current token expires


def configured() -> bool:
    """Return True if all three credentials are set."""
    return bool(CLIENT_ID and CLIENT_SECRET and REFRESH_TOKEN)


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

        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    _TOKEN_URL,
                    data={
                        "grant_type":    "refresh_token",
                        "refresh_token": REFRESH_TOKEN,
                    },
                    auth=(CLIENT_ID, CLIENT_SECRET),
                    timeout=10,
                )
                r.raise_for_status()
                j = r.json()
                _access_token = j["access_token"]
                _expiry       = time.monotonic() + j["expires_in"]
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
