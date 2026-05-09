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
from mozaiksai.core.events.event_serialization import serialize_event_content
from mozaiksai.core.workflow.runtime_signals import SYSTEM_RESUME_SIGNAL

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


def _extract_component_hint(request_obj: Any) -> Optional[str]:
    if request_obj is None:
        return None
    try:
        if hasattr(request_obj, "tool_name"):
            value = getattr(request_obj, "tool_name", None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        if isinstance(request_obj, dict):
            for key in ("tool_name", "component", "component_type"):
                value = request_obj.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    except Exception:
        return None
    return None


def _extract_request_payload(request_obj: Any) -> Dict[str, Any]:
    raw_payload: Any = None
    try:
        if request_obj is not None:
            if hasattr(request_obj, "model_dump"):
                raw_payload = request_obj.model_dump()  # type: ignore[attr-defined]
            elif isinstance(request_obj, dict):
                raw_payload = dict(request_obj)
    except Exception:
        raw_payload = None

    if not isinstance(raw_payload, dict):
        return {}

    raw_payload.pop("respond", None)
    try:
        serialized = serialize_event_content(raw_payload)
        return serialized if isinstance(serialized, dict) else {}
    except Exception:
        return {}


async def _emit_awaiting_reply_event(
    *,
    ctx: "StreamContext",
    state: "StreamState",
    prompt: str,
) -> None:
    if not ctx.transport:
        return
    try:
        await ctx.transport.send_event_to_ui(
            {
                "kind": "awaiting_reply",
                "agent": state.turn_agent or "Agent",
                "chat_id": ctx.chat_id,
                "workflow_name": ctx.workflow_name,
                "display": "composer",
                "interaction_type": "input_request",
                "reason": "awaiting_user_reply",
                "prompt": prompt,
                "source_agent": state.turn_agent or "Agent",
                "metadata": {
                    "source": "ag2_group_feedback_compat",
                },
            },
            ctx.chat_id,
        )
    except Exception as emit_err:
        ctx.wf_logger.debug(
            f"Failed to emit awaiting_reply event for {ctx.chat_id}: {emit_err}"
        )


async def _invoke_respond_callback(respond_cb: Any, response_value: str) -> bool:
    if not callable(respond_cb):
        return False
    result = respond_cb(response_value)
    if hasattr(result, "__await__"):
        await result
    return True


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

        async def _clear_auto_resumed_pending_input() -> None:
            state.pending_input_requests.pop(request_id, None)
            try:
                await ctx.persistence_manager.clear_pending_input_request(
                    chat_id=ctx.chat_id,
                    app_id=ctx.app_id,
                )
            except Exception as e:
                ctx.wf_logger.debug(
                    f"Failed clearing auto-resumed pending input request {request_id}: {e}"
                )

        if suppressed_generic_prompt and callable(respond_cb):
            latest_role = str(getattr(state, "last_text_role", None) or "").strip().lower()
            latest_content = str(getattr(state, "last_text_content", None) or "").strip()
            if not latest_role and not latest_content:
                latest_message: Dict[str, Any] = {}
                try:
                    latest = await ctx.persistence_manager.get_latest_message(
                        chat_id=ctx.chat_id,
                        app_id=ctx.app_id,
                    )
                    latest_message = latest if isinstance(latest, dict) else {}
                except Exception as e:
                    ctx.wf_logger.debug(
                        f"Failed reading latest persisted message for input request {request_id}: {e}"
                    )
                latest_role = str(latest_message.get("role") or "").strip().lower()
                latest_content = str(latest_message.get("content") or "").strip()

            recent_submit = False
            if ctx.transport and hasattr(ctx.transport, "consume_recent_input_submit"):
                try:
                    recent_submit = bool(ctx.transport.consume_recent_input_submit(ctx.chat_id))
                except Exception as e:
                    ctx.wf_logger.debug(
                        f"Failed checking recent input submit marker for {request_id}: {e}"
                    )
            if recent_submit and (latest_role != "assistant" or not latest_content):
                try:
                    await _invoke_respond_callback(respond_cb, SYSTEM_RESUME_SIGNAL)
                    await _clear_auto_resumed_pending_input()
                    ctx.wf_logger.info(
                        f" [{ctx.workflow_name_upper}] Auto-resumed suppressed AG2 feedback prompt "
                        f"after recent user input for request {request_id}"
                    )
                    state.awaiting_user_input = False
                    return None
                except Exception as e:
                    ctx.wf_logger.debug(
                        f"Failed auto-resuming suppressed AG2 feedback prompt for {request_id}: {e}"
                    )

            if latest_role != "assistant" or not latest_content:
                try:
                    await _invoke_respond_callback(respond_cb, SYSTEM_RESUME_SIGNAL)
                    await _clear_auto_resumed_pending_input()
                    ctx.wf_logger.info(
                        f" [{ctx.workflow_name_upper}] Auto-resumed suppressed AG2 feedback prompt "
                        f"for input request {request_id}"
                    )
                    state.awaiting_user_input = False
                    return None
                except Exception as e:
                    ctx.wf_logger.debug(
                        f"Failed auto-resuming suppressed AG2 feedback prompt for {request_id}: {e}"
                    )

        state.awaiting_user_input = True

        if callable(respond_cb):
            async def _tracked_respond(response_value: str) -> None:
                try:
                    await _invoke_respond_callback(respond_cb, response_value)
                finally:
                    state.pending_input_requests.pop(request_id, None)
                    state.awaiting_user_input = bool(state.pending_input_requests)

            state.pending_input_requests[request_id] = _tracked_respond

            try:
                if ctx.transport:
                    registered_id = ctx.transport.register_input_request(
                        ctx.chat_id, request_id, _tracked_respond
                    )
                    if registered_id and registered_id != request_id:
                        state.pending_input_requests.pop(request_id, None)
                        state.pending_input_requests[registered_id] = _tracked_respond
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

        component_hint = _extract_component_hint(request_obj)
        component_type = component_hint or "UserInputRequest"
        tool_name = component_hint or component_type
        request_payload = _extract_request_payload(request_obj)
        password = bool(
            getattr(event, "password", False)
            or request_payload.get("password", False)
        )
        requested_display = request_payload.get("display") or request_payload.get("mode")
        display_mode = (
            str(requested_display).strip()
            if requested_display
            else ("inline" if component_hint or password else "composer")
        )
        normalized_payload = {
            **request_payload,
            "input_request_id": request_id,
            "request_id": request_id,
            "prompt": prompt_hint or "",
            "password": password,
            "workflow_name": ctx.workflow_name,
            "component_type": component_type,
            "display": request_payload.get("display") or request_payload.get("mode") or display_mode,
            "mode": request_payload.get("mode") or request_payload.get("display") or display_mode,
            "interaction_type": "input_request",
        }
        if suppressed_generic_prompt:
            normalized_payload.setdefault("resume_ui_kind", "awaiting_reply")
            normalized_payload.setdefault("metadata_source", metadata_source)
            normalized_payload.setdefault("generic_feedback_prompt_suppressed", True)

        # Persist pending input request for resume support
        try:
            await ctx.persistence_manager.save_pending_input_request(
                chat_id=ctx.chat_id,
                app_id=ctx.app_id,
                request_id=request_id,
                agent=state.turn_agent or "Agent",
                prompt=prompt_hint or "",
                component_type=component_type,
                workflow_name=ctx.workflow_name,
                tool_name=tool_name,
                display=display_mode,
                interaction_type="input_request",
                password=password,
                raw_payload=normalized_payload,
            )
        except Exception as e:
            ctx.wf_logger.debug(f"Failed to persist pending input request: {e}")

        if suppressed_generic_prompt:
            await _emit_awaiting_reply_event(
                ctx=ctx,
                state=state,
                prompt=prompt_hint or "",
            )

        # Build response-required workflow UI payload for transport.
        return {
            "kind": "tool_call",
            "tool_call_id": request_id,
            "corr": request_id,
            "tool_name": tool_name,
            "component_type": component_type,
            "workflow_name": ctx.workflow_name,
            "interaction_type": "input_request",
            "awaiting_response": True,
            "display": display_mode,
            "display_type": display_mode,
            "agent": state.turn_agent or "Agent",
            "chat_id": ctx.chat_id,
            "payload": normalized_payload,
            "metadata": {
                "source": metadata_source,
                "has_respond_callback": callable(respond_cb),
                "generic_feedback_prompt_suppressed": suppressed_generic_prompt,
            },
        }

    def should_break(self, event: Any, state: "StreamState") -> bool:
        """InputRequestEvent does not terminate the stream."""
        return False
