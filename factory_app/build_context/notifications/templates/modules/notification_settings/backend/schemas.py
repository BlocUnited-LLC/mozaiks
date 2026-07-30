from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def timestamp_now() -> str:
    return datetime.now(UTC).isoformat()


def notification_preferences_doc(
    *,
    user_id: str,
    app_id: str | None,
    email_enabled: bool = True,
    push_enabled: bool = True,
    sms_enabled: bool = False,
) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "app_id": app_id,
        "email_enabled": email_enabled,
        "push_enabled": push_enabled,
        "sms_enabled": sms_enabled,
        "updated_at": timestamp_now(),
    }


def preferences_response(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "email_enabled": bool(doc.get("email_enabled", True)),
        "push_enabled": bool(doc.get("push_enabled", True)),
        "sms_enabled": bool(doc.get("sms_enabled", False)),
    }
