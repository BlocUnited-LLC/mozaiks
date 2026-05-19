from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping
from typing import Any

_SAFE_IDENTIFIER_PATTERN = re.compile(r"[^a-z0-9_]+")
_UNDERSCORE_PATTERN = re.compile(r"_+")
_MAX_COLLECTION_NAME_LENGTH = 120
_APP_SLUG_LENGTH = 32
_SEGMENT_LENGTH = 32
_HASH_LENGTH = 8


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip() if value is not None else ""
    if not normalized:
        raise ValueError(f"{name} is required")
    return normalized


def safe_identifier(value: Any, *, default: str = "item", max_length: int = _SEGMENT_LENGTH) -> str:
    """Return a Mongo-safe readable identifier segment."""

    text = str(value).strip() if value is not None else ""
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    safe = _SAFE_IDENTIFIER_PATTERN.sub("_", ascii_text)
    safe = _UNDERSCORE_PATTERN.sub("_", safe).strip("_")
    if not safe:
        safe = default
    return safe[:max_length].rstrip("_") or default


def short_stable_hash(value: Any, *, length: int = _HASH_LENGTH) -> str:
    """Return a deterministic short hash. This intentionally does not use hash()."""

    normalized = _require_non_empty("value", str(value))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return digest[:length]


def collection_name_for(
    *,
    app_id: str,
    module_id: str,
    entity_name: str,
    app_slug: str | None = None,
) -> str:
    """Build the deterministic collection name for generated module persistence."""

    normalized_app_id = _require_non_empty("app_id", app_id)
    normalized_module_id = _require_non_empty("module_id", module_id)
    normalized_entity_name = _require_non_empty("entity_name", entity_name)

    slug_source = app_slug if app_slug is not None else "app"
    safe_app_slug = safe_identifier(slug_source, default="app", max_length=_APP_SLUG_LENGTH)
    app_hash = short_stable_hash(normalized_app_id)
    module_segment = safe_identifier(normalized_module_id, max_length=_SEGMENT_LENGTH)
    entity_segment = safe_identifier(normalized_entity_name, max_length=_SEGMENT_LENGTH)

    collection_name = f"app_{safe_app_slug}_{app_hash}__{module_segment}__{entity_segment}"
    if len(collection_name) > _MAX_COLLECTION_NAME_LENGTH:
        return collection_name[:_MAX_COLLECTION_NAME_LENGTH].rstrip("_")
    return collection_name


def scope_filter_for(app_id: str, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return an app-scoped query filter. Extra filters may not override app_id."""

    normalized_app_id = _require_non_empty("app_id", app_id)
    if extra and "app_id" in extra:
        raise ValueError("extra filters cannot override app_id")
    return {"app_id": normalized_app_id, **dict(extra or {})}


def scope_metadata(
    app_id: str,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, str]:
    """Return canonical app scope metadata for persisted documents."""

    metadata = {"app_id": _require_non_empty("app_id", app_id)}
    optional_values = {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "user_id": user_id,
    }
    for key, value in optional_values.items():
        normalized = str(value).strip() if value is not None else ""
        if normalized:
            metadata[key] = normalized
    return metadata


__all__ = [
    "collection_name_for",
    "safe_identifier",
    "scope_filter_for",
    "scope_metadata",
    "short_stable_hash",
]
