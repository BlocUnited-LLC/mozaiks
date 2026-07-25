from __future__ import annotations

from typing import Any

from mozaiksai.control_plane.contracts import ControlPlaneToolContext


def normalize_context(context: ControlPlaneToolContext | dict[str, Any] | None) -> ControlPlaneToolContext:
    if isinstance(context, ControlPlaneToolContext):
        return context
    return ControlPlaneToolContext.model_validate(dict(context or {}))


def text_excerpt(value: Any, *, max_length: int = 400) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) <= max_length:
        return text
    return text[: max_length - 3].rstrip() + "..."


def list_head(values: Any, *, limit: int = 8) -> list[Any]:
    if not isinstance(values, list):
        return []
    return list(values[:limit])
