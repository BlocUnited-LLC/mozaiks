# ==============================================================================
# FILE: mozaiksai/core/workflow/stream/handlers/streaming_handler.py
# DESCRIPTION: Handler for StreamEvent (token-by-token streaming)
# ==============================================================================

"""
Streaming Event Handler

Handles StreamEvent for real-time token streaming from AG2's LLM client.
Enables typewriter effect in the frontend.

AG2 emits StreamEvent when the active model client streams token chunks.
This handler forwards those chunks to the frontend via stream_chunk events.
"""

from typing import TYPE_CHECKING, Any, Dict, Optional, Set, Type

from .base import BaseEventHandler

if TYPE_CHECKING:
    from ..context import StreamContext, StreamState

from autogen.events.client_events import StreamEvent


class StreamingEventHandler(BaseEventHandler):
    """
    Handler for StreamEvent (token streaming).

    Processes streaming chunks from AG2's LLM client and emits
    stream_chunk events to the frontend for real-time display.
    """

    def event_types(self) -> Set[Type]:
        """Handle StreamEvent."""
        return {StreamEvent}

    async def handle(
        self,
        event: Any,
        ctx: "StreamContext",
        state: "StreamState",
    ) -> Optional[Dict[str, Any]]:
        """
        Handle StreamEvent by emitting stream_chunk.

        Args:
            event: StreamEvent instance with content field
            ctx: Stream context
            state: Stream state

        Returns:
            stream_chunk payload for UI
        """
        # Extract chunk content
        content = getattr(event, "content", "")
        if not content:
            return None

        # Get current agent name
        agent_name = state.turn_agent or "Agent"

        # Track streaming state
        state.stream_sequence += 1

        if state.stream_id is None:
            state.stream_id = f"{ctx.chat_id}:{agent_name}:{state.sequence_counter}"

        ctx.wf_logger.debug(
            f" [{ctx.workflow_name_upper}] StreamEvent chunk: "
            f"seq={state.stream_sequence} len={len(content)}"
        )

        # Build stream_chunk payload
        return {
            "kind": "stream_chunk",
            "agent": agent_name,
            "content": content,
            "chunk_seq": state.stream_sequence,
            "stream_id": state.stream_id,
        }

    def should_break(self, event: Any, state: "StreamState") -> bool:
        """StreamEvent does not terminate the stream."""
        return False
