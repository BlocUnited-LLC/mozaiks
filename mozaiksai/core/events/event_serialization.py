# ==============================================================================
# FILE: mozaiksai/core/events/event_serialization.py
# DESCRIPTION: Shared serialization helpers for AG2 runtime events.
# ==============================================================================
"""AG2 Runtime Event Serialization Helpers

Utility functions used by the event dispatcher, auto-tool handler, and
runtime event builders to normalize and serialize AG2 event content.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


def normalize_text_content(raw: Any) -> str:
	"""Convert AG2 text payload variants into a displayable string."""
	if raw is None:
		return ""
	if isinstance(raw, str):
		return raw
	if hasattr(raw, 'model_dump') and callable(raw.model_dump):
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
	if isinstance(raw, Enum):
		return serialize_event_content(raw.value)
	try:
		if hasattr(raw, 'model_dump') and callable(raw.model_dump):
			return serialize_event_content(raw.model_dump())  # type: ignore[attr-defined]
	except Exception:
		pass
	try:
		if hasattr(raw, 'dict') and callable(raw.dict):
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


def extract_agent_name(obj: Any) -> str | None:
	"""Attempt to extract the logical agent/sender name from diverse AG2 objects."""
	try:
		if isinstance(obj, dict):
			for k in ("sender", "agent", "agent_name", "name"):
				v = obj.get(k)
				if isinstance(v, str) and v.strip():
					return v.strip()
			for k in ("sender", "agent", "agent_name", "name", "content"):
				v = obj.get(k)
				if isinstance(v, dict):
					inner = extract_agent_name(v)
					if inner:
						return inner
				if isinstance(v, str):
					import re
					m = re.search(r"sender(?:=|\"\s*:)['\"]([^'\"\\]+)['\"]", v)
					if m:
						return m.group(1).strip()
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


__all__ = [
	"normalize_text_content",
	"serialize_event_content",
	"extract_agent_name",
]
