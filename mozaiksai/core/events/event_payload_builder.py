# ==============================================================================
# FILE: event_payload_builder.py
# DESCRIPTION: 
# ==============================================================================

# === MOZAIKS-CORE-HEADER ===

"""
Event Payload Builder - Constructs UI-ready payloads from AG2 events.

Purpose:
- Transform AG2 internal events into UI-friendly JSON payloads
- Extract tool call/response metadata and correlation IDs
- Handle structured outputs and agent attribution
- Support all AG2 event types (Text, InputRequest, SelectSpeaker, ToolCall, etc.)

Extracted from orchestration_patterns.py to reduce complexity.
This is the largest helper (300+ lines) that was bloating the main orchestration file.
"""

from typing import Any, Dict, Optional
import logging

from mozaiksai.core.workflow.outputs.structured import (
    agent_has_structured_output,
    get_structured_output_model_fields,
)
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager as _PM
from ..workflow.messages.utils import (
    extract_agent_name,
    normalize_text_content,
    serialize_event_content,
)

logger = logging.getLogger(__name__)

__all__ = ['build_ui_event_payload']


def build_ui_event_payload(
    *,
    ev: Any,
    workflow_name: str,
    turn_agent: Optional[str],
    wf_logger: logging.Logger,
    tool_call_initiators: Dict[str, str],
    tool_names_by_id: Dict[str, str],
    workflow_name_upper: str,
) -> Optional[Dict[str, Any]]:
    """Build the UI event payload for a single AG2 event.

    This function transforms AG2 internal events into standardized JSON payloads
    that can be sent to the UI transport layer. It handles all AG2 event types
    and extracts relevant metadata for correlation and display.

    Args:
        ev: AG2 event object (TextEvent, ToolCallEvent, SelectSpeakerEvent, etc.)
        workflow_name: Name of the workflow generating the event
        turn_agent: Current agent's turn (for fallback attribution)
        wf_logger: Logger instance for workflow-specific logging
        tool_call_initiators: Mapping of tool_call_id -> initiating agent name
        tool_names_by_id: Mapping of tool_call_id -> tool name
        workflow_name_upper: Uppercase workflow name for logging

    Returns:
        Dictionary payload ready for UI transport, or None if event cannot be processed
    """
    try:
        from autogen.events.agent_events import (
            TextEvent as _T,
            InputRequestEvent as _IR,
            RunCompletionEvent as _RC,
            ErrorEvent as _EE,
            FunctionCallEvent as _FCe,
            ToolCallEvent as _TCe,
            FunctionResponseEvent as _FRe,
            ToolResponseEvent as _TRe,
            SelectSpeakerEvent as _SS,
            GroupChatResumeEvent as _GR,
            GroupChatRunChatEvent as _GRCE,
        )
        from autogen.events.client_events import UsageSummaryEvent as _US
        try:
            from autogen.events.print_event import PrintEvent as _PE
        except Exception:  # pragma: no cover
            _PE = object  # type: ignore
    except Exception:
        return {"event_type": ev.__class__.__name__, "kind": "unknown"}

    et_name = ev.__class__.__name__
    payload: Dict[str, Any] = {"event_type": et_name}

    # TextEvent - Agent message content
    if isinstance(ev, _T):
        sender = extract_agent_name(ev)
        raw_content_obj = getattr(ev, "content", None)
        clean_content = normalize_text_content(raw_content_obj)
        serialized_raw = serialize_event_content(raw_content_obj) if raw_content_obj is not None else None
        
        payload.update({"kind": "text", "agent": sender, "content": clean_content})
        
        if serialized_raw is not None and not isinstance(serialized_raw, str):
            payload["raw_content"] = serialized_raw
            
        # Agent attribution fallback chain
        if not payload.get("agent"):
            fallback_agent = extract_agent_name(serialized_raw) if serialized_raw is not None else None
            if not fallback_agent:
                fallback_agent = extract_agent_name(getattr(ev, "sender", None))
            if not fallback_agent:
                fallback_agent = str(turn_agent) if turn_agent else None
            payload["agent"] = fallback_agent or "Assistant"
        
        # Structured output extraction (if agent has schema)
        try:
            if sender and workflow_name and agent_has_structured_output(workflow_name, sender):
                structured = _PM._extract_json_from_text(clean_content) if hasattr(_PM, '_extract_json_from_text') else None
                if structured:
                    payload["structured_output"] = structured
                    schema_fields = get_structured_output_model_fields(workflow_name, sender)
                    if schema_fields:
                        payload["structured_schema"] = schema_fields
                    try:
                        import json as _json
                        if isinstance(structured, dict):
                            so_keys = list(structured.keys())
                        elif isinstance(structured, list):
                            so_keys = [f"list[{len(structured)}]"]
                        else:
                            so_keys = [type(structured).__name__]
                        so_json = _json.dumps(structured, ensure_ascii=False)
                        max_len = 2000
                        if len(so_json) > max_len:
                            so_json = so_json[:max_len] + "...<truncated>"
                        wf_logger.info(f" [STRUCTURED_OUTPUT] agent={sender} keys={so_keys} json={so_json}")
                    except Exception as _so_log_err:  # pragma: no cover
                        wf_logger.debug(f"[STRUCTURED_OUTPUT] log skipped: {_so_log_err}")
        except Exception as so_err:  # pragma: no cover
            wf_logger.debug(f"Structured output attach failed sender={sender}: {so_err}")
        
        return payload

    # PrintEvent - Console output from agents
    if isinstance(ev, _PE):
        payload.update({
            "kind": "print",
            "agent": extract_agent_name(ev),
            "content": normalize_text_content(getattr(ev, "content", None)),
        })
        return payload

    # InputRequestEvent - Agent requesting user input
    if isinstance(ev, _IR):
        agent_name = extract_agent_name(ev)
        request_obj = getattr(ev, "content", None)
        prompt_text = getattr(ev, "_mozaiks_prompt", None) or getattr(ev, "prompt", None)
        component_hint = None
        raw_payload = None
        
        if request_obj is not None:
            try:
                if prompt_text is None:
                    if hasattr(request_obj, "prompt"):
                        prompt_text = getattr(request_obj, "prompt")
                    elif isinstance(request_obj, dict):
                        prompt_text = request_obj.get("prompt") or request_obj.get("message")
                        
                if hasattr(request_obj, "ui_tool_id"):
                    component_hint = getattr(request_obj, "ui_tool_id")
                elif isinstance(request_obj, dict):
                    component_hint = request_obj.get("ui_tool_id") or request_obj.get("component") or request_obj.get("component_type")
                    
                if hasattr(request_obj, "model_dump"):
                    raw_payload = request_obj.model_dump()  # type: ignore[attr-defined]
                elif isinstance(request_obj, dict):
                    raw_payload = request_obj
            except Exception as prompt_err:
                wf_logger.debug(f"InputRequest prompt extraction failed: {prompt_err}")
                
        request_id = getattr(ev, "_mozaiks_request_id", None)
        if not request_id:
            request_id = getattr(ev, "uuid", None) or getattr(ev, "id", None)
        if request_id:
            request_id = str(request_id)
            
        payload.update({
            "kind": "input_request",
            "agent": agent_name,
            "request_id": request_id,
            "prompt": (prompt_text or ""),
        })
        payload["password"] = bool(getattr(ev, "password", False))
        
        if component_hint:
            payload["component_type"] = component_hint
        if raw_payload is not None:
            payload["raw_payload"] = raw_payload
            
        return payload

    # SelectSpeakerEvent - Next speaker selected
    if isinstance(ev, _SS):
        agent_name = extract_agent_name(ev)
        next_agent_obj = getattr(ev, "agent", None)
        next_agent = None
        if next_agent_obj:
            next_agent = getattr(next_agent_obj, "name", None) or str(next_agent_obj)
        payload.update({
            "kind": "select_speaker",
            "agent": agent_name,
            "next": next_agent,
        })
        return payload

    # GroupChatResumeEvent - Resume boundary marker
    if isinstance(ev, _GR):
        payload.update({"kind": "resume_boundary"})
        return payload

    # GroupChatRunChatEvent - Internal AG2 event
    if isinstance(ev, _GRCE):
        payload.update({"kind": "unknown"})
        return payload

    # FunctionCallEvent / ToolCallEvent - Tool invocation
    if isinstance(ev, (_FCe, _TCe)):
        content_obj = None
        tool_name = None
        
        # Extract tool name from various event structures
        tool_calls = getattr(ev, "tool_calls", None)
        if isinstance(tool_calls, list) and tool_calls:
            first_call = tool_calls[0]
            fn = getattr(first_call, "function", None)
            name_attr = getattr(fn, "name", None)
            if isinstance(name_attr, str):
                tool_name = name_attr
                
        if not tool_name:
            function_call = getattr(ev, "function_call", None)
            fn_name = getattr(function_call, "name", None)
            if isinstance(fn_name, str):
                tool_name = fn_name
                
        if not tool_name:
            content_obj = getattr(ev, "content", None)
            tool_name = (
                getattr(ev, "tool_name", None)
                or getattr(content_obj, "name", None)
                or getattr(content_obj, "tool_name", None)
            )
            if not tool_name and content_obj:
                tool_calls = getattr(content_obj, "tool_calls", None)
                if isinstance(tool_calls, list) and tool_calls:
                    first_tool = tool_calls[0]
                    function_obj = getattr(first_tool, "function", None)
                    if function_obj:
                        tool_name = getattr(function_obj, "name", None)
                        if tool_name:
                            wf_logger.debug(f" [TOOL_EXTRACT] Found tool name: {tool_name}")
                            
        if not tool_name:
            tool_name = "unknown_tool"
            
        tool_call_id = (
            getattr(ev, "id", None)
            or getattr(ev, "uuid", None)
            or f"tool_{tool_name}"
        )
        
        # Extract tool arguments
        extracted_args: Dict[str, Any] = {}
        try:
            if isinstance(tool_calls, list) and tool_calls:
                first_tool = tool_calls[0]
                f_fn = getattr(first_tool, "function", None)
                if f_fn is not None:
                    poss_args = getattr(f_fn, "arguments", None)
                    if isinstance(poss_args, dict):
                        extracted_args = poss_args
                        
            if not extracted_args:
                function_call = getattr(ev, "function_call", None)
                if function_call is not None:
                    poss_args = getattr(function_call, "arguments", None)
                    if isinstance(poss_args, dict):
                        extracted_args = poss_args
                        
            if not extracted_args and content_obj is None:
                content_obj = getattr(ev, "content", None)
                
            if not extracted_args and content_obj is not None:
                poss_args = getattr(content_obj, "arguments", None)
                if isinstance(poss_args, dict):
                    extracted_args = poss_args
        except Exception as arg_ex:
            wf_logger.debug(f"[TOOL_ARGS] extraction failed for {tool_name}: {arg_ex}")
            
        agent_for_tool = extract_agent_name(ev) or turn_agent or getattr(ev, "sender", None)
        
        # Track tool call metadata for correlation
        if tool_call_id:
            tool_names_by_id[str(tool_call_id)] = str(tool_name)
        init_agent = agent_for_tool or payload.get("agent")
        if init_agent and tool_call_id:
            tool_call_initiators[str(tool_call_id)] = init_agent
            
        # Only emit payload if we have arguments (avoid noise from introspection calls)
        if extracted_args:
            payload.update({
                "kind": "tool_call",
                "agent": agent_for_tool,
                "tool_name": str(tool_name),
                "tool_call_id": str(tool_call_id),
                "corr": str(tool_call_id),
                "component_type": "inline",
                "awaiting_response": True,
                "payload": {
                    "tool_args": extracted_args,
                    "interaction_type": "input",
                    "agent_name": agent_for_tool,
                },
            })
            logger.info(f" [TOOL_CALL] agent={agent_for_tool} tool={tool_name} id={tool_call_id} args_keys={list(extracted_args.keys())}")
        else:
            logger.debug(f" [TOOL_CALL_SUPPRESSED] tool={tool_name} id={tool_call_id} (no args)")
            
        return payload

    # FunctionResponseEvent / ToolResponseEvent - Tool result
    if isinstance(ev, (_FRe, _TRe)):
        tool_name = getattr(ev, "tool_name", None)
        content_obj = getattr(ev, "content", None)
        
        if not tool_name and content_obj:
            tool_name = (
                getattr(content_obj, "tool_name", None)
                or getattr(content_obj, "name", None)
            )
            
        if not tool_name and content_obj:
            tool_calls = getattr(content_obj, "tool_calls", None)
            if isinstance(tool_calls, list) and tool_calls:
                first_tool = tool_calls[0]
                function_obj = getattr(first_tool, "function", None)
                if function_obj:
                    tool_name = getattr(function_obj, "name", None)
                    if tool_name:
                        wf_logger.debug(f" [TOOL_EXTRACT_RESPONSE] Found tool name: {tool_name}")
                        
        if not tool_name:
            tool_name = "unknown_tool"
            
        agent_name = extract_agent_name(ev)
        tool_response_id = (
            getattr(ev, "id", None)
            or getattr(ev, "uuid", None)
            or getattr(ev, "tool_call_id", None)
        )
        
        # Use initiator agent as fallback if response doesn't specify
        if not agent_name and tool_response_id:
            fallback_agent = tool_call_initiators.get(str(tool_response_id))
            if fallback_agent:
                agent_name = fallback_agent
                logger.debug(f" [TOOL_RESPONSE_AGENT_FALLBACK] Using initiator agent={agent_name} for tool_response id={tool_response_id}")
                
        # Use tracked tool name if response doesn't specify
        if (not tool_name or tool_name == "unknown_tool") and tool_response_id:
            tool_name = tool_names_by_id.get(str(tool_response_id), tool_name)
            
        payload.update({
            "kind": "tool_response",
            "tool_name": str(tool_name),
            "agent": agent_name,
            "tool_call_id": str(tool_response_id) if tool_response_id else None,
            "corr": str(tool_response_id) if tool_response_id else None,
            "content": getattr(ev, "content", None),
        })
        wf_logger.debug(f" [TOOL_RESPONSE] agent={agent_name} tool={tool_name} id={tool_response_id}")
        return payload

    # UsageSummaryEvent - Token usage metrics
    if isinstance(ev, _US):
        for f in ("total_tokens", "prompt_tokens", "completion_tokens", "cost", "model"):
            if hasattr(ev, f):
                payload[f] = getattr(ev, f)
        payload.update({"kind": "usage_summary"})
        return payload

    # ErrorEvent - Error notification
    if isinstance(ev, _EE):
        agent_name = extract_agent_name(ev)
        # Prefer explicit message field, then any attached error object, then content
        raw_msg = None
        if hasattr(ev, 'message') and getattr(ev, 'message'):
            raw_msg = getattr(ev, 'message')
        elif hasattr(ev, 'error') and getattr(ev, 'error'):
            # Some AG2 ErrorEvent use 'error' to carry exception objects
            try:
                raw_msg = str(getattr(ev, 'error'))
            except Exception:
                raw_msg = None
        elif hasattr(ev, 'content') and getattr(ev, 'content'):
            raw_msg = getattr(ev, 'content')
        else:
            raw_msg = str(ev)

        # Clean up known AG2 UUID-wrapped content patterns if present
        try:
            from mozaiksai.core.transport.simple_transport import _extract_clean_content
            cleaned = _extract_clean_content(raw_msg)
        except Exception:
            cleaned = raw_msg

        payload.update({
            "kind": "error",
            "agent": agent_name,
            "message": cleaned or "Unknown error",
        })
        return payload

    # RunCompletionEvent - Conversation completion
    if isinstance(ev, _RC):
        rc_agent = extract_agent_name(ev) or getattr(ev, "agent", None) or "workflow"
        payload.update({
            "kind": "run_complete",
            "agent": rc_agent,
        })
        return payload

    # Unknown event type
    payload.update({"kind": "unknown"})
    return payload

