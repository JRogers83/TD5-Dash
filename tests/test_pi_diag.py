"""Tests for backend/obd/pi_diag.py — concurrency guard, log creation, message format."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import obd.pi_diag as pi_diag


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_manager():
    mgr = MagicMock()
    mgr.broadcast = AsyncMock()
    return mgr


# ── Concurrency guard ─────────────────────────────────────────────────────────

def test_run_full_test_rejects_when_already_running():
    """POST /obd/full-test returns error dict when a test is already in progress."""
    pi_diag._test_running = True
    try:
        result = asyncio.get_event_loop().run_until_complete(
            pi_diag.run_full_test(_make_manager())
        )
        assert result == {"error": "already running"}
    finally:
        pi_diag._test_running = False


# ── Stage message format ──────────────────────────────────────────────────────

def test_broadcast_stage_message_format():
    """_broadcast_stage sends the correct obd_test WS message structure."""
    loop = asyncio.new_event_loop()
    manager = _make_manager()

    pi_diag._broadcast_stage(loop, manager, 2, "Protocol Self-Test", "pass", "Checksum OK")

    loop.run_until_complete(asyncio.sleep(0))  # flush coroutine
    manager.broadcast.assert_called_once()
    payload = manager.broadcast.call_args[0][0]
    assert payload["type"] == "obd_test"
    assert payload["data"]["stage"] == 2
    assert payload["data"]["name"] == "Protocol Self-Test"
    assert payload["data"]["status"] == "pass"
    assert payload["data"]["detail"] == "Checksum OK"
    loop.close()


# ── Log file creation ─────────────────────────────────────────────────────────

def test_run_test_creates_log_file(tmp_path):
    """_run_test creates a log file at the given path and writes stage info."""
    loop = asyncio.new_event_loop()
    manager = _make_manager()
    log_path = str(tmp_path / "obd_test.log")

    # Patch KLineConnection.open to raise immediately (simulates no hardware)
    # so the test completes quickly.
    with patch("obd.pi_diag.KLineConnection") as mock_conn_cls:
        mock_conn_cls.return_value.__enter__ = MagicMock(side_effect=Exception("no hardware"))
        mock_conn_cls.return_value.open = MagicMock(side_effect=Exception("no hardware"))
        pi_diag._run_test(manager, loop, log_path)

    assert Path(log_path).exists()
    content = Path(log_path).read_text()
    assert "STAGE 1" in content
    assert "STAGE 2" in content
    loop.close()


# ── Protocol self-test vectors ────────────────────────────────────────────────

def test_protocol_self_test_vectors():
    """Verify the known checksum and seed-key values used in Stage 2."""
    from obd import protocol as P

    # StartCommunication checksum — vehicle-confirmed
    frame = bytes([0x81, 0x13, 0xF7, 0x81])
    assert P.checksum(frame) == 0x0C

    # Seed-key LFSR — vehicle-confirmed
    assert P.td5_seed_to_key(0xBA08) == 0x70DC
