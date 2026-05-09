from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ControlPlaneToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    target: Optional[str] = None


class ControlPlaneToolContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoint: Optional[str] = None
    app_id: Optional[str] = None
    user_id: Optional[str] = None
    artifact_kind: Optional[str] = None
    artifact_key: Optional[str] = None
    artifact_version_id: Optional[str] = None
    requested_workflow_id: Optional[str] = None
    source_surface: Optional[str] = None
    raw_user_request: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


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


class CodingWorkerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_id: str
    user_id: Optional[str] = None
    artifact_kind: str
    artifact_key: Optional[str] = None
    artifact_version_id: Optional[str] = None
    requested_workflow_id: Optional[str] = None
    raw_user_request: str = ""
    source_surface: Optional[str] = None
    change_class: str
    files: dict[str, str] = Field(default_factory=dict)
    validation_strategy: Optional[str] = None
    start_preview: bool = False
    context_seed: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScopeProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution: Literal["scoped_files", "clarify", "workflow"] = "scoped_files"
    selected_paths: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    clarification_question: Optional[str] = None
    signals: list[str] = Field(default_factory=list)


class HarnessDecisionAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    action_type: Literal["confirm_workflow", "run_workflow", "clarify", "review_patch", "apply_scope"] = "run_workflow"
    workflow_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class HarnessDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_type: Literal[
        "workflow_reentry",
        "core_restart",
        "auto_patch",
        "clarify_scope",
        "fallback_workflow",
    ]
    message: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    recommended_workflow_id: Optional[str] = None
    selected_paths: list[str] = Field(default_factory=list)
    clarification_question: Optional[str] = None
    requires_confirmation: bool = False
    actions: list[HarnessDecisionAction] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CodingWorkerPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)
    owned_paths: list[str] = Field(default_factory=list)
    updated_files: dict[str, str] = Field(default_factory=dict)
    validation_strategy: Literal["skip", "local", "e2b"] = "skip"
    validation_commands: list[str] = Field(default_factory=list)
    start_preview: bool = False
    needs_human_review: bool = False
    rationale: str = Field(min_length=1)


class CodingWorkerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eligible: bool
    execution_mode: Literal["coding_worker"] = "coding_worker"
    status: Literal["planned", "validated", "ineligible", "failed"]
    provider: str = "control_plane_coding"
    plan: Optional[CodingWorkerPlan] = None
    applied_files: dict[str, str] = Field(default_factory=dict)
    validation_result: Optional[dict[str, Any]] = None
    blocked_reason: Optional[str] = None
    error: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
