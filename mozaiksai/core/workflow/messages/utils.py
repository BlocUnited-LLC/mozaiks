"""
Message normalization and text extraction utilities for AG2 orchestration.

Purpose:
- Normalize AG2 messages to strict format
- Extract text content from complex AG2 payloads
- Serialize event content for transport
- Extract agent names from event objects
Extracted from orchestration_patterns.py to reduce complexity and improve maintainability.
"""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    'normalize_to_strict_ag2',
    'normalize_text_content',
    'serialize_event_content',
    'extract_agent_name',
]


def normalize_to_strict_ag2(
    raw_msgs: list[Any] | None,
    *,
    default_user_name: str = "user",
) -> list[dict[str, Any]]:
    """
    Ensure every message is in strict AG2 shape:
      {"role": "user"|"assistant", "name": "<exact agent name>", "content": <str|dict|list>}
    Assumes persisted messages already follow this; mainly fixes locally-seeded items.
    """
    if not raw_msgs:
        return []
    out: list[dict[str, Any]] = []
    for m in raw_msgs:
        if not isinstance(m, dict):
            # ignore non-dicts
            continue

        role = m.get("role")
        name = m.get("name") or m.get("agent_name")
        content = m.get("content")

        # Accept strict messages as-is
        if role in ("user", "assistant") and isinstance(name, str) and name and content is not None:
            out.append({"role": role, "name": name, "content": content})
            continue

        # Try minimal fix-up for messages missing name/role (only for new seeds we add)
        # - If role == "user" and name missing -> set name to "user"
        # - If role missing but name == "user" -> set role to "user"
        # - Otherwise, if assistant-like seed comes through without name, we skip (cannot guess agent)
        if role == "user" and not name:
            name = default_user_name
        if not role and name == default_user_name:
            role = "user"

        if role in ("user", "assistant") and name and content is not None:
            out.append({"role": role, "name": name, "content": content})
        # else drop silently; strictness prevents bad resume
    return out


def normalize_text_content(raw: Any) -> str:
    """Convert AG2 text payloads (which may be dicts/BaseModels) into displayable strings."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if hasattr(raw, 'model_dump') and callable(raw.model_dump):
        try:
            return normalize_text_content(raw.model_dump())
        except Exception:
            pass
    if isinstance(raw, dict):
        for key in ('content', 'text', 'message'):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value
    if isinstance(raw, (list, tuple)):
        try:
            return ' '.join(str(x) for x in raw)
        except Exception:
            pass
    return str(raw)


def serialize_event_content(raw: Any) -> Any:
    """Best-effort conversion of AG2 event content into JSON-serializable structures."""
    if raw is None or isinstance(raw, (str, int, float, bool)):
        return raw
    try:
        if hasattr(raw, 'model_dump') and callable(raw.model_dump):
            return serialize_event_content(raw.model_dump())
    except Exception:
        pass
    try:
        if hasattr(raw, 'dict') and callable(raw.dict):
            return serialize_event_content(raw.dict())
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
    """Best-effort extraction of an agent/sender name from AG2 event/message objects.

    Traverses nested structures (dicts, lists, dataclasses) and falls back to string pattern
    matching so that tool and agent messages surface their logical speaker in the UI.
    """

    def _scan(candidate: Any) -> str | None:
        if candidate is None:
            return None
        if isinstance(candidate, str):
            value = candidate.strip()
            if not value:
                return None
            match = re.search(r"sender(?:=|\"\s*:)['\"]([^'\"\\]+)['\"]", value)
            if match:
                return match.group(1)
            if ' ' not in value and len(value) <= 64:
                return value
            return None
        if isinstance(candidate, dict):
            for key in ("sender", "agent", "agent_name", "name"):
                val = candidate.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
            for key in ("sender", "agent", "agent_name", "name", "content"):
                val = candidate.get(key)
                result = _scan(val)
                if result:
                    return result
            return None
        if isinstance(candidate, (list, tuple, set)):
            for item in candidate:
                result = _scan(item)
                if result:
                    return result
            return None
        for key in ("sender", "agent", "agent_name", "name"):
            attr = getattr(candidate, key, None)
            if isinstance(attr, str) and attr.strip():
                return attr.strip()
            result = _scan(attr)
            if result:
                return result
        content = getattr(candidate, "content", None)
        if content is not None:
            return _scan(content)
        return None

    try:
        return _scan(obj)
    except Exception:  # pragma: no cover
        return None





