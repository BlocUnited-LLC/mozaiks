from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, WebSocket
from starlette.websockets import WebSocketDisconnect

from mozaiksai.core.auth import UserPrincipal, authenticate_websocket_with_path_user, require_user

logger = logging.getLogger(__name__)

MESSAGE_SENT_EVENT = "app.messages.message.sent"
NOTIFICATION_CREATED_EVENT = "notification.created"


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _recipient_ids(payload: dict[str, Any]) -> list[str]:
    explicit = _string_list(payload.get("recipient_ids"))
    if explicit:
        return explicit
    participant_ids = _string_list(payload.get("participant_ids"))
    sender_id = str(payload.get("sender_id") or "").strip()
    return [uid for uid in participant_ids if uid != sender_id]


class MessageDeliveryWorker:
    """Runtime-extension worker: real-time per-user message delivery.

    Maintains a registry of live WebSocket connections keyed by user_id.
    Subscribes to ``app.messages.message.sent`` and ``notification.created``
    on the unified event dispatcher and pushes envelopes directly to all
    connected sockets for the relevant recipients.

    Intentionally separate from the workflow WebSocket transport — message
    sockets are user-scoped, not session-scoped.
    """

    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = {}
        self._registered = False

    # ------------------------------------------------------------------
    # Startup / shutdown (called by the runtime via startup_service contract)
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._registered:
            return
        try:
            from mozaiksai.core.events import get_event_dispatcher

            dispatcher = get_event_dispatcher()
            dispatcher.register_handler(MESSAGE_SENT_EVENT, self._handle_message_sent)
            dispatcher.register_handler(
                NOTIFICATION_CREATED_EVENT, self._handle_notification_created
            )
            self._registered = True
            logger.info(
                "MESSAGE_DELIVERY_WORKER_READY: subscribed to %s, %s",
                MESSAGE_SENT_EVENT,
                NOTIFICATION_CREATED_EVENT,
            )
        except Exception as exc:
            logger.warning("MESSAGE_DELIVERY_WORKER_NOT_STARTED: %s", exc)

    def stop(self) -> None:
        self._connections.clear()
        self._registered = False

    # ------------------------------------------------------------------
    # WebSocket lifecycle
    # ------------------------------------------------------------------

    async def connect(self, user_id: str, websocket: WebSocket) -> None:
        ws_user = await authenticate_websocket_with_path_user(websocket, user_id)
        if ws_user is None:
            return  # already closed by auth helper

        await websocket.accept()
        normalized_user_id = str(ws_user.user_id or user_id)
        self._connections.setdefault(normalized_user_id, set()).add(websocket)

        try:
            await websocket.send_json(
                {"type": "messages.connected", "user_id": normalized_user_id}
            )
            # Keep the connection alive; client sends pings as plain text
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.debug(
                "MESSAGE_SOCKET_CLOSED: user_id=%s error=%s", normalized_user_id, exc
            )
        finally:
            self.disconnect(normalized_user_id, websocket)

    def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        connections = self._connections.get(user_id)
        if not connections:
            return
        connections.discard(websocket)
        if not connections:
            self._connections.pop(user_id, None)

    # ------------------------------------------------------------------
    # Presence
    # ------------------------------------------------------------------

    def online_user_ids(self) -> frozenset[str]:
        """Return the set of user_ids that currently have at least one live socket."""
        return frozenset(self._connections.keys())

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    async def _handle_message_sent(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        recipient_ids = _recipient_ids(payload)
        if not recipient_ids:
            return
        envelope = {"type": MESSAGE_SENT_EVENT, "payload": payload}
        for recipient_id in recipient_ids:
            await self._push(recipient_id, envelope)

    async def _handle_notification_created(self, payload: dict[str, Any]) -> None:
        """Broadcast a badge-invalidation signal to ALL connected users.

        No notification content is pushed over the wire — only the signal.
        Clients refetch /api/modules/messages/notifications/count to refresh badge.
        """
        envelope = {"type": "notification.count_changed"}
        for user_id in list(self._connections.keys()):
            await self._push(user_id, envelope)

    async def _push(self, user_id: str, envelope: dict[str, Any]) -> None:
        stale: list[WebSocket] = []
        for websocket in list(self._connections.get(user_id, set())):
            try:
                await websocket.send_json(envelope)
            except Exception:
                stale.append(websocket)
        for websocket in stale:
            self.disconnect(user_id, websocket)


_WORKER = MessageDeliveryWorker()


def get_worker() -> MessageDeliveryWorker:
    return _WORKER


def get_router() -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws/{user_id}")
    async def messages_websocket(websocket: WebSocket, user_id: str) -> None:
        await _WORKER.connect(user_id, websocket)

    @router.get("/presence")
    async def get_presence(
        user_ids: str = Query(
            ...,
            description="Comma-separated list of user_ids to check online status for.",
        ),
        _user: UserPrincipal = Depends(require_user),
    ) -> dict[str, Any]:
        """Return which of the requested user_ids are currently online.

        Only authenticated users may query presence. The response never
        enumerates all connected users — callers must supply specific ids.
        """
        ids = [uid.strip() for uid in user_ids.split(",") if uid.strip()]
        if not ids:
            return {"online": {}}
        # Cap to 50 ids per request to avoid abuse
        ids = ids[:50]
        online = _WORKER.online_user_ids()
        return {"online": {uid: uid in online for uid in ids}}

    return router
