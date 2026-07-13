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
APP_NAME_SOURCES = ("provisional", "value_engine_concept", "imported_app", "manual")
APP_NAME_SOURCE_SET = set(APP_NAME_SOURCES)
GENERIC_APP_NAMES = {"new app", "untitled app", "untitled", "app", "my app"}


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


def is_generic_app_name(value: object) -> bool:
    text = normalize_optional_text(value)
    return text is None or text.lower() in GENERIC_APP_NAMES


def validate_name_source(value: str | None, *, has_real_name: bool) -> str:
    normalized = str(value or "").strip().lower()
    if normalized:
        if normalized not in APP_NAME_SOURCE_SET:
            raise ValueError("name_source must be one of: " + ", ".join(APP_NAME_SOURCES))
        return normalized
    return "manual" if has_real_name else "provisional"
