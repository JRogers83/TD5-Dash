"""Endpoint-level regression tests (bare TestClient — no lifespan, see test_main_ota.py)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from fastapi.testclient import TestClient

import main


def test_clear_dtc_never_claims_success():
    # No verified ECU clear frame exists yet; the endpoint must not tell the driver
    # faults were cleared when nothing was sent (review finding #1).
    for mock in ("1", "0"):
        os.environ["TD5_MOCK"] = mock
        body = TestClient(main.app).post("/obd/clear-dtc").json()
        assert body["ok"] is False
        assert body["detail"]
    os.environ["TD5_MOCK"] = "1"


def test_relay_reports_not_switched_when_no_hardware():
    # Amp relay is an unwired stub; it must not claim success (review finding #10).
    body = TestClient(main.app).post("/system/relay", json={"name": "amp", "state": True}).json()
    assert body["ok"] is False
    assert body["state"] is False


def test_provision_status_endpoint_returns_diff(monkeypatch):
    import provision_service
    monkeypatch.setattr(provision_service, "get_status", lambda: {
        "available": True, "up_to_date": False, "pending_install": ["feh"],
        "pending_remove": [], "cleanup_present": False})
    body = TestClient(main.app).get("/system/provision").json()
    assert body["pending_install"] == ["feh"] and body["up_to_date"] is False


def test_provision_apply_endpoint_returns_result_and_fresh_status(monkeypatch):
    import provision_service
    monkeypatch.setattr(provision_service, "apply",
                        lambda: {"ok": True, "output": "== done", "needs_setup": False})
    monkeypatch.setattr(provision_service, "get_status", lambda: {
        "available": True, "up_to_date": True, "pending_install": [],
        "pending_remove": [], "cleanup_present": False})
    body = TestClient(main.app).post("/system/provision/apply").json()
    assert body["ok"] is True and body["status"]["up_to_date"] is True


def test_provision_apply_reports_needs_setup(monkeypatch):
    import provision_service
    monkeypatch.setattr(provision_service, "apply",
                        lambda: {"ok": False, "output": "sudo: a password is required", "needs_setup": True})
    monkeypatch.setattr(provision_service, "get_status", lambda: {"available": True, "up_to_date": False,
                        "pending_install": ["feh"], "pending_remove": [], "cleanup_present": False})
    body = TestClient(main.app).post("/system/provision/apply").json()
    assert body["ok"] is False and body["needs_setup"] is True
