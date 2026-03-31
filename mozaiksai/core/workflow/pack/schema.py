from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class WorkflowDependency(BaseModel):
    """Explicit dependency edge in the global pack graph."""

    model_config = ConfigDict(extra="forbid")

    id: str
    scope: Literal["app", "user"] = "app"
    reason: Optional[str] = None
    gating: Literal["required", "optional"] = "required"

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        val = str(value or "").strip()
        if not val:
            raise ValueError("dependency id must be a non-empty string")
        return val


class WorkflowEntry(BaseModel):
    """Workflow registry entry in the global pack graph."""

    model_config = ConfigDict(extra="forbid")

    id: str
    description: Optional[str] = None
    dependencies: List[Union[str, WorkflowDependency]] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        val = str(value or "").strip()
        if not val:
            raise ValueError("workflow id must be a non-empty string")
        return val


JourneyStep = Union[str, List[str]]


class GlobalJourney(BaseModel):
    """Across-workflow sequencing definition."""

    model_config = ConfigDict(extra="forbid")

    id: str
    description: Optional[str] = None
    steps: List[JourneyStep] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        val = str(value or "").strip()
        if not val:
            raise ValueError("journey id must be a non-empty string")
        return val

    @field_validator("steps")
    @classmethod
    def _validate_steps(cls, value: List[JourneyStep]) -> List[JourneyStep]:
        if not isinstance(value, list) or not value:
            raise ValueError("journey steps must be a non-empty list")
        normalized: List[JourneyStep] = []
        for raw in value:
            if isinstance(raw, str):
                step = raw.strip()
                if not step:
                    raise ValueError("journey step string must be non-empty")
                normalized.append(step)
                continue
            if isinstance(raw, list):
                group: List[str] = []
                for item in raw:
                    if not isinstance(item, str) or not item.strip():
                        raise ValueError("parallel journey step entries must be non-empty strings")
                    group.append(item.strip())
                if not group:
                    raise ValueError("parallel journey step must contain at least one workflow id")
                normalized.append(group)
                continue
            raise ValueError("journey step must be a string or a list of strings")
        return normalized


class GlobalPackGraph(BaseModel):
    """Canonical global pack graph."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[2]
    workflows: List[WorkflowEntry] = Field(default_factory=list)
    journeys: List[GlobalJourney] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_uniqueness(self) -> "GlobalPackGraph":
        wf_ids: List[str] = [w.id for w in self.workflows]
        if len(wf_ids) != len(set(wf_ids)):
            raise ValueError("global pack graph contains duplicate workflow ids")
        journey_ids: List[str] = [j.id for j in self.journeys]
        if len(journey_ids) != len(set(journey_ids)):
            raise ValueError("global pack graph contains duplicate journey ids")
        return self


class MFJContract(BaseModel):
    """Input/output contract for fan-out/fan-in."""

    model_config = ConfigDict(extra="forbid")

    required: List[str] = Field(default_factory=list)
    optional: List[str] = Field(default_factory=list)

    @field_validator("required", "optional")
    @classmethod
    def _validate_keys(cls, value: List[str]) -> List[str]:
        normalized: List[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("contract keys must be strings")
            key = item.strip()
            if not key:
                raise ValueError("contract keys must be non-empty")
            normalized.append(key)
        return normalized


class MFJFanOutConfig(BaseModel):
    """Fan-out execution config."""

    model_config = ConfigDict(extra="forbid")

    spawn_mode: Literal["workflow", "workflow_authoring_subrun"]
    authoring_workflow: Optional[str] = None
    child_initial_agent: Optional[str] = None
    max_children: int = 10
    timeout_seconds: int = 600
    input_contract: MFJContract = Field(default_factory=MFJContract)
    child_context_seed: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_spawn_mode(self) -> "MFJFanOutConfig":
        if self.spawn_mode == "workflow_authoring_subrun":
            if not isinstance(self.authoring_workflow, str) or not self.authoring_workflow.strip():
                raise ValueError(
                    "fan_out.authoring_workflow is required for "
                    "spawn_mode=workflow_authoring_subrun"
                )
            self.authoring_workflow = self.authoring_workflow.strip()
        if self.max_children <= 0:
            raise ValueError("fan_out.max_children must be > 0")
        if self.timeout_seconds < 0:
            raise ValueError("fan_out.timeout_seconds must be >= 0")
        if isinstance(self.child_initial_agent, str):
            self.child_initial_agent = self.child_initial_agent.strip() or None
        return self


class MFJFanInConfig(BaseModel):
    """Fan-in aggregation and resume config."""

    model_config = ConfigDict(extra="forbid")

    resume_agent: str
    resume_entry_agent: str
    aggregation_strategy: str
    inject_as: str
    on_partial_failure: Literal[
        "resume_with_available",
        "fail_all",
        "retry_failed",
        "prompt_user",
    ] = "resume_with_available"
    timeout_seconds: int = 60

    @field_validator("resume_agent", "resume_entry_agent")
    @classmethod
    def _validate_agent_fields(cls, value: str) -> str:
        val = str(value or "").strip()
        if not val:
            raise ValueError("fan_in agent fields must be non-empty")
        return val

    @field_validator("inject_as")
    @classmethod
    def _validate_inject_as(cls, value: str) -> str:
        val = str(value or "").strip()
        if not val:
            raise ValueError("fan_in.inject_as must be non-empty")
        if not val.startswith("mfj_"):
            raise ValueError("fan_in.inject_as must start with 'mfj_'")
        return val

    @field_validator("aggregation_strategy")
    @classmethod
    def _validate_strategy(cls, value: str) -> str:
        val = str(value or "").strip()
        if not val:
            raise ValueError("fan_in.aggregation_strategy must be non-empty")
        allowed = {"collect_all", "merge_bundles", "concatenate", "first_success", "majority_vote"}
        if val in allowed:
            return val
        if val.startswith("custom:"):
            name = val.split(":", 1)[1].strip()
            if not name:
                raise ValueError("fan_in.aggregation_strategy custom:<name> requires a non-empty name")
            return f"custom:{name}"
        raise ValueError(
            "fan_in.aggregation_strategy must be one of "
            "collect_all, merge_bundles, concatenate, first_success, majority_vote, or custom:<name>"
        )

    @field_validator("timeout_seconds")
    @classmethod
    def _validate_timeout(cls, value: int) -> int:
        if value < 0:
            raise ValueError("fan_in.timeout_seconds must be >= 0")
        return value


class MidFlightJourney(BaseModel):
    """Canonical per-workflow MFJ trigger config."""

    model_config = ConfigDict(extra="forbid")

    id: str
    description: Optional[str] = None
    trigger_agent: str
    trigger_on: Literal["agent_output"] = "agent_output"
    requires: List[str] = Field(default_factory=list)
    fan_out: MFJFanOutConfig
    fan_in: MFJFanInConfig
    output_contract: MFJContract = Field(default_factory=MFJContract)

    @field_validator("id", "trigger_agent")
    @classmethod
    def _validate_non_empty(cls, value: str) -> str:
        val = str(value or "").strip()
        if not val:
            raise ValueError("mid_flight_journey string fields must be non-empty")
        return val

    @field_validator("requires")
    @classmethod
    def _validate_requires(cls, value: List[str]) -> List[str]:
        normalized: List[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("requires entries must be strings")
            req = item.strip()
            if not req:
                raise ValueError("requires entries must be non-empty")
            normalized.append(req)
        return normalized


class WorkflowPackGraph(BaseModel):
    """Canonical per-workflow pack graph."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[3]
    mid_flight_journeys: List[MidFlightJourney] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_uniqueness(self) -> "WorkflowPackGraph":
        ids: List[str] = [j.id for j in self.mid_flight_journeys]
        if len(ids) != len(set(ids)):
            raise ValueError("workflow pack graph contains duplicate mid_flight_journey ids")
        return self


def normalize_step_groups(steps: List[JourneyStep]) -> List[List[str]]:
    """Normalize GlobalJourney steps to grouped execution layers."""
    groups: List[List[str]] = []
    for raw in steps:
        if isinstance(raw, str):
            groups.append([raw])
        else:
            groups.append(list(raw))
    return groups


def parse_global_pack_graph(raw: Dict[str, Any]) -> GlobalPackGraph:
    try:
        return GlobalPackGraph.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid global pack graph: {exc}") from exc


def parse_workflow_pack_graph(raw: Dict[str, Any]) -> WorkflowPackGraph:
    try:
        return WorkflowPackGraph.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid workflow pack graph: {exc}") from exc


__all__ = [
    "WorkflowDependency",
    "WorkflowEntry",
    "GlobalJourney",
    "GlobalPackGraph",
    "MFJContract",
    "MFJFanOutConfig",
    "MFJFanInConfig",
    "MidFlightJourney",
    "WorkflowPackGraph",
    "normalize_step_groups",
    "parse_global_pack_graph",
    "parse_workflow_pack_graph",
]
