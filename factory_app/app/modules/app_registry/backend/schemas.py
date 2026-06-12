from __future__ import annotations

from typing import Any

from .policy import normalize_optional_text, validate_lifecycle_state


def ensure_create_payload(
    *,
    name: str,
    description: str | None = None,
    status: str = "draft",
    app_id: str | None = None,
) -> dict[str, Any]:
    normalized_name = normalize_optional_text(name)
    if not normalized_name:
        raise ValueError("name is required")
    return {
        "name": normalized_name,
        "description": normalize_optional_text(description),
        "status": validate_lifecycle_state(status or "draft"),
        "app_id": normalize_optional_text(app_id),
    }


def ensure_status_payload(
    *,
    build_registry_id: str,
    status: str,
    bundle_path: str | None = None,
) -> dict[str, Any]:
    normalized_record_id = normalize_optional_text(build_registry_id)
    if not normalized_record_id:
        raise ValueError("build_registry_id is required")
    return {
        "build_registry_id": normalized_record_id,
        "status": validate_lifecycle_state(status),
        "bundle_path": normalize_optional_text(bundle_path),
    }
