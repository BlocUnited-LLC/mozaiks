from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class ControlPlaneToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    target: Optional[str] = None


class ControlPlaneToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool = True
    output: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None


class ControlPlaneToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    description: str
    entrypoint: str
    available_to: list[str] = Field(default_factory=list)
