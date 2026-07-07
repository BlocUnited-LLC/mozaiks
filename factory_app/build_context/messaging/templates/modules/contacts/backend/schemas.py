from __future__ import annotations

from datetime import UTC, datetime
from typing import TypedDict


class Contact(TypedDict):
    contact_id: str
    user_id: str           # the user who added the contact
    contact_user_id: str   # the user being followed/added
    status: str            # "active" | "removed"
    created_at: str
    updated_at: str


def timestamp_now() -> str:
    return datetime.now(UTC).isoformat()


def coerce_limit(limit: int | None, *, default: int = 20, maximum: int = 100) -> int:
    try:
        v = int(limit or default)
    except (TypeError, ValueError):
        return default
    return max(1, min(v, maximum))


def serialize_contact(doc: dict) -> dict:
    return {
        "contact_id": doc.get("contact_id"),
        "contact_user_id": doc.get("contact_user_id"),
        "status": doc.get("status"),
        "created_at": doc.get("created_at"),
    }
