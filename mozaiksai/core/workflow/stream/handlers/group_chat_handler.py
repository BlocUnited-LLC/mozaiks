# ==============================================================================
# FILE: mozaiksai/core/workflow/stream/handlers/group_chat_handler.py
# DESCRIPTION: Handler for GroupChatRunChatEvent and GroupChatResumeEvent
# ==============================================================================

"""
Group Chat Event Handlers

Handles group chat lifecycle events:
- GroupChatRunChatEvent: Group chat initialization
- GroupChatResumeEvent: Resume boundary markers

These events are primarily for logging/debugging and don't typically
require UI emission.
"""

from typing import TYPE_CHECKING, Any, Dict, Optional, Set, Type

from .base import BaseEventHandler

if TYPE_CHECKING:
    from ..context import StreamContext, StreamState

# Import AG2 group chat events
try:
    from autogen.events.agent_events import GroupChatRunChatEvent
    HAS_RUN_CHAT_EVENT = True
except ImportError:
    HAS_RUN_CHAT_EVENT = False
    GroupChatRunChatEvent = type(None)  # type: ignore

try:
    from autogen.events.agent_events import GroupChatResumeEvent
    HAS_RESUME_EVENT = True
except ImportError:
    HAS_RESUME_EVENT = False
    GroupChatResumeEvent = type(None)  # type: ignore


class GroupChatRunHandler(BaseEventHandler):
    """
    Handler for GroupChatRunChatEvent.

    Logs group chat initialization. No UI payload required.
    """

    def event_types(self) -> Set[Type]:
        """Handle GroupChatRunChatEvent if available."""
        if HAS_RUN_CHAT_EVENT:
            return {GroupChatRunChatEvent}
        return set()

    async def handle(
        self,
        event: Any,
        ctx: "StreamContext",
        state: "StreamState",
    ) -> Optional[Dict[str, Any]]:
        """
        Handle GroupChatRunChatEvent.

        Logs the event for debugging. No UI payload emitted.

        Args:
            event: GroupChatRunChatEvent instance
            ctx: Stream context
            state: Stream state

        Returns:
            None (no UI emission)
        """
        ctx.wf_logger.debug(
            f" [{ctx.workflow_name_upper}] GroupChatRunChatEvent received "
            f"chat_id={ctx.chat_id}"
        )
        return None

    def should_break(self, event: Any, state: "StreamState") -> bool:
        return False

    def priority(self) -> int:
        return 100


class GroupChatResumeHandler(BaseEventHandler):
    """
    Handler for GroupChatResumeEvent.

    Marks resume boundaries in the event stream. Optionally emits
    a resume_boundary event to UI for debugging.
    """

    def event_types(self) -> Set[Type]:
        """Handle GroupChatResumeEvent if available."""
        if HAS_RESUME_EVENT:
            return {GroupChatResumeEvent}
        return set()

    async def handle(
        self,
        event: Any,
        ctx: "StreamContext",
        state: "StreamState",
    ) -> Optional[Dict[str, Any]]:
        """
        Handle GroupChatResumeEvent.

        Logs the resume boundary and optionally emits to UI.

        Args:
            event: GroupChatResumeEvent instance
            ctx: Stream context
            state: Stream state

        Returns:
            resume_boundary payload for UI (optional)
        """
        ctx.wf_logger.info(
            f" [{ctx.workflow_name_upper}] GroupChatResumeEvent: "
            f"Resume boundary reached chat_id={ctx.chat_id}"
        )

        # Emit resume_boundary event for UI/debugging
        return {
            "kind": "resume_boundary",
            "chat_id": ctx.chat_id,
            "sequence": state.sequence_counter,
            "metadata": {
                "source": "group_chat_resume",
            },
        }

    def should_break(self, event: Any, state: "StreamState") -> bool:
        return False

    def priority(self) -> int:
        return 100
