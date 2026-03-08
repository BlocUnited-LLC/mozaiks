# ==============================================================================
# FILE: mozaiksai/transport/websocket/connection_manager.py
# DESCRIPTION: WebSocket connection lifecycle, buffering, heartbeat, backpressure.
#
# Phase 3 migration: AG2 serialization removed.  Callers must inject a
# ``serialize_fn`` callback (``adapters.ag2.serializer.serialize_ag2_object``)
# so this module contains zero AG2 knowledge.
# ==============================================================================
import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from mozaiksai.transport.websocket.connection_state import ConnectionState
from logs.logging_config import get_core_logger

logger = get_core_logger("transport.connection_manager")


def _safe_serialize(obj: Any) -> Any:
    """Minimal JSON-safe conversion used when no AG2 serializer is injected.

    Handles primitives, dicts, and lists.  Non-serializable objects are
    converted to their string representation.  Inject
    ``serialize_ag2_object`` from ``adapters.ag2.serializer`` to handle
    raw AG2 event objects.
    """
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: _safe_serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_serialize(v) for v in obj]
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        try:
            return str(obj)
        except Exception:
            return "<unserializable>"


class ConnectionManager:
    """Owns WebSocket connections, heartbeat, buffering, and backpressure.

    Extracted from ``SimpleTransport`` (Phase 5) to isolate all WebSocket
    protocol-level concerns into a single, testable class.

    Responsibilities:
    - Connection state tracking (``ConnectionState`` per ``chat_id``)
    - Monotonic sequence numbering (for resume/replay)
    - Message queuing with backpressure control
    - Pre-connection buffering (events emitted before WS handshake completes)
    - Heartbeat for detecting silent disconnects

    Phase 3 change: AG2 event serialization removed.  Inject ``serialize_fn``
    (from ``adapters.ag2.serializer.serialize_ag2_object``) to get full AG2
    object serialization.  When not injected, ``_safe_serialize`` is used as a
    JSON-safe fallback for primitive dicts/lists.
    """

    def __init__(
        self,
        *,
        serialize_fn: Optional[Callable[[Any], Any]] = None,
    ) -> None:
        self._serialize_fn = serialize_fn or _safe_serialize
        self.connections: Dict[str, ConnectionState] = {}
        self._sequence_counters: Dict[str, int] = {}
        self._message_queues: Dict[str, List[Dict[str, Any]]] = {}
        self._heartbeat_tasks: Dict[str, asyncio.Task] = {}
        self._max_queue_size: int = 100
        self._heartbeat_interval: int = 120
        self._pre_connection_buffers: Dict[str, List[Dict[str, Any]]] = {}
        self._max_pre_connection_buffer: int = 200
        self._scheduled_flush_tasks: Dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Sequence tracking (T3)
    # ------------------------------------------------------------------

    def get_next_sequence(self, chat_id: str) -> int:
        """Get the next monotonic sequence number for a chat session."""
        if chat_id not in self._sequence_counters:
            self._sequence_counters[chat_id] = 0
        self._sequence_counters[chat_id] += 1
        return self._sequence_counters[chat_id]

    # ------------------------------------------------------------------
    # Backpressure (H1)
    # ------------------------------------------------------------------

    async def check_backpressure(self, chat_id: str) -> bool:
        """Check if connection should be throttled due to backpressure."""
        if chat_id not in self._message_queues:
            self._message_queues[chat_id] = []

        queue_size = len(self._message_queues[chat_id])
        if queue_size >= self._max_queue_size:
            logger.warning(f"🚨 Backpressure triggered for {chat_id}: queue size {queue_size}")
            dropped = queue_size - self._max_queue_size + 10  # Keep some buffer
            self._message_queues[chat_id] = self._message_queues[chat_id][dropped:]
            logger.info(f"📉 Dropped {dropped} queued messages for {chat_id}")
            return True
        return False

    async def queue_message_with_backpressure(self, chat_id: str, message_data: Any) -> bool:
        """Queue message with backpressure control."""
        if await self.check_backpressure(chat_id):
            pass  # Connection is under backpressure — oldest messages dropped
        # Early serialization guard: ensure no raw objects linger in queue.
        if not isinstance(message_data, (dict, list, tuple, str, int, float, bool, type(None))):
            try:
                message_data = self._serialize_fn(message_data)
            except Exception:
                message_data = {"type": "log", "data": {"message": str(message_data)}}

        self._message_queues[chat_id].append(message_data)
        return True

    async def flush_message_queue(self, chat_id: str) -> None:
        """Flush queued messages for a connection."""
        if chat_id not in self._message_queues or not self._message_queues[chat_id]:
            return

        logger.info(
            f"🔄 [TRANSPORT] Flushing message queue for chat_id={chat_id}, "
            f"queue_size={len(self._message_queues[chat_id])}"
        )

        if chat_id in self.connections:
            websocket = self.connections[chat_id].websocket
            messages_to_send = self._message_queues[chat_id].copy()
            self._message_queues[chat_id].clear()

            for message in messages_to_send:
                try:
                    # Check if message is already in proper format for WebSocket
                    if isinstance(message, dict) and 'type' in message and 'data' in message:
                        # Ensure the 'data' payload is JSON-serializable (may contain AG2 objects)
                        try:
                            safe_message = message.copy()
                            safe_message['data'] = self._serialize_fn(message['data'])

                            # Extract agent name from data payload and add to top-level
                            # envelope for frontend attribution
                            if isinstance(safe_message.get('data'), dict):
                                agent_from_data = (
                                    safe_message['data'].get('agent')
                                    or safe_message['data'].get('sender')
                                )
                                if agent_from_data and isinstance(agent_from_data, str):
                                    safe_message['agent'] = agent_from_data
                                elif 'agent' not in safe_message:
                                    safe_message['agent'] = 'Agent'

                            if safe_message.get('type') == 'chat.tool_call':
                                payload_obj = safe_message.get('data', {}).get('payload', {})
                                payload_keys = list(payload_obj.keys()) if isinstance(payload_obj, dict) else []
                                logger.info('TRANSPORT payload keys before send: %s', payload_keys[:12])
                            await websocket.send_json(safe_message)
                            # Yield to event loop so the transport layer can flush
                            # the TCP write buffer before we queue the next message.
                            await asyncio.sleep(0)
                        except Exception:
                            # Fallback: attempt to serialize whole message as a last resort
                            try:
                                await websocket.send_json(self._serialize_fn(message))
                            except Exception:
                                raise
                    else:
                        serialized_message = self._serialize_fn(message)
                        await websocket.send_json(serialized_message)
                except Exception as e:
                    logger.error(f"Failed to send queued message to {chat_id}: {e}. Will retry shortly.")
                    # Re-queue remaining (including current) for retry
                    remaining = [message] + messages_to_send[messages_to_send.index(message) + 1:]
                    self._message_queues[chat_id] = remaining + self._message_queues[chat_id]
                    # Schedule a retry flush with small backoff
                    self.schedule_flush_retry(chat_id)
                    break

    def schedule_flush_retry(self, chat_id: str, delay: float = 0.5) -> None:
        """Schedule a single retry flush if not already pending."""
        if chat_id in self._scheduled_flush_tasks and not self._scheduled_flush_tasks[chat_id].done():
            return  # already scheduled

        async def _delayed():
            try:
                await asyncio.sleep(delay)
                await self.flush_message_queue(chat_id)
            finally:
                self._scheduled_flush_tasks.pop(chat_id, None)

        self._scheduled_flush_tasks[chat_id] = asyncio.create_task(_delayed())

    # ------------------------------------------------------------------
    # Heartbeat (H2)
    # ------------------------------------------------------------------

    async def start_heartbeat(self, chat_id: str, websocket: Any) -> None:
        """Start heartbeat task for a connection."""
        if chat_id in self._heartbeat_tasks:
            self._heartbeat_tasks[chat_id].cancel()

        self._heartbeat_tasks[chat_id] = asyncio.create_task(
            self._heartbeat_loop(chat_id, websocket)
        )
        logger.info(f"💓 Started heartbeat for {chat_id}")

    async def _heartbeat_loop(self, chat_id: str, websocket: Any) -> None:
        """Heartbeat loop for detecting silent disconnects."""
        try:
            while chat_id in self.connections:
                await asyncio.sleep(self._heartbeat_interval)

                ping_data = {
                    "type": "ping",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

                try:
                    await websocket.send_json(ping_data)
                    logger.debug(f"📡 Sent ping to {chat_id}")
                except Exception as e:
                    logger.warning(f"💔 Heartbeat failed for {chat_id}: {e}")
                    # Connection is dead — clean up
                    await self.cleanup_connection(chat_id)
                    break
        except asyncio.CancelledError:
            logger.debug(f"💔 Heartbeat cancelled for {chat_id}")
        except Exception as e:
            logger.error(f"💔 Heartbeat error for {chat_id}: {e}")

    async def stop_heartbeat(self, chat_id: str) -> None:
        """Stop heartbeat task for a connection."""
        if chat_id in self._heartbeat_tasks:
            self._heartbeat_tasks[chat_id].cancel()
            del self._heartbeat_tasks[chat_id]
            logger.debug(f"💔 Stopped heartbeat for {chat_id}")

    # ------------------------------------------------------------------
    # Broadcast
    # ------------------------------------------------------------------

    async def broadcast_to_websockets(
        self, event_data: Dict[str, Any], target_chat_id: Optional[str] = None
    ) -> None:
        """Broadcast event data to relevant WebSocket connections."""
        # If a chat_id is specified, only send to that connection
        if target_chat_id:
            connection_info = self.connections.get(target_chat_id)
            if connection_info and connection_info.websocket:
                # H1: Use message queuing with backpressure control
                await self.queue_message_with_backpressure(target_chat_id, event_data)
                await self.flush_message_queue(target_chat_id)
            else:
                # H4: Buffer message until the websocket connects
                buf = self._pre_connection_buffers.setdefault(target_chat_id, [])
                buf.append(event_data)
                if len(buf) > self._max_pre_connection_buffer:
                    # Drop oldest while keeping newest insight
                    overflow = len(buf) - self._max_pre_connection_buffer
                    del buf[0:overflow]
                    logger.warning(
                        f"🧹 Dropped {overflow} pre-connection buffered messages "
                        f"for {target_chat_id}"
                    )
                logger.debug(
                    f"🕑 Buffered pre-connection message for {target_chat_id} "
                    f"(size={len(buf)})"
                )
            return

        # Otherwise, broadcast to all connections
        active_connections = list(self.connections.items())
        for chat_id, info in active_connections:
            websocket = info.websocket
            if websocket:
                await self.queue_message_with_backpressure(chat_id, event_data)
                await self.flush_message_queue(chat_id)

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def accept_connection(
        self,
        websocket: Any,
        chat_id: str,
        user_id: str,
        workflow_name: str,
        app_id: Optional[str] = None,
        ws_id: Optional[int] = None,
    ) -> None:
        """Accept WebSocket, store ConnectionState, start heartbeat, flush pre-connection buffer."""
        await websocket.accept()

        if ws_id is None:
            ws_id = id(websocket)

        self.connections[chat_id] = ConnectionState(
            websocket=websocket,
            user_id=user_id,
            workflow_name=workflow_name,
            app_id=app_id,
            active=True,
            ws_id=ws_id,
        )
        logger.info(f"🔌 WebSocket connected for chat_id: {chat_id} (ws_id={ws_id})")

        # H2: Start heartbeat for connection
        await self.start_heartbeat(chat_id, websocket)

        # H1: Initialize message queue for backpressure control
        self._message_queues[chat_id] = []

        # H4: Flush any pre-connection buffered messages (if orchestration
        # started emitting before the UI finished the handshake)
        if chat_id in self._pre_connection_buffers:
            buffered = self._pre_connection_buffers.pop(chat_id)
            if buffered:
                logger.info(
                    f"📤 Flushing {len(buffered)} pre-connection buffered messages "
                    f"for {chat_id}"
                )
                for msg in buffered:
                    await self.queue_message_with_backpressure(chat_id, msg)
                await self.flush_message_queue(chat_id)

    async def auto_resume_if_needed(
        self, chat_id: str, websocket: Any, app_id: Optional[str]
    ) -> None:
        """Automatically restore chat history for IN_PROGRESS chats on WebSocket connection."""
        try:
            if not app_id:
                logger.debug(f"[AUTO_RESUME] No app_id for {chat_id}, skipping auto-resume")
                return

            # Get workflow name and startup_mode from connection
            workflow_name = None
            startup_mode = None
            if chat_id in self.connections:
                workflow_name = self.connections[chat_id].workflow_name
                if workflow_name:
                    try:
                        from mozaiksai.kernel.workflow_manager import workflow_manager

                        config = workflow_manager.get_config(workflow_name)
                        startup_mode = config.get("startup_mode", "AgentDriven")
                        logger.debug(
                            f"[AUTO_RESUME] Retrieved startup_mode={startup_mode} "
                            f"for workflow={workflow_name}"
                        )
                    except Exception as cfg_err:
                        logger.warning(f"[AUTO_RESUME] Failed to get workflow config: {cfg_err}")

            # Use SessionResumer for proper message replay with filtering
            from mozaiksai.transport.websocket.resume import SessionResumer

            resumer = SessionResumer()

            async def send_event_wrapper(
                event_dict: Dict[str, Any], target_chat_id: Optional[str]
            ) -> None:
                """Wrapper to convert resume events to transport format."""
                if not isinstance(event_dict, dict):
                    return

                kind = event_dict.get("kind")
                if kind == "text":
                    # Convert to chat.text format
                    await self.queue_message_with_backpressure(chat_id, {
                        "type": "chat.text",
                        "data": {
                            "index": event_dict.get("index", 0),
                            "content": event_dict.get("content", ""),
                            "role": event_dict.get("role", "user"),
                            "agent": event_dict.get("agent", "user"),
                            "sender": event_dict.get("agent", "user"),
                            "replay": event_dict.get("replay", True),
                            "timestamp": event_dict.get("timestamp"),
                            "metadata": event_dict.get("metadata"),
                        },
                    })
                elif kind == "resume_boundary":
                    # Convert boundary to transport format
                    await self.queue_message_with_backpressure(chat_id, {
                        "type": "chat.resume_boundary",
                        "data": event_dict.get("data", {}),
                    })

            # Call the resumer with startup_mode filtering
            await resumer.auto_resume_if_needed(
                chat_id=chat_id,
                app_id=app_id,
                send_event=send_event_wrapper,
                startup_mode=startup_mode,
            )

            await self.flush_message_queue(chat_id)

        except Exception as e:
            logger.warning(f"[AUTO_RESUME] Failed to auto-resume chat {chat_id}: {e}")

    async def handle_resume_request(
        self,
        chat_id: str,
        last_client_index: int,
        websocket: Any,
        *,
        send_event_cb: Callable,
    ) -> None:
        """Handle explicit client resume request.

        Delegates to SessionResumer for message replay, then updates the
        internal sequence counter so live events continue from the correct offset.
        """
        try:
            conn_meta = self.connections.get(chat_id)
            app_id = conn_meta.app_id if conn_meta else None
            if not app_id:
                raise RuntimeError("Missing app_id for resume")

            from mozaiksai.transport.websocket.resume import SessionResumer

            resumer = SessionResumer()
            summary = await resumer.handle_resume_request(
                chat_id=str(chat_id),
                app_id=str(app_id),
                last_client_index=int(last_client_index),
                send_event=send_event_cb,
            )

            # Real-time sequence continuity: do not reduce existing counter.
            last_idx_sent = (
                summary.get("last_message_index") if isinstance(summary, dict) else None
            )
            if isinstance(last_idx_sent, int):
                existing_seq = self._sequence_counters.get(chat_id, 0)
                if existing_seq < last_idx_sent + 1:
                    self._sequence_counters[chat_id] = last_idx_sent + 1

            logger.info(
                "✅ Resume complete chat=%s replayed=%s missing_from>%s now_at_index=%s total=%s",
                chat_id,
                (summary.get("replayed_messages") if isinstance(summary, dict) else None),
                last_client_index,
                last_idx_sent,
                (summary.get("total_messages") if isinstance(summary, dict) else None),
            )
        except Exception as e:
            logger.error(f"❌ Resume failed chat={chat_id}: {e}")
            raise

    async def cleanup_connection(self, chat_id: str) -> None:
        """Clean up connection resources."""
        if chat_id in self.connections:
            del self.connections[chat_id]

        if chat_id in self._message_queues:
            del self._message_queues[chat_id]

        await self.stop_heartbeat(chat_id)
        logger.info(f"🧹 Cleaned up connection resources for {chat_id}")

    # ------------------------------------------------------------------
    # Serialization note (Phase 3)
    # ------------------------------------------------------------------
    # AG2-aware serialization has been moved to:
    #   mozaiksai/adapters/ag2/serializer.py :: serialize_ag2_object()
    # Inject it via the ``serialize_fn`` constructor parameter.
    # The module-level ``_safe_serialize`` is used when no injector is
    # provided (handles primitives and plain dicts only).
