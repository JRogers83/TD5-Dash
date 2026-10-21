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
