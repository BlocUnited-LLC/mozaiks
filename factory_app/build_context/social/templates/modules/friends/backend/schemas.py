"""Type shapes and pure helpers for the friends module."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypedDict

# ── Status constants ─────────────────────────────────────────────────────────

REQUEST_STATUS_PENDING = "pending"
REQUEST_STATUS_ACCEPTED = "accepted"
REQUEST_STATUS_DECLINED = "declined"
REQUEST_STATUS_WITHDRAWN = "withdrawn"

FRIENDSHIP_STATUS_NONE = "none"
FRIENDSHIP_STATUS_FRIENDS = "friends"
FRIENDSHIP_STATUS_REQUEST_SENT = "request_sent"
FRIENDSHIP_STATUS_REQUEST_RECEIVED = "request_received"


# ── TypedDicts ───────────────────────────────────────────────────────────────

class FriendRequest(TypedDict, total=False):
    request_id: str
    requester_id: str
    requester_name: str
    recipient_id: str
    status: str          # pending | accepted | declined | withdrawn
    created_at: str
    updated_at: str


class Friendship(TypedDict, total=False):
    friendship_id: str
    user_id: str
    friend_user_id: str
    since: str           # ISO timestamp of when the friendship was created
    created_at: str


# ── Helpers ──────────────────────────────────────────────────────────────────

def timestamp_now() -> str:
    return datetime.now(UTC).isoformat()


def coerce_limit(value: Any, default: int = 20, maximum: int = 100) -> int:
    try:
        return max(1, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def safe_str(value: Any) -> str:
    """Return a non-empty stripped string or raise ValueError."""
    s = str(value or "").strip()
    if not s:
        raise ValueError("value must be a non-empty string")
    return s
