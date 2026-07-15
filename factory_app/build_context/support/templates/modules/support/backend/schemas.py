from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypedDict
from uuid import uuid4

SUPPORT_STATUSES = {"open", "waiting", "resolved", "archived"}
SUPPORT_SEVERITIES = {"low", "medium", "high"}


class SupportRequestRecord(TypedDict, total=False):
    request_id: str
    subject_app_id: str
    requester_id: str | None
    subject: str
    message: str
    page_url: str | None
    page_title: str | None
    severity: str
    status: str
    assignee_id: str | None
    message_thread_id: str | None
    created_at: str
    updated_at: str
    resolved_at: str | None


def timestamp_now() -> str:
    return datetime.now(UTC).isoformat()


def normalize_string(value: Any) -> str:
    return str(value or "").strip()


def normalize_status(value: Any) -> str:
    status = normalize_string(value) or "open"
    return status if status in SUPPORT_STATUSES else "open"


def normalize_severity(value: Any) -> str:
    severity = normalize_string(value) or "low"
    return severity if severity in SUPPORT_SEVERITIES else "low"


def coerce_limit(value: Any, *, default: int = 50, maximum: int = 100) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(1, min(parsed, maximum))


def build_support_request_record(
    *,
    subject_app_id: str,
    requester_id: str | None,
    message: str,
    subject: str | None = None,
    page_url: str | None = None,
    page_title: str | None = None,
    severity: str = "low",
    message_thread_id: str | None = None,
) -> SupportRequestRecord:
    now = timestamp_now()
    clean_message = normalize_string(message)
    return {
        "request_id": f"sr_{uuid4().hex}",
        "subject_app_id": normalize_string(subject_app_id),
        "requester_id": normalize_string(requester_id) or None,
        "subject": normalize_string(subject) or (clean_message[:80] if clean_message else "Support request"),
        "message": clean_message,
        "page_url": normalize_string(page_url) or None,
        "page_title": normalize_string(page_title) or None,
        "severity": normalize_severity(severity),
        "status": "open",
        "assignee_id": None,
        "message_thread_id": normalize_string(message_thread_id) or None,
        "created_at": now,
        "updated_at": now,
        "resolved_at": None,
    }
