from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Set

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _trim_required(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _normalize_unique(values: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for raw in values:
        text = str(raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


class CapabilityExecutionMode(str, Enum):
    WORKFLOW = "workflow"
    ACTION = "action"
    MODULE = "module"


class ViewType(str, Enum):
    LIST = "list"
    DETAIL = "detail"
    CREATE = "create"
    EDIT = "edit"
    DASHBOARD = "dashboard"
    SEARCH = "search"


class ActionType(str, Enum):
    MUTATION = "mutation"
    INTEGRATION = "integration"
    TRIGGER = "trigger"


class PlanningBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppSpec(PlanningBaseModel):
    name: str
    summary: str
    user_personas: List[str] = Field(default_factory=list)
    core_jobs: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    non_goals: List[str] = Field(default_factory=list)

    @field_validator("name", "summary")
    @classmethod
    def _required_text(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _trim_required(value, field_name=info.field_name)

    @field_validator("user_personas", "core_jobs", "constraints", "non_goals")
    @classmethod
    def _normalize_lists(cls, value: List[str]) -> List[str]:
        return _normalize_unique(value)


class Capability(PlanningBaseModel):
    capability_id: str
    label: str
    mode: CapabilityExecutionMode
    summary: str = ""
    automatable: bool = True
    entity_refs: List[str] = Field(default_factory=list)
    view_refs: List[str] = Field(default_factory=list)
    action_refs: List[str] = Field(default_factory=list)
    module_refs: List[str] = Field(default_factory=list)
    workflow_refs: List[str] = Field(default_factory=list)
    policy_refs: List[str] = Field(default_factory=list)

    @field_validator("capability_id", "label")
    @classmethod
    def _required_text(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _trim_required(value, field_name=info.field_name)

    @field_validator(
        "entity_refs",
        "view_refs",
        "action_refs",
        "module_refs",
        "workflow_refs",
        "policy_refs",
    )
    @classmethod
    def _normalize_ref_lists(cls, value: List[str]) -> List[str]:
        return _normalize_unique(value)

    @model_validator(mode="after")
    def _validate_mode_mapping(self) -> "Capability":
        if self.mode is CapabilityExecutionMode.WORKFLOW and not self.workflow_refs:
            raise ValueError(
                f"capability '{self.capability_id}' with mode='workflow' must declare workflow_refs"
            )
        if self.mode is CapabilityExecutionMode.ACTION and not self.action_refs:
            raise ValueError(
                f"capability '{self.capability_id}' with mode='action' must declare action_refs"
            )
        if self.mode is CapabilityExecutionMode.MODULE and not self.module_refs:
            raise ValueError(
                f"capability '{self.capability_id}' with mode='module' must declare module_refs"
            )
        return self


class EntityFieldSpec(PlanningBaseModel):
    name: str
    field_type: str
    required: bool = False
    description: str = ""

    @field_validator("name", "field_type")
    @classmethod
    def _required_text(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _trim_required(value, field_name=info.field_name)


class EntitySpec(PlanningBaseModel):
    name: str
    purpose: str
    key_fields: List[EntityFieldSpec] = Field(default_factory=list)
    relations: List[str] = Field(default_factory=list)

    @field_validator("name", "purpose")
    @classmethod
    def _required_text(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _trim_required(value, field_name=info.field_name)

    @field_validator("relations")
    @classmethod
    def _normalize_relations(cls, value: List[str]) -> List[str]:
        return _normalize_unique(value)


class ViewSpec(PlanningBaseModel):
    name: str
    view_type: ViewType
    entity: Optional[str] = None
    module: Optional[str] = None
    fields: List[str] = Field(default_factory=list)
    filters: List[str] = Field(default_factory=list)
    sort: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _required_name(cls, value: Any) -> str:
        return _trim_required(value, field_name="name")

    @field_validator("entity", "module", "sort")
    @classmethod
    def _normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("fields", "filters")
    @classmethod
    def _normalize_list_fields(cls, value: List[str]) -> List[str]:
        return _normalize_unique(value)


class ActionSpec(PlanningBaseModel):
    name: str
    action_type: ActionType
    summary: str
    reads: List[str] = Field(default_factory=list)
    writes: List[str] = Field(default_factory=list)
    required_inputs: List[str] = Field(default_factory=list)

    @field_validator("name", "summary")
    @classmethod
    def _required_text(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _trim_required(value, field_name=info.field_name)

    @field_validator("reads", "writes", "required_inputs")
    @classmethod
    def _normalize_lists(cls, value: List[str]) -> List[str]:
        return _normalize_unique(value)


class ModuleSpec(PlanningBaseModel):
    name: str
    purpose: str
    route: str
    primary_views: List[str] = Field(default_factory=list)

    @field_validator("name", "purpose")
    @classmethod
    def _required_text(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _trim_required(value, field_name=info.field_name)

    @field_validator("route")
    @classmethod
    def _normalize_route(cls, value: Any) -> str:
        route = _trim_required(value, field_name="route")
        if not route.startswith("/"):
            raise ValueError("route must start with '/'")
        return route

    @field_validator("primary_views")
    @classmethod
    def _normalize_views(cls, value: List[str]) -> List[str]:
        return _normalize_unique(value)


class WorkflowSpec(PlanningBaseModel):
    name: str
    purpose: str
    entry_reason: str
    outputs: List[str] = Field(default_factory=list)

    @field_validator("name", "purpose", "entry_reason")
    @classmethod
    def _required_text(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _trim_required(value, field_name=info.field_name)

    @field_validator("outputs")
    @classmethod
    def _normalize_outputs(cls, value: List[str]) -> List[str]:
        return _normalize_unique(value)


class PolicySpec(PlanningBaseModel):
    name: str
    scope: str
    rule: str
    targets: List[str] = Field(default_factory=list)

    @field_validator("name", "scope", "rule")
    @classmethod
    def _required_text(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _trim_required(value, field_name=info.field_name)

    @field_validator("targets")
    @classmethod
    def _normalize_targets(cls, value: List[str]) -> List[str]:
        return _normalize_unique(value)


class BundlePlan(PlanningBaseModel):
    config_files: List[str] = Field(default_factory=list)
    module_paths: List[str] = Field(default_factory=list)
    workflow_paths: List[str] = Field(default_factory=list)
    data_model_paths: List[str] = Field(default_factory=list)

    @field_validator("config_files", "module_paths", "workflow_paths", "data_model_paths")
    @classmethod
    def _validate_paths(cls, value: List[str]) -> List[str]:
        normalized = _normalize_unique(value)
        for path in normalized:
            if "\\" in path:
                raise ValueError("bundle paths must use '/' separators")
        return normalized


class DecompositionPackage(PlanningBaseModel):
    app_spec: AppSpec
    capabilities: List[Capability] = Field(default_factory=list)
    entities: List[EntitySpec] = Field(default_factory=list)
    views: List[ViewSpec] = Field(default_factory=list)
    actions: List[ActionSpec] = Field(default_factory=list)
    modules: List[ModuleSpec] = Field(default_factory=list)
    workflows: List[WorkflowSpec] = Field(default_factory=list)
    policies: List[PolicySpec] = Field(default_factory=list)
    bundle_plan: Optional[BundlePlan] = None

    @staticmethod
    def _names(specs: Iterable[Any], attr: str = "name") -> Set[str]:
        out: Set[str] = set()
        for spec in specs:
            out.add(getattr(spec, attr))
        return out

    @model_validator(mode="after")
    def _validate_cross_references(self) -> "DecompositionPackage":
        entity_names = self._names(self.entities)
        view_names = self._names(self.views)
        action_names = self._names(self.actions)
        module_names = self._names(self.modules)
        workflow_names = self._names(self.workflows)
        policy_names = self._names(self.policies)

        errors: List[str] = []
        for cap in self.capabilities:
            for name in cap.entity_refs:
                if name not in entity_names:
                    errors.append(
                        f"capability '{cap.capability_id}' references unknown entity '{name}'"
                    )
            for name in cap.view_refs:
                if name not in view_names:
                    errors.append(
                        f"capability '{cap.capability_id}' references unknown view '{name}'"
                    )
            for name in cap.action_refs:
                if name not in action_names:
                    errors.append(
                        f"capability '{cap.capability_id}' references unknown action '{name}'"
                    )
            for name in cap.module_refs:
                if name not in module_names:
                    errors.append(
                        f"capability '{cap.capability_id}' references unknown module '{name}'"
                    )
            for name in cap.workflow_refs:
                if name not in workflow_names:
                    errors.append(
                        f"capability '{cap.capability_id}' references unknown workflow '{name}'"
                    )
            for name in cap.policy_refs:
                if name not in policy_names:
                    errors.append(
                        f"capability '{cap.capability_id}' references unknown policy '{name}'"
                    )

        if errors:
            raise ValueError("; ".join(errors))
        return self


def build_decomposition_package(payload: Dict[str, Any]) -> DecompositionPackage:
    return DecompositionPackage.model_validate(payload)


__all__ = [
    "CapabilityExecutionMode",
    "ViewType",
    "ActionType",
    "AppSpec",
    "Capability",
    "EntityFieldSpec",
    "EntitySpec",
    "ViewSpec",
    "ActionSpec",
    "ModuleSpec",
    "WorkflowSpec",
    "PolicySpec",
    "BundlePlan",
    "DecompositionPackage",
    "build_decomposition_package",
]
