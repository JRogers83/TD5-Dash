"""
Tests for spotify_auth refresh-token handling — the June 2026 Spotify policy
changes: env-vs-DB reconciliation, refresh-token rotation persistence, and
invalid_grant -> auth_required handling.

httpx is stubbed with a minimal fake AsyncClient so no network is touched.
"""
import importlib

import pytest

import spotify_auth


# ── httpx stub ───────────────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    """Async context manager whose .post returns a queued response."""
    _next: _FakeResponse | None = None
    last_post_data: dict | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, data=None, auth=None, timeout=None):
        _FakeClient.last_post_data = data
        return _FakeClient._next


@pytest.fixture
def auth(monkeypatch):
    """Fresh spotify_auth state with credentials set and httpx stubbed."""
    monkeypatch.setattr(spotify_auth, "CLIENT_ID", "cid")
    monkeypatch.setattr(spotify_auth, "CLIENT_SECRET", "secret")
    monkeypatch.setattr(spotify_auth, "REFRESH_TOKEN", "env-token")
    monkeypatch.setattr(spotify_auth, "_access_token", "")
    monkeypatch.setattr(spotify_auth, "_expiry", 0.0)
    monkeypatch.setattr(spotify_auth, "_active_token", None)
    monkeypatch.setattr(spotify_auth, "_resolved", False)
    monkeypatch.setattr(spotify_auth, "auth_required", False)
    monkeypatch.setattr(spotify_auth.httpx, "AsyncClient", _FakeClient)
    _FakeClient._next = None
    _FakeClient.last_post_data = None
    return spotify_auth


# ── DB stub ──────────────────────────────────────────────────────────────────

class _FakeDB:
    """In-memory stand-in for the db module's settings helpers."""
    def __init__(self, initial=None):
        self.store = dict(initial or {})

    def get_setting(self, key, default=None):
        return self.store.get(key, default)

    def set_settings(self, updates):
        self.store.update({k: str(v) for k, v in updates.items()})


def _patch_db(monkeypatch, fake):
    import sys
    monkeypatch.setitem(sys.modules, "db", fake)


# ── Reconciliation ─────────────────────────────────────────────────────────

class TestResolveActiveToken:
    def test_adopts_new_env_token_when_seed_differs(self, auth, monkeypatch):
        fake = _FakeDB({"spotify_refresh_seed": "old-token",
                        "spotify_refresh_token": "rotated-old"})
        _patch_db(monkeypatch, fake)

        token = auth._resolve_active_token()

        assert token == "env-token"                      # new env wins
        assert fake.store["spotify_refresh_seed"] == "env-token"
        assert fake.store["spotify_refresh_token"] == "env-token"

    def test_uses_stored_token_when_env_matches_seed(self, auth, monkeypatch):
        # Env token already adopted previously; DB holds a rotated token.
        fake = _FakeDB({"spotify_refresh_seed": "env-token",
                        "spotify_refresh_token": "rotated-current"})
        _patch_db(monkeypatch, fake)

        assert auth._resolve_active_token() == "rotated-current"

    def test_falls_back_to_env_when_db_unavailable(self, auth, monkeypatch):
        import sys
        # Make `import db` raise.
        class _Boom:
            def __getattr__(self, _):
                raise RuntimeError("no db")
        monkeypatch.setitem(sys.modules, "db", _Boom())

        assert auth._resolve_active_token() == "env-token"


# ── Refresh: rotation + invalid_grant ──────────────────────────────────────

class TestGetToken:
    async def test_refresh_success(self, auth, monkeypatch):
        _patch_db(monkeypatch, _FakeDB({"spotify_refresh_seed": "env-token",
                                        "spotify_refresh_token": "env-token"}))
        _FakeClient._next = _FakeResponse(200, {"access_token": "AT", "expires_in": 3600})

        token = await auth.get_token()

        assert token == "AT"
        assert auth.auth_required is False
        assert _FakeClient.last_post_data["refresh_token"] == "env-token"

    async def test_rotation_persisted(self, auth, monkeypatch):
        fake = _FakeDB({"spotify_refresh_seed": "env-token",
                        "spotify_refresh_token": "env-token"})
        _patch_db(monkeypatch, fake)
        _FakeClient._next = _FakeResponse(
            200, {"access_token": "AT", "expires_in": 3600, "refresh_token": "NEW-RT"})

        await auth.get_token()

        assert fake.store["spotify_refresh_token"] == "NEW-RT"
        assert auth._active_token == "NEW-RT"

    async def test_invalid_grant_sets_auth_required_and_returns_none(self, auth, monkeypatch):
        _patch_db(monkeypatch, _FakeDB({"spotify_refresh_seed": "env-token",
                                        "spotify_refresh_token": "env-token"}))
        _FakeClient._next = _FakeResponse(400, {"error": "invalid_grant"})

        token = await auth.get_token()

        assert token is None
        assert auth.auth_required is True

    async def test_success_clears_auth_required(self, auth, monkeypatch):
        _patch_db(monkeypatch, _FakeDB({"spotify_refresh_seed": "env-token",
                                        "spotify_refresh_token": "env-token"}))
        monkeypatch.setattr(spotify_auth, "auth_required", True)
        _FakeClient._next = _FakeResponse(200, {"access_token": "AT", "expires_in": 3600})

        await auth.get_token()

        assert auth.auth_required is False
