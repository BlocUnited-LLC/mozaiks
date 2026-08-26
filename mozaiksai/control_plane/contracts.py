from __future__ import annotations

from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RefinementLane(StrEnum):
    """Canonical refinement lanes — the second classification dimension.

    ``ChangeClass`` (patch|design|feature|core) decides the route; the lane
    describes what kind of work the request is, and drives promotion policy,
    context-freshness policy, validation requirements, and (future) coding
    provider selection. These were previously bare string literals scattered
    across dry_run, promotion_policy, app_context_policy, and
    validation_runner; every lane comparison must reference this enum.
    """

    UI_PATCH = "ui_patch"
    EXPERIENCE_DESIGN = "experience_design"
    FEATURE_ADDITION = "feature_addition"
    INTEGRATION = "integration"
    MANAGED_CAPABILITY_CHANGE = "managed_capability_change"
    DATA_MODEL_MIGRATION = "data_model_migration"
    ARCHITECTURE_REPLAN = "architecture_replan"
    CONCEPTUAL_REFRAME = "conceptual_reframe"

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


# Canonical secret-sensitive path policy for staged/coding write paths. This is
# the union of the term lists previously duplicated across dry_run, staging,
# promotion, and scoped_execution; it lives here (a leaf module) so write-path
# modules can share it without import cycles.
SECRET_SENSITIVE_PATH_TERMS = (
    ".env",
    ".key",
    ".pem",
    "apikey",
    "api_key",
    "credential",
    "credentials",
    "id_dsa",
    "id_rsa",
    "password",
    "private-key",
    "private_key",
    "secret",
    "secrets",
    "token",
    "vault",
)


def is_secret_sensitive_path(path: str) -> bool:
    """True when a bundle-relative path matches the secret-path policy."""
    normalized = str(path or "").replace("\\", "/").lower()
    parts = [part for part in normalized.split("/") if part]
    return any(term in normalized for term in SECRET_SENSITIVE_PATH_TERMS) or any(
        part == ".env" for part in parts
    )


def safe_artifact_relpath(raw: Any) -> str | None:
    """Normalize a proposed artifact path to a safe bundle-relative POSIX path.

    Returns ``None`` for anything that is not a plain relative path: non-string
    values, empty strings, null bytes, POSIX-absolute and UNC paths,
    drive-qualified Windows paths (which ``PurePosixPath`` would treat as
    relative, letting ``workspace / path`` escape the workspace on Windows),
    and any path with a ``..`` traversal component.
    """
    if not isinstance(raw, str):
        return None
    normalized = raw.replace("\\", "/").strip()
    if not normalized or "\x00" in normalized or normalized.startswith("/"):
        return None
    if ":" in normalized.split("/", 1)[0]:
        return None
    posix_path = PurePosixPath(normalized)
    if posix_path.is_absolute() or any(part == ".." for part in posix_path.parts):
        return None
    return str(posix_path)


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
    validation_strategy: Literal["skip", "local"]
    validation_commands: list[str]
    start_preview: bool
    needs_human_review: bool
    rationale: str = Field(min_length=1)


class ProposedFileChange(BaseModel):
    """One staged file change proposed by a coding execution provider."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    op: Literal["create", "update"] = "update"
    content: str


class StagedPatchProposal(BaseModel):
    """Durable, provider-neutral output of one coding execution attempt.

    This is the Mozaiks-owned contract between the refinement coding worker and
    whichever provider produced the scoped patch (the structured-output
    provider today; ACP-backed CLI coding providers behind the same boundary
    later). Provider-specific objects must never cross this boundary — the
    worker consumes only this shape for validation, artifact persistence, and
    review.
    """

    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    provider_model: str | None = None
    status: Literal[
        "completed",
        "failed",
        "empty",
        "rejected_scope",
        "timeout",
        "budget_exceeded",
        "unavailable",
    ]
    usage: dict[str, int] | None = None
    summary: str = ""
    rationale: str = ""
    changed_files: list[ProposedFileChange] = Field(default_factory=list)
    owned_paths: list[str] = Field(default_factory=list)
    validation_strategy_hint: str | None = None
    validation_commands: list[str] = Field(default_factory=list)
    start_preview: bool = False
    needs_human_review: bool = False
    tool_context_loaded: bool = False
    error: str | None = None


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
