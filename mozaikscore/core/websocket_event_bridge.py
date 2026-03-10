# ==============================================================================
# FILE: mozaikscore/core/websocket_event_bridge.py
# DESCRIPTION: Bridges event_bus events to WebSocket push.
#              Subscribes to key events and forwards them to connected users
#              via websocket_manager.send_to_user().
# ==============================================================================
import logging
import asyncio

from mozaikscore.core.event_bus import event_bus
from mozaikscore.core.websocket_manager import websocket_manager

logger = logging.getLogger("mozaikscore.ws_bridge")

# Events that get pushed to the specific user_id found in the event data
_USER_TARGETED_EVENTS = [
    "notification_created",
    "notification_read",
    "notification_deleted",
    "all_notifications_read",
    "module_executed",
    "module_execution_error",
    "module_settings_updated",
    "settings_updated",
    "profile_updated",
    "subscription_updated",
    "subscription_canceled",
    "theme_changed",
    "notification_preferences_updated",
]

# Events that get broadcast to all connected users
_BROADCAST_EVENTS = [
    "module_refresh_complete",
    "system_announcement",
]


def register_websocket_events():
    """Register event_bus subscribers that forward events to WebSocket clients."""
    for event_name in _USER_TARGETED_EVENTS:
        event_bus.subscribe(event_name, _make_user_handler(event_name))
        logger.info("WS bridge: subscribed to user-targeted event '%s'", event_name)

    for event_name in _BROADCAST_EVENTS:
        event_bus.subscribe(event_name, _make_broadcast_handler(event_name))
        logger.info("WS bridge: subscribed to broadcast event '%s'", event_name)


def _make_user_handler(event_name: str):
    """Create an async handler that pushes the event to a specific user."""

    async def _handler(data):
        user_id = data.get("user_id")
        if not user_id:
            logger.debug("WS bridge: no user_id in event '%s', skipping", event_name)
            return

        conns = websocket_manager.active_connections.get(user_id)
        if not conns:
            return  # User not connected — event is still persisted by the source

        payload = {
            "type": "event",
            "event": event_name,
            "data": _sanitize_for_json(data),
        }
        await websocket_manager.send_to_user(user_id, payload)
        logger.debug("WS push: '%s' -> user %s", event_name, user_id)

    _handler.__name__ = f"ws_push_{event_name}"
    return _handler


def _make_broadcast_handler(event_name: str):
    """Create an async handler that broadcasts the event to all users."""

    async def _handler(data):
        if not websocket_manager.active_connections:
            return

        payload = {
            "type": "broadcast",
            "event": event_name,
            "data": _sanitize_for_json(data),
        }
        await websocket_manager.broadcast(payload)
        logger.debug("WS broadcast: '%s' -> %d users", event_name, len(websocket_manager.active_connections))

    _handler.__name__ = f"ws_broadcast_{event_name}"
    return _handler


def _sanitize_for_json(data: dict) -> dict:
    """Strip non-serializable fields before sending over WebSocket."""
    clean = {}
    for k, v in data.items():
        if k.startswith("_"):
            continue  # Skip internal context fields
        if isinstance(v, (str, int, float, bool, type(None))):
            clean[k] = v
        elif isinstance(v, (list, dict)):
            clean[k] = v
        else:
            clean[k] = str(v)
    return clean
