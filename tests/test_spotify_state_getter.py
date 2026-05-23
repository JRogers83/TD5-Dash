"""Spotify state getter used by game_service to decide whether to auto-pause."""
import spotify_service


class TestCurrentState:
    def test_returns_none_before_first_broadcast(self):
        spotify_service._last_payload = None
        assert spotify_service.current_state() is None

    def test_returns_last_payload_after_set(self):
        spotify_service._last_payload = {"playing": True, "track": "Doom Theme"}
        state = spotify_service.current_state()
        assert state == {"playing": True, "track": "Doom Theme"}
