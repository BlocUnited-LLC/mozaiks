"""MozaiksIOStream — bridges AG2 IOStream events to SimpleTransport.

This module provides a custom IOStream implementation that AG2 uses during
workflow execution.  Every event AG2 emits (``StreamEvent``, ``TextEvent``,
``InputRequestEvent``, etc.) flows through ``send()`` where we can:

1. Forward **StreamEvent** chunks as ``chat.stream_chunk`` to the frontend
   via SimpleTransport for real-time token-level display.
2. Forward **InputRequestEvent** to the transport's input-request protocol.
3. Let all other events pass through to the standard ``response.events``
   async iterator (no interference with the existing orchestration loop).

The bridge is intentionally lightweight — it does NOT replace the existing
event processing loop in ``orchestration.py``.  Instead it adds a *parallel
fast-path* for streaming tokens that would otherwise only appear after the
full message is assembled into a ``TextEvent``.

Thread / async safety
---------------------
AG2's ``IOStream.send()`` is called from the agent execution thread (which
may be a background thread via ``ThreadIOStream``).  Since SimpleTransport
is async, we use ``asyncio.run_coroutine_threadsafe`` to bridge the gap.

Usage
-----
::

    from mozaiksai.adapters.ag2.iostream_bridge import create_iostream_bridge
    from autogen.io import IOStream

    bridge = create_iostream_bridge(
        chat_id=chat_id,
        transport=transport,
        loop=asyncio.get_event_loop(),
    )
    # Set as context-local default so AG2 agents use it
    with IOStream.set_default(bridge):
        response = await a_run_group_chat(pattern=pattern, ...)
        async for ev in response.events:
            ...  # existing event handling unchanged
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Optional

from autogen.events.print_event import PrintEvent

logger = logging.getLogger(__name__)


class MozaiksIOStream:
    """Custom IOStream that bridges AG2 events to the Mozaiks transport layer.

    Implements the three methods required by AG2's ``IOStreamProtocol``:
    ``print()``, ``send()``, and ``input()``.

    ``send()`` is the single choke point — all AG2 structured events flow
    here.  We inspect the event type and, for streaming-relevant events,
    fire-and-forget an async task that forwards the chunk to SimpleTransport.

    Parameters
    ----------
    chat_id : str
        The current chat session identifier.
    transport : Any
        The ``SimpleTransport`` instance (or anything with ``send_event_to_ui``).
    loop : asyncio.AbstractEventLoop | None
        The running event loop.  Required when ``send()`` is called from a
        background thread (the normal AG2 pattern).  If *None*, the bridge
        will attempt ``asyncio.get_event_loop()`` at send time.
    agent_filter_cb : callable | None
        Optional ``(agent_name: str) -> bool`` callback.  If provided, only
        events from agents where the callback returns True will be forwarded
        as stream chunks.  ``None`` means forward all.
    """

    def __init__(
        self,
        *,
        chat_id: str,
        transport: Any,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        agent_filter_cb: Any = None,
    ) -> None:
        self._chat_id = chat_id
        self._transport = transport
        self._loop = loop
        self._agent_filter_cb = agent_filter_cb
        # Track the active streaming agent so the frontend can attribute chunks
        self._active_agent: Optional[str] = None
        # Monotonic counter for ordering chunks within a single turn
        self._chunk_seq: int = 0
        # Running accumulation buffer per agent turn (for stream_end summary)
        self._accumulator: str = ""
        logger.info(
            "[IOSTREAM_BRIDGE] Initialized for chat=%s transport=%s",
            chat_id,
            type(transport).__name__,
        )

    # ------------------------------------------------------------------
    # IOStreamProtocol: send (primary hook)
    # ------------------------------------------------------------------

    def send(self, message: Any) -> None:
        """Receive an AG2 BaseEvent and optionally forward to transport.

        This method is called synchronously from AG2's execution thread.
        """
        try:
            cls_name = type(message).__name__
            # Lazily import event classes to avoid import-chain issues
            is_stream = cls_name == "StreamEvent"
            is_input = cls_name == "InputRequestEvent"

            if is_stream:
                self._handle_stream_event(message)
            elif is_input:
                self._handle_input_request_event(message)
            elif cls_name == "GroupChatRunChatEvent":
                # Speaker change — update active agent for chunk attribution
                speaker = getattr(message, "speaker", None)
                if speaker:
                    agent_name = getattr(speaker, "name", None) or str(speaker)
                    self._flush_stream(reason="speaker_change")
                    self._active_agent = agent_name
                    self._chunk_seq = 0
                    self._accumulator = ""
            elif cls_name in ("RunCompletionEvent", "TerminationEvent"):
                self._flush_stream(reason="run_complete")

            # All events are also available via response.events; we do NOT
            # consume them here (no queue draining).
        except Exception:
            logger.debug("[IOSTREAM_BRIDGE] send() error", exc_info=True)

    # ------------------------------------------------------------------
    # IOStreamProtocol: print (wraps as send)
    # ------------------------------------------------------------------

    def print(self, *objects: Any, sep: str = " ", end: str = "\n", flush: bool = False) -> None:
        """AG2 may call print() for legacy output.  Convert to send()."""
        event = PrintEvent(*objects, sep=sep, end=end)
        self.send(event)

    # ------------------------------------------------------------------
    # IOStreamProtocol: input (async bridge)
    # ------------------------------------------------------------------

    def input(self, prompt: str = "", *, password: bool = False) -> str:
        """Blocking input — delegates to transport's input-request mechanism.

        In the Mozaiks architecture, human input flows through the WebSocket
        input-request protocol (``chat.input_request`` → frontend → REST
        submit → ``transport.submit_user_input``).

        Since this method must return synchronously but the transport is
        async, we use ``run_coroutine_threadsafe`` and block on the future.

        *Note*: The standard AG2 ``ThreadIOStream`` handles input via its own
        queue; this is only used when ``MozaiksIOStream`` is the context
        default and AG2 calls ``iostream.input()`` directly (rare in group
        chat patterns).
        """
        # For now, return empty to let the standard orchestration input
        # protocol handle it (InputRequestEvent → pending_input_requests).
        # This avoids blocking the agent thread.
        logger.debug("[IOSTREAM_BRIDGE] input() called — returning empty to trigger termination")
        return ""

    # ------------------------------------------------------------------
    # Internal: StreamEvent handling
    # ------------------------------------------------------------------

    def _handle_stream_event(self, event: Any) -> None:
        """Forward a StreamEvent chunk to the transport layer."""
        content = getattr(event, "content", "")
        if not content:
            return

        agent = self._active_agent or "Agent"

        # Optional agent filter
        if self._agent_filter_cb and not self._agent_filter_cb(agent):
            return

        self._chunk_seq += 1
        self._accumulator += content

        payload = {
            "kind": "stream_chunk",
            "agent": agent,
            "content": content,
            "chunk_seq": self._chunk_seq,
            "stream_id": f"{self._chat_id}:{agent}:{id(self)}",
        }

        self._fire_and_forget(self._transport.send_event_to_ui(payload, self._chat_id))

    def _handle_input_request_event(self, event: Any) -> None:
        """Forward an InputRequestEvent to the transport.

        The orchestration loop already handles this via ``response.events``,
        so this is a supplementary fast-path for immediate frontend delivery.
        We emit a lightweight notification; the full input-request protocol
        still runs through the orchestration loop.
        """
        prompt = getattr(event, "prompt", "")
        request_id = getattr(event, "uuid", None)

        # Register the respond callback so submit_user_input can find it
        respond_cb = getattr(event, "respond", None)
        if respond_cb and hasattr(self._transport, "register_input_request"):
            rid = str(request_id) if request_id else uuid.uuid4().hex
            self._transport.register_input_request(self._chat_id, rid, respond_cb)
            logger.debug("[IOSTREAM_BRIDGE] Registered input request %s", rid)

    def _flush_stream(self, *, reason: str = "end") -> None:
        """Emit a stream_end event to signal the frontend that a streaming
        turn has completed, then reset the accumulator."""
        if not self._accumulator:
            return

        payload = {
            "kind": "stream_end",
            "agent": self._active_agent or "Agent",
            "full_content": self._accumulator,
            "chunk_count": self._chunk_seq,
            "reason": reason,
            "stream_id": f"{self._chat_id}:{self._active_agent}:{id(self)}",
        }

        self._fire_and_forget(self._transport.send_event_to_ui(payload, self._chat_id))

        self._accumulator = ""
        self._chunk_seq = 0

    # ------------------------------------------------------------------
    # Async bridge helper
    # ------------------------------------------------------------------

    def _fire_and_forget(self, coro: Any) -> None:
        """Schedule an async coroutine from a sync context (thread-safe)."""
        loop = self._loop
        if loop is None:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                logger.debug("[IOSTREAM_BRIDGE] No event loop available for fire-and-forget")
                return

        if loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, loop)
        else:
            # If the loop isn't running, we can't schedule — drop silently
            logger.debug("[IOSTREAM_BRIDGE] Event loop not running, dropping event")


def create_iostream_bridge(
    *,
    chat_id: str,
    transport: Any,
    loop: Optional[asyncio.AbstractEventLoop] = None,
    agent_filter_cb: Any = None,
) -> MozaiksIOStream:
    """Factory: create a MozaiksIOStream bridge for a single workflow run.

    Parameters
    ----------
    chat_id : str
        The active chat session ID.
    transport : Any
        The SimpleTransport singleton.
    loop : asyncio.AbstractEventLoop | None
        The running event loop (auto-detected if None).
    agent_filter_cb : callable | None
        Optional filter; see ``MozaiksIOStream``.

    Returns
    -------
    MozaiksIOStream
        Ready to use with ``IOStream.set_default(bridge)``.
    """
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

    return MozaiksIOStream(
        chat_id=chat_id,
        transport=transport,
        loop=loop,
        agent_filter_cb=agent_filter_cb,
    )
