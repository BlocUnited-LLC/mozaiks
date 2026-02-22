"""Helper for registering UI tools with canonical metadata."""

from __future__ import annotations

from typing import Any

from ..domain.tool_spec import ToolSpec
from .registry import ToolHandler, ToolRegistry


def use_ui_tool(
    registry: ToolRegistry,
    *,
    name: str,
    description: str,
    handler: ToolHandler,
    component: str,
    mode: str,
    blocking: bool = True,
    input_schema: dict[str, Any] | None = None,
    auto_invoke: bool = True,
    metadata: dict[str, Any] | None = None,
) -> ToolSpec:
    merged_metadata: dict[str, Any] = dict(metadata or {})
    merged_metadata["is_ui_tool"] = True
    merged_metadata["tool_type"] = "UI_Tool"
    merged_metadata["ui_component"] = component
    merged_metadata["ui_mode"] = mode
    merged_metadata["blocking"] = blocking
    merged_metadata["ui"] = {
        "component": component,
        "mode": mode,
        "blocking": blocking,
    }
    return registry.register_callable(
        name=name,
        description=description,
        handler=handler,
        input_schema=input_schema,
        auto_invoke=auto_invoke,
        metadata=merged_metadata,
    )


__all__ = ["use_ui_tool"]
