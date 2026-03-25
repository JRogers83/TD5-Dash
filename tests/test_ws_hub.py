"""
Tests for backend/ws_hub.py — ConnectionManager state caching and stale detection.
"""

import time
import pytest
from ws_hub import ConnectionManager


# ── State caching ────────────────────────────────────────────────────────────

class TestStateCache:
    @pytest.mark.asyncio
    async def test_broadcast_stores_state(self, manager):
        """Broadcasting a message caches its data under the topic key."""
        await manager.broadcast({"type": "engine", "data": {"rpm": 850}})
        state = manager.get_state("engine")
        assert state["data"] == {"rpm": 850}

    @pytest.mark.asyncio
    async def test_get_state_returns_empty_for_unknown_topic(self, manager):
        assert manager.get_state("nonexistent") == {}

    @pytest.mark.asyncio
    async def test_get_all_state(self, manager):
        """get_state(None) returns all topics."""
        await manager.broadcast({"type": "engine", "data": {"rpm": 850}})
        await manager.broadcast({"type": "victron", "data": {"soc_pct": 87}})
        state = manager.get_state()
        assert "engine" in state
        assert "victron" in state
        assert state["engine"]["data"]["rpm"] == 850
        assert state["victron"]["data"]["soc_pct"] == 87

    @pytest.mark.asyncio
    async def test_broadcast_updates_existing_state(self, manager):
        """Subsequent broadcasts for the same topic overwrite the cache."""
        await manager.broadcast({"type": "engine", "data": {"rpm": 850}})
        await manager.broadcast({"type": "engine", "data": {"rpm": 2500}})
        state = manager.get_state("engine")
        assert state["data"]["rpm"] == 2500

    @pytest.mark.asyncio
    async def test_state_includes_updated_at(self, manager):
        """Cached state must include an ISO-8601 updated_at timestamp."""
        await manager.broadcast({"type": "engine", "data": {"rpm": 850}})
        state = manager.get_state("engine")
        assert "updated_at" in state
        assert "T" in state["updated_at"]  # ISO-8601 format

    @pytest.mark.asyncio
    async def test_broadcast_without_type_does_not_cache(self, manager):
        """Messages without a 'type' field should not be cached."""
        await manager.broadcast({"data": {"rpm": 850}})
        assert manager.get_state() == {}


# ── Stale detection ──────────────────────────────────────────────────────────

class TestStaleDetection:
    @pytest.mark.asyncio
    async def test_fresh_data_is_not_stale(self, manager):
        """Data just broadcast should not be stale."""
        await manager.broadcast({"type": "engine", "data": {"rpm": 850}})
        state = manager.get_state("engine")
        assert state["stale"] is False

    @pytest.mark.asyncio
    async def test_old_data_is_stale(self, manager):
        """Data older than 30 seconds should be marked stale."""
        await manager.broadcast({"type": "engine", "data": {"rpm": 850}})
        # Simulate 31 seconds elapsed by patching the cached timestamp
        manager._state["engine"]["updated_at"] = time.time() - 31
        state = manager.get_state("engine")
        assert state["stale"] is True

    @pytest.mark.asyncio
    async def test_stale_boundary(self, manager):
        """Data at 29 seconds should not be stale."""
        await manager.broadcast({"type": "engine", "data": {"rpm": 850}})
        manager._state["engine"]["updated_at"] = time.time() - 29
        state = manager.get_state("engine")
        assert state["stale"] is False


# ── Connection lifecycle ─────────────────────────────────────────────────────

class TestConnectionLifecycle:
    def test_initial_state_empty(self, manager):
        """A fresh manager has no connections and no state."""
        assert manager._connections == []
        assert manager.get_state() == {}

    def test_disconnect_nonexistent_is_safe(self, manager):
        """Disconnecting a WebSocket not in the list should not raise."""
        class FakeWS:
            pass
        manager.disconnect(FakeWS())  # should not raise
