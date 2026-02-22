"""Tool execution request/response contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

TOOL_EXECUTION_SCHEMA_VERSION = "1.0.0"


class ToolExecutionRequest(BaseModel):
    """Contract for deterministic tool invocation requests."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(..., min_length=1)
    seq: int = Field(..., ge=0)
    tool_name: str = Field(..., min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] | None = None


class ToolExecutionResult(BaseModel):
    """Contract for deterministic tool invocation results."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    output: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: int = Field(default=0, ge=0)
    schema_version: str = Field(default=TOOL_EXECUTION_SCHEMA_VERSION, min_length=1)


__all__ = [
    "TOOL_EXECUTION_SCHEMA_VERSION",
    "ToolExecutionRequest",
    "ToolExecutionResult",
]
