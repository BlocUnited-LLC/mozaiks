"""Runtime events emitted from validated workflow structured outputs."""

from __future__ import annotations

from typing import Any

from .runtime_validation import validate_agent_structured_output


async def emit_validated_agent_output(
    *,
    current_agent_name: str,
    last_reply: Any,
    workflow_name: str,
    chat_id: str,
    app_id: str,
    user_id: str | None,
    turn_sequence: int,
    context_vars_dict: dict[str, Any],
    context_bridge: Any,
    structured_registry: dict[str, Any],
    auto_tool_agents: set[str],
    wf_logger: Any,
) -> dict[str, Any] | None:
    """Validate an agent reply and emit the canonical runtime output event."""

    if current_agent_name not in structured_registry or last_reply is None:
        return None

    validation = validate_agent_structured_output(
        agent_name=current_agent_name,
        reply=last_reply,
        structured_registry=structured_registry,
    )
    if validation is None:
        return None
    if not validation.validation_passed or validation.structured_data is None:
        wf_logger.warning(
            "[%s] Structured output validation failed for %s: %s",
            workflow_name.upper(),
            current_agent_name,
            validation.error,
        )
        return None

    structured_data = validation.structured_data

    try:
        from mozaiksai.core.events.runtime_events import (
            RUNTIME_AGENT_OUTPUT_VALIDATED,
            build_runtime_agent_output_validated_event,
            build_runtime_context_payload,
            build_turn_idempotency_key,
        )
        from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher

        context_payload = build_runtime_context_payload(
            chat_id=chat_id,
            app_id=app_id,
            workflow_name=workflow_name,
            turn_sequence=turn_sequence,
            user_id=user_id,
            context_variables=context_vars_dict,
        )
        event_payload = build_runtime_agent_output_validated_event(
            agent=current_agent_name,
            model_name=validation.model_name,
            structured_data=structured_data,
            auto_tool_call=current_agent_name in auto_tool_agents,
            context=context_payload,
            source="ag2_network_orchestration",
            turn_idempotency_key=build_turn_idempotency_key(chat_id, turn_sequence),
            pattern_context_ref=context_bridge,
            validation_passed=True,
        )
        await get_event_dispatcher().emit(RUNTIME_AGENT_OUTPUT_VALIDATED, event_payload)
    except Exception as err:
        wf_logger.warning(
            "[%s] runtime.agent_output_validated dispatch failed for %s: %s",
            workflow_name.upper(),
            current_agent_name,
            err,
        )

    return structured_data


__all__ = ["emit_validated_agent_output"]
