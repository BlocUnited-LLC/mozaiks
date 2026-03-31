# ==============================================================================
# FILE: mozaiksai/core/workflow/stream/handlers/streaming_handler.py
# DESCRIPTION: Handler for StreamEvent (token-by-token streaming)
# ==============================================================================

"""
Streaming Event Handler

Handles StreamEvent for real-time token streaming from AG2's LLM client.
Enables typewriter effect in the frontend.

When stream=True is set in llm_config, AG2 emits StreamEvent for each
token/chunk from the LLM response. This handler forwards these chunks
to the frontend via stream_chunk events.
"""

from typing import TYPE_CHECKING, Any, Dict, Optional, Set, Type

from .base import BaseEventHandler

if TYPE_CHECKING:
    from ..context import StreamContext, StreamState

# Import AG2 StreamEvent
try:
    from autogen.events.client_events import StreamEvent
    HAS_STREAM_EVENT = True
except ImportError:
    HAS_STREAM_EVENT = False
    StreamEvent = type(None)  # type: ignore


class StreamingEventHandler(BaseEventHandler):
    """
    Handler for StreamEvent (token streaming).

    Processes streaming chunks from AG2's LLM client and emits
    stream_chunk events to the frontend for real-time display.
    """

    def event_types(self) -> Set[Type]:
        """Handle StreamEvent if available."""
        if HAS_STREAM_EVENT:
            return {StreamEvent}
        return set()

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
        if not hasattr(state, '_stream_sequence'):
            state._stream_sequence = 0
        state._stream_sequence += 1

        if not hasattr(state, '_stream_id'):
            state._stream_id = f"{ctx.chat_id}:{agent_name}:{state.sequence_counter}"

        ctx.wf_logger.debug(
            f" [{ctx.workflow_name_upper}] StreamEvent chunk: "
            f"seq={state._stream_sequence} len={len(content)}"
        )

        # Build stream_chunk payload
        return {
            "kind": "stream_chunk",
            "agent": agent_name,
            "content": content,
            "chunk_seq": state._stream_sequence,
            "stream_id": state._stream_id,
        }

    def should_break(self, event: Any, state: "StreamState") -> bool:
        """StreamEvent does not terminate the stream."""
        return False

    def priority(self) -> int:
        """High priority to process chunks quickly."""
        return 10  # Higher priority than text (50)
