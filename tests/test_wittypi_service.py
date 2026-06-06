"""Tests for wittypi_service /system/shutdown-prepare endpoint."""
import os
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from fastapi import FastAPI


def _make_client(wittypi_enabled: str = "1"):
    """Build a test client with WITTYPI_ENABLED set."""
    os.environ["WITTYPI_ENABLED"] = wittypi_enabled
    import importlib
    import wittypi_service
    importlib.reload(wittypi_service)
    app = FastAPI()
    app.include_router(wittypi_service.router)
    return TestClient(app)


class TestShutdownPrepareDisabled:
    def test_returns_501_when_disabled(self):
        client = _make_client(wittypi_enabled="0")
        r = client.post("/system/shutdown-prepare")
        assert r.status_code == 501
        assert r.json()["detail"]["error"] == "wittypi_not_enabled"


class TestShutdownPrepareOverrideMode:
    def test_returns_409_when_override_mode_active(self):
        import shared_state
        shared_state.override_mode = True
        try:
            client = _make_client(wittypi_enabled="1")
            r = client.post("/system/shutdown-prepare")
            assert r.status_code == 409
            assert r.json()["detail"]["error"] == "override_active"
        finally:
            shared_state.override_mode = False


class TestShutdownPrepareSuccess:
    def test_returns_200_with_cleaned_up_list(self, monkeypatch):
        import shared_state
        shared_state.override_mode = False
        monkeypatch.setattr("db.wal_checkpoint", lambda: None)
        client = _make_client(wittypi_enabled="1")
        r = client.post("/system/shutdown-prepare")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert isinstance(data["cleaned_up"], list)
        assert "db_checkpointed" in data["cleaned_up"]
        assert "shutdown_logged" in data["cleaned_up"]

    def test_game_mode_cleanup_called_when_active(self, monkeypatch):
        import shared_state
        import wittypi_service
        import importlib
        shared_state.override_mode = False
        os.environ["WITTYPI_ENABLED"] = "1"
        importlib.reload(wittypi_service)
        monkeypatch.setattr("db.wal_checkpoint", lambda: None)

        stop_called = []
        async def fake_stop():
            stop_called.append(True)

        with patch("wittypi_service._get_game_service_state",
                   return_value=("running", fake_stop)):
            app = FastAPI()
            app.include_router(wittypi_service.router)
            client = TestClient(app)
            r = client.post("/system/shutdown-prepare")

        assert r.status_code == 200
        assert "game_mode_stopped" in r.json()["cleaned_up"]
