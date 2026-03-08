"""Sandbox execution result contract."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SandboxExecutionResult(BaseModel):
    """Canonical sandbox execution result payload."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    output_json: dict[str, object] | None = None
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration_ms: int = 0
    artifacts: list[dict[str, object]] = Field(default_factory=list)


__all__ = ["SandboxExecutionResult"]
