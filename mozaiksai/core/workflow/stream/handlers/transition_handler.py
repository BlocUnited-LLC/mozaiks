# ==============================================================================
# FILE: mozaiksai/core/workflow/stream/handlers/transition_handler.py
# DESCRIPTION: Handler for AG2 transition events
# ==============================================================================

"""
Transition Event Handler

Handles AG2 group transition events, particularly when the workflow resolves
an after-work transition to RevertToUserTarget.

These events matter when AG2 hands control back to the user without emitting a
separate InputRequestEvent. Mozaiks must preserve that pause as real runtime
state so the composer, resume flow, and smoke harness can continue the
workflow cleanly.
"""

from typing import TYPE_CHECKING, Any, Dict, Optional, Set, Type

from autogen.agentchat.group.events.transition_events import AfterWorksTransitionEvent
from autogen.agentchat.group.targets.transition_target import RevertToUserTarget

from .base import BaseEventHandler

if TYPE_CHECKING:
    from ..context import StreamContext, StreamState


class TransitionHandler(BaseEventHandler):
    """
    Handler for AfterWorksTransitionEvent.

    Detects native AG2 transitions to RevertToUserTarget and marks the run as
    awaiting another human turn. This is the canonical fallback when AG2 pauses
    for user feedback without emitting a dedicated InputRequestEvent.
    """

    def event_types(self) -> Set[Type]:
        """Return the AG2 transition event type handled by this class."""
        return {AfterWorksTransitionEvent}

    async def handle(
        self,
        event: Any,
        ctx: "StreamContext",
        state: "StreamState",
    ) -> Optional[Dict[str, Any]]:
        """
        Handle AfterWorksTransitionEvent.

        A user-target transition means the workflow is paused for another human
        turn. Preserve that state explicitly and emit a lightweight composer
        signal so the shell or smoke harness can continue the workflow without
        inventing a second bespoke pause mechanism.

        Args:
            event: AfterWorksTransitionEvent instance
            ctx: Stream context
            state: Stream state

        Returns:
            Awaiting-reply payload for the shell/harness, or None for non-user
            transitions.
        """
        target = getattr(event, "transition_target", None)
        if not isinstance(target, RevertToUserTarget):
            ctx.wf_logger.debug(
                f" [{ctx.workflow_name_upper}] Transition event to non-user target: "
                f"{type(target).__name__ if target else 'None'}"
            )
            return None

        source_agent = getattr(event, "source_agent", None)
        if source_agent:
            source_name = getattr(source_agent, "name", None) or str(source_agent)
        else:
            source_name = "Agent"

        ctx.wf_logger.info(
            f" [{ctx.workflow_name_upper}] Observed native AG2 handoff to user from "
            f"{source_name}. Marking run as awaiting another human turn."
        )
        state.awaiting_user_input = True
        return {
            "kind": "awaiting_reply",
            "agent": source_name,
            "chat_id": ctx.chat_id,
            "workflow_name": ctx.workflow_name,
            "display": "composer",
            "interaction_type": "input_request",
            "reason": "awaiting_user_reply",
            "source_agent": source_name,
        }

    def should_break(self, event: Any, state: "StreamState") -> bool:
        """
        Transition metadata must not terminate the event loop.
        """
        return False
