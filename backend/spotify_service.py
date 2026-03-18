"""
Spotify Web API polling service.

Polls /me/player at 1 s (playing) or 5 s (idle/paused/disconnected).
Publishes {"type": "spotify", "data": {...}} over the WebSocket hub.

Configuration via spotify_auth.py env vars:
  SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REFRESH_TOKEN

send_command(action) is also called directly by the /spotify/command
HTTP endpoint in main.py — it works regardless of the mock/live setting.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

import spotify_auth
from ws_hub import ConnectionManager

log = logging.getLogger(__name__)

_PLAYER_URL = "https://api.spotify.com/v1/me/player"

_IDLE_INTERVAL    = 5.0   # seconds between polls when paused / not playing
_PLAYING_INTERVAL = 1.0   # seconds between polls when actively playing

_DISCONNECTED: dict = {
    "connected":     False,
    "playing":       False,
    "error":         False,
    "track":         "",
    "artist":        "",
    "album":         "",
    "album_art_url": None,
    "progress_s":    0,
    "duration_s":    0,
    "device_name":   "",
    "track_id":      "",
    "liked":         False,
}

# Broadcast on genuine auth / network failures — error flag lets the UI
# show a different message from the normal "no active device" state.
_ERROR: dict = {**_DISCONNECTED, "error": True}

_COMMAND_URLS: dict[str, tuple[str, str]] = {
    "play":  ("PUT",  "https://api.spotify.com/v1/me/player/play"),
    "pause": ("PUT",  "https://api.spotify.com/v1/me/player/pause"),
    "next":  ("POST", "https://api.spotify.com/v1/me/player/next"),
    "prev":  ("POST", "https://api.spotify.com/v1/me/player/previous"),
}


def _parse(body: dict) -> dict:
    """Parse a /me/player JSON response into the WS payload dict."""
    item    = body.get("item") or {}
    images  = (item.get("album") or {}).get("images") or []
    artists = item.get("artists") or []
    device  = body.get("device") or {}

    # Album art: choose image closest to 300 px wide
    art_url: str | None = None
    if images:
        best    = min(images, key=lambda img: abs((img.get("width") or 0) - 300))
        art_url = best.get("url")

    return {
        "connected":     True,
        "playing":       body.get("is_playing", False),
        "track":         item.get("name", ""),
        "artist":        ", ".join(a.get("name", "") for a in artists),
        "album":         (item.get("album") or {}).get("name", ""),
        "album_art_url": art_url,
        "progress_s":    round((body.get("progress_ms") or 0) / 1000),
        "duration_s":    round((item.get("duration_ms") or 0) / 1000),
        "device_name":   device.get("name", ""),
        "track_id":      item.get("id", ""),
    }


async def broadcast_loop(manager: ConnectionManager) -> None:
    """Poll Spotify Web API and broadcast updates over the WebSocket hub."""
    if not spotify_auth.configured():
        log.warning("Spotify credentials not configured — service inactive")
        while True:
            await manager.broadcast({"type": "spotify", "data": _ERROR})
            await asyncio.sleep(_IDLE_INTERVAL)
        return  # unreachable — satisfies type checkers

    _liked_track_id: str       = ""
    _liked_status:   bool      = False
    _last_payload:   dict | None = None   # last successful player state

    async with httpx.AsyncClient() as client:
        while True:
            token = await spotify_auth.get_token()
            if token is None:
                await manager.broadcast({"type": "spotify", "data": _ERROR})
                await asyncio.sleep(_IDLE_INTERVAL)
                continue

            try:
                r = await client.get(
                    _PLAYER_URL,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=5,
                )
            except Exception as exc:
                log.warning("Spotify poll error: %s", exc)
                await manager.broadcast({"type": "spotify", "data": _ERROR})
                await asyncio.sleep(_IDLE_INTERVAL)
                continue

            if r.status_code == 204:
                # No active device — re-broadcast last known state (paused) so
                # the UI keeps showing the track rather than going blank.
                if _last_payload is not None:
                    await manager.broadcast({"type": "spotify", "data": {**_last_payload, "playing": False}})
                else:
                    await manager.broadcast({"type": "spotify", "data": _DISCONNECTED})
                await asyncio.sleep(_IDLE_INTERVAL)
                continue

            if r.status_code == 401:
                # Token rejected — force expiry so get_token() refreshes next call
                log.info("Spotify 401 — forcing token refresh")
                spotify_auth._expiry = 0.0
                await asyncio.sleep(0.5)
                continue

            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", 10))
                log.warning("Spotify rate limited — backing off %ds", retry_after)
                await asyncio.sleep(retry_after)
                continue

            if r.status_code != 200:
                log.warning("Spotify poll unexpected status: %d", r.status_code)
                await asyncio.sleep(_IDLE_INTERVAL)
                continue

            payload = _parse(r.json())
            track_id = payload.get("track_id", "")
            if track_id and track_id != _liked_track_id:
                _liked_track_id = track_id
                _liked_status   = await check_track_saved(track_id)
            payload["liked"] = _liked_status
            _last_payload = payload
            await manager.broadcast({"type": "spotify", "data": payload})

            await asyncio.sleep(
                _PLAYING_INTERVAL if payload["playing"] else _IDLE_INTERVAL
            )


async def get_playlists() -> list[dict] | None:
    """
    Fetch the current user's playlists (up to 50).
    Returns None if credentials are missing or the request fails.
    """
    token = await spotify_auth.get_token()
    if token is None:
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://api.spotify.com/v1/me/playlists",
                headers={"Authorization": f"Bearer {token}"},
                params={"limit": 50},
                timeout=10,
            )
        if r.status_code != 200:
            log.warning("get_playlists returned %d", r.status_code)
            return None
        result = []
        for p in (r.json().get("items") or []):
            if not p:
                continue
            images = p.get("images") or []
            img_url: str | None = None
            if images:
                best    = min(images, key=lambda i: abs((i.get("width") or 0) - 300))
                img_url = best.get("url")
            result.append({
                "id":        p["id"],
                "uri":       p["uri"],
                "name":      p.get("name", ""),
                "image_url": img_url,
            })
        return result
    except Exception as exc:
        log.error("get_playlists failed: %s", exc)
        return None


async def get_playlist_tracks(playlist_id: str) -> list[dict] | None:
    """
    Fetch tracks for a playlist (up to 100).
    Skips local files and null track items.
    Returns None if credentials are missing or the request fails.
    """
    token = await spotify_auth.get_token()
    if token is None:
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"https://api.spotify.com/v1/playlists/{playlist_id}/items",
                headers={"Authorization": f"Bearer {token}"},
                params={"limit": 100},
                timeout=10,
            )
        if r.status_code == 403:
            log.info("get_playlist_tracks: playlist restricted (followed/editorial)")
            return []
        if r.status_code != 200:
            log.warning("get_playlist_tracks returned %d", r.status_code)
            return None
        result = []
        for item in (r.json().get("items") or []):
            track = (item or {}).get("item") or (item or {}).get("track")
            if not track or not track.get("uri", "").startswith("spotify:track:"):
                continue   # skip local files, podcasts, null items
            artists = track.get("artists") or []
            result.append({
                "uri":        track["uri"],
                "name":       track.get("name", ""),
                "artist":     ", ".join(a.get("name", "") for a in artists),
                "duration_s": round((track.get("duration_ms") or 0) / 1000),
            })
        return result
    except Exception as exc:
        log.error("get_playlist_tracks failed: %s", exc)
        return None


async def play_context(context_uri: str, track_uri: str | None = None) -> bool:
    """
    Start playback of a context URI (e.g. a playlist), optionally from a
    specific track.  If track_uri is None, playback starts from the beginning.
    Returns True on success (200 or 204), False otherwise.
    """
    token = await spotify_auth.get_token()
    if token is None:
        return False
    body: dict = {"context_uri": context_uri}
    if track_uri:
        body["offset"] = {"uri": track_uri}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.put(
                "https://api.spotify.com/v1/me/player/play",
                headers={"Authorization": f"Bearer {token}"},
                json=body,
                timeout=5,
            )
        if r.status_code in (200, 204):
            return True
        log.warning("play_context returned %d: %s", r.status_code, r.text[:200])
        return False
    except Exception as exc:
        log.error("play_context failed: %s", exc)
        return False


async def check_track_saved(track_id: str) -> bool:
    """Return True if the track is in the user's library."""
    token = await spotify_auth.get_token()
    if token is None:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://api.spotify.com/v1/me/library/contains",
                headers={"Authorization": f"Bearer {token}"},
                params={"uris": f"spotify:track:{track_id}"},
                timeout=5,
            )
        if r.status_code == 200:
            result = r.json()
            return bool(result[0]) if result else False
        log.warning("check_track_saved returned %d", r.status_code)
        return False
    except Exception as exc:
        log.error("check_track_saved failed: %s", exc)
        return False


async def save_track(track_id: str) -> bool:
    """Add a track to the user's Liked Songs library."""
    token = await spotify_auth.get_token()
    if token is None:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.put(
                "https://api.spotify.com/v1/me/library",
                headers={"Authorization": f"Bearer {token}"},
                params={"uris": f"spotify:track:{track_id}"},
                timeout=5,
            )
        if r.status_code not in (200, 204):
            log.warning("save_track returned %d: %s", r.status_code, r.text[:300])
            return False
        return True
    except Exception as exc:
        log.error("save_track failed: %s", exc)
        return False


async def send_command(action: str) -> bool:
    """
    Send a Spotify playback command.
    Returns True on success, False if not configured or the request fails.
    """
    if action not in _COMMAND_URLS:
        log.warning("Unknown Spotify command: %s", action)
        return False

    token = await spotify_auth.get_token()
    if token is None:
        return False

    method, url = _COMMAND_URLS[action]
    try:
        async with httpx.AsyncClient() as client:
            r = await client.request(
                method, url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=5,
            )
        if r.status_code in (200, 204):
            return True
        log.warning("Spotify command %s returned %d", action, r.status_code)
        return False
    except Exception as exc:
        log.error("Spotify command %s failed: %s", action, exc)
        return False
