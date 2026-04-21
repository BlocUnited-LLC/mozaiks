# ==============================================================================
# FILE: mozaiksai/core/workflow/stream/handlers/input_handler.py
# DESCRIPTION: Handler for InputRequestEvent (user input prompts)
# ==============================================================================

"""
Input Request Event Handler

Handles InputRequestEvent when the workflow needs user input.
Extracts the respond callback and registers it for later invocation.

The transport layer will use the registered callback to inject user
responses back into the AG2 conversation.
"""

import re
import uuid
from typing import TYPE_CHECKING, Any, Dict, Optional, Set, Type

from .base import BaseEventHandler

if TYPE_CHECKING:
    from ..context import StreamContext, StreamState

from autogen.events.agent_events import InputRequestEvent


_GENERIC_GROUP_FEEDBACK_PROMPT_RE = re.compile(
    r"^Please give feedback to [A-Za-z0-9_-]+\. Press enter to skip and use auto-reply, or type 'exit' to stop the conversation:\s*$",
    re.IGNORECASE,
)


def _normalize_prompt_hint(prompt_hint: Any) -> tuple[str, str, bool]:
    prompt_text = str(prompt_hint or "").strip()
    if _GENERIC_GROUP_FEEDBACK_PROMPT_RE.match(prompt_text):
        return "", "ag2_group_feedback_compat", True
    return prompt_text, "input_request_event", False


class InputRequestHandler(BaseEventHandler):
    """
    Handler for InputRequestEvent.

    When user input is requested:
    1. Extract request UUID/ID
    2. Extract respond callback from event
    3. Register callback with transport for later invocation
    4. Store in pending_input_requests for tracking
    5. Return input_request payload for UI

    The respond callback allows user input to be injected into the
    AG2 conversation via InputRequestEvent.content.respond().
    """

    def event_types(self) -> Set[Type]:
        """Handle InputRequestEvent."""
        return {InputRequestEvent}

    async def handle(
        self,
        event: Any,
        ctx: "StreamContext",
        state: "StreamState",
    ) -> Optional[Dict[str, Any]]:
        """
        Handle InputRequestEvent.

        Extracts the respond callback and registers it for user input injection.

        Args:
            event: InputRequestEvent instance
            ctx: Stream context
            state: Stream state (pending_input_requests will be modified)

        Returns:
            input_request payload for UI
        """
        # Extract request content object (may contain respond callback)
        request_obj = getattr(event, "content", None)

        # Extract request UUID/ID from event or content
        request_uuid = (
            getattr(event, "uuid", None)
            or getattr(event, "id", None)
        )
        if request_uuid is None and request_obj is not None:
            request_uuid = (
                getattr(request_obj, "uuid", None)
                or getattr(request_obj, "id", None)
            )
        if request_uuid is None:
            request_uuid = uuid.uuid4()

        request_id = str(request_uuid)

        # Store request ID on event for downstream reference
        setattr(event, "_mozaiks_request_id", request_id)

        # Extract respond callback
        respond_cb = getattr(event, "respond", None)
        if not callable(respond_cb) and request_obj is not None:
            respond_cb = getattr(request_obj, "respond", None)

        if callable(respond_cb):
            # Store in state for tracking
            state.pending_input_requests[request_id] = respond_cb

            # Register with transport for user input handling
            try:
                if ctx.transport:
                    registered_id = ctx.transport.register_input_request(
                        ctx.chat_id, request_id, respond_cb
                    )
                    # Transport may return a different ID
                    if registered_id and registered_id != request_id:
                        state.pending_input_requests.pop(request_id, None)
                        state.pending_input_requests[registered_id] = respond_cb
                        setattr(event, "_mozaiks_request_id", registered_id)
                        request_id = registered_id
            except Exception as e:
                ctx.wf_logger.debug(
                    f"Failed to register input request {request_id}: {e}"
                )
        else:
            ctx.wf_logger.debug(
                f"No respond callback available for input request {request_id}"
            )

        # Extract prompt hint if available
        prompt_hint = getattr(event, "prompt", None)
        if prompt_hint is None and request_obj is not None:
            prompt_hint = (
                getattr(request_obj, "prompt", None)
                or getattr(request_obj, "message", None)
            )
        metadata_source = "input_request_event"
        suppressed_generic_prompt = False
        if prompt_hint is not None:
            normalized_prompt, metadata_source, suppressed_generic_prompt = _normalize_prompt_hint(prompt_hint)
            prompt_hint = normalized_prompt
            setattr(event, "_mozaiks_prompt", prompt_hint)
            if suppressed_generic_prompt:
                ctx.wf_logger.info(
                    f" [{ctx.workflow_name_upper}] Suppressing generic AG2 group feedback prompt "
                    f"for input request {request_id}"
                )

        # Persist pending input request for resume support
        try:
            await ctx.persistence_manager.save_pending_input_request(
                chat_id=ctx.chat_id,
                app_id=ctx.app_id,
                request_id=request_id,
                agent=state.turn_agent or "Agent",
                prompt=prompt_hint or "",
            )
        except Exception as e:
            ctx.wf_logger.debug(f"Failed to persist pending input request: {e}")

        # Build input_request payload for UI
        return {
            "kind": "input_request",
            "request_id": request_id,
            "agent": state.turn_agent or "Agent",
            "prompt": prompt_hint or "",
            "chat_id": ctx.chat_id,
            "metadata": {
                "source": metadata_source,
                "has_respond_callback": callable(respond_cb),
                "generic_feedback_prompt_suppressed": suppressed_generic_prompt,
            },
        }

    def should_break(self, event: Any, state: "StreamState") -> bool:
        """InputRequestEvent does not terminate the stream."""
        return False
