import json
import time
from fastapi import WebSocket

# A topic is considered stale if it hasn't been broadcast within this window.
# All services publish at least every 5 s in normal operation; 30 s therefore
# means the vehicle is off, the service has crashed, or comms have been lost.
_STALE_AFTER_S = 30.0


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: list[WebSocket] = []
        self._state: dict[str, dict] = {}   # topic → {data, updated_at, updated_at_iso}

    # ── State snapshot (REST API) ──────────────────────────────────────────────

    def get_state(self, topic: str | None = None) -> dict:
        """
        Return the last-known state for one or all topics.

        Each entry includes:
          data        — the raw payload dict last broadcast for this topic
          updated_at  — ISO-8601 UTC timestamp of the last broadcast
          stale       — True if no broadcast received in the last 30 s
                        (vehicle off / service down / no comms)
        """
        now = time.time()

        def _entry(t: str, e: dict) -> dict:
            return {
                "data":       e["data"],
                "updated_at": e["updated_at_iso"],
                "stale":      (now - e["updated_at"]) > _STALE_AFTER_S,
            }

        if topic is not None:
            if topic not in self._state:
                return {}
            return _entry(topic, self._state[topic])

        return {t: _entry(t, e) for t, e in self._state.items()}

    # ── WebSocket lifecycle ────────────────────────────────────────────────────

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self._connections:
            self._connections.remove(ws)

    # ── Broadcast ─────────────────────────────────────────────────────────────

    async def broadcast(self, message: dict) -> None:
        # Cache the latest payload per topic for the REST API
        topic = message.get("type")
        if topic:
            now = time.time()
            self._state[topic] = {
                "data":          message.get("data", {}),
                "updated_at":    now,
                "updated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            }

        if not self._connections:
            return

        payload = json.dumps(message)
        dead: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections.remove(ws)
