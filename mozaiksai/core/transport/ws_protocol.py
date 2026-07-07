# ==============================================================================
# FILE: mozaiksai/core/transport/ws_protocol.py
# DESCRIPTION: WebSocket protocol layer - heartbeat, backpressure, message queuing
# ==============================================================================
"""
WebSocket protocol mixin for SimpleTransport.

This module contains low-level WebSocket mechanics:
- Heartbeat/keepalive
- Message queuing with backpressure
- Connection cleanup
- Auto-resume on reconnection

Usage:
    class SimpleTransport(WebSocketProtocolMixin):
        ...
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import WebSocket

logger = logging.getLogger("simple_transport.protocol")


class WebSocketProtocolMixin:
    """Mixin providing WebSocket protocol functionality.

    Expects the following attributes on the class:
        - connections: Dict[str, Dict[str, Any]]
        - _sequence_counters: Dict[str, int]
        - _message_queues: Dict[str, List[Dict[str, Any]]]
        - _max_queue_size: int
        - _heartbeat_tasks: Dict[str, asyncio.Task]
        - _heartbeat_interval: int
        - _scheduled_flush_tasks: Dict[str, asyncio.Task]
        - _serialize_ag2_events(obj): method
        - _stringify_unknown(obj): method
    """

    # ==================================================================================
    # SEQUENCE MANAGEMENT
    # ==================================================================================

    def _get_next_sequence(self, chat_id: str) -> int:
        """Get the next sequence number for a chat session."""
        if chat_id not in self._sequence_counters:
            self._sequence_counters[chat_id] = 0
        self._sequence_counters[chat_id] += 1
        return self._sequence_counters[chat_id]

    # ==================================================================================
    # H1: SERVER BACKPRESSURE
    # ==================================================================================

    async def _check_backpressure(self, chat_id: str) -> bool:
        """Check if connection should be throttled due to backpressure."""
        if chat_id not in self._message_queues:
            self._message_queues[chat_id] = []

        queue_size = len(self._message_queues[chat_id])
        if queue_size >= self._max_queue_size:
            logger.warning("Backpressure triggered for %s: queue size %s", chat_id, queue_size)
            # Drop oldest messages to make room
            dropped = queue_size - self._max_queue_size + 10  # Keep some buffer
            self._message_queues[chat_id] = self._message_queues[chat_id][dropped:]
            logger.debug("WS_QUEUE_DROPPED count=%s chat=%s", dropped, chat_id)
            return True
        return False

    async def _queue_message_with_backpressure(self, chat_id: str, message_data: dict[str, Any]) -> bool:
        """Queue message with backpressure control."""
        if await self._check_backpressure(chat_id):
            # Connection is under backpressure - message may have been dropped
            pass
        # Early serialization guard: ensure no raw AG2 objects linger in queue.
        if not isinstance(message_data, (dict, list, tuple, str, int, float, bool, type(None))):
            try:
                message_data = self._serialize_ag2_events(message_data)
            except Exception:
                message_data = {"type": "log", "data": {"message": self._stringify_unknown(message_data)}}

        self._message_queues[chat_id].append(message_data)
        return True

    async def _flush_message_queue(self, chat_id: str) -> None:
        """Flush queued messages for a connection."""
        if chat_id not in self._message_queues or not self._message_queues[chat_id]:
            return

        logger.debug("[PROTOCOL] Flushing message queue for chat_id=%s, queue_size=%s", chat_id, len(self._message_queues[chat_id]))

        if chat_id in self.connections:
            websocket = self.connections[chat_id]["websocket"]
            messages_to_send = self._message_queues[chat_id].copy()
            self._message_queues[chat_id].clear()

            for message in messages_to_send:
                try:
                    # Check if message is already in proper format for WebSocket
                    if isinstance(message, dict) and "type" in message and "data" in message:
                        # Ensure the 'data' payload is JSON-serializable (may contain AG2 objects)
                        try:
                            safe_message = message.copy()
                            safe_message["data"] = self._serialize_ag2_events(message["data"])

                            # Extract agent name from data payload and add to top-level envelope for frontend attribution
                            if isinstance(safe_message.get("data"), dict):
                                agent_from_data = safe_message["data"].get("agent") or safe_message["data"].get("sender")
                                if agent_from_data and isinstance(agent_from_data, str):
                                    safe_message["agent"] = agent_from_data
                                elif "agent" not in safe_message:
                                    # Fallback to generic if no agent in data
                                    safe_message["agent"] = "Agent"

                            if safe_message.get("type") == "chat.tool_call":
                                payload_obj = safe_message.get("data", {}).get("payload", {})
                                payload_keys = list(payload_obj.keys()) if isinstance(payload_obj, dict) else []
                                logger.debug("PROTOCOL_TOOL_CALL_KEYS payload=%s", payload_keys[:12])
                            await websocket.send_json(safe_message)
                            logger.debug("[PROTOCOL] WebSocket send_json completed for envelope type=%s, chat_id=%s", safe_message.get('type'), chat_id)
                        except Exception:
                            # Fallback: attempt to serialize whole message as a last resort
                            try:
                                await websocket.send_json(self._serialize_ag2_events(message))
                            except Exception:
                                raise
                    else:
                        serialized_message = self._serialize_ag2_events(message)
                        await websocket.send_json(serialized_message)
                except Exception as e:
                    if chat_id not in self.connections:
                        logger.debug(
                            "Dropping queued messages for disconnected chat %s after send failure: %s",
                            chat_id,
                            e,
                        )
                        self._message_queues.pop(chat_id, None)
                        break
                    logger.error("Failed to send queued message to %s: %s. Will retry shortly.", chat_id, e, exc_info=True)
                    # Re-queue remaining (including current) for retry
                    remaining = [message] + messages_to_send[messages_to_send.index(message) + 1 :]
                    self._message_queues[chat_id] = remaining + self._message_queues.get(chat_id, [])
                    # Schedule a retry flush with small backoff
                    self._schedule_flush_retry(chat_id)
                    break

    def _schedule_flush_retry(self, chat_id: str, delay: float = 0.5) -> None:
        """Schedule a single retry flush if not already pending."""
        if chat_id in self._scheduled_flush_tasks and not self._scheduled_flush_tasks[chat_id].done():
            return  # already scheduled

        async def _delayed():
            try:
                await asyncio.sleep(delay)
                await self._flush_message_queue(chat_id)
            finally:
                # Clear handle so future retries can be scheduled
                self._scheduled_flush_tasks.pop(chat_id, None)

        self._scheduled_flush_tasks[chat_id] = asyncio.create_task(_delayed())

    # ==================================================================================
    # H2: HEARTBEAT / KEEPALIVE
    # ==================================================================================

    async def _start_heartbeat(self, chat_id: str, websocket: WebSocket) -> None:
        """Start heartbeat task for a connection."""
        if chat_id in self._heartbeat_tasks:
            self._heartbeat_tasks[chat_id].cancel()

        self._heartbeat_tasks[chat_id] = asyncio.create_task(
            self._heartbeat_loop(chat_id, websocket)
        )
        logger.debug("WS_HEARTBEAT_STARTED chat=%s", chat_id)

    async def _heartbeat_loop(self, chat_id: str, websocket: WebSocket) -> None:
        """Heartbeat loop for detecting silent disconnects.

        Sends a JSON ping every `_heartbeat_interval` seconds. Also enforces
        an idle timeout: if the client has not sent any message (including a
        pong) for `_heartbeat_idle_timeout` seconds, the connection is treated
        as dead and cleaned up. Set `_heartbeat_idle_timeout` to 0 to disable.
        """
        try:
            while chat_id in self.connections:
                await asyncio.sleep(self._heartbeat_interval)

                # Check idle timeout — if client has been silent too long,
                # treat the connection as a half-open TCP socket and close it.
                idle_timeout = getattr(self, "_heartbeat_idle_timeout", 0)
                if idle_timeout:
                    conn = self.connections.get(chat_id)
                    if isinstance(conn, dict):
                        last_recv = conn.get("last_received_at", 0)
                        if last_recv and (time.time() - last_recv) > idle_timeout:
                            logger.warning(
                                "WS_IDLE_TIMEOUT chat=%s idle_seconds=%.0f limit=%d — closing",
                                chat_id,
                                time.time() - last_recv,
                                idle_timeout,
                            )
                            await self._cleanup_connection(chat_id)
                            break

                # Send ping
                ping_data = {
                    "type": "ping",
                    "timestamp": datetime.now(UTC).isoformat(),
                }

                try:
                    await websocket.send_json(ping_data)
                    logger.debug("Sent ping to %s", chat_id)
                except Exception as e:
                    logger.warning("Heartbeat failed for %s: %s", chat_id, e)
                    # Connection is dead - clean up
                    await self._cleanup_connection(chat_id)
                    break
        except asyncio.CancelledError:
            logger.debug("Heartbeat cancelled for %s", chat_id)
        except Exception as e:
            logger.error("Heartbeat error for %s: %s", chat_id, e, exc_info=True)

    async def _stop_heartbeat(self, chat_id: str) -> None:
        """Stop heartbeat task for a connection."""
        if chat_id in self._heartbeat_tasks:
            self._heartbeat_tasks[chat_id].cancel()
            del self._heartbeat_tasks[chat_id]
            logger.debug("Stopped heartbeat for %s", chat_id)

    # ==================================================================================
    # RUN REPLAY ON RECONNECT
    # ==================================================================================

    async def _replay_run_on_connect_if_needed(self, chat_id: str, websocket: WebSocket, app_id: str | None) -> None:
        """Replay chat history for IN_PROGRESS chats on WebSocket connection."""
        _ = websocket
        try:
            if not app_id:
                logger.debug("[RUN_REPLAY] No app_id for %s, skipping replay", chat_id)
                return

            # Get workflow name and workflow_startup_mode from connection
            workflow_name = None
            workflow_startup_mode = None
            if chat_id in self.connections:
                workflow_name = self.connections[chat_id].get("workflow_name")
                if workflow_name:
                    try:
                        from mozaiksai.core.workflow.workflow_manager import workflow_manager

                        config = workflow_manager.get_config(workflow_name)
                        workflow_startup_mode = config.get("workflow_startup_mode", "AgentDriven")
                        logger.debug("[RUN_REPLAY] Retrieved workflow_startup_mode=%s for workflow=%s", workflow_startup_mode, workflow_name)
                    except Exception as cfg_err:
                        logger.warning("[RUN_REPLAY] Failed to get workflow config: %s", cfg_err)

            from mozaiksai.core.transport.run_replay import WorkflowRunReplayer

            replayer = WorkflowRunReplayer()

            async def send_event_wrapper(event_dict: dict[str, Any], target_chat_id: str | None) -> None:
                """Wrapper to convert replay events to transport format."""
                _ = target_chat_id
                if not isinstance(event_dict, dict):
                    return

                kind = event_dict.get("kind")
                if kind == "text":
                    # Convert to chat.text format
                    await self._queue_message_with_backpressure(
                        chat_id,
                        {
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
                        },
                    )
                elif kind == "resume_boundary":
                    # Convert boundary to transport format
                    boundary = {k: v for k, v in event_dict.items() if k != "kind"}
                    await self._queue_message_with_backpressure(
                        chat_id,
                        {
                            "type": "chat.resume_boundary",
                            "data": boundary,
                        },
                    )
                elif kind == "awaiting_reply":
                    awaiting = {k: v for k, v in event_dict.items() if k != "kind"}
                    await self._queue_message_with_backpressure(
                        chat_id,
                        {
                            "type": "chat.awaiting_reply",
                            "data": awaiting,
                        },
                    )

            # Call the replayer with workflow_startup_mode filtering.
            await replayer.replay_on_connect_if_needed(
                chat_id=chat_id,
                app_id=app_id,
                send_event=send_event_wrapper,
                workflow_startup_mode=workflow_startup_mode,
            )

            await self._flush_message_queue(chat_id)

        except Exception as e:
            logger.warning("[RUN_REPLAY] Failed to replay chat %s: %s", chat_id, e)

    # ==================================================================================
    # CONNECTION CLEANUP
    # ==================================================================================

    async def _cleanup_connection(self, chat_id: str) -> None:
        """Clean up connection resources."""
        if chat_id in self.connections:
            del self.connections[chat_id]

        if chat_id in self._message_queues:
            del self._message_queues[chat_id]

        overflow_counts = getattr(self, "_pre_connection_buffer_overflow_counts", None)
        if isinstance(overflow_counts, dict):
            overflow_counts.pop(chat_id, None)

        # Clear any pending input request callbacks to prevent memory leaks when
        # the client disconnects mid-workflow while awaiting user input.
        input_registries = getattr(self, "_input_request_registries", None)
        if isinstance(input_registries, dict):
            input_registries.pop(chat_id, None)

        await self._stop_heartbeat(chat_id)
        logger.debug("WS_CONN_CLEANUP chat=%s", chat_id)
