from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Contract surface types
# ---------------------------------------------------------------------------

ContractSurfaceKind = Literal[
    "module_action",    # module.yaml + handler + service + schemas (+ repo/policy)
    "module_contract",  # module.yaml declarations only (events, capabilities, settings)
    "page_binding",     # ui/pages/*.yaml + app.json
    "data_schema",      # schemas.py + optionally config/data.json
    "workflow_tool",    # tools.yaml + tool Python file
    "workflow_agent",   # agents.yaml + structured_outputs.yaml + handoffs.yaml
    "ui_component",     # ui/{WorkflowName}/components/*.js
    "app_config",       # app.json, shell.json, theme_config.json
]

# Canonical dependency ordering — lower runs first.
CONTRACT_SURFACE_DEPENDENCY_ORDER: dict[str, int] = {
    "data_schema": 0,
    "module_contract": 1,
    "module_action": 2,
    "workflow_tool": 1,
    "workflow_agent": 2,
    "page_binding": 3,
    "ui_component": 3,
    "app_config": 4,
}

# Canonical owned paths per surface kind.
# {target_id} is substituted at resolution time.
CONTRACT_SURFACE_CANONICAL_PATHS: dict[str, list[str]] = {
    "module_action": [
        "modules/{target_id}/module.yaml",
        "modules/{target_id}/backend/handler.py",
        "modules/{target_id}/backend/service.py",
        "modules/{target_id}/backend/schemas.py",
        "modules/{target_id}/backend/repo.py",
        "modules/{target_id}/backend/policy.py",
    ],
    "module_contract": [
        "modules/{target_id}/module.yaml",
        "modules/{target_id}/contracts/events.yaml",
    ],
    "page_binding": [
        "ui/pages/{target_id}.yaml",
        "app.json",
    ],
    "data_schema": [
        "modules/{target_id}/backend/schemas.py",
    ],
    "workflow_tool": [
        "workflows/{target_id}/tools.yaml",
    ],
    "workflow_agent": [
        "workflows/{target_id}/agents.yaml",
        "workflows/{target_id}/structured_outputs.yaml",
        "workflows/{target_id}/handoffs.yaml",
    ],
    "ui_component": [
        "workflows/{target_id}/ui_config.yaml",
    ],
    "app_config": [
        "app.json",
    ],
}


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


class ContractSurfaceUpdate(BaseModel):
    """One contract surface to update as part of a targeted regeneration plan."""

    model_config = ConfigDict(extra="forbid")

    kind: ContractSurfaceKind
    target_id: str = Field(min_length=1)
    target_kind: str = Field(min_length=1)
    affected_paths: list[str] = Field(default_factory=list)
    dependency_order: int = Field(default=0, ge=0)
    rationale: str = Field(min_length=1)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    generation_hint: str = ""


class ContractSurfacePlan(BaseModel):
    """Ordered plan of contract surface updates for a targeted regeneration."""

    model_config = ConfigDict(extra="forbid")

    surfaces: list[ContractSurfaceUpdate] = Field(default_factory=list)
    summary: str = Field(min_length=1)
    change_class: str = ""
    artifact_kind: str = ""
    requires_schema_migration: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    fallback_to_workflow: bool = False
    fallback_reason: Optional[str] = None


class HarnessDecisionAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    action_type: Literal[
        "confirm_workflow",
        "run_workflow",
        "clarify",
        "review_patch",
        "apply_scope",
        "review_surface",
    ] = "run_workflow"
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
        "targeted_regeneration",
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


class SurfaceExecutionRecord(BaseModel):
    """Result for one surface within a ContractSurfacePlan execution."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    target_id: str
    status: Literal["success", "failed", "skipped"]
    applied_files: dict[str, str] = Field(default_factory=dict)
    error: Optional[str] = None


class SurfacePlanExecutionResult(BaseModel):
    """Aggregated result of executing all surfaces in a ContractSurfacePlan.

    all_files is the merged set of file rewrites across every surface,
    in dependency order — suitable for passing directly to artifact persistence
    or Studio diff review.
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["success", "partial", "failed"]
    surfaces_executed: list[SurfaceExecutionRecord] = Field(default_factory=list)
    all_files: dict[str, str] = Field(default_factory=dict)
    requires_schema_migration: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
