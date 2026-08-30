from __future__ import annotations

from typing import Any


def normalize_app_id(value: Any) -> str | None:
    if value is None:
        return None
    # bool is a subclass of int, so True/False would otherwise become the
    # scope strings "True"/"False" and silently pass the tenant boundary.
    # A boolean is never a valid app id, so reject it up front.
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed or None
    trimmed = str(value).strip()
    return trimmed or None


def coalesce_app_id(*, app_id: Any = None) -> str | None:
    """Normalize app_id value from mixed sources."""

    return normalize_app_id(app_id)


def build_app_scope_filter(app_id: str) -> dict[str, Any]:
    """Mongo filter matching canonical scope field."""

    normalized = normalize_app_id(app_id)
    if not normalized:
        return {"app_id": "__invalid__"}
    return {"app_id": normalized}


def dual_write_app_scope(doc: dict[str, Any], app_id: str) -> dict[str, Any]:
    """Write canonical scope key."""

    normalized = normalize_app_id(app_id)
    if not normalized:
        return doc
    doc["app_id"] = normalized
    return doc


__all__ = [
    "normalize_app_id",
    "coalesce_app_id",
    "build_app_scope_filter",
    "dual_write_app_scope",
]
