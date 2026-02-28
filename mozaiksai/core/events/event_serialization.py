# ==============================================================================
# FILE: event_serialization.py
# DESCRIPTION: 
# ==============================================================================

# === MOZAIKS-CORE-HEADER ===

"""AG2 Runtime Event Serialization - Third Event Type Handler

This module handles the THIRD type of event in MozaiksAI's event system:

1. Business Events: emit_business_event(log_event_type=...) -> UnifiedEventDispatcher  
2. UI Tool Events: emit_ui_tool_event(ui_tool_id=...) -> UnifiedEventDispatcher
3. AG2 Runtime Events: AutoGen events with 'kind' field -> THIS MODULE -> WebSocket

This module centralizes logic for transforming raw AutoGen (AG2) runtime events  
into transport-friendly payload dictionaries consumed by the WebSocket/UI layer.

Key transformation: 'kind' field (internal) -> 'type' field (WebSocket/frontend)
Examples: {"kind": "text"} -> {"type": "chat.text", "data": {...}}

Goals:
 - Single source of truth for AG2 event mapping, normalization, structured output flags
 - Keep orchestration loop lean and focused on control flow  
 - Provide testable, deterministic helpers

Public entry point: build_ui_event_payload(...)
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from dataclasses import dataclass

from mozaiksai.core.workflow.validation.tools import (
	SENTINEL_AGENT_KEY,
	SENTINEL_ERRORS_KEY,
	SENTINEL_EXPECTED_MODEL_KEY,
	SENTINEL_FLAG,
	SENTINEL_MESSAGE_KEY,
	SENTINEL_STATUS,
	SENTINEL_TOOL_KEY,
)

# Lightweight dataclass to pass context (avoids long arg lists if extended later)
@dataclass
class EventBuildContext:
	workflow_name: str
	turn_agent: Optional[str]
	tool_call_initiators: Dict[str, str]
	tool_names_by_id: Dict[str, str]
	workflow_name_upper: str
	wf_logger: Any  # lazy typed to avoid circular import with logging setup


# ---------------------------------------------------------------------------
# Basic content normalization helpers
# ---------------------------------------------------------------------------
def normalize_text_content(raw: Any) -> str:
	"""Convert AG2 text payload variants into a displayable string."""
	if raw is None:
		return ""
	if isinstance(raw, str):
		return raw
	if hasattr(raw, 'model_dump') and callable(getattr(raw, 'model_dump')):
		try:
			return normalize_text_content(raw.model_dump())  # type: ignore[attr-defined]
		except Exception:
			pass
	if isinstance(raw, dict):
		for key in ("content", "text", "message"):
			val = raw.get(key)
			if isinstance(val, str) and val.strip():
				return val
	if isinstance(raw, (list, tuple)):
		try:
			return " ".join(str(x) for x in raw)
		except Exception:
			pass
	return str(raw)


def serialize_event_content(raw: Any) -> Any:
	"""Best-effort JSON-serializable form of an AG2 event content object."""
	if raw is None or isinstance(raw, (str, int, float, bool)):
		return raw
	try:
		if hasattr(raw, 'model_dump') and callable(getattr(raw, 'model_dump')):
			return serialize_event_content(raw.model_dump())  # type: ignore[attr-defined]
	except Exception:
		pass
	try:
		if hasattr(raw, 'dict') and callable(getattr(raw, 'dict')):
			return serialize_event_content(raw.dict())  # type: ignore[attr-defined]
	except Exception:
		pass
	if isinstance(raw, dict):
		return {k: serialize_event_content(v) for k, v in raw.items()}
	if isinstance(raw, (list, tuple, set)):
		return [serialize_event_content(v) for v in list(raw)]
	if hasattr(raw, '__dict__'):
		try:
			return serialize_event_content(vars(raw))
		except Exception:
			pass
	return str(raw)


def extract_agent_name(obj: Any) -> Optional[str]:
	"""Attempt to extract the logical agent/sender name from diverse AG2 objects."""
	try:
		# Direct attributes
		for k in ("sender", "agent", "agent_name", "name"):
			v = getattr(obj, k, None)
			if isinstance(v, str) and v.strip():
				return v.strip()
			if v and hasattr(v, "name"):
				nv = getattr(v, "name", None)
				if isinstance(nv, str) and nv.strip():
					return nv.strip()
		content = getattr(obj, "content", None)
		if isinstance(content, dict):
			for k in ("sender", "agent", "agent_name", "name"):
				try:
					v = content.get(k) if hasattr(content, 'get') else None
					if isinstance(v, str) and v.strip():
						return v.strip()
				except (TypeError, AttributeError):
					continue
		if isinstance(content, str):
			import re
			m = re.search(r"sender(?:=|\"\s*:)['\"]([^'\"\\]+)['\"]", content)
			if m:
				return m.group(1).strip()
		return None
	except Exception:
		return None


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------
def build_ui_event_payload(*, ev: Any, ctx: EventBuildContext) -> Optional[Dict[str, Any]]:
	"""Return a UI payload dict for a single AG2 event (or None if unsupported)."""
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

	# TextEvent -------------------------------------------------------
	if isinstance(ev, _T):
		sender = extract_agent_name(ev)
		raw_content_obj = getattr(ev, "content", None)
		clean_content = normalize_text_content(raw_content_obj)
		serialized_raw = serialize_event_content(raw_content_obj) if raw_content_obj is not None else None
		payload.update({"kind": "text", "agent": sender, "content": clean_content})
		payload["source"] = payload.get("source") or "ag2_textevent"
		if serialized_raw is not None and not isinstance(serialized_raw, str):
			payload["raw_content"] = serialized_raw
		if not payload.get("agent"):
			fallback_agent = extract_agent_name(serialized_raw) if serialized_raw is not None else None
			if not fallback_agent:
				fallback_agent = extract_agent_name(getattr(ev, "sender", None))
			if not fallback_agent:
				fallback_agent = str(ctx.turn_agent) if ctx.turn_agent else None
			payload["agent"] = fallback_agent or "Assistant"
		# Structured outputs (best-effort)
		try:
			if sender and ctx.workflow_name:
				from mozaiksai.core.workflow.outputs.structured import (
					agent_has_structured_output,
					get_structured_output_model_fields,
				)
				if agent_has_structured_output(ctx.workflow_name, sender):
					ctx.wf_logger.debug(f" [STRUCTURED_DEBUG] agent={sender} has_structured_output=True, clean_content_len={len(clean_content) if clean_content else 0}")
					ctx.wf_logger.debug(f" [STRUCTURED_DEBUG] clean_content_preview: {clean_content[:200] if clean_content else 'None'}...")
					from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager as _PM  # lazy import
					if hasattr(_PM, '_extract_json_from_text'):
						structured = _PM._extract_json_from_text(clean_content)  # type: ignore
						ctx.wf_logger.debug(f" [STRUCTURED_DEBUG] _extract_json_from_text result: {structured is not None}")
					else:
						structured = None
						ctx.wf_logger.debug(f" [STRUCTURED_DEBUG] _PM._extract_json_from_text not available")
					if structured:
						payload["structured_output"] = structured
						schema_fields = get_structured_output_model_fields(ctx.workflow_name, sender)
						if schema_fields:
							payload["structured_schema"] = schema_fields
						try:
							import json as _json
							if isinstance(structured, dict):
								so_keys = list(structured.keys())
							elif isinstance(structured):
								so_keys = [f"list[{len(structured)}]"]
							else:
								so_keys = [type(structured).__name__]
							so_json = _json.dumps(structured, ensure_ascii=False)
							max_len = 2000
							if len(so_json) > max_len:
								so_json = so_json[:max_len] + "...<truncated>"
							ctx.wf_logger.info(f" [STRUCTURED_OUTPUT] agent={sender} keys={so_keys} json={so_json}")
						except Exception as _so_log_err:  # pragma: no cover
							ctx.wf_logger.debug(f"[STRUCTURED_OUTPUT] log skipped: {_so_log_err}")
					else:
						ctx.wf_logger.debug(f" [STRUCTURED_DEBUG] No structured output extracted for {sender}")
		except Exception as so_err:  # pragma: no cover
			ctx.wf_logger.debug(f"Structured output attach failed sender={sender}: {so_err}")
		return payload

	# PrintEvent ------------------------------------------------------
	if isinstance(ev, _PE):
		payload.update({
			"kind": "print",
			"agent": extract_agent_name(ev),
			"content": normalize_text_content(getattr(ev, "content", None)),
		})
		return payload

	# InputRequestEvent -----------------------------------------------
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
				ctx.wf_logger.debug(f"InputRequest prompt extraction failed: {prompt_err}")
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


	# ToolCallEvent / FunctionCallEvent -------------------------------
	if isinstance(ev, (_TCe, _FCe)):
		call_id = getattr(ev, "id", None) or getattr(ev, "call_id", None) or getattr(ev, "uuid", None)
		if call_id:
			call_id = str(call_id)
		name = getattr(ev, "name", None) or getattr(ev, "function", None) or getattr(ev, "tool", None)
		args_obj = getattr(ev, "arguments", None) or getattr(ev, "args", None)
		serialized_args = serialize_event_content(args_obj) if args_obj is not None else None
		initiator = ctx.tool_call_initiators.get(str(call_id), None)
		if initiator is None:
			initiator = extract_agent_name(ev) or ctx.turn_agent
		payload.update({
			"kind": "tool_call",
			"call_id": call_id,
			"name": name,
			"agent": initiator,
		})
		if serialized_args is not None:
			payload["arguments"] = serialized_args
		return payload

	# ToolResponseEvent / FunctionResponseEvent -----------------------
	if isinstance(ev, (_TRe, _FRe)):
		call_id = getattr(ev, "id", None) or getattr(ev, "call_id", None) or getattr(ev, "uuid", None)
		if call_id:
			call_id = str(call_id)
		name = ctx.tool_names_by_id.get(str(call_id), None) or getattr(ev, "name", None)
		result_obj = getattr(ev, "content", None) or getattr(ev, "result", None)
		sentinel_info = None
		clean_result_obj = result_obj
		if isinstance(result_obj, dict) and result_obj.get(SENTINEL_FLAG):
			sentinel_info = {
				"message": result_obj.get(SENTINEL_MESSAGE_KEY),
				"errors": result_obj.get(SENTINEL_ERRORS_KEY),
				"expected_model": result_obj.get(SENTINEL_EXPECTED_MODEL_KEY),
				"agent": result_obj.get(SENTINEL_AGENT_KEY),
				"tool": result_obj.get(SENTINEL_TOOL_KEY),
			}
			clean_result_obj = {k: v for k, v in result_obj.items() if k != SENTINEL_FLAG}
		serialized_result = serialize_event_content(clean_result_obj) if clean_result_obj is not None else None
		origin = ctx.tool_call_initiators.get(str(call_id), None) or extract_agent_name(ev)
		if sentinel_info:
			sentinel_info.setdefault("agent", origin)
			sentinel_info.setdefault("tool", name)
		payload.update({
			"kind": "tool_response",
			"call_id": call_id,
			"name": name,
			"agent": origin,
		})
		if serialized_result is not None:
			payload["result"] = serialized_result
		if sentinel_info:
			payload["status"] = SENTINEL_STATUS
			payload["error"] = sentinel_info
		else:
			payload.setdefault("status", "ok")
		return payload

	# SelectSpeakerEvent ----------------------------------------------
	if isinstance(ev, _SS):
		selected = getattr(ev, "selected", None) or getattr(ev, "next", None)
		current_agent = extract_agent_name(ev) or ctx.turn_agent
		payload.update({
			"kind": "select_speaker",
			"agent": current_agent,
			"selected_speaker": selected,  # Include both for clarity
		})
		# Enhanced logging to trace handoffs
		ctx.wf_logger.info(f"🎭 [SPEAKER_SELECT] {current_agent} → {selected} (turn handoff)")
		return payload

	# GroupChatResumeEvent --------------------------------------------
	if isinstance(ev, _GR):
		payload.update({
			"kind": "resume_boundary",
			"agent": extract_agent_name(ev) or ctx.turn_agent,
			"reason": normalize_text_content(getattr(ev, "content", None)),
		})
		return payload

	# GroupChatRunChatEvent -------------------------------------------
	if isinstance(ev, _GRCE):
		# Map run lifecycle orchestration marker to a semantic kind so UI can
		# optionally display or ignore it deterministically instead of treating
		# it as a generic 'unknown'. This also avoids spinner lock conditions
		# that previously depended on chat.text only.
		payload.update({
			"kind": "run_start",
			"agent": extract_agent_name(ev) or ctx.turn_agent,
			"message": "Workflow run initialized"
		})
		return payload

	# UsageSummaryEvent ------------------------------------------------
	if isinstance(ev, _US):
		usage_obj = getattr(ev, "content", None) or getattr(ev, "usage", None)
		normalized = serialize_event_content(usage_obj)
		payload.update({
			"kind": "usage_summary",
			"agent": extract_agent_name(ev) or ctx.turn_agent,
			"usage": normalized,
		})
		return payload

	# RunCompletionEvent -----------------------------------------------
	if isinstance(ev, _RC):
		# Extract comprehensive completion metadata from AG2's RunCompletionEvent
		summary = getattr(ev, "summary", None)
		history = getattr(ev, "history", None)
		cost = getattr(ev, "cost", None)
		last_speaker = getattr(ev, "last_speaker", None)
		context_vars = getattr(ev, "context_variables", None)
		
		payload.update({
			"kind": "run_complete",
			"agent": last_speaker or extract_agent_name(ev) or ctx.turn_agent,
			"status": 1,  # Workflow completed successfully
		})
		
		# Add optional metadata if available
		if summary:
			payload["summary"] = summary
		if cost:
			payload["cost"] = serialize_event_content(cost)
		
		# Extract duration and token usage from cost if available
		if isinstance(cost, dict):
			total_tokens = cost.get("total_tokens") or cost.get("usage", {}).get("total_tokens")
			if total_tokens:
				payload["total_tokens"] = total_tokens
		
		return payload

	# ErrorEvent -------------------------------------------------------
	if isinstance(ev, _EE):
		err_msg = getattr(ev, "message", None) or normalize_text_content(getattr(ev, "content", None))
		code = getattr(ev, "code", None) or getattr(ev, "error_code", None)
		payload.update({
			"kind": "error",
			"agent": extract_agent_name(ev) or ctx.turn_agent,
			"message": err_msg,
			"code": code,
		})
		return payload

	# Fallback marker
	payload.update({"kind": "unknown"})
	return payload

def build_structured_output_ready_event(
	agent: str,
	model_name: str,
	structured_data: Any,
	auto_tool_mode: bool,
	context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
	"""Build normalized payload for chat.structured_output_ready events."""
	return {
		"kind": "structured_output_ready",
		"agent": agent,
		"agent_name": agent,
		"model_name": model_name,
		"structured_data": serialize_event_content(structured_data),
		"auto_tool_mode": bool(auto_tool_mode),
		"context": context or {},
	}

__all__ = [
	"EventBuildContext",
	"normalize_text_content",
	"serialize_event_content",
	"extract_agent_name",
	"build_ui_event_payload",
	"build_structured_output_ready_event",
]
