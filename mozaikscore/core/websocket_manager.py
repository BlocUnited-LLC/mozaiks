# ==============================================================================
# FILE: mozaikscore/core/websocket_manager.py
# DESCRIPTION: WebSocket connection registry.  Allows any backend service to
#              push JSON messages to a specific user in real-time.
# ORIGIN: Migrated from mozaiks-core-public/backend/core/websocket_manager.py
# ==============================================================================
import logging
from typing import Dict, List

from fastapi import WebSocket

logger = logging.getLogger("mozaikscore.websocket_manager")


class WebSocketManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, user_id: str, websocket: WebSocket):
        """Accept and register a WebSocket connection for *user_id*."""
        await websocket.accept()
        self.active_connections.setdefault(user_id, []).append(websocket)
        logger.info("User %s connected via WebSocket", user_id)

    def disconnect(self, user_id: str, websocket: WebSocket):
        """Remove a connection for *user_id*."""
        conns = self.active_connections.get(user_id)
        if conns:
            try:
                conns.remove(websocket)
            except ValueError:
                pass
            if not conns:
                del self.active_connections[user_id]
        logger.info("User %s disconnected from WebSocket", user_id)

    async def send_to_user(self, user_id: str, message: dict):
        """Send a JSON message to all active connections for *user_id*."""
        for conn in self.active_connections.get(user_id, []):
            try:
                await conn.send_json(message)
            except Exception as exc:
                logger.error("Error sending message to %s: %s", user_id, exc)

    async def broadcast(self, message: dict):
        """Broadcast a JSON message to every connected user."""
        for user_id, connections in self.active_connections.items():
            for conn in connections:
                try:
                    await conn.send_json(message)
                except Exception as exc:
                    logger.error("Error broadcasting to %s: %s", user_id, exc)


# Singleton
websocket_manager = WebSocketManager()
