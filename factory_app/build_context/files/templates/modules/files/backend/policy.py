from __future__ import annotations

from typing import Any


def files_scope(ctx, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the live-record query, merged with any extra fields that are set.

    ctx.persistence owns app scoping and rejects caller-supplied app_id filters.
    """
    scope: dict[str, Any] = {"deleted": {"$ne": True}}
    if extra:
        scope.update({key: value for key, value in extra.items() if value is not None})
    return scope


def actor_id(ctx) -> str:
    """File records belong to an identified caller, never a shared anonymous owner."""
    value = getattr(ctx, "user_id", None)
    if not isinstance(value, str) or not value.strip():
        raise PermissionError("Sign in to manage files.")
    return value.strip()


def readable_file_scope(ctx, *, file_id: str) -> dict[str, Any]:
    return files_scope(ctx, {
        "file_id": file_id,
        "$or": [
            {"created_by": {"$eq": actor_id(ctx), "$not": {"$type": "array"}}},
            {"is_public": {"$eq": True, "$not": {"$type": "array"}}},
        ],
    })


def owned_file_scope(ctx, *, file_id: str) -> dict[str, Any]:
    return files_scope(ctx, {
        "file_id": file_id,
        "created_by": {"$eq": actor_id(ctx), "$not": {"$type": "array"}},
    })
