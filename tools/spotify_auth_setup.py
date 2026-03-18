#!/usr/bin/env python3
"""
One-time Spotify OAuth setup — obtains the refresh token for SPOTIFY_REFRESH_TOKEN.

Prerequisites
─────────────
1. Create a Spotify Developer app at https://developer.spotify.com/dashboard
2. In the app settings add the redirect URI: http://127.0.0.1:8888/callback
3. Set the following environment variables (or pass inline):
     SPOTIFY_CLIENT_ID
     SPOTIFY_CLIENT_SECRET

Usage
─────
    python tools/spotify_auth_setup.py

A browser window will open at the Spotify auth page.  After you approve,
the script catches the redirect, exchanges the code for tokens, and prints
the refresh token — paste it into deploy/td5-dash.service as
SPOTIFY_REFRESH_TOKEN=<value>.
"""

import os
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx

# Load .env from the project root (one level up from tools/) if present.
# Explicit environment variables take precedence.
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

CLIENT_ID     = os.getenv("SPOTIFY_CLIENT_ID",    "")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")

REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES       = "user-read-playback-state user-modify-playback-state playlist-read-private playlist-read-collaborative user-library-modify user-library-read"

_AUTH_URL  = "https://accounts.spotify.com/authorize"
_TOKEN_URL = "https://accounts.spotify.com/api/token"

_code: str | None = None
_server: HTTPServer | None = None


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        global _code
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))

        if "error" in params:
            print(f"\nAuth error: {params['error']}")
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Auth failed. You can close this tab.")
        elif "code" in params:
            _code = params["code"]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Auth complete! You can close this tab.")
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Unexpected request. You can close this tab.")

        # Shut the server down after handling the callback
        threading.Thread(target=_server.shutdown, daemon=True).start()

    def log_message(self, *_) -> None:
        pass   # silence access log


def main() -> None:
    global _server

    if not CLIENT_ID or not CLIENT_SECRET:
        print("Error: SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set.")
        sys.exit(1)

    auth_params = urllib.parse.urlencode({
        "client_id":     CLIENT_ID,
        "response_type": "code",
        "redirect_uri":  REDIRECT_URI,
        "scope":         SCOPES,
        "show_dialog":   "true",
    })
    auth_url = f"{_AUTH_URL}?{auth_params}"

    print("Starting local callback server on port 8888…")
    _server = HTTPServer(("127.0.0.1", 8888), _CallbackHandler)

    print("Opening Spotify auth page in your browser…")
    print(f"If it doesn't open automatically, visit:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    # Block until the callback is received
    _server.serve_forever()

    if _code is None:
        print("No authorisation code received. Exiting.")
        sys.exit(1)

    print("Exchanging code for tokens…")
    r = httpx.post(
        _TOKEN_URL,
        data={
            "grant_type":   "authorization_code",
            "code":         _code,
            "redirect_uri": REDIRECT_URI,
        },
        auth=(CLIENT_ID, CLIENT_SECRET),
    )
    r.raise_for_status()
    tokens = r.json()

    refresh_token = tokens.get("refresh_token", "")
    if not refresh_token:
        print("Error: response did not include a refresh_token.")
        print(tokens)
        sys.exit(1)

    print("\n" + "=" * 60)
    print("SUCCESS — add this to deploy/td5-dash.service:")
    print(f"\n  SPOTIFY_REFRESH_TOKEN={refresh_token}\n")
    print("=" * 60)


if __name__ == "__main__":
    main()
