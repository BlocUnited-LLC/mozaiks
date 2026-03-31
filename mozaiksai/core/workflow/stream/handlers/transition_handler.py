# ==============================================================================
# FILE: mozaiksai/core/workflow/stream/handlers/transition_handler.py
# DESCRIPTION: Handler for AfterWorksTransitionEvent (handoff_to_user)
# ==============================================================================

"""
Transition Event Handler

Handles AG2 group transition events, particularly detecting when the workflow
hands off to the user via RevertToUserTarget.

When handoff_to_user is detected:
1. Emits input_request event to UI
2. Sets stream state to completed
3. Signals event loop to break
"""

from typing import TYPE_CHECKING, Any, Dict, Optional, Set, Type

from .base import BaseEventHandler

if TYPE_CHECKING:
    from ..context import StreamContext, StreamState

# Import AG2 transition types conditionally.
try:
    from autogen.agentchat.group import RevertToUserTarget
    from autogen.agentchat.group.events import AfterWorksTransitionEvent
    HAS_HANDOFF_EVENTS = True
except ImportError:
    HAS_HANDOFF_EVENTS = False
    AfterWorksTransitionEvent = type(None)  # type: ignore
    RevertToUserTarget = type(None)  # type: ignore


class TransitionHandler(BaseEventHandler):
    """
    Handler for AfterWorksTransitionEvent.

    Detects handoff_to_user transitions (RevertToUserTarget) and emits
    input_request events to the UI. Terminates the event stream when
    handoff is detected.

    This handler is critical for group-chat patterns where agents can hand off
    control back to the user mid-conversation.
    """

    def event_types(self) -> Set[Type]:
        """Return AfterWorksTransitionEvent if available."""
        if HAS_HANDOFF_EVENTS:
            return {AfterWorksTransitionEvent}
        return set()

    async def handle(
        self,
        event: Any,
        ctx: "StreamContext",
        state: "StreamState",
    ) -> Optional[Dict[str, Any]]:
        """
        Handle AfterWorksTransitionEvent, detecting handoff_to_user.

        When RevertToUserTarget is detected:
        1. Extract source agent name
        2. Log the handoff
        3. Mark stream state as completed
        4. Return input_request payload for UI

        Args:
            event: AfterWorksTransitionEvent instance
            ctx: Stream context
            state: Stream state (will be modified)

        Returns:
            input_request payload if handoff_to_user, None otherwise
        """
        if not HAS_HANDOFF_EVENTS:
            return None

        # Check if this is a handoff to user
        target = getattr(event, "target", None)
        if not isinstance(target, RevertToUserTarget):
            # Not a user handoff - could be agent-to-agent transition
            ctx.wf_logger.debug(
                f" [{ctx.workflow_name_upper}] Transition event to non-user target: "
                f"{type(target).__name__ if target else 'None'}"
            )
            return None

        # Extract source agent information
        source_agent = getattr(event, "source", None)
        if source_agent:
            source_name = getattr(source_agent, "name", None) or str(source_agent)
        else:
            source_name = "Agent"

        ctx.wf_logger.info(
            f" [{ctx.workflow_name_upper}] Handoff to user detected from {source_name}. "
            f"Emitting input_request and ending stream."
        )

        # Mark stream state for termination
        state.run_completed = True
        state.handoff_to_user = True

        # Build input_request payload for UI
        return {
            "kind": "input_request",
            "agent": source_name,
            "prompt": "",
            "chat_id": ctx.chat_id,
            "metadata": {
                "source": "handoff_to_user",
                "transition_target": "RevertToUserTarget",
            },
        }

    def should_break(self, event: Any, state: "StreamState") -> bool:
        """
        Break out of event loop on handoff_to_user.

        The stream should terminate when control is handed to the user,
        as the AG2 task will be waiting for input that won't come through
        the event stream.

        Args:
            event: The transition event
            state: Current stream state

        Returns:
            True if handoff_to_user was detected
        """
        return state.handoff_to_user

    def priority(self) -> int:
        """High priority to ensure handoff detection happens first."""
        return 10
