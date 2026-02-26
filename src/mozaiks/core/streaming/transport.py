"""WebSocket transport layer (shared runtime layer).

:class:`SimpleTransport` is the singleton WebSocket transport that manages
connections to browser clients and provides send/receive primitives for
UI-tool events, chat events, and general messages.

It wraps :class:`~mozaiks.core.streaming.RunStreamHub` for per-run pub/sub
and adds higher-level helpers that platform code relies on.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


class SimpleTransport:
    """Singleton WebSocket transport for the mozaiks runtime.

    Platform code obtains the singleton via::

        transport = await SimpleTransport.get_instance()

    The transport tracks active WebSocket connections keyed by chat/run ID
    and provides helpers for:
    * Sending UI-tool events and waiting for user responses
    * Sending arbitrary events to the frontend
    * Sending messages to specific chat sessions
    """

    _instance: SimpleTransport | None = None
    _lock: asyncio.Lock = asyncio.Lock()

    def __init__(self) -> None:
        self.connections: dict[str, Any] = {}
        self._pending_responses: dict[str, asyncio.Future[dict[str, Any]]] = {}

    @classmethod
    async def get_instance(cls, *args: Any, **kwargs: Any) -> SimpleTransport:
        """Return the global singleton, creating it on first call."""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # -- Connection management -------------------------------------------------

    def register_connection(self, chat_id: str, websocket: Any) -> None:
        """Track *websocket* as the active connection for *chat_id*."""
        self.connections[chat_id] = websocket
        logger.debug("SimpleTransport: registered connection for %s", chat_id)

    def unregister_connection(self, chat_id: str) -> None:
        """Remove the connection for *chat_id*."""
        self.connections.pop(chat_id, None)
        logger.debug("SimpleTransport: unregistered connection for %s", chat_id)

    # -- Sending events --------------------------------------------------------

    async def send_event_to_ui(
        self,
        event: Any,
        chat_id: str | None = None,
    ) -> None:
        """Send *event* to the frontend via the WebSocket for *chat_id*.

        If *chat_id* is ``None`` the event is broadcast to all connections.
        """
        targets = (
            [self.connections[chat_id]]
            if chat_id and chat_id in self.connections
            else list(self.connections.values())
        )
        payload = event if isinstance(event, (str, bytes)) else _serialize(event)
        for ws in targets:
            try:
                if isinstance(payload, bytes):
                    await ws.send_bytes(payload)
                else:
                    await ws.send_text(payload)
            except Exception as exc:
                logger.warning("SimpleTransport: send failed: %s", exc)

    async def send_message(self, chat_id: str, message: Any) -> None:
        """Convenience wrapper — send a single message dict to *chat_id*."""
        await self.send_event_to_ui(message, chat_id=chat_id)

    # -- UI-tool request/response flow -----------------------------------------

    async def send_ui_tool_event(
        self,
        *,
        event_id: str | None = None,
        tool_name: str = "",
        payload: dict[str, Any] | None = None,
        chat_id: str | None = None,
        **extra: Any,
    ) -> str:

        """Emit a UI-tool event to the frontend and return the *event_id*.

        Callers that need to wait for the user response should subsequently
        call :meth:`wait_for_ui_tool_response` with the returned *event_id*.
        """
        eid = event_id or str(uuid4())
        event = {
            "type": "chat.tool_call",
            "event_id": eid,
            "tool_name": tool_name,
            "payload": payload or {},
            **extra,
        }
        await self.send_event_to_ui(event, chat_id=chat_id)

        # Prepare a future so wait_for_ui_tool_response can resolve it.
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending_responses[eid] = future

        return eid

    async def wait_for_ui_tool_response(
        self,
        event_id: str,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Block until the frontend submits a response for *event_id*.

        Parameters
        ----------
        event_id:
            The event ID returned by :meth:`send_ui_tool_event`.
        timeout:
            Seconds to wait.  ``None`` means wait indefinitely.

        Returns
        -------
        dict
            The user's response payload.

        Raises
        ------
        asyncio.TimeoutError
            If *timeout* elapses without a response.
        """
        future = self._pending_responses.get(event_id)
        if future is None:
            raise KeyError(f"No pending UI-tool request for event_id={event_id}")

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending_responses.pop(event_id, None)

    def resolve_ui_tool_response(self, event_id: str, response: dict[str, Any]) -> None:
        """Resolve a pending UI-tool future (called when the frontend responds)."""
        future = self._pending_responses.get(event_id)
        if future is not None and not future.done():
            future.set_result(response)


# ---------------------------------------------------------------------------
# Serialization helper
# ---------------------------------------------------------------------------

def _serialize(obj: Any) -> str:
    import json

    if hasattr(obj, "model_dump"):
        return json.dumps(obj.model_dump(), default=str)
    if hasattr(obj, "__dict__"):
        return json.dumps(obj.__dict__, default=str)
    return json.dumps(obj, default=str)


__all__ = ["SimpleTransport"]
