# ==============================================================================
# FILE: websocket.py
# DESCRIPTION: 
# ==============================================================================

# === MOZAIKS-CORE-HEADER ===

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from logs.logging_config import get_core_logger


class WebSocketSessionManager:
    """Centralizes WebSocket connection lifecycle concerns for transports."""

    def __init__(
        self,
        *,
        stringify_unknown: Callable[[Any], str],
        logger=None,
        max_queue_size: int = 100,
        heartbeat_interval: int = 120,
        max_pre_connection_buffer: int = 200,
    ) -> None:
        self.logger = logger or get_core_logger("websocket_session_manager")
        self._stringify_unknown = stringify_unknown
        self.connections: Dict[str, Dict[str, Any]] = {}
        self._message_queues: Dict[str, List[Any]] = {}
        self._heartbeat_tasks: Dict[str, asyncio.Task] = {}
        self._scheduled_flush_tasks: Dict[str, asyncio.Task] = {}
        self._pre_connection_buffers: Dict[str, List[Any]] = {}
        self._max_queue_size = max_queue_size
        self._heartbeat_interval = heartbeat_interval
        self._max_pre_connection_buffer = max_pre_connection_buffer

    # ---------------------------------------------------------------------
    # Connection management
    # ---------------------------------------------------------------------
    def has_connection(self, chat_id: str) -> bool:
        return chat_id in self.connections

    def get_connection(self, chat_id: str) -> Optional[Dict[str, Any]]:
        return self.connections.get(chat_id)

    async def register(self, chat_id: str, websocket, metadata: Dict[str, Any]) -> None:
        """Register a newly accepted WebSocket connection."""
        connection_meta = dict(metadata)
        connection_meta.update({
            "websocket": websocket,
            "active": True,
            "connected_at": datetime.now(timezone.utc).isoformat(),
        })
        self.connections[chat_id] = connection_meta
        self._message_queues.setdefault(chat_id, [])
        self.logger.info("WebSocket connected for %s", chat_id)

        await self.start_heartbeat(chat_id, websocket)
        await self.flush_pre_connection_buffer(chat_id)

    async def cleanup(self, chat_id: str) -> None:
        """Clean up resources tied to a WebSocket connection."""
        connection = self.connections.pop(chat_id, None)
        if connection:
            connection.setdefault("websocket", None)
            connection["active"] = False
        self._message_queues.pop(chat_id, None)
        self._pre_connection_buffers.pop(chat_id, None)

        task = self._scheduled_flush_tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()

        await self.stop_heartbeat(chat_id)
        self.logger.info("Cleaned up WebSocket resources for %s", chat_id)

    # ---------------------------------------------------------------------
    # Message queuing + delivery
    # ---------------------------------------------------------------------
    async def broadcast(self, event_data: Dict[str, Any], target_chat_id: Optional[str] = None) -> None:
        """Broadcast an event to a specific chat or all active chats."""
        if target_chat_id:
            if self.has_connection(target_chat_id):
                await self._queue_message(target_chat_id, event_data)
                await self.flush_message_queue(target_chat_id)
            else:
                self._buffer_pre_connection(target_chat_id, event_data)
            return

        for chat_id in list(self.connections.keys()):
            await self._queue_message(chat_id, event_data)
            await self.flush_message_queue(chat_id)

    async def flush_message_queue(self, chat_id: str) -> None:
        """Attempt to flush the queued messages for a chat."""
        queue = self._message_queues.get(chat_id)
        if not queue:
            return

        websocket = self.connections.get(chat_id, {}).get("websocket")
        if websocket is None:
            return

        messages_to_send = list(queue)
        queue.clear()

        for idx, message in enumerate(messages_to_send):
            try:
                if isinstance(message, dict) and "type" in message and "data" in message:
                    await websocket.send_json(message)
                else:
                    await websocket.send_json({
                        "type": "log",
                        "data": {"message": self._stringify_unknown(message)},
                    })
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.error(
                    "Failed to send queued message to %s: %s. Scheduling retry.",
                    chat_id,
                    exc,
                )
                remaining = messages_to_send[idx:]
                self._message_queues[chat_id] = remaining + self._message_queues.get(chat_id, [])
                self._schedule_flush_retry(chat_id)
                break

    async def flush_pre_connection_buffer(self, chat_id: str) -> None:
        buffered = self._pre_connection_buffers.pop(chat_id, None) or []
        if not buffered:
            return
        self.logger.info("Flushing %d buffered messages for %s", len(buffered), chat_id)
        for event in buffered:
            await self._queue_message(chat_id, event)
        await self.flush_message_queue(chat_id)

    async def _queue_message(self, chat_id: str, message_data: Any) -> None:
        if await self._check_backpressure(chat_id):
            # Queue trimmed to relieve pressure; continue appending newest data.
            pass
        if not isinstance(message_data, (dict, list, tuple, str, int, float, bool, type(None))):
            message_data = {
                "type": "log",
                "data": {"message": self._stringify_unknown(message_data)},
            }
        self._message_queues.setdefault(chat_id, []).append(message_data)

    async def _check_backpressure(self, chat_id: str) -> bool:
        queue = self._message_queues.setdefault(chat_id, [])
        queue_size = len(queue)
        if queue_size >= self._max_queue_size:
            self.logger.warning(
                "Backpressure triggered for %s: queue size %d", chat_id, queue_size
            )
            dropped = queue_size - self._max_queue_size + 10
            if dropped > 0:
                del queue[0:dropped]
                self.logger.info("Dropped %d queued messages for %s", dropped, chat_id)
            return True
        return False

    def _buffer_pre_connection(self, chat_id: str, event_data: Dict[str, Any]) -> None:
        buffer_ref = self._pre_connection_buffers.setdefault(chat_id, [])
        buffer_ref.append(event_data)
        if len(buffer_ref) > self._max_pre_connection_buffer:
            overflow = len(buffer_ref) - self._max_pre_connection_buffer
            del buffer_ref[0:overflow]
            self.logger.warning(
                "Dropped %d pre-connection buffered messages for %s", overflow, chat_id
            )
        else:
            self.logger.debug(
                "Buffered pre-connection message for %s (size=%d)", chat_id, len(buffer_ref)
            )

    def _schedule_flush_retry(self, chat_id: str, delay: float = 0.5) -> None:
        existing = self._scheduled_flush_tasks.get(chat_id)
        if existing and not existing.done():
            return

        async def _delayed_flush() -> None:
            try:
                await asyncio.sleep(delay)
                await self.flush_message_queue(chat_id)
            finally:
                self._scheduled_flush_tasks.pop(chat_id, None)

        self._scheduled_flush_tasks[chat_id] = asyncio.create_task(_delayed_flush())

    # ---------------------------------------------------------------------
    # Heartbeat management
    # ---------------------------------------------------------------------
    async def start_heartbeat(self, chat_id: str, websocket) -> None:
        task = self._heartbeat_tasks.get(chat_id)
        if task and not task.done():
            task.cancel()
        self._heartbeat_tasks[chat_id] = asyncio.create_task(
            self._heartbeat_loop(chat_id, websocket)
        )
        self.logger.info("Started WebSocket heartbeat for %s", chat_id)

    async def stop_heartbeat(self, chat_id: str) -> None:
        task = self._heartbeat_tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except Exception:
                pass
        self.logger.debug("Stopped WebSocket heartbeat for %s", chat_id)

    async def _heartbeat_loop(self, chat_id: str, websocket) -> None:
        try:
            while chat_id in self.connections:
                await asyncio.sleep(self._heartbeat_interval)
                ping_data = {
                    "type": "ping",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                try:
                    await websocket.send_json(ping_data)
                    self.logger.debug("Sent heartbeat ping to %s", chat_id)
                except Exception as exc:
                    self.logger.warning("Heartbeat failed for %s: %s", chat_id, exc)
                    await self.cleanup(chat_id)
                    break
        except asyncio.CancelledError:
            self.logger.debug("Heartbeat loop cancelled for %s", chat_id)
        except Exception as exc:
            self.logger.error("Heartbeat loop error for %s: %s", chat_id, exc)


__all__ = ["WebSocketSessionManager"]

