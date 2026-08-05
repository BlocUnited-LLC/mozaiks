from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Contract surface types
# ---------------------------------------------------------------------------

ContractSurfaceKind = Literal[
    "module_action",    # module.yaml + handler + service + schemas (+ repo/policy)
    "module_contract",  # module.yaml declarations only (events, capabilities, settings)
    "page_binding",     # ui/pages/*.yaml + app.json
    "data_schema",      # schemas.py + optionally data/contract.json
    "workflow_tool",    # tools.yaml + tool Python file
    "workflow_agent",   # agents.yaml + structured_outputs.yaml + transition_graph.yaml
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
        "workflows/{target_id}/transition_graph.yaml",
    ],
    "ui_component": [
        "workflows/{target_id}/ui_config.yaml",
    ],
    "app_config": [
        "app.json",
    ],
}


def _remap_legacy_artifact_fields(values: Any) -> Any:
    if not isinstance(values, dict):
        return values
    for old, new in (
        ("artifact_kind", "build_family"),
        ("artifact_key", "build_key"),
        ("artifact_version_id", "build_record_id"),
    ):
        if old in values:
            if new not in values:
                values[new] = values[old]
            values.pop(old, None)
    return values


class ControlPlaneToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    target: str | None = None


class ControlPlaneToolContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoint: str | None = None
    app_id: str | None = None
    user_id: str | None = None
    build_family: str | None = None
    build_key: str | None = None
    build_record_id: str | None = None
    requested_workflow_id: str | None = None
    source_surface: str | None = None
    raw_user_request: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _remap_legacy_fields(cls, values: Any) -> Any:
        return _remap_legacy_artifact_fields(values)

    @property
    def artifact_kind(self) -> str | None:
        return self.build_family

    @property
    def artifact_key(self) -> str | None:
        return self.build_key

    @property
    def artifact_version_id(self) -> str | None:
        return self.build_record_id


class ControlPlaneToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool = True
    output: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


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
    user_id: str | None = None
    build_family: str
    build_key: str | None = None
    build_record_id: str | None = None
    requested_workflow_id: str | None = None
    raw_user_request: str = ""
    source_surface: str | None = None
    change_class: str
    files: dict[str, str] = Field(default_factory=dict)
    validation_strategy: str | None = None
    start_preview: bool = False
    context_seed: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _remap_legacy_fields(cls, values: Any) -> Any:
        return _remap_legacy_artifact_fields(values)

    @property
    def artifact_kind(self) -> str:
        return self.build_family

    @property
    def artifact_key(self) -> str | None:
        return self.build_key

    @property
    def artifact_version_id(self) -> str | None:
        return self.build_record_id


class ScopeProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution: Literal["scoped_files", "clarify", "workflow"]
    selected_paths: list[str]
    rationale: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    clarification_question: str | None
    signals: list[str]


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
    build_family: str = ""
    requires_schema_migration: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    fallback_to_workflow: bool = False
    fallback_reason: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _remap_legacy_fields(cls, values: Any) -> Any:
        return _remap_legacy_artifact_fields(values)

    @property
    def artifact_kind(self) -> str:
        return self.build_family


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
    workflow_id: str | None = None
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
    recommended_workflow_id: str | None = None
    selected_paths: list[str] = Field(default_factory=list)
    clarification_question: str | None = None
    requires_confirmation: bool = False
    actions: list[HarnessDecisionAction] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FileUpdate(BaseModel):
    """A single file path + full updated content pair in an LLM structured output.

    Used instead of ``dict[str, str]`` so that ``updated_files`` can appear in
    a JSON-schema ``required[]`` array, which OpenAI strict structured-output
    mode requires for every property.
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    content: str


class CodingWorkerPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)
    owned_paths: list[str]
    updated_files: list[FileUpdate]
    validation_strategy: Literal["skip", "local", "e2b"]
    validation_commands: list[str]
    start_preview: bool
    needs_human_review: bool
    rationale: str = Field(min_length=1)


class CodingWorkerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eligible: bool
    execution_mode: Literal["coding_worker"] = "coding_worker"
    status: Literal["planned", "validated", "ineligible", "failed"]
    provider: str = "control_plane_coding"
    plan: CodingWorkerPlan | None = None
    applied_files: dict[str, str] = Field(default_factory=dict)
    validation_result: dict[str, Any] | None = None
    blocked_reason: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SurfaceExecutionRecord(BaseModel):
    """Result for one surface within a ContractSurfacePlan execution."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    target_id: str
    status: Literal["success", "failed", "skipped"]
    applied_files: dict[str, str] = Field(default_factory=dict)
    error: str | None = None


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
