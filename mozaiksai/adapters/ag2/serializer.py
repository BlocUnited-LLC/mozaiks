# ==============================================================================
# FILE: mozaiksai/adapters/ag2/serializer.py
# DESCRIPTION: AG2 event serialization — converts AG2 objects to JSON-safe dicts.
#
# This is the single place in the codebase that knows about AG2 event class
# shapes.  Transport components receive only JSON-safe dicts; they must never
# import or pattern-match on AG2 types.
#
# Extracted from:
#   - transport/websocket/connection_manager.py  (serialize_ag2_events, stringify_unknown)
#   - transport/websocket/event_sender.py        (extract_event_sender_name)
# ==============================================================================
from __future__ import annotations

import json
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Low-level string conversion
# ---------------------------------------------------------------------------

def stringify_unknown(obj: Any) -> str:
    """Safely convert any object to a string for logging / transport."""
    try:
        if obj is None:
            return ""
        if isinstance(obj, (str, int, float, bool)):
            return str(obj)
        # Try JSON first with default=str to preserve structure
        return json.dumps(obj, default=str)
    except Exception:
        try:
            return str(obj)
        except Exception:
            return "<unserializable>"


# ---------------------------------------------------------------------------
# AG2 event object → JSON-safe dict
# ---------------------------------------------------------------------------

def serialize_ag2_object(obj: Any) -> Any:
    """Convert an AG2 event object (or any value) to a JSON-serializable form.

    This is the canonical serialization path for AG2 objects before they are
    handed off to the transport layer.  Transport components must call this
    (or receive already-serialized dicts) and must never call it themselves.

    Parameters
    ----------
    obj : Any
        An AG2 event object, primitive, dict, or list.

    Returns
    -------
    Any
        A JSON-serializable value (dict, list, str, int, float, bool, or None).
    """
    try:
        # Primitive fast-path
        if obj is None or isinstance(obj, (str, int, float, bool)):
            return obj

        # Dict / list recursive handling
        if isinstance(obj, dict):
            return {k: serialize_ag2_object(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [serialize_ag2_object(v) for v in list(obj)]

        # -- AG2 helper: extract sender name from event object ---------------
        def _extract_sender(o: Any) -> str:
            s = getattr(o, "sender", None)
            if s is None:
                content_obj = getattr(o, "content", None)
                s = getattr(content_obj, "sender", None)
                if s is None and isinstance(content_obj, dict):
                    s = content_obj.get("sender")
            try:
                if s is not None and hasattr(s, "name"):
                    return getattr(s, "name")
            except Exception:
                pass
            return stringify_unknown(s)

        # -- AG2 helper: extract recipient name from event object -------------
        def _extract_recipient(o: Any) -> str:
            r = getattr(o, "recipient", None)
            if r is None:
                content_obj = getattr(o, "content", None)
                r = getattr(content_obj, "recipient", None)
                if r is None and isinstance(content_obj, dict):
                    r = content_obj.get("recipient")
            try:
                if r is not None and hasattr(r, "name"):
                    return getattr(r, "name")
            except Exception:
                pass
            return stringify_unknown(r)

        cls_name = obj.__class__.__name__
        event_type_token = getattr(obj, "type", None)
        if isinstance(event_type_token, str):
            event_type_token = event_type_token.strip().lower()
        else:
            event_type_token = None

        # -- TextEvent -------------------------------------------------------
        try:
            if event_type_token == "text" or "TextEvent" in cls_name:
                return {
                    "uuid": str(getattr(obj, "uuid", "")),
                    "content": stringify_unknown(getattr(obj, "content", None)),
                    "sender": _extract_sender(obj),
                    "recipient": _extract_recipient(obj),
                    "_ag2_event_type": "TextEvent",
                }
        except Exception:
            pass

        # -- InputRequestEvent -----------------------------------------------
        if event_type_token == "input_request":
            prompt_value = getattr(obj, "prompt", None)
            if prompt_value is None:
                content_obj = getattr(obj, "content", None)
                prompt_value = getattr(content_obj, "prompt", None)
                if prompt_value is None and isinstance(content_obj, dict):
                    prompt_value = content_obj.get("prompt") or content_obj.get("message")
            return {
                "uuid": str(
                    getattr(obj, "uuid", None)
                    or getattr(getattr(obj, "content", None), "uuid", "")
                    or ""
                ),
                "prompt": stringify_unknown(prompt_value),
                "password": None,  # never forward secrets
                "type": stringify_unknown(getattr(obj, "type", None)),
                "_ag2_event_type": "InputRequestEvent",
            }

        # -- ToolResponseEvent -----------------------------------------------
        if event_type_token == "tool_response":
            tool_name = getattr(obj, "tool_name", None)
            content_obj = getattr(obj, "content", None)
            if tool_name is None:
                responses = getattr(content_obj, "tool_responses", None)
                if isinstance(responses, list) and responses:
                    first_response = responses[0]
                    tool_name = (
                        getattr(first_response, "name", None)
                        or getattr(first_response, "tool_name", None)
                    )
            return {
                "uuid": str(
                    getattr(obj, "uuid", None)
                    or getattr(getattr(obj, "content", None), "uuid", "")
                    or ""
                ),
                "tool_name": stringify_unknown(tool_name),
                "content": stringify_unknown(
                    content_obj if content_obj is not None else getattr(obj, "result", None)
                ),
                "sender": _extract_sender(obj),
                "recipient": _extract_recipient(obj),
                "_ag2_event_type": "ToolResponseEvent",
            }

        # -- Generic event-like objects with a small public attribute surface -
        public_attrs: dict = {}
        attr_count = 0
        for name in dir(obj):
            if name.startswith("_"):
                continue
            if attr_count > 25:
                break
            try:
                value = getattr(obj, name)
            except Exception:
                continue
            if callable(value):
                continue
            attr_count += 1
            public_attrs[name] = serialize_ag2_object(value)

        if public_attrs:
            public_attrs["_ag2_event_type"] = cls_name
            return public_attrs

        # Fallback textual representation
        return stringify_unknown(obj)

    except Exception:
        return stringify_unknown(obj)


# ---------------------------------------------------------------------------
# Sender name extraction from AG2 event objects
# ---------------------------------------------------------------------------

def extract_sender_name(event: Any) -> Optional[str]:
    """Best-effort extraction of the sending agent name from an AG2 event object.

    This is the AG2-aware implementation.  Transport components must not
    perform this traversal themselves.

    Parameters
    ----------
    event : Any
        An AG2 event object, dict, or DomainEvent.

    Returns
    -------
    Optional[str]
        Agent name string, or None if not determinable.
    """
    if event is None:
        return None

    # DomainEvent has a direct .agent attribute
    agent_direct = getattr(event, "agent", None)
    if isinstance(agent_direct, str) and agent_direct.strip():
        return agent_direct.strip()

    # Dict payloads (from iostream_bridge, send_chat_message, etc.)
    if isinstance(event, dict):
        for key in ("agent", "agent_name", "sender"):
            val = event.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return None

    # AG2 event object: walk .sender attribute path
    sender_obj = getattr(event, "sender", None)
    if isinstance(sender_obj, str) and sender_obj.strip():
        return sender_obj.strip()
    if sender_obj is not None:
        sender_name = getattr(sender_obj, "name", None)
        if isinstance(sender_name, str) and sender_name.strip():
            return sender_name.strip()

    # Nested in .content
    content_obj = getattr(event, "content", None)
    if isinstance(content_obj, dict):
        sender = content_obj.get("sender")
        if isinstance(sender, str) and sender.strip():
            return sender.strip()
    else:
        nested_sender = getattr(content_obj, "sender", None)
        if isinstance(nested_sender, str) and nested_sender.strip():
            return nested_sender.strip()

    return None


__all__ = [
    "stringify_unknown",
    "serialize_ag2_object",
    "extract_sender_name",
]
