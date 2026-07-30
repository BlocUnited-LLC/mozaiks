from __future__ import annotations

from typing import Any


def files_scope(ctx, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a base query scope keyed by app_id, optionally merged with extra fields."""
    scope: dict[str, Any] = {"app_id": getattr(ctx, "app_id", None)}
    if extra:
        scope.update({key: value for key, value in extra.items() if value is not None})
    return scope
