# ==============================================================================
# FILE: mozaiksai/core/workflow/stream/websocket_iostream.py
# DESCRIPTION: Custom IOStream that forwards AG2 output to WebSocket transport
# ==============================================================================

"""
WebSocket IOStream Bridge

Custom IOStream implementation that intercepts AG2 print output and forwards
chunks to the WebSocket transport in real-time, enabling typewriter effect.

Usage:
    from mozaiksai.core.workflow.stream.websocket_iostream import WebSocketIOStream

    # Create and set as default before running workflow
    ws_stream = WebSocketIOStream(transport, chat_id, agent_name="Agent")
    IOStream.set_global_default(ws_stream)

    # AG2 output will now stream to WebSocket
"""

import asyncio
import logging
from typing import Any, Optional, TYPE_CHECKING

from autogen.io.base import IOStream

if TYPE_CHECKING:
    from mozaiksai.core.transport.simple_transport import SimpleTransport

logger = logging.getLogger(__name__)


class WebSocketIOStream(IOStream):
    """
    IOStream implementation that forwards output to WebSocket transport.

    When AG2 agents print output (via iostream.print()), this class:
    1. Accumulates the text
    2. Sends chunks to the WebSocket transport as stream_chunk events
    3. Enables real-time typewriter effect in the frontend
    """

    def __init__(
        self,
        transport: "SimpleTransport",
        chat_id: str,
        *,
        agent_name: str = "Agent",
        chunk_size: int = 0,  # 0 = send immediately, >0 = batch
    ):
        """
        Initialize WebSocket IOStream.

        Args:
            transport: SimpleTransport instance for sending events
            chat_id: Chat session ID
            agent_name: Default agent name for chunks (can be updated)
            chunk_size: Characters to batch before sending (0 = immediate)
        """
        self._transport = transport
        self._chat_id = chat_id
        self._agent_name = agent_name
        self._chunk_size = chunk_size
        self._buffer = ""
        self._sequence = 0
        self._stream_id: Optional[str] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Try to get event loop
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

    def set_agent(self, agent_name: str) -> None:
        """Update the current agent name for chunks."""
        self._agent_name = agent_name
        # Reset stream ID when agent changes
        self._stream_id = f"{self._chat_id}:{agent_name}:{self._sequence}"

    def print(
        self,
        *objects: Any,
        sep: str = " ",
        end: str = "\n",
        flush: bool = False,
    ) -> None:
        """
        Print data to both console and WebSocket.

        Intercepts AG2 print output and forwards to transport.
        """
        # Convert objects to string
        text = sep.join(str(obj) for obj in objects) + end

        # Also print to console for debugging
        print(text, end="", flush=flush)

        # Skip empty chunks
        if not text.strip() and not flush:
            return

        # Send to WebSocket
        self._send_chunk(text, flush=flush)

    def _send_chunk(self, content: str, *, flush: bool = False) -> None:
        """Send chunk to WebSocket transport."""
        if self._chunk_size > 0:
            # Batching mode
            self._buffer += content
            if len(self._buffer) >= self._chunk_size or flush:
                self._emit_chunk(self._buffer)
                self._buffer = ""
        else:
            # Immediate mode
            self._emit_chunk(content)

    def _emit_chunk(self, content: str) -> None:
        """Emit stream_chunk event via transport."""
        if not content or not self._transport:
            return

        self._sequence += 1
        if not self._stream_id:
            self._stream_id = f"{self._chat_id}:{self._agent_name}:{self._sequence}"

        chunk_event = {
            "kind": "stream_chunk",
            "agent": self._agent_name,
            "content": content,
            "chunk_seq": self._sequence,
            "stream_id": self._stream_id,
        }

        try:
            if self._loop and self._loop.is_running():
                # Schedule async send
                asyncio.run_coroutine_threadsafe(
                    self._transport.send_event_to_ui(chunk_event, self._chat_id),
                    self._loop,
                )
            else:
                # Try to create task (may fail if no loop)
                try:
                    asyncio.create_task(
                        self._transport.send_event_to_ui(chunk_event, self._chat_id)
                    )
                except RuntimeError:
                    logger.debug("[WS_IOSTREAM] No event loop for chunk emission")
        except Exception as e:
            logger.debug(f"[WS_IOSTREAM] Failed to emit chunk: {e}")

    def input(self, prompt: str = "", *, password: bool = False) -> str:
        """
        Request input from user.

        This is handled via the normal input_request event flow,
        not through IOStream. Returns empty string.
        """
        # Input is handled through AG2's InputRequestEvent
        # which goes through our normal event handlers
        logger.debug(f"[WS_IOSTREAM] Input requested (prompt: {prompt[:50]}...)")
        return ""

    def send(self, message: Any) -> None:
        """
        Send a message/event through the IOStream.

        AG2 uses this for PrintEvent and other events.
        """
        # Handle PrintEvent
        try:
            from autogen.events.print_event import PrintEvent
            if isinstance(message, PrintEvent):
                # Extract text from PrintEvent
                text = " ".join(str(obj) for obj in message.objects)
                if message.end:
                    text += message.end
                self._send_chunk(text, flush=True)
                return
        except ImportError:
            pass

        # Handle other message types - just log
        logger.debug(f"[WS_IOSTREAM] Received message: {type(message).__name__}")

    def flush_buffer(self) -> None:
        """Flush any remaining buffered content."""
        if self._buffer:
            self._emit_chunk(self._buffer)
            self._buffer = ""

    def end_stream(self) -> None:
        """
        Signal end of current stream.

        Sends stream_end event to frontend to finalize the message.
        """
        self.flush_buffer()

        if not self._transport:
            return

        end_event = {
            "kind": "stream_end",
            "agent": self._agent_name,
            "stream_id": self._stream_id,
        }

        try:
            if self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self._transport.send_event_to_ui(end_event, self._chat_id),
                    self._loop,
                )
            else:
                try:
                    asyncio.create_task(
                        self._transport.send_event_to_ui(end_event, self._chat_id)
                    )
                except RuntimeError:
                    pass
        except Exception as e:
            logger.debug(f"[WS_IOSTREAM] Failed to emit stream_end: {e}")

        # Reset for next stream
        self._stream_id = None
        self._sequence = 0
