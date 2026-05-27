"""
WebSocket broadcasting (RAGX-Trader)

Purpose:
  Hold all browser WebSocket connections and fan out JSON messages from one place.
  The Binance stream service should not know about individual clients — it only
  publishes normalized events; this module delivers them to every connected tab.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import WebSocket


class WebSocketBroadcaster:
    """Tracks FastAPI/WebSocket clients and broadcasts serializable payloads."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def register(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)

    async def unregister(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def broadcast_json(self, payload: dict[str, Any]) -> None:
        """Send the same JSON object to every connected client; drop dead sockets."""
        text = json.dumps(payload, separators=(",", ":"))
        async with self._lock:
            targets = list(self._clients)

        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)
