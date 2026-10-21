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


def test_backlight_status_endpoint_never_raises():
    body = TestClient(main.app).get("/system/backlight").json()
    assert set(body) == {"device", "writable", "problem"}
