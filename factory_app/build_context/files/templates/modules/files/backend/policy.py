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
