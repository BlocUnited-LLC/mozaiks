from __future__ import annotations

from typing import Any

from .policy import (
    is_generic_app_name,
    normalize_optional_text,
    validate_lifecycle_state,
    validate_name_source,
)


def ensure_create_payload(
    *,
    name: str | None = None,
    description: str | None = None,
    status: str = "draft",
    app_id: str | None = None,
    chat_app_id: str | None = None,
    active_chat_id: str | None = None,
    active_workflow_id: str | None = None,
    name_source: str | None = None,
    build_context_profile: dict[str, Any] | None = None,
    current_build_run: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_name = normalize_optional_text(name)
    has_real_name = not is_generic_app_name(normalized_name)
    return {
        "name": normalized_name if has_real_name else None,
        "description": normalize_optional_text(description),
        "status": validate_lifecycle_state(status or "draft"),
        "app_id": normalize_optional_text(app_id),
        "chat_app_id": normalize_optional_text(chat_app_id),
        "active_chat_id": normalize_optional_text(active_chat_id),
        "active_workflow_id": normalize_optional_text(active_workflow_id),
        "name_source": validate_name_source(name_source, has_real_name=has_real_name),
        "name_status": "named" if has_real_name else "provisional",
        "build_context_profile": _sanitize_object(build_context_profile),
        "current_build_run": _sanitize_object(current_build_run),
    }


def ensure_status_payload(
    *,
    build_registry_id: str,
    status: str,
    bundle_path: str | None = None,
    artifact_version_id: str | None = None,
    workflow_sequence: str | None = None,
    active_chat_id: str | None = None,
    active_workflow_id: str | None = None,
    current_build_run: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_record_id = normalize_optional_text(build_registry_id)
    if not normalized_record_id:
        raise ValueError("build_registry_id is required")
    return {
        "build_registry_id": normalized_record_id,
        "status": validate_lifecycle_state(status),
        "bundle_path": normalize_optional_text(bundle_path),
        "artifact_version_id": normalize_optional_text(artifact_version_id),
        "workflow_sequence": normalize_optional_text(workflow_sequence),
        "active_chat_id": normalize_optional_text(active_chat_id),
        "active_workflow_id": normalize_optional_text(active_workflow_id),
        "current_build_run": _sanitize_object(current_build_run),
    }


def _sanitize_object(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    sanitized = _sanitize_jsonish(value)
    return sanitized if isinstance(sanitized, dict) and sanitized else None


def _sanitize_jsonish(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        return text[:2000] if text else None
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:100]:
            key = str(raw_key).strip()
            if not key:
                continue
            sanitized = _sanitize_jsonish(raw_value, depth=depth + 1)
            if sanitized is not None:
                output[key[:160]] = sanitized
        return output
    if isinstance(value, (list, tuple)):
        list_output: list[Any] = []
        for item in list(value)[:100]:
            sanitized = _sanitize_jsonish(item, depth=depth + 1)
            if sanitized is not None:
                list_output.append(sanitized)
        return list_output
    return str(value)[:2000]
