from __future__ import annotations

APP_LIFECYCLE_STATES = (
    "draft",
    "building",
    "review",
    "configuring",
    "deploying",
    "active",
    "needs_revision",
    "archived",
)

APP_LIFECYCLE_STATE_SET = set(APP_LIFECYCLE_STATES)


def validate_lifecycle_state(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in APP_LIFECYCLE_STATE_SET:
        raise ValueError(
            "status must be one of: " + ", ".join(APP_LIFECYCLE_STATES)
        )
    return normalized


def normalize_optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None
