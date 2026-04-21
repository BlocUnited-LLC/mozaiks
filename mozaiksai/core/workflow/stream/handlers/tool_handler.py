# ==============================================================================
# FILE: mozaiksai/core/workflow/stream/handlers/tool_handler.py
# DESCRIPTION: Handlers for ToolCallEvent, ToolResponseEvent, and UI interactions
# ==============================================================================

"""
Tool Event Handlers

Handles tool-related AG2 events:
- ToolCallEvent / FunctionCallEvent: Tool invocations
- ToolResponseEvent / FunctionResponseEvent: Tool results

Responsibilities:
- Track tool call initiators for response correlation
- Handle UI tool interactions
- Process schema validation sentinel responses
- Build tool_call and tool_response payloads for UI
"""

import json
from typing import TYPE_CHECKING, Any, Dict, Optional, Set, Type

from .base import BaseEventHandler

if TYPE_CHECKING:
    from ..context import StreamContext, StreamState

# Import AG2 event types
from autogen.events.agent_events import (
    FunctionCallEvent,
    FunctionResponseEvent,
    ToolCallEvent,
    ToolResponseEvent,
)

# Import serialization utilities
from mozaiksai.core.events.event_serialization import (
    serialize_event_content,
    extract_agent_name,
)

from mozaiksai.core.workflow.validation import SENTINEL_STATUS


class ToolCallHandler(BaseEventHandler):
    """
    Handler for ToolCallEvent and FunctionCallEvent.

    Records tool call metadata for correlation with responses and
    handles UI tool interactions.
    """

    def event_types(self) -> Set[Type]:
        """Handle ToolCallEvent and FunctionCallEvent."""
        return {ToolCallEvent, FunctionCallEvent}

    async def handle(
        self,
        event: Any,
        ctx: "StreamContext",
        state: "StreamState",
    ) -> Optional[Dict[str, Any]]:
        """
        Handle tool call events.

        Records call metadata for response correlation and processes
        any UI tool interactions.

        Args:
            event: ToolCallEvent or FunctionCallEvent instance
            ctx: Stream context
            state: Stream state (tool_call_initiators will be modified)

        Returns:
            tool_call payload for UI
        """
        # Extract call details
        call_id = getattr(event, "call_id", None) or getattr(event, "id", None)
        tool_name = getattr(event, "tool_name", None) or getattr(event, "name", None)
        arguments = getattr(event, "arguments", None) or {}

        # Extract agent name
        agent_name = extract_agent_name(event) or state.turn_agent

        # Record for response correlation
        if call_id:
            state.record_tool_call(
                call_id=str(call_id),
                agent_name=agent_name or "unknown",
                tool_name=tool_name or "unknown",
            )

        # Handle UI tool interactions
        await self._handle_ui_interaction(event, ctx)

        # Serialize arguments
        try:
            serialized_args = serialize_event_content(arguments)
        except Exception:
            serialized_args = str(arguments)

        # Build payload
        return {
            "kind": "tool_call",
            "call_id": str(call_id) if call_id else None,
            "name": tool_name,
            "tool_name": tool_name,
            "arguments": serialized_args,
            "agent": agent_name,
            "chat_id": ctx.chat_id,
            "sequence": state.sequence_counter,
        }

    async def _handle_ui_interaction(
        self,
        event: Any,
        ctx: "StreamContext",
    ) -> None:
        """Handle UI tool interaction if applicable."""
        try:
            from mozaiksai.core.workflow.ui_tools import (
                handle_tool_call_for_ui_interaction,
            )
            ui_response = await handle_tool_call_for_ui_interaction(event, ctx.chat_id)
            if ui_response and ctx.transport:
                await ctx.transport.send_event_to_ui(
                    {
                        "kind": "tool_ui_response",
                        "tool_name": getattr(event, "tool_name", None),
                        "response": ui_response,
                        "chat_id": ctx.chat_id,
                    },
                    ctx.chat_id,
                )
        except Exception as tool_err:
            ctx.wf_logger.debug(f"Tool UI interaction error: {tool_err}")

    def should_break(self, event: Any, state: "StreamState") -> bool:
        return False


class ToolResponseHandler(BaseEventHandler):
    """
    Handler for ToolResponseEvent and FunctionResponseEvent.

    Correlates responses with their originating calls and handles
    schema validation failures with retry logic.
    """

    def event_types(self) -> Set[Type]:
        """Handle ToolResponseEvent and FunctionResponseEvent."""
        return {ToolResponseEvent, FunctionResponseEvent}

    async def handle(
        self,
        event: Any,
        ctx: "StreamContext",
        state: "StreamState",
    ) -> Optional[Dict[str, Any]]:
        """
        Handle tool response events.

        Correlates with originating call, handles schema validation failures,
        and builds response payload.

        Args:
            event: ToolResponseEvent or FunctionResponseEvent instance
            ctx: Stream context
            state: Stream state

        Returns:
            tool_response payload for UI
        """
        # Extract response details
        call_id = getattr(event, "call_id", None) or getattr(event, "id", None)
        call_id_str = str(call_id) if call_id else None

        # Correlate with original call
        agent_name = state.get_tool_initiator(call_id_str) if call_id_str else None
        tool_name = state.get_tool_name(call_id_str) if call_id_str else None

        # Fallback to event attributes
        if not tool_name:
            tool_name = getattr(event, "tool_name", None) or getattr(event, "name", None)
        if not agent_name:
            agent_name = extract_agent_name(event) or state.turn_agent

        # Extract result content
        result = getattr(event, "result", None) or getattr(event, "content", None)

        # Serialize result
        try:
            serialized_result = serialize_event_content(result)
        except Exception:
            serialized_result = str(result)

        # Build base payload
        payload: Dict[str, Any] = {
            "kind": "tool_response",
            "call_id": call_id_str,
            "name": tool_name,
            "tool_name": tool_name,
            "result": serialized_result,
            "agent": agent_name,
            "chat_id": ctx.chat_id,
            "sequence": state.sequence_counter,
        }

        # Check for schema validation failure
        status = getattr(event, "status", None)
        if status == SENTINEL_STATUS:
            payload["status"] = SENTINEL_STATUS
            await self._handle_schema_failure(payload, ctx, state)

        return payload

    async def _handle_schema_failure(
        self,
        payload: Dict[str, Any],
        ctx: "StreamContext",
        state: "StreamState",
    ) -> None:
        """
        Handle schema validation failure with retry logic.

        Implements automatic retry by sending feedback to the agent.
        """
        agent_name = payload.get("agent")
        tool_name = payload.get("name") or payload.get("tool_name") or "unknown_tool"
        call_id = payload.get("call_id")

        # Build retry key for deduplication
        retry_key_parts = [str(p) for p in (call_id, agent_name, tool_name) if p]
        retry_key = "|".join(retry_key_parts) if retry_key_parts else f"agent:{agent_name}|tool:{tool_name}"

        if not state.should_retry_schema(retry_key):
            attempts = state.schema_retry_tracker.get(retry_key, 0)
            if attempts == state.MAX_SCHEMA_RETRIES:
                ctx.wf_logger.warning(
                    f" [{ctx.workflow_name_upper}] Schema validation failed {attempts} time(s) "
                    f"for agent={agent_name} tool={tool_name} call_id={call_id}. No further auto-retries."
                )
                state.schema_retry_tracker[retry_key] = state.MAX_SCHEMA_RETRIES + 1

                # Send error to UI
                if ctx.transport:
                    error_payload = {
                        "kind": "error",
                        "agent": agent_name,
                        "code": "SCHEMA_VALIDATION_FAILED",
                        "message": (
                            f"Schema validation failed repeatedly for tool '{tool_name}'. "
                            "Manual follow-up is required."
                        ),
                    }
                    try:
                        await ctx.transport.send_event_to_ui(error_payload, ctx.chat_id)
                    except Exception as err:
                        ctx.wf_logger.debug(
                            f"Failed to send schema failure error event for {ctx.chat_id}: {err}"
                        )
            return

        # Record retry attempt
        attempts = state.record_schema_retry(retry_key)

        # Get group manager and target agent for retry
        gm = ctx.group_manager
        target_agent = ctx.agents.get(agent_name) if agent_name else None

        if not gm or not target_agent:
            ctx.wf_logger.debug(
                f" [{ctx.workflow_name_upper}] Schema retry skipped; "
                f"agent or group manager missing for agent={agent_name}"
            )
            return

        # Build retry message
        error_info = payload.get("error") or {}
        expected_model = error_info.get("expected_model")
        validation_errors = error_info.get("errors")

        message_lines = []
        if attempts > 1:
            message_lines.append(
                f"Retry attempt {attempts} of {state.MAX_SCHEMA_RETRIES}."
            )
        if expected_model:
            message_lines.append(
                f"The previous call to `{tool_name}` failed schema validation for model `{expected_model}`."
            )
        else:
            message_lines.append(
                f"The previous call to `{tool_name}` failed schema validation."
            )
        message_lines.append(
            "Review the validation errors and call the tool again with arguments that satisfy the schema."
        )
        if validation_errors:
            try:
                message_lines.append(
                    "Validation errors: " + json.dumps(validation_errors, ensure_ascii=False)
                )
            except Exception:
                message_lines.append(f"Validation errors: {validation_errors}")

        feedback_text = "\n".join(message_lines)

        try:
            await gm.a_send(
                message=feedback_text,
                recipient=target_agent,
                request_reply=True,
                silent=True,
            )
            ctx.wf_logger.info(
                f" [{ctx.workflow_name_upper}] Requested schema retry for "
                f"agent={agent_name} tool={tool_name} attempt={attempts}"
            )
        except Exception as retry_err:
            ctx.wf_logger.warning(
                f" [{ctx.workflow_name_upper}] Failed to enqueue schema retry for "
                f"agent={agent_name} tool={tool_name}: {retry_err}"
            )

    def should_break(self, event: Any, state: "StreamState") -> bool:
        return False
