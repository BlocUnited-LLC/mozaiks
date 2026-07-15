from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypedDict
from uuid import uuid4

MAX_MESSAGE_LENGTH = 4000
MESSAGE_PREVIEW_LENGTH = 160

THREAD_TYPES = {"group", "direct", "support"}
THREAD_SCOPE_TYPES = {"app", "workspace"}
THREAD_STATUSES = {"open", "resolved", "archived"}


class ThreadRecord(TypedDict, total=False):
    thread_id: str
    scope_type: str
    scope_id: str | None
    subject_app_id: str | None
    title: str
    thread_type: str
    participant_ids: list[str]
    status: str
    created_by: str
    created_at: str
    updated_at: str
    last_message_at: str | None
    last_message: dict[str, Any] | None
    related_type: str | None
    related_id: str | None
    metadata: dict[str, Any]


class MessageRecord(TypedDict, total=False):
    message_id: str
    thread_id: str
    sender_id: str
    sender_role: str
    body: str
    message_type: str
    created_at: str
    edited_at: str | None
    is_deleted: bool
    metadata: dict[str, Any]


def timestamp_now() -> str:
    return datetime.now(UTC).isoformat()


def coerce_limit(value: Any, *, default: int = 50, maximum: int = 100) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(1, min(parsed, maximum))


def normalize_string(value: Any) -> str:
    return str(value or "").strip()


def normalize_participant_ids(values: list[str] | None, *, required_user_id: str | None = None) -> list[str]:
    raw = [required_user_id, *(values or [])] if required_user_id else list(values or [])
    return list(dict.fromkeys(user_id for user_id in (normalize_string(item) for item in raw) if user_id))


def normalize_thread_type(value: Any) -> str:
    thread_type = normalize_string(value) or "group"
    return thread_type if thread_type in THREAD_TYPES else "group"


def normalize_scope_type(value: Any) -> str:
    scope_type = normalize_string(value) or "app"
    return scope_type if scope_type in THREAD_SCOPE_TYPES else "app"


def normalize_status(value: Any) -> str:
    status = normalize_string(value) or "open"
    return status if status in THREAD_STATUSES else "open"


def message_preview(body: str) -> str:
    return body[:MESSAGE_PREVIEW_LENGTH]


def build_thread_record(
    *,
    created_by: str,
    scope_type: str = "app",
    scope_id: str | None = None,
    subject_app_id: str | None = None,
    title: str | None = None,
    participant_ids: list[str] | None = None,
    thread_type: str = "group",
    related_type: str | None = None,
    related_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ThreadRecord:
    now = timestamp_now()
    return {
        "thread_id": f"thr_{uuid4().hex}",
        "scope_type": normalize_scope_type(scope_type),
        "scope_id": normalize_string(scope_id) or None,
        "subject_app_id": normalize_string(subject_app_id) or None,
        "title": normalize_string(title),
        "thread_type": normalize_thread_type(thread_type),
        "participant_ids": normalize_participant_ids(participant_ids, required_user_id=created_by),
        "status": "open",
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
        "last_message_at": None,
        "last_message": None,
        "related_type": normalize_string(related_type) or None,
        "related_id": normalize_string(related_id) or None,
        "metadata": dict(metadata or {}),
    }


def build_message_record(
    *,
    thread_id: str,
    sender_id: str,
    body: str,
    sender_role: str = "user",
    message_type: str = "text",
    metadata: dict[str, Any] | None = None,
) -> MessageRecord:
    now = timestamp_now()
    return {
        "message_id": f"msg_{uuid4().hex}",
        "thread_id": thread_id,
        "sender_id": sender_id,
        "sender_role": normalize_string(sender_role) or "user",
        "body": body,
        "message_type": normalize_string(message_type) or "text",
        "created_at": now,
        "edited_at": None,
        "is_deleted": False,
        "metadata": dict(metadata or {}),
    }
