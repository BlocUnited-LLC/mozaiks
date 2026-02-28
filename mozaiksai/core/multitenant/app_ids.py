from __future__ import annotations

from typing import Any, Dict, Mapping, Optional


def normalize_app_id(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed or None
    return str(value) or None


def coalesce_app_id(*, app_id: Any = None) -> Optional[str]:
    """Normalize app_id value from mixed sources."""

    return normalize_app_id(app_id)


def build_app_scope_filter(app_id: str) -> Dict[str, Any]:
    """Mongo filter matching canonical scope field."""

    normalized = normalize_app_id(app_id)
    if not normalized:
        return {"app_id": "__invalid__"}
    return {"app_id": normalized}


def dual_write_app_scope(doc: Dict[str, Any], app_id: str) -> Dict[str, Any]:
    """Write canonical scope key."""

    normalized = normalize_app_id(app_id)
    if not normalized:
        return doc
    doc["app_id"] = normalized
    return doc


def extract_app_id(payload: Mapping[str, Any], *, default: Optional[str] = None) -> Optional[str]:
    """Extract app_id from mixed-casing payloads (HTTP/WS/UI)."""

    for key in ("app_id", "appId", "AppId"):
        if key in payload:
            resolved = normalize_app_id(payload.get(key))
            if resolved:
                return resolved
    return default


__all__ = [
    "normalize_app_id",
    "coalesce_app_id",
    "build_app_scope_filter",
    "dual_write_app_scope",
    "extract_app_id",
]
