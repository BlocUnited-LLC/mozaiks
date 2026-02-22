"""Tool specification and invocation models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, kw_only=True)
class ToolSpec:
    """Pure metadata for a callable tool."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    auto_invoke: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class ToolCall:
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    task_id: str | None = None


@dataclass(slots=True, kw_only=True)
class ToolResult:
    tool_name: str
    success: bool
    output: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
