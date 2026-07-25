from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .contracts import ControlPlaneToolDefinition

ControlPlaneCheckpointMode = Literal["ag2_structured_agent", "deterministic_handler"]
ControlPlaneCheckpointEvent = Literal[
    "request_submitted",
    "route_requested",
    "decision_requested",
    "scope_requested",
    "contract_surface_requested",
    "coding_requested",
]
RefinementHarnessOutputContract = Literal[
    "ChangeClassifierResult",
    "ScopeProposal",
    "ContractSurfaceClassification",
    "CodingWorkerPlan",
]

AG2_CHECKPOINT_OUTPUT_CONTRACTS: dict[ControlPlaneCheckpointEvent, RefinementHarnessOutputContract] = {
    "request_submitted": "ChangeClassifierResult",
    "scope_requested": "ScopeProposal",
    "contract_surface_requested": "ContractSurfaceClassification",
    "coding_requested": "CodingWorkerPlan",
}

CHECKPOINT_IDS: dict[ControlPlaneCheckpointEvent, str] = {
    "request_submitted": "request_intake",
    "route_requested": "refinement_route",
    "decision_requested": "decision",
    "scope_requested": "scope_selection",
    "contract_surface_requested": "contract_surface_planning",
    "coding_requested": "coding_refinement",
}


class ControlPlaneCheckpointManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: ControlPlaneCheckpointEvent
    prompt_id: str | None = None
    tool_ids: list[str] = Field(default_factory=list)
    ui_tool_ids: list[str] = Field(default_factory=list)

    @property
    def id(self) -> str:
        return CHECKPOINT_IDS[self.event]

    @property
    def mode(self) -> ControlPlaneCheckpointMode:
        return "ag2_structured_agent" if self.event in AG2_CHECKPOINT_OUTPUT_CONTRACTS else "deterministic_handler"

    @property
    def output_contract(self) -> RefinementHarnessOutputContract | None:
        return AG2_CHECKPOINT_OUTPUT_CONTRACTS.get(self.event)

    @model_validator(mode="after")
    def _validate_mode_contract(self) -> ControlPlaneCheckpointManifest:
        if self.mode == "ag2_structured_agent" and not self.prompt_id:
            raise ValueError(f"{self.event} checkpoints must declare prompt_id")
        if self.mode == "deterministic_handler" and self.prompt_id:
            raise ValueError(f"{self.event} checkpoints must not declare prompt_id")
        return self


class ControlPlaneChangeRouteManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_sequence: str = Field(min_length=1)

    @field_validator("workflow_sequence")
    @classmethod
    def _normalize_workflow_sequence(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("refinement harness route workflow_sequence must be non-empty")
        return normalized


class ControlPlaneArtifactChangeRoutesManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patch: ControlPlaneChangeRouteManifest
    design: ControlPlaneChangeRouteManifest
    feature: ControlPlaneChangeRouteManifest
    core: ControlPlaneChangeRouteManifest


class ControlPlaneArtifactRoutingManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_kind: str = Field(min_length=1)
    label: str | None = None
    routes: ControlPlaneArtifactChangeRoutesManifest

    @field_validator("artifact_kind")
    @classmethod
    def _normalize_artifact_kind(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if not normalized:
            raise ValueError("artifact_kind must be non-empty")
        return normalized


class ControlPlaneRoutingManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_artifact_kind: str = Field(default="app_bundle", min_length=1)
    artifacts: list[ControlPlaneArtifactRoutingManifest] = Field(default_factory=list)

    @field_validator("default_artifact_kind")
    @classmethod
    def _normalize_default_artifact_kind(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if not normalized:
            raise ValueError("default_artifact_kind must be non-empty")
        return normalized

    @model_validator(mode="after")
    def _unique_artifact_kinds(self) -> ControlPlaneRoutingManifest:
        artifact_kinds = [artifact.artifact_kind for artifact in self.artifacts]
        if len(artifact_kinds) != len(set(artifact_kinds)):
            raise ValueError("harness.yaml routing.artifacts artifact_kind values must be unique")
        return self


class ControlPlaneManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["mozaiks.refinement_harness.v1"]
    routing: ControlPlaneRoutingManifest = Field(default_factory=ControlPlaneRoutingManifest)
    checkpoints: list[ControlPlaneCheckpointManifest] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_checkpoints(self) -> ControlPlaneManifest:
        checkpoint_ids = [checkpoint.id for checkpoint in self.checkpoints]
        if len(checkpoint_ids) != len(set(checkpoint_ids)):
            raise ValueError("harness.yaml checkpoint ids must be unique")
        checkpoint_events = [checkpoint.event for checkpoint in self.checkpoints]
        if len(checkpoint_events) != len(set(checkpoint_events)):
            raise ValueError("harness.yaml checkpoint event values must be unique")
        return self


class ControlPlanePromptDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    content: str = Field(min_length=1)


class ControlPlanePromptsManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["mozaiks.refinement_harness.v1.prompts"]
    prompts: list[ControlPlanePromptDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_prompt_ids(self) -> ControlPlanePromptsManifest:
        prompt_ids = [prompt.id for prompt in self.prompts]
        if len(prompt_ids) != len(set(prompt_ids)):
            raise ValueError("prompts.yaml prompt ids must be unique")
        return self


class ControlPlaneToolsManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["mozaiks.refinement_harness.tools.v1"]
    tools: list[ControlPlaneToolDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_tool_ids(self) -> ControlPlaneToolsManifest:
        tool_ids = [tool.id for tool in self.tools]
        if len(tool_ids) != len(set(tool_ids)):
            raise ValueError("tools.yaml tool ids must be unique")
        return self


class ControlPlaneScopePolicyManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_selected_paths: int = Field(default=3, ge=1, le=20)
    auto_apply_max_paths: int = Field(default=1, ge=1, le=20)
    overflow_behavior: Literal["clarify", "workflow"] = "clarify"

    @model_validator(mode="after")
    def _validate_thresholds(self) -> ControlPlaneScopePolicyManifest:
        if self.auto_apply_max_paths > self.max_selected_paths:
            raise ValueError("scope.auto_apply_max_paths must be <= scope.max_selected_paths")
        return self


class ControlPlanePoliciesManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["mozaiks.refinement_harness.policies.v1"] = "mozaiks.refinement_harness.policies.v1"
    scope: ControlPlaneScopePolicyManifest = Field(default_factory=ControlPlaneScopePolicyManifest)


class LoadedControlPlanePack(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    path: Path
    manifest: ControlPlaneManifest
    prompts: ControlPlanePromptsManifest
    tools: ControlPlaneToolsManifest
    policies: ControlPlanePoliciesManifest = Field(default_factory=ControlPlanePoliciesManifest)

    def prompt_by_id(self, prompt_id: str) -> ControlPlanePromptDefinition | None:
        for prompt in self.prompts.prompts:
            if prompt.id == prompt_id:
                return prompt
        return None

    def tool_by_id(self, tool_id: str) -> ControlPlaneToolDefinition | None:
        for tool in self.tools.tools:
            if tool.id == tool_id:
                return tool
        return None

    def checkpoint_by_id(self, checkpoint_id: str) -> ControlPlaneCheckpointManifest | None:
        for checkpoint in self.manifest.checkpoints:
            if checkpoint.id == checkpoint_id:
                return checkpoint
        return None

    def checkpoint_by_event(self, event_name: str) -> ControlPlaneCheckpointManifest | None:
        for checkpoint in self.manifest.checkpoints:
            if checkpoint.event == event_name:
                return checkpoint
        return None

    def routing_for_artifact(self, artifact_kind: str) -> ControlPlaneArtifactRoutingManifest | None:
        requested = str(artifact_kind or "").strip().lower()
        artifacts = list(self.manifest.routing.artifacts)
        for candidate in [requested, str(self.manifest.routing.default_artifact_kind or "").strip().lower()]:
            if not candidate:
                continue
            for artifact in artifacts:
                if artifact.artifact_kind == candidate:
                    return artifact
        return None


ControlPlaneManifest.model_rebuild()
