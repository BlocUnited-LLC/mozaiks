"""
Message normalization and text extraction utilities for AG2 orchestration.

Purpose:
- Normalize AG2 messages to strict format
- Extract text content from complex AG2 payloads
- Serialize event content for transport
- Extract agent names from event objects
- Extract images from conversation history (AG2 message parsing)

Extracted from orchestration_patterns.py to reduce complexity and improve maintainability.
"""

from typing import Any, Dict, List, Optional
import re
import logging

logger = logging.getLogger(__name__)

__all__ = [
    'normalize_to_strict_ag2',
    'normalize_text_content',
    'serialize_event_content',
    'extract_agent_name',
    'safe_context_snapshot',
    'extract_images_from_conversation',
]


def normalize_to_strict_ag2(
    raw_msgs: Optional[List[Any]],
    *,
    default_user_name: str = "user",
) -> List[Dict[str, Any]]:
    """
    Ensure every message is in strict AG2 shape:
      {"role": "user"|"assistant", "name": "<exact agent name>", "content": <str|dict|list>}
    Assumes persisted messages already follow this; mainly fixes locally-seeded items.
    """
    if not raw_msgs:
        return []
    out: List[Dict[str, Any]] = []
    for m in raw_msgs:
        if not isinstance(m, dict):
            # ignore non-dicts
            continue

        role = m.get("role")
        name = m.get("name")
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
    if hasattr(raw, 'model_dump') and callable(getattr(raw, 'model_dump')):
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
        if hasattr(raw, 'model_dump') and callable(getattr(raw, 'model_dump')):
            return serialize_event_content(raw.model_dump())
    except Exception:
        pass
    try:
        if hasattr(raw, 'dict') and callable(getattr(raw, 'dict')):
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


def extract_agent_name(obj: Any) -> Optional[str]:
    """Best-effort extraction of an agent/sender name from AG2 event/message objects.

    Traverses nested structures (dicts, lists, dataclasses) and falls back to string pattern
    matching so that tool and agent messages surface their logical speaker in the UI.
    """

    def _scan(candidate: Any) -> Optional[str]:
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


def safe_context_snapshot(ctx) -> Dict[str, Any]:
    """Safe snapshot for verbose context logging (avoids secrets).
    
    Redacts sensitive keys and truncates long strings to prevent log bloat.
    """
    out: Dict[str, Any] = {}
    try:
        data = None
        if ctx is None:
            return {}
        if hasattr(ctx, 'data') and isinstance(getattr(ctx, 'data'), dict):
            data = getattr(ctx, 'data')
        elif hasattr(ctx, 'to_dict') and callable(getattr(ctx, 'to_dict')):
            data = ctx.to_dict()
        elif isinstance(ctx, dict):
            data = ctx
        if not isinstance(data, dict):
            return {"_repr": str(ctx)[:200]}
        for k, v in data.items():
            lk = k.lower()
            if any(s in lk for s in ("secret", "api", "key", "token", "password")):
                out[k] = "***REDACTED***"
                continue
            try:
                sv = str(v)
            except Exception:
                sv = "<non-serializable>"
            if isinstance(sv, str) and len(sv) > 300:
                sv = sv[:300] + "..."
            out[k] = sv
    except Exception as _snap_err:
        out["_error"] = f"snapshot_failed:{_snap_err}"  # pragma: no cover
    return out


def extract_images_from_conversation(sender, recipient):
    """Extract PIL images from agent conversation history (for image generation capabilities).
    
    Parses GPT-4V format messages where content is an array with image_url entries.
    
    Args:
        sender: Agent that sent messages (image generator)
        recipient: Agent that received messages
        
    Returns:
        List of PIL Image objects found in conversation
        
    Raises:
        ValueError: If no images found in message history
    """
    try:
        from autogen.agentchat.contrib import img_utils
    except ImportError:
        logger.error("[IMAGE_EXTRACT] Failed to import img_utils from autogen.agentchat.contrib")
        raise ImportError("autogen.agentchat.contrib.img_utils not available - install ag2[lmm]")
    
    images = []
    all_messages = sender.chat_messages.get(recipient, [])
    
    logger.debug(f"[IMAGE_EXTRACT] Scanning {len(all_messages)} messages from {sender.name} to {recipient.name}")
    
    for idx, message in enumerate(reversed(all_messages)):
        contents = message.get("content", [])
        
        # Handle both string and array content formats
        if isinstance(contents, str):
            continue
            
        if not isinstance(contents, list):
            logger.warning(f"[IMAGE_EXTRACT] Message {idx} has unexpected content type: {type(contents)}")
            continue
        
        for content_idx, content in enumerate(contents):
            if isinstance(content, str):
                continue
                
            if not isinstance(content, dict):
                continue
                
            if content.get("type") == "image_url":
                img_data = content.get("image_url", {}).get("url")
                if img_data:
                    try:
                        img = img_utils.get_pil_image(img_data)
                        images.append(img)
                        logger.info(f"[IMAGE_EXTRACT] Found image in message {idx}, content {content_idx}")
                    except Exception as img_err:
                        logger.warning(f"[IMAGE_EXTRACT] Failed to load image from message {idx}: {img_err}")
    
    if not images:
        logger.error(f"[IMAGE_EXTRACT] No images found in {len(all_messages)} messages")
        raise ValueError("No image data found in conversation history")
    
    logger.info(f"[IMAGE_EXTRACT] Successfully extracted {len(images)} images")
    return images

