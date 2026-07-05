"""
Endpoint-level test for POST /system/rollback against an empty history.

Uses a bare TestClient(app) instance (no `with` block) so the app's full
lifespan — which starts background broadcast loops for engine/spotify/etc —
is never triggered. A single request through TestClient still dispatches
through the ASGI app and its routing/exception handling without needing the
lifespan to run, since none of the mocked defaults (TD5_MOCK=1, etc.) require
real hardware or network access at import time.
"""
import sys
import os
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from fastapi.testclient import TestClient

import update_service
import main


def test_rollback_endpoint_returns_400_when_history_empty(monkeypatch):
    monkeypatch.setattr(update_service.db, "get_update_history", MagicMock(return_value=[]))
    monkeypatch.setattr(update_service.db, "pop_update_history", MagicMock())

    client = TestClient(main.app)
    response = client.post("/system/rollback")

    assert response.status_code == 400
    assert response.json() == {"error": "no_previous_version"}
