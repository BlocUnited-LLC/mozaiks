# ==============================================================================
# FILE: mozaiksai/core/workflow/stream/handlers/transition_handler.py
# DESCRIPTION: Handler for AG2 transition events
# ==============================================================================

"""
Transition Event Handler

Handles AG2 group transition events, particularly when the workflow resolves
an after-work transition to RevertToUserTarget.

These events are informational. The live pause/resume path is AG2's native
InputRequestEvent, which carries the respond callback used by the runtime to
continue execution. Mozaiks UI tools remain a separate custom wait path.
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

    Detects native AG2 transitions to RevertToUserTarget and records them in
    logs only. The actual user-input handoff remains driven by InputRequestEvent.
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

        A user-target transition is native AG2 metadata. It must not synthesize
        an extra input request or terminate the event loop because AG2's
        InputRequestEvent is the canonical live input channel.

        Args:
            event: AfterWorksTransitionEvent instance
            ctx: Stream context
            state: Stream state

        Returns:
            None. Transition metadata is not forwarded to the UI as a second
            pause mechanism.
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
            f"{source_name}. Waiting for InputRequestEvent to drive live resume."
        )
        return None

    def should_break(self, event: Any, state: "StreamState") -> bool:
        """
        Transition metadata must not terminate the event loop.
        """
        return False
