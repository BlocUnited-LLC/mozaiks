"""Versioned AI runner request contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

AI_RUNNER_PROTOCOL_VERSION = "1.0.0"


class RunRequest(BaseModel):
    """Request payload for starting a workflow run."""

    run_id: str = Field(..., min_length=1)
    workflow_name: str = Field(..., min_length=1)
    workflow_version: str = Field(default="1.0.0", min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    app_id: str | None = None
    user_id: str | None = None
    chat_id: str | None = None
    tool_specs: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_payload(cls, raw: Any) -> Any:
        if not isinstance(raw, dict):
            return raw
        if "payload" not in raw and "input" in raw:
            updated = dict(raw)
            updated["payload"] = raw["input"]
            return updated
        return raw

    @property
    def input(self) -> dict[str, Any]:
        return self.payload


class ResumeRequest(BaseModel):
    """Request payload for resuming a workflow run."""

    run_id: str = Field(..., min_length=1)
    last_seq: int = Field(default=0, ge=0)
    workflow_name: str = Field(default="unknown", min_length=1)
    workflow_version: str | None = None
    checkpoint_id: str = Field(default="latest", min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    app_id: str | None = None
    user_id: str | None = None
    chat_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_checkpoint_id(cls, raw: Any) -> Any:
        if not isinstance(raw, dict):
            return raw
        updated = dict(raw)
        if "last_seq" not in updated and "from_seq" in updated:
            updated["last_seq"] = updated["from_seq"]
        if "checkpoint_id" not in raw and "checkpoint_key" in raw:
            updated["checkpoint_id"] = raw["checkpoint_key"]
        return updated

    @property
    def checkpoint_key(self) -> str:
        return self.checkpoint_id


__all__ = ["AI_RUNNER_PROTOCOL_VERSION", "ResumeRequest", "RunRequest"]
