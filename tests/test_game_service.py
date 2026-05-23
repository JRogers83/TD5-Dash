"""Tests for backend/game_service.py — Doom mode endpoint behaviour.

All tests use FastAPI's TestClient and monkeypatch out:
  - subprocess.Popen      (no real launcher process is spawned)
  - shared_state.chromium_pid  (set to a mock PID where freeze is tested)
  - spotify_service       (no real Spotify HTTP calls)
  - psutil.Process        (no real process introspection)
"""
import os
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import game_service


@pytest.fixture
def app():
    a = FastAPI()
    a.include_router(game_service.router)
    return a


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_state():
    """Reset game_service module-level state between tests."""
    game_service._launcher_proc = None
    game_service._current_mode = None
    game_service._spotify_was_playing = False
    game_service._watcher_task = None
    game_service._last_error = None
    yield
    game_service._launcher_proc = None
    game_service._current_mode = None
    game_service._spotify_was_playing = False
    game_service._watcher_task = None
    game_service._last_error = None


@pytest.fixture(autouse=True)
def production_env(monkeypatch):
    """Default test env: not dev mode, DISPLAY set so the env guards pass."""
    monkeypatch.delenv("DEV_MODE", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")


class TestStatus:
    def test_status_not_running_initially(self, client):
        r = client.get("/system/game-mode/status")
        assert r.status_code == 200
        assert r.json() == {"running": False, "mode": None, "last_error": None}


class TestStartValidation:
    def test_start_invalid_mode(self, client):
        r = client.post("/system/game-mode/start",
                        json={"mode": "foo", "skill": 3})
        assert r.status_code == 400
        assert r.json()["detail"] == {"error": "invalid_mode"}

    def test_start_invalid_skill_low(self, client):
        r = client.post("/system/game-mode/start",
                        json={"mode": "single", "skill": 0})
        assert r.status_code == 400
        assert r.json()["detail"] == {"error": "invalid_skill"}

    def test_start_invalid_skill_high(self, client):
        r = client.post("/system/game-mode/start",
                        json={"mode": "single", "skill": 6})
        assert r.status_code == 400
        assert r.json()["detail"] == {"error": "invalid_skill"}

    def test_start_dev_mode_blocked(self, client, monkeypatch):
        monkeypatch.setenv("DEV_MODE", "1")
        r = client.post("/system/game-mode/start",
                        json={"mode": "single", "skill": 3})
        assert r.status_code == 501
        assert r.json()["detail"] == {"error": "game_mode_not_available_in_dev"}

    def test_start_display_unset_returns_500(self, client, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        r = client.post("/system/game-mode/start",
                        json={"mode": "single", "skill": 3})
        assert r.status_code == 500
        assert r.json()["detail"] == {"error": "display_not_available"}

    def test_start_wad_missing(self, client, monkeypatch, tmp_path):
        # Point the module's WAD path to a file that doesn't exist
        monkeypatch.setattr(game_service, "_WAD_PATH", tmp_path / "nope.wad")
        r = client.post("/system/game-mode/start",
                        json={"mode": "single", "skill": 3})
        assert r.status_code == 500
        assert r.json()["detail"] == {"error": "wad_missing"}

    def test_start_already_running_returns_409(self, client):
        # Simulate a live launcher by injecting a fake Popen-like object
        class FakeRunningProc:
            def poll(self):
                return None  # poll() == None means "still running"
        game_service._launcher_proc = FakeRunningProc()

        r = client.post("/system/game-mode/start",
                        json={"mode": "single", "skill": 3})
        assert r.status_code == 409
        assert r.json()["detail"] == {"error": "already_running"}
