from __future__ import annotations

from typing import Any, Dict, List


def _require_text(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _require_text_list(value: Any, *, field_name: str) -> List[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of strings")
    items = [str(item).strip() for item in value if str(item).strip()]
    if not items:
        raise ValueError(f"{field_name} must contain at least one value")
    return items


def ensure_thread_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "thread_id": str(payload.get("thread_id") or "").strip() or None,
        "title": _require_text(payload.get("title"), field_name="title"),
        "thread_type": str(payload.get("thread_type") or "direct").strip() or "direct",
        "participant_ids": _require_text_list(payload.get("participant_ids"), field_name="participant_ids"),
        "created_by": str(payload.get("created_by") or "").strip() or None,
    }


def ensure_message_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    attachments = payload.get("attachments") if isinstance(payload.get("attachments"), list) else []
    return {
        "message_id": str(payload.get("message_id") or "").strip() or None,
        "thread_id": _require_text(payload.get("thread_id"), field_name="thread_id"),
        "sender_id": str(payload.get("sender_id") or "").strip() or None,
        "recipient_ids": [str(item).strip() for item in payload.get("recipient_ids", []) if str(item).strip()] if isinstance(payload.get("recipient_ids"), list) else [],
        "body": _require_text(payload.get("body"), field_name="body"),
        "attachments": [str(item).strip() for item in attachments if str(item).strip()],
    }


def ensure_read_receipt_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "thread_id": _require_text(payload.get("thread_id"), field_name="thread_id"),
        "actor_id": str(payload.get("actor_id") or "").strip() or None,
        "message_id": _require_text(payload.get("message_id"), field_name="message_id"),
    }


def ensure_announcement_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "announcement_id": str(payload.get("announcement_id") or "").strip() or None,
        "title": _require_text(payload.get("title"), field_name="title"),
        "body": _require_text(payload.get("body"), field_name="body"),
        "audience_scope": _require_text(payload.get("audience_scope"), field_name="audience_scope"),
        "sent_by": str(payload.get("sent_by") or "").strip() or None,
    }


__all__ = [
    "ensure_announcement_payload",
    "ensure_message_payload",
    "ensure_read_receipt_payload",
    "ensure_thread_payload",
]