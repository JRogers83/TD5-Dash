"""Tests for backend/game_service.py — Doom mode endpoint behaviour.

All tests use FastAPI's TestClient and monkeypatch out:
  - subprocess.Popen      (no real launcher process is spawned)
  - shared_state.chromium_pid  (set to a mock PID where freeze is tested)
  - spotify_service       (no real Spotify HTTP calls)
  - psutil.Process        (no real process introspection)
"""
import asyncio
import os
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import game_service
import shared_state


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
    game_service._stop_lock = asyncio.Lock()
    yield
    game_service._launcher_proc = None
    game_service._current_mode = None
    game_service._spotify_was_playing = False
    game_service._watcher_task = None
    game_service._last_error = None
    game_service._stop_lock = asyncio.Lock()


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
        # Both override and FreeDoom fallback point to non-existent files
        monkeypatch.setattr(game_service, "_WAD_OVERRIDE", tmp_path / "nope.wad")
        monkeypatch.setattr(game_service, "_WAD_FREEDOOM", tmp_path / "nope2.wad")
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


class TestStartHappyPath:
    @pytest.fixture(autouse=True)
    def wad_exists(self, monkeypatch, tmp_path):
        wad = tmp_path / "doom.wad"
        wad.write_bytes(b"FAKE_WAD")
        monkeypatch.setattr(game_service, "_WAD_OVERRIDE", wad)

    @pytest.fixture
    def mock_popen(self, monkeypatch):
        import threading
        captured = {}
        _never = threading.Event()  # never set → wait() blocks until timeout
        class FakeProc:
            pid = 4242
            def __init__(self, *a, **kw):
                captured["args"] = a
                captured["kwargs"] = kw
            def poll(self): return None
            def wait(self, timeout=None):
                # Block briefly then return 0 — simulates a process that exits
                # only after the test has finished asserting.
                _never.wait(timeout=0.05)
                return 0
        monkeypatch.setattr(game_service.subprocess, "Popen", FakeProc)
        return captured

    def test_start_returns_ok(self, client, mock_popen):
        r = client.post("/system/game-mode/start",
                        json={"mode": "single", "skill": 3})
        assert r.status_code == 200
        assert r.json() == {"ok": True}

    def test_start_passes_env_to_launcher(self, client, mock_popen):
        client.post("/system/game-mode/start",
                    json={"mode": "coop", "skill": 4})
        env = mock_popen["kwargs"]["env"]
        assert env["MODE"]  == "coop"
        assert env["SKILL"] == "4"
        assert "WAD" in env
        assert env["WAD"].endswith("doom.wad")

    def test_start_uses_new_session(self, client, mock_popen):
        client.post("/system/game-mode/start",
                    json={"mode": "single", "skill": 3})
        assert mock_popen["kwargs"]["start_new_session"] is True

    def test_start_sets_current_mode(self, client, mock_popen):
        client.post("/system/game-mode/start",
                    json={"mode": "deathmatch", "skill": 5})
        r = client.get("/system/game-mode/status")
        assert r.json()["running"] is True
        assert r.json()["mode"] == "deathmatch"

    def test_start_second_call_returns_409(self, client, mock_popen):
        # First start succeeds
        client.post("/system/game-mode/start",
                    json={"mode": "single", "skill": 3})
        # Second start while running returns 409
        r = client.post("/system/game-mode/start",
                        json={"mode": "single", "skill": 3})
        assert r.status_code == 409
        assert r.json()["detail"] == {"error": "already_running"}


class TestStop:
    @pytest.fixture(autouse=True)
    def wad_exists(self, monkeypatch, tmp_path):
        wad = tmp_path / "doom.wad"
        wad.write_bytes(b"FAKE_WAD")
        monkeypatch.setattr(game_service, "_WAD_OVERRIDE", wad)

    def test_stop_when_not_running_returns_ok(self, client):
        r = client.post("/system/game-mode/stop")
        assert r.status_code == 200
        assert r.json() == {"ok": True}

    def test_stop_kills_process_group(self, client, monkeypatch):
        killpg_calls = []
        monkeypatch.setattr(game_service, "_killpg",
                            lambda pid, sig: killpg_calls.append((pid, sig)))
        # Inject a running mock proc
        import signal
        class RunningProc:
            pid = 9999
            def poll(self): return None
            def wait(self, timeout=None): return 0
        game_service._launcher_proc = RunningProc()

        r = client.post("/system/game-mode/stop")
        assert r.status_code == 200
        assert (9999, signal.SIGTERM) in killpg_calls


class TestExitCodeMapping:
    def test_controllers_missing(self):
        assert game_service._EXIT_CODE_MESSAGES[2] == "Controllers required for this mode"

    def test_matchbox_failed(self):
        assert game_service._EXIT_CODE_MESSAGES[3] == "Window manager failed; check journalctl"

    def test_doom_failed(self):
        assert game_service._EXIT_CODE_MESSAGES[4] == "Doom failed to start; check journalctl"

    def test_unknown_code_not_in_dict(self):
        # Default applied via .get(...) in _watch_for_exit
        assert 99 not in game_service._EXIT_CODE_MESSAGES


class TestWatcherTask:
    @pytest.fixture(autouse=True)
    def wad_exists(self, monkeypatch, tmp_path):
        wad = tmp_path / "doom.wad"
        wad.write_bytes(b"FAKE_WAD")
        monkeypatch.setattr(game_service, "_WAD_OVERRIDE", wad)

    @pytest.mark.asyncio
    async def test_watcher_maps_exit_code_2(self, monkeypatch):
        class FakeProc:
            pid = 4242
            def poll(self): return 2
            def wait(self, timeout=None): return 2
        monkeypatch.setattr(game_service, "_killpg", lambda *a, **k: None)
        game_service._launcher_proc = FakeProc()
        await game_service._watch_for_exit()
        assert game_service._last_error == "Controllers required for this mode"

    @pytest.mark.asyncio
    async def test_watcher_maps_unknown_exit_code(self, monkeypatch):
        class FakeProc:
            pid = 4242
            def poll(self): return 99
            def wait(self, timeout=None): return 99
        monkeypatch.setattr(game_service, "_killpg", lambda *a, **k: None)
        game_service._launcher_proc = FakeProc()
        await game_service._watch_for_exit()
        assert game_service._last_error == "Game exited unexpectedly"

    @pytest.mark.asyncio
    async def test_watcher_clean_exit_no_error(self, monkeypatch):
        class FakeProc:
            pid = 4242
            def poll(self): return 0
            def wait(self, timeout=None): return 0
        monkeypatch.setattr(game_service, "_killpg", lambda *a, **k: None)
        game_service._launcher_proc = FakeProc()
        await game_service._watch_for_exit()
        assert game_service._last_error is None

    def test_last_error_cleared_on_next_start(self, client, monkeypatch):
        game_service._last_error = "Something broke"
        class FakeProc:
            pid = 4242
            def poll(self): return None
            def wait(self, timeout=None): return 0
        monkeypatch.setattr(game_service.subprocess, "Popen",
                            lambda *a, **k: FakeProc())
        client.post("/system/game-mode/start",
                    json={"mode": "single", "skill": 3})
        assert game_service._last_error is None


class TestChromiumFreeze:
    @pytest.fixture(autouse=True)
    def wad_exists(self, monkeypatch, tmp_path):
        wad = tmp_path / "doom.wad"
        wad.write_bytes(b"FAKE_WAD")
        monkeypatch.setattr(game_service, "_WAD_OVERRIDE", wad)

    @pytest.fixture
    def mock_popen(self, monkeypatch):
        import threading
        _never = threading.Event()
        class FakeProc:
            pid = 4242
            def poll(self): return None
            def wait(self, timeout=None):
                _never.wait(timeout=0.05)
                return 0
        monkeypatch.setattr(game_service.subprocess, "Popen",
                            lambda *a, **k: FakeProc())

    def test_chromium_pid_none_skips_freeze(self, client, mock_popen, monkeypatch):
        # PID discovery hasn't completed yet
        monkeypatch.setattr(shared_state, "chromium_pid", None)
        # Should not raise — the freeze path is a no-op
        r = client.post("/system/game-mode/start",
                        json={"mode": "single", "skill": 3})
        assert r.status_code == 200

    def test_freeze_walks_tree(self, client, mock_popen, monkeypatch):
        suspended = []
        class FakeProc:
            def __init__(self, pid): self.pid_ = pid
            def suspend(self): suspended.append(("suspend", self.pid_))
            def resume(self):  suspended.append(("resume",  self.pid_))
        class FakeParent:
            def __init__(self, pid): self.pid_ = pid
            def suspend(self): suspended.append(("suspend", self.pid_))
            def resume(self):  suspended.append(("resume",  self.pid_))
            def children(self, recursive=False):
                return [FakeProc(1001), FakeProc(1002), FakeProc(1003)]
        monkeypatch.setattr(shared_state, "chromium_pid", 1000)
        monkeypatch.setattr(game_service.psutil, "Process",
                            lambda pid: FakeParent(pid))

        client.post("/system/game-mode/start",
                    json={"mode": "single", "skill": 3})

        suspends = [pid for action, pid in suspended if action == "suspend"]
        # Order is deterministic here because FakeParent.children() returns a
        # fixed list — real psutil traversal order is not guaranteed.
        assert suspends == [1000, 1001, 1002, 1003]

    @pytest.mark.asyncio
    async def test_unfreeze_walks_tree(self, monkeypatch):
        actions = []
        class FakeProc:
            def __init__(self, pid): self.pid_ = pid
            def resume(self): actions.append(("resume", self.pid_))
            def suspend(self): pass
        class FakeParent:
            def __init__(self, pid): self.pid_ = pid
            def resume(self): actions.append(("resume", self.pid_))
            def suspend(self): pass
            def children(self, recursive=False):
                return [FakeProc(2001), FakeProc(2002)]
        monkeypatch.setattr(shared_state, "chromium_pid", 2000)
        monkeypatch.setattr(game_service.psutil, "Process",
                            lambda pid: FakeParent(pid))

        game_service._unfreeze_chromium_tree()

        resumes = [pid for action, pid in actions]
        assert resumes == [2000, 2001, 2002]


class TestSpotifyIntegration:
    @pytest.fixture(autouse=True)
    def wad_exists(self, monkeypatch, tmp_path):
        wad = tmp_path / "doom.wad"
        wad.write_bytes(b"FAKE_WAD")
        monkeypatch.setattr(game_service, "_WAD_OVERRIDE", wad)

    @pytest.fixture
    def mock_popen(self, monkeypatch):
        import threading
        _never = threading.Event()
        class FakeProc:
            pid = 4242
            def poll(self): return None
            def wait(self, timeout=None):
                _never.wait(timeout=0.05)
                return 0
        monkeypatch.setattr(game_service.subprocess, "Popen",
                            lambda *a, **k: FakeProc())

    def test_pauses_spotify_when_playing(self, client, mock_popen, monkeypatch):
        import spotify_service
        commands = []
        async def fake_cmd(action): commands.append(action); return True
        monkeypatch.setattr(spotify_service, "current_state",
                            lambda: {"playing": True})
        monkeypatch.setattr(spotify_service, "send_command", fake_cmd)

        client.post("/system/game-mode/start",
                    json={"mode": "single", "skill": 3})
        assert commands == ["pause"]
        assert game_service._spotify_was_playing is True

    def test_does_not_pause_when_not_playing(self, client, mock_popen, monkeypatch):
        import spotify_service
        commands = []
        async def fake_cmd(action): commands.append(action); return True
        monkeypatch.setattr(spotify_service, "current_state",
                            lambda: {"playing": False})
        monkeypatch.setattr(spotify_service, "send_command", fake_cmd)

        client.post("/system/game-mode/start",
                    json={"mode": "single", "skill": 3})
        assert commands == []
        assert game_service._spotify_was_playing is False

    @pytest.mark.asyncio
    async def test_resumes_only_if_was_playing(self, monkeypatch):
        import spotify_service
        commands = []
        async def fake_cmd(action): commands.append(action); return True
        monkeypatch.setattr(spotify_service, "send_command", fake_cmd)

        # Set state as if a paused-by-game session is active
        class FakeProc:
            pid = 4242
            def poll(self): return None
            def wait(self, timeout=None): return 0
        game_service._launcher_proc = FakeProc()
        game_service._spotify_was_playing = True
        monkeypatch.setattr(game_service, "_killpg", lambda *a, **k: None)
        monkeypatch.setattr(shared_state, "chromium_pid", None)

        await game_service._stop_internal()
        assert commands == ["play"]
        assert game_service._spotify_was_playing is False

    @pytest.mark.asyncio
    async def test_does_not_resume_if_not_paused_by_game(self, monkeypatch):
        import spotify_service
        commands = []
        async def fake_cmd(action): commands.append(action); return True
        monkeypatch.setattr(spotify_service, "send_command", fake_cmd)

        class FakeProc:
            pid = 4242
            def poll(self): return None
            def wait(self, timeout=None): return 0
        game_service._launcher_proc = FakeProc()
        game_service._spotify_was_playing = False
        monkeypatch.setattr(game_service, "_killpg", lambda *a, **k: None)
        monkeypatch.setattr(shared_state, "chromium_pid", None)

        await game_service._stop_internal()
        assert commands == []

    def test_does_not_track_as_paused_if_command_fails(self, client, mock_popen, monkeypatch):
        import spotify_service
        async def failing_cmd(action): return False  # simulate auth/network failure
        monkeypatch.setattr(spotify_service, "current_state",
                            lambda: {"playing": True})
        monkeypatch.setattr(spotify_service, "send_command", failing_cmd)

        client.post("/system/game-mode/start",
                    json={"mode": "single", "skill": 3})
        # Even though we tried to pause, the command failed, so we must NOT track
        # this as our doing — otherwise a spurious resume fires on Doom exit.
        assert game_service._spotify_was_playing is False
