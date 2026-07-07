from __future__ import annotations

from datetime import UTC, datetime
from typing import TypedDict

MAX_MESSAGE_LENGTH = 4000
MESSAGE_PREVIEW_LENGTH = 200

# Thread types
THREAD_TYPE_DM = "dm"
THREAD_TYPE_GROUP = "group"
THREAD_TYPES = {THREAD_TYPE_DM, THREAD_TYPE_GROUP}

# Thread statuses
THREAD_STATUS_OPEN = "open"
THREAD_STATUS_CLOSED = "closed"
THREAD_STATUS_ARCHIVED = "archived"
THREAD_STATUSES = {THREAD_STATUS_OPEN, THREAD_STATUS_CLOSED, THREAD_STATUS_ARCHIVED}


def timestamp_now() -> str:
    return datetime.now(UTC).isoformat()


def coerce_limit(value, default: int = 20, maximum: int = 100) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(1, min(parsed, maximum))


class LastMessagePreview(TypedDict):
    message_id: str
    sender_id: str
    body_preview: str
    sent_at: str


class Thread(TypedDict):
    thread_id: str
    title: str
    thread_type: str              # dm | group
    participant_ids: list[str]
    context_id: str | None        # optional reference to a related entity
    status: str                   # open | closed | archived
    created_by: str
    created_at: str
    updated_at: str
    last_message_at: str | None
    last_message: LastMessagePreview | None


class Message(TypedDict):
    message_id: str
    thread_id: str
    sender_id: str
    body: str
    message_type: str             # text | system
    created_at: str
    edited_at: str | None
    is_deleted: bool


class ReadState(TypedDict):
    thread_id: str
    user_id: str
    last_read_message_id: str | None
    read_at: str


class Notification(TypedDict):
    notification_id: str
    user_id: str
    event_type: str
    payload: dict
    read: bool
    created_at: str
