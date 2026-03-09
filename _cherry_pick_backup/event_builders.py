"""AG2 → UI payload event builders.

This is the canonical location for all code that imports AG2 event classes
and converts them to UI-ready payload dicts.  Moved here from
``mozaiksai.engine.events.serialization`` so that all AG2 event-class
imports are contained within the adapter layer.

The ``engine/events/serialization.py`` module is now a compat shim that
re-exports from here; existing callers continue to work unchanged.

AG2 event types handled
-----------------------
TextEvent, PrintEvent, InputRequestEvent, SelectSpeakerEvent,
FunctionCallEvent, ToolCallEvent, FunctionResponseEvent, ToolResponseEvent,
GroupChatResumeEvent, GroupChatRunChatEvent, UsageSummaryEvent,
RunCompletionEvent, ErrorEvent, custom BaseEvent subclasses.

Public API
----------
EventBuildContext      — context dataclass passed into the builder
build_ui_event_payload — main dispatcher; returns UI dict or None
build_structured_output_ready_event — payload for structured-output events
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from dataclasses import dataclass
import logging

# ---------------------------------------------------------------------------
# AG2 event imports — ALL contained here inside the adapter layer
# ---------------------------------------------------------------------------
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
from autogen.events.base_event import BaseEvent as _BE
from autogen.events.print_event import PrintEvent as _PE

from mozaiksai.engine.validation.tools import (
	SENTINEL_AGENT_KEY,
	SENTINEL_ERRORS_KEY,
	SENTINEL_EXPECTED_MODEL_KEY,
	SENTINEL_FLAG,
	SENTINEL_MESSAGE_KEY,
	SENTINEL_STATUS,
	SENTINEL_TOOL_KEY,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Context dataclass — pure Python, no AG2 dependencies
# ---------------------------------------------------------------------------

@dataclass
class EventBuildContext:
	workflow_name: str
	turn_agent: Optional[str]
	tool_call_initiators: Dict[str, str]
	tool_names_by_id: Dict[str, str]
	workflow_name_upper: str
	wf_logger: Any  # lazy typed to avoid circular import with logging setup
	# Per-event metadata set by the orchestration loop before calling build_ui_event_payload.
	# These replace the previous pattern of setattr()-ing annotations onto raw AG2 event objects.
	pending_request_id: Optional[str] = None
	pending_prompt_hint: Optional[str] = None


# ---------------------------------------------------------------------------
# Content normalization helpers (no AG2 imports needed)
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
# Main builder — all AG2 isinstance checks live here
# ---------------------------------------------------------------------------

def build_ui_event_payload(*, ev: Any, ctx: EventBuildContext) -> Optional[Dict[str, Any]]:
	"""Return a UI payload dict for a single AG2 event (or None if unsupported)."""
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
				from mozaiksai.engine.outputs.structured import (
					agent_has_structured_output,
					get_structured_output_model_fields,
				)
				if agent_has_structured_output(ctx.workflow_name, sender):
					ctx.wf_logger.debug(f" [STRUCTURED_DEBUG] agent={sender} has_structured_output=True, clean_content_len={len(clean_content) if clean_content else 0}")
					ctx.wf_logger.debug(f" [STRUCTURED_DEBUG] clean_content_preview: {clean_content[:200] if clean_content else 'None'}...")
					from mozaiksai.runtime.data.persistence.persistence_manager import AG2PersistenceManager as _PM  # lazy import
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
							elif isinstance(structured, list):
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
		prompt_text = ctx.pending_prompt_hint or getattr(ev, "_mozaiks_prompt", None) or getattr(ev, "prompt", None)
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
		# Prefer the request_id forwarded through EventBuildContext (set by orchestration
		# loop without mutating the raw AG2 event).  Fall back to legacy setattr pattern
		# for any callers that still use the old path.
		request_id = ctx.pending_request_id or getattr(ev, "_mozaiks_request_id", None)
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
			"selected_speaker": selected,
		})
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
		payload.update({
			"kind": "run_start",
			"agent": extract_agent_name(ev) or ctx.turn_agent,
			"message": "Workflow run initialized",
		})
		return payload

	# UsageSummaryEvent -----------------------------------------------
	if isinstance(ev, _US):
		usage_obj = getattr(ev, "content", None) or getattr(ev, "usage", None)
		normalized = serialize_event_content(usage_obj)
		payload.update({
			"kind": "usage_summary",
			"agent": extract_agent_name(ev) or ctx.turn_agent,
			"usage": normalized,
		})
		return payload

	# RunCompletionEvent ----------------------------------------------
	if isinstance(ev, _RC):
		summary = getattr(ev, "summary", None)
		history = getattr(ev, "history", None)
		cost = getattr(ev, "cost", None)
		last_speaker = getattr(ev, "last_speaker", None)
		context_vars = getattr(ev, "context_variables", None)

		payload.update({
			"kind": "run_complete",
			"agent": last_speaker or extract_agent_name(ev) or ctx.turn_agent,
			"status": 1,
		})
		if summary:
			payload["summary"] = summary
		if cost:
			payload["cost"] = serialize_event_content(cost)
		if isinstance(cost, dict):
			total_tokens = cost.get("total_tokens") or cost.get("usage", {}).get("total_tokens")
			if total_tokens:
				payload["total_tokens"] = total_tokens
		return payload

	# ErrorEvent ------------------------------------------------------
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

	# Custom BaseEvent ------------------------------------------------
	custom_content = getattr(ev, "content", None)
	if isinstance(custom_content, _BE):
		payload.update({
			"kind": "custom_event",
			"event_name": custom_content.__class__.__name__,
			"agent": extract_agent_name(ev) or ctx.turn_agent,
			"content": serialize_event_content(custom_content),
		})
		return payload

	# Fallback marker
	payload.update({"kind": "unknown"})
	return payload


def build_select_speaker_ui_dict(domain_ev: Any) -> Dict[str, Any]:
	"""Build a serializable UI dict from a SELECT_SPEAKER DomainEvent.

	Replaces ``transport.send_event_to_ui(raw_ag2_ev, ...)`` in the
	orchestration loop with a clean dict so no raw AG2 event objects
	cross the engine → transport boundary.
	"""
	return {
		"kind": "select_speaker",
		"agent": domain_ev.agent,
		"candidates": domain_ev.metadata.get("candidates"),
		"selected": domain_ev.metadata.get("selected_agent"),
	}


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
	"build_select_speaker_ui_dict",
	"build_structured_output_ready_event",
]
