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


def _normalize_optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_paths(values: Iterable[str]) -> List[str]:
    normalized = _normalize_unique(values)
    for path in normalized:
        if "\\" in path:
            raise ValueError("bundle paths must use '/' separators")
    return normalized


class PrimarySurface(str, Enum):
    MODULE = "module"
    ACTION = "action"
    WORKFLOW = "workflow"


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


class AutomationEffectKind(str, Enum):
    WORKFLOW_RUN = "workflow.run"
    WORKFLOW_RESUME = "workflow.resume"
    ARTIFACT_UPSERT = "artifact.upsert"
    NOTIFICATION_SEND = "notification.send"
    NONE = "none"


class DeclarativeFamily(str, Enum):
    APP_MANIFEST = "app_manifest"
    SHELL = "shell"
    APP_SUBSTRATE = "app_substrate"
    MODULES = "modules"
    AUTOMATION = "automation"
    WORKFLOWS = "workflows"


class BuilderWorkflowRole(str, Enum):
    INTENT_MODELER = "IntentModeler"
    ARCHITECTURE_PLANNER = "ArchitecturePlanner"
    AUTOMATION_PLANNER = "AutomationPlanner"
    WORKFLOW_AUTHOR = "WorkflowAuthor"
    BUNDLE_COMPILER = "BundleCompiler"
    VALIDATOR = "Validator"


class BuilderIntentMode(str, Enum):
    CREATE = "create"
    CHANGE = "change"


class PlatformProvisionMode(str, Enum):
    CORE_PROVIDED = "core_provided"
    CORE_CONFIGURED = "core_configured"
    APP_STUB = "app_stub"
    EXTERNAL_INTEGRATION = "external_integration"
    DISABLED = "disabled"


_ALLOWED_DECLARATIVE_FAMILIES_BY_ROLE: Dict[BuilderWorkflowRole, frozenset[DeclarativeFamily]] = {
    BuilderWorkflowRole.INTENT_MODELER: frozenset(),
    BuilderWorkflowRole.ARCHITECTURE_PLANNER: frozenset(),
    BuilderWorkflowRole.AUTOMATION_PLANNER: frozenset(),
    BuilderWorkflowRole.WORKFLOW_AUTHOR: frozenset({DeclarativeFamily.WORKFLOWS}),
    BuilderWorkflowRole.BUNDLE_COMPILER: frozenset(
        {
            DeclarativeFamily.APP_MANIFEST,
            DeclarativeFamily.SHELL,
            DeclarativeFamily.APP_SUBSTRATE,
            DeclarativeFamily.MODULES,
            DeclarativeFamily.AUTOMATION,
        }
    ),
    BuilderWorkflowRole.VALIDATOR: frozenset(),
}


class PlanningBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IntentBrief(PlanningBaseModel):
    source_request: str
    product_summary: str
    user_personas: List[str] = Field(default_factory=list)
    bounded_contexts: List[str] = Field(default_factory=list)
    business_entities: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    non_goals: List[str] = Field(default_factory=list)
    success_criteria: List[str] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)

    @field_validator("source_request", "product_summary")
    @classmethod
    def _required_text(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _trim_required(value, field_name=info.field_name)

    @field_validator(
        "user_personas",
        "bounded_contexts",
        "business_entities",
        "constraints",
        "non_goals",
        "success_criteria",
        "open_questions",
    )
    @classmethod
    def _normalize_lists(cls, value: List[str]) -> List[str]:
        return _normalize_unique(value)


class ConceptBlueprint(PlanningBaseModel):
    intent_mode: BuilderIntentMode
    app_name: str
    product_summary: str
    value_proposition: str
    primary_users: List[str] = Field(default_factory=list)
    approved_scope: List[str] = Field(default_factory=list)
    deferred_scope: List[str] = Field(default_factory=list)
    core_outcomes: List[str] = Field(default_factory=list)
    success_signals: List[str] = Field(default_factory=list)
    approval_notes: List[str] = Field(default_factory=list)
    change_summary: Optional[str] = None

    @field_validator("app_name", "product_summary", "value_proposition")
    @classmethod
    def _required_text(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _trim_required(value, field_name=info.field_name)

    @field_validator("change_summary")
    @classmethod
    def _normalize_change_summary(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_optional_text(value)

    @field_validator(
        "primary_users",
        "approved_scope",
        "deferred_scope",
        "core_outcomes",
        "success_signals",
        "approval_notes",
    )
    @classmethod
    def _normalize_lists(cls, value: List[str]) -> List[str]:
        return _normalize_unique(value)

    @model_validator(mode="after")
    def _validate_change_mode(self) -> "ConceptBlueprint":
        if self.intent_mode is BuilderIntentMode.CHANGE and not self.change_summary:
            raise ValueError("concept blueprint with intent_mode='change' requires change_summary")
        return self


class PlatformProvisionSpec(PlanningBaseModel):
    provision_id: str
    label: str
    category: str
    runtime_owner: str
    mode: PlatformProvisionMode
    summary: str
    depends_on: List[str] = Field(default_factory=list)
    config_paths: List[str] = Field(default_factory=list)
    stub_paths: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)

    @field_validator("provision_id", "label", "category", "runtime_owner", "summary")
    @classmethod
    def _required_text(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _trim_required(value, field_name=info.field_name)

    @field_validator("depends_on", "notes")
    @classmethod
    def _normalize_lists(cls, value: List[str]) -> List[str]:
        return _normalize_unique(value)

    @field_validator("config_paths", "stub_paths")
    @classmethod
    def _normalize_provision_paths(cls, value: List[str]) -> List[str]:
        return _normalize_paths(value)

    @model_validator(mode="after")
    def _validate_mode_shape(self) -> "PlatformProvisionSpec":
        if self.mode is PlatformProvisionMode.CORE_PROVIDED and self.stub_paths:
            raise ValueError(
                f"platform provision '{self.provision_id}' with mode='core_provided' "
                "may not declare stub_paths"
            )
        if self.mode is PlatformProvisionMode.CORE_CONFIGURED:
            if not self.config_paths:
                raise ValueError(
                    f"platform provision '{self.provision_id}' with mode='core_configured' "
                    "must declare config_paths"
                )
            if self.stub_paths:
                raise ValueError(
                    f"platform provision '{self.provision_id}' with mode='core_configured' "
                    "may not declare stub_paths"
                )
        if self.mode is PlatformProvisionMode.APP_STUB and not self.stub_paths:
            raise ValueError(
                f"platform provision '{self.provision_id}' with mode='app_stub' "
                "must declare stub_paths"
            )
        if self.mode is PlatformProvisionMode.EXTERNAL_INTEGRATION and not (
            self.config_paths or self.stub_paths
        ):
            raise ValueError(
                f"platform provision '{self.provision_id}' with mode='external_integration' "
                "must declare config_paths or stub_paths"
            )
        if self.mode is PlatformProvisionMode.DISABLED and (self.config_paths or self.stub_paths):
            raise ValueError(
                f"platform provision '{self.provision_id}' with mode='disabled' "
                "may not declare config_paths or stub_paths"
            )
        return self


class PlatformProvisionPlan(PlanningBaseModel):
    provisions: List[PlatformProvisionSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_provisions(self) -> "PlatformProvisionPlan":
        provision_ids = [item.provision_id for item in self.provisions]
        if len(provision_ids) != len(set(provision_ids)):
            raise ValueError("platform provision plan contains duplicate provision_id values")

        known_ids = set(provision_ids)
        errors: List[str] = []
        for provision in self.provisions:
            for dep in provision.depends_on:
                if dep not in known_ids:
                    errors.append(
                        f"platform provision '{provision.provision_id}' depends on unknown "
                        f"provision '{dep}'"
                    )
        if errors:
            raise ValueError("; ".join(errors))
        return self


class ImpactSet(PlanningBaseModel):
    change_summary: str
    affected_capability_ids: List[str] = Field(default_factory=list)
    affected_provision_ids: List[str] = Field(default_factory=list)
    affected_workflows: List[str] = Field(default_factory=list)
    affected_bundle_paths: List[str] = Field(default_factory=list)
    affected_declarative_families: List[DeclarativeFamily] = Field(default_factory=list)
    requires_concept_revision: bool = False
    requires_replan: bool = False
    requires_rebuild: bool = True

    @field_validator("change_summary")
    @classmethod
    def _required_text(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _trim_required(value, field_name=info.field_name)

    @field_validator(
        "affected_capability_ids",
        "affected_provision_ids",
        "affected_workflows",
    )
    @classmethod
    def _normalize_refs(cls, value: List[str]) -> List[str]:
        return _normalize_unique(value)

    @field_validator("affected_bundle_paths")
    @classmethod
    def _normalize_impact_paths(cls, value: List[str]) -> List[str]:
        return _normalize_paths(value)

    @field_validator("affected_declarative_families")
    @classmethod
    def _normalize_impact_families(cls, value: List[DeclarativeFamily]) -> List[DeclarativeFamily]:
        ordered: List[DeclarativeFamily] = []
        seen: Set[DeclarativeFamily] = set()
        for family in value:
            if family in seen:
                continue
            seen.add(family)
            ordered.append(family)
        return ordered

    @model_validator(mode="after")
    def _validate_non_empty_impact(self) -> "ImpactSet":
        if not (
            self.affected_capability_ids
            or self.affected_provision_ids
            or self.affected_workflows
            or self.affected_bundle_paths
            or self.affected_declarative_families
        ):
            raise ValueError("impact set must declare at least one affected surface")
        return self


class CapabilityMapItem(PlanningBaseModel):
    capability_id: str
    label: str
    summary: str
    actor: Optional[str] = None
    primary_surface: PrimarySurface
    requires_durable_state: bool = False
    requires_reasoning: bool = False
    can_be_event_triggered: bool = False
    entity_candidates: List[str] = Field(default_factory=list)
    action_candidates: List[str] = Field(default_factory=list)
    module_candidates: List[str] = Field(default_factory=list)
    workflow_candidates: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)

    @field_validator("capability_id", "label", "summary")
    @classmethod
    def _required_text(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _trim_required(value, field_name=info.field_name)

    @field_validator("actor")
    @classmethod
    def _normalize_actor(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_optional_text(value)

    @field_validator(
        "entity_candidates",
        "action_candidates",
        "module_candidates",
        "workflow_candidates",
        "notes",
    )
    @classmethod
    def _normalize_lists(cls, value: List[str]) -> List[str]:
        return _normalize_unique(value)

    @model_validator(mode="after")
    def _validate_primary_surface_candidates(self) -> "CapabilityMapItem":
        if self.primary_surface is PrimarySurface.MODULE and not self.module_candidates:
            raise ValueError(
                f"capability '{self.capability_id}' with primary_surface='module' "
                "must declare module_candidates"
            )
        if self.primary_surface is PrimarySurface.ACTION and not self.action_candidates:
            raise ValueError(
                f"capability '{self.capability_id}' with primary_surface='action' "
                "must declare action_candidates"
            )
        if self.primary_surface is PrimarySurface.WORKFLOW and not self.workflow_candidates:
            raise ValueError(
                f"capability '{self.capability_id}' with primary_surface='workflow' "
                "must declare workflow_candidates"
            )
        return self


class CapabilityMap(PlanningBaseModel):
    capabilities: List[CapabilityMapItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_unique_capability_ids(self) -> "CapabilityMap":
        capability_ids = [item.capability_id for item in self.capabilities]
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("capability map contains duplicate capability_id values")
        return self


class AppSpec(PlanningBaseModel):
    name: str
    summary: str
    user_personas: List[str] = Field(default_factory=list)
    bounded_contexts: List[str] = Field(default_factory=list)
    core_jobs: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    non_goals: List[str] = Field(default_factory=list)

    @field_validator("name", "summary")
    @classmethod
    def _required_text(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _trim_required(value, field_name=info.field_name)

    @field_validator("user_personas", "bounded_contexts", "core_jobs", "constraints", "non_goals")
    @classmethod
    def _normalize_lists(cls, value: List[str]) -> List[str]:
        return _normalize_unique(value)


class Capability(PlanningBaseModel):
    capability_id: str
    label: str
    primary_surface: PrimarySurface
    summary: str = ""
    actor: Optional[str] = None
    automatable: bool = True
    entity_refs: List[str] = Field(default_factory=list)
    view_refs: List[str] = Field(default_factory=list)
    action_refs: List[str] = Field(default_factory=list)
    module_refs: List[str] = Field(default_factory=list)
    workflow_refs: List[str] = Field(default_factory=list)
    policy_refs: List[str] = Field(default_factory=list)
    event_refs: List[str] = Field(default_factory=list)
    automation_route_refs: List[str] = Field(default_factory=list)

    @field_validator("capability_id", "label")
    @classmethod
    def _required_text(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _trim_required(value, field_name=info.field_name)

    @field_validator("summary")
    @classmethod
    def _normalize_summary(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("actor")
    @classmethod
    def _normalize_actor(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_optional_text(value)

    @field_validator(
        "entity_refs",
        "view_refs",
        "action_refs",
        "module_refs",
        "workflow_refs",
        "policy_refs",
        "event_refs",
        "automation_route_refs",
    )
    @classmethod
    def _normalize_ref_lists(cls, value: List[str]) -> List[str]:
        return _normalize_unique(value)

    @model_validator(mode="after")
    def _validate_surface_mapping(self) -> "Capability":
        if self.primary_surface is PrimarySurface.WORKFLOW and not self.workflow_refs:
            raise ValueError(
                f"capability '{self.capability_id}' with primary_surface='workflow' "
                "must declare workflow_refs"
            )
        if self.primary_surface is PrimarySurface.ACTION and not self.action_refs:
            raise ValueError(
                f"capability '{self.capability_id}' with primary_surface='action' "
                "must declare action_refs"
            )
        if self.primary_surface is PrimarySurface.MODULE and not self.module_refs:
            raise ValueError(
                f"capability '{self.capability_id}' with primary_surface='module' "
                "must declare module_refs"
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
    def _normalize_optional_text_field(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_optional_text(value)

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
    entry_modes: List[str] = Field(default_factory=list)
    surface: Optional[str] = None

    @field_validator("name", "purpose", "entry_reason")
    @classmethod
    def _required_text(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _trim_required(value, field_name=info.field_name)

    @field_validator("surface")
    @classmethod
    def _normalize_surface(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_optional_text(value)

    @field_validator("outputs", "entry_modes")
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


class DomainEventSpec(PlanningBaseModel):
    event_type: str
    producer: str
    description: str = ""
    source_event: Optional[str] = None
    payload_schema: Dict[str, Any] = Field(default_factory=dict)
    correlation_keys: List[str] = Field(default_factory=list)
    post_commit_only: bool = True

    @field_validator("event_type", "producer")
    @classmethod
    def _required_text(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _trim_required(value, field_name=info.field_name)

    @field_validator("source_event")
    @classmethod
    def _normalize_source_event(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_optional_text(value)

    @field_validator("correlation_keys")
    @classmethod
    def _normalize_correlation_keys(cls, value: List[str]) -> List[str]:
        return _normalize_unique(value)


class AutomationEffectSpec(PlanningBaseModel):
    kind: AutomationEffectKind
    workflow: Optional[str] = None
    surface: str = "background"
    message_template: Optional[str] = None

    @field_validator("workflow", "surface", "message_template")
    @classmethod
    def _normalize_optional_text_field(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_optional_text(value)

    @model_validator(mode="after")
    def _validate_workflow_requirement(self) -> "AutomationEffectSpec":
        if self.kind in {
            AutomationEffectKind.WORKFLOW_RUN,
            AutomationEffectKind.WORKFLOW_RESUME,
        } and not self.workflow:
            raise ValueError("workflow is required for workflow.run and workflow.resume effects")
        return self


class AutomationRouteSpec(PlanningBaseModel):
    route_id: str
    event_type: str
    when: Dict[str, Any] = Field(default_factory=dict)
    effect: AutomationEffectSpec
    bindings: Dict[str, str] = Field(default_factory=dict)
    enabled: bool = True

    @field_validator("route_id", "event_type")
    @classmethod
    def _required_text(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _trim_required(value, field_name=info.field_name)

    @field_validator("bindings")
    @classmethod
    def _normalize_bindings(cls, value: Dict[str, str]) -> Dict[str, str]:
        normalized: Dict[str, str] = {}
        for key, raw in value.items():
            name = str(key or "").strip()
            target = str(raw or "").strip()
            if name and target:
                normalized[name] = target
        return normalized


class BundlePlan(PlanningBaseModel):
    manifest_paths: List[str] = Field(default_factory=list)
    shell_paths: List[str] = Field(default_factory=list)
    substrate_paths: List[str] = Field(default_factory=list)
    module_paths: List[str] = Field(default_factory=list)
    automation_paths: List[str] = Field(default_factory=list)
    workflow_paths: List[str] = Field(default_factory=list)

    @field_validator(
        "manifest_paths",
        "shell_paths",
        "substrate_paths",
        "module_paths",
        "automation_paths",
        "workflow_paths",
    )
    @classmethod
    def _validate_paths(cls, value: List[str]) -> List[str]:
        return _normalize_paths(value)

    def family_paths(self) -> Dict[DeclarativeFamily, List[str]]:
        return {
            DeclarativeFamily.APP_MANIFEST: list(self.manifest_paths),
            DeclarativeFamily.SHELL: list(self.shell_paths),
            DeclarativeFamily.APP_SUBSTRATE: list(self.substrate_paths),
            DeclarativeFamily.MODULES: list(self.module_paths),
            DeclarativeFamily.AUTOMATION: list(self.automation_paths),
            DeclarativeFamily.WORKFLOWS: list(self.workflow_paths),
        }

    def all_paths(self) -> List[str]:
        return [
            *self.manifest_paths,
            *self.shell_paths,
            *self.substrate_paths,
            *self.module_paths,
            *self.automation_paths,
            *self.workflow_paths,
        ]

    @model_validator(mode="after")
    def _validate_unique_bundle_paths(self) -> "BundlePlan":
        seen: Dict[str, DeclarativeFamily] = {}
        errors: List[str] = []
        for family, paths in self.family_paths().items():
            for path in paths:
                prior = seen.get(path)
                if prior is not None:
                    errors.append(
                        f"bundle path '{path}' is assigned to both '{prior.value}' and '{family.value}'"
                    )
                    continue
                seen[path] = family
        if errors:
            raise ValueError("; ".join(errors))
        return self


class BuildTask(PlanningBaseModel):
    task_id: str
    title: str
    builder_workflow: BuilderWorkflowRole
    depends_on: List[str] = Field(default_factory=list)
    capability_refs: List[str] = Field(default_factory=list)
    provision_refs: List[str] = Field(default_factory=list)
    consumes: List[str] = Field(default_factory=list)
    produces: List[str] = Field(default_factory=list)
    declarative_families: List[DeclarativeFamily] = Field(default_factory=list)
    bundle_paths: List[str] = Field(default_factory=list)
    report_paths: List[str] = Field(default_factory=list)
    parallel_safe: bool = False
    review_required: bool = False

    @field_validator("task_id", "title")
    @classmethod
    def _required_text(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _trim_required(value, field_name=info.field_name)

    @field_validator("depends_on", "capability_refs", "provision_refs", "consumes", "produces")
    @classmethod
    def _normalize_string_lists(cls, value: List[str]) -> List[str]:
        return _normalize_unique(value)

    @field_validator("bundle_paths", "report_paths")
    @classmethod
    def _normalize_task_paths(cls, value: List[str]) -> List[str]:
        return _normalize_paths(value)

    @field_validator("declarative_families")
    @classmethod
    def _normalize_families(cls, value: List[DeclarativeFamily]) -> List[DeclarativeFamily]:
        ordered: List[DeclarativeFamily] = []
        seen: Set[DeclarativeFamily] = set()
        for family in value:
            if family in seen:
                continue
            seen.add(family)
            ordered.append(family)
        return ordered

    @model_validator(mode="after")
    def _validate_write_boundary(self) -> "BuildTask":
        allowed_families = _ALLOWED_DECLARATIVE_FAMILIES_BY_ROLE[self.builder_workflow]
        declared_families = set(self.declarative_families)
        disallowed = declared_families - allowed_families
        if disallowed:
            allowed = sorted(family.value for family in allowed_families)
            raise ValueError(
                f"build task '{self.task_id}' assigned to {self.builder_workflow.value} "
                f"may only write declarative families {allowed}"
            )

        if self.bundle_paths and not self.declarative_families:
            raise ValueError(
                f"build task '{self.task_id}' declares bundle_paths but no declarative_families"
            )

        if self.declarative_families and not self.bundle_paths:
            raise ValueError(
                f"build task '{self.task_id}' declares declarative_families but no bundle_paths"
            )

        if self.bundle_paths and not (self.capability_refs or self.provision_refs):
            raise ValueError(
                f"build task '{self.task_id}' declares bundle_paths but no capability_refs "
                "or provision_refs"
            )

        return self


class BuildGraph(PlanningBaseModel):
    tasks: List[BuildTask] = Field(default_factory=list)
    entry_tasks: List[str] = Field(default_factory=list)
    terminal_tasks: List[str] = Field(default_factory=list)

    @field_validator("entry_tasks", "terminal_tasks")
    @classmethod
    def _normalize_task_refs(cls, value: List[str]) -> List[str]:
        return _normalize_unique(value)

    def bundle_paths(self) -> List[str]:
        paths: List[str] = []
        for task in self.tasks:
            paths.extend(task.bundle_paths)
        return paths

    @model_validator(mode="after")
    def _validate_graph(self) -> "BuildGraph":
        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("build graph contains duplicate task_id values")

        known_ids = set(task_ids)
        errors: List[str] = []
        seen_paths: Dict[str, str] = {}
        for task in self.tasks:
            for dep in task.depends_on:
                if dep not in known_ids:
                    errors.append(
                        f"build task '{task.task_id}' depends on unknown task '{dep}'"
                    )
            for path in task.bundle_paths:
                prior = seen_paths.get(path)
                if prior is not None:
                    errors.append(
                        f"bundle path '{path}' is assigned to both build tasks '{prior}' and "
                        f"'{task.task_id}'"
                    )
                    continue
                seen_paths[path] = task.task_id

        for task_id in self.entry_tasks:
            if task_id not in known_ids:
                errors.append(f"entry_tasks references unknown task '{task_id}'")
        for task_id in self.terminal_tasks:
            if task_id not in known_ids:
                errors.append(f"terminal_tasks references unknown task '{task_id}'")

        if errors:
            raise ValueError("; ".join(errors))
        return self


class DecompositionPackage(PlanningBaseModel):
    app_spec: AppSpec
    capabilities: List[Capability] = Field(default_factory=list)
    entities: List[EntitySpec] = Field(default_factory=list)
    views: List[ViewSpec] = Field(default_factory=list)
    actions: List[ActionSpec] = Field(default_factory=list)
    modules: List[ModuleSpec] = Field(default_factory=list)
    events: List[DomainEventSpec] = Field(default_factory=list)
    automation_routes: List[AutomationRouteSpec] = Field(default_factory=list)
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
        event_names = self._names(self.events, attr="event_type")
        route_names = self._names(self.automation_routes, attr="route_id")
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
            for name in cap.event_refs:
                if name not in event_names:
                    errors.append(
                        f"capability '{cap.capability_id}' references unknown event '{name}'"
                    )
            for name in cap.automation_route_refs:
                if name not in route_names:
                    errors.append(
                        f"capability '{cap.capability_id}' references unknown automation route "
                        f"'{name}'"
                    )

        for route in self.automation_routes:
            if route.event_type not in event_names:
                errors.append(
                    f"automation route '{route.route_id}' references unknown event_type "
                    f"'{route.event_type}'"
                )
            if route.effect.workflow and route.effect.workflow not in workflow_names:
                errors.append(
                    f"automation route '{route.route_id}' references unknown workflow "
                    f"'{route.effect.workflow}'"
                )

        if errors:
            raise ValueError("; ".join(errors))
        return self


class BuilderBlueprint(PlanningBaseModel):
    concept_blueprint: ConceptBlueprint
    intent_brief: IntentBrief
    capability_map: CapabilityMap
    platform_provision_plan: PlatformProvisionPlan
    decomposition: DecompositionPackage
    build_graph: BuildGraph
    impact_set: Optional[ImpactSet] = None

    @model_validator(mode="after")
    def _validate_alignment(self) -> "BuilderBlueprint":
        capability_map_ids = {item.capability_id for item in self.capability_map.capabilities}
        decomposition_ids = {cap.capability_id for cap in self.decomposition.capabilities}
        provision_ids = {item.provision_id for item in self.platform_provision_plan.provisions}
        workflow_names = {workflow.name for workflow in self.decomposition.workflows}
        errors: List[str] = []

        if self.concept_blueprint.app_name != self.decomposition.app_spec.name:
            errors.append(
                "concept_blueprint.app_name must match decomposition.app_spec.name"
            )

        if self.concept_blueprint.intent_mode is BuilderIntentMode.CHANGE and self.impact_set is None:
            errors.append("builder blueprint with intent_mode='change' requires impact_set")

        missing_in_decomposition = sorted(capability_map_ids - decomposition_ids)
        if missing_in_decomposition:
            errors.append(
                "capability_map contains capability_ids missing from decomposition: "
                + ", ".join(missing_in_decomposition)
            )

        missing_in_map = sorted(decomposition_ids - capability_map_ids)
        if missing_in_map:
            errors.append(
                "decomposition contains capability_ids missing from capability_map: "
                + ", ".join(missing_in_map)
            )

        covered_capabilities: Set[str] = set()
        covered_provisions: Set[str] = set()
        for task in self.build_graph.tasks:
            for capability_id in task.capability_refs:
                if capability_id not in decomposition_ids:
                    errors.append(
                        f"build task '{task.task_id}' references unknown capability "
                        f"'{capability_id}'"
                    )
                    continue
                covered_capabilities.add(capability_id)
            for provision_id in task.provision_refs:
                if provision_id not in provision_ids:
                    errors.append(
                        f"build task '{task.task_id}' references unknown platform provision "
                        f"'{provision_id}'"
                    )
                    continue
                covered_provisions.add(provision_id)

        missing_capability_coverage = sorted(decomposition_ids - covered_capabilities)
        if missing_capability_coverage:
            errors.append(
                "capabilities without build task coverage: "
                + ", ".join(missing_capability_coverage)
            )

        active_provision_ids = {
            provision.provision_id
            for provision in self.platform_provision_plan.provisions
            if provision.mode is not PlatformProvisionMode.DISABLED
        }
        missing_provision_coverage = sorted(active_provision_ids - covered_provisions)
        if missing_provision_coverage:
            errors.append(
                "platform provisions without build task coverage: "
                + ", ".join(missing_provision_coverage)
            )

        bundle_plan = self.decomposition.bundle_plan
        if bundle_plan is None:
            errors.append("builder blueprint requires decomposition.bundle_plan")
        else:
            path_family_map: Dict[str, DeclarativeFamily] = {}
            for family, paths in bundle_plan.family_paths().items():
                for path in paths:
                    path_family_map[path] = family

            assigned_paths = set()
            for task in self.build_graph.tasks:
                declared_families = set(task.declarative_families)
                for path in task.bundle_paths:
                    assigned_paths.add(path)
                    planned_family = path_family_map.get(path)
                    if planned_family is None:
                        errors.append(
                            f"build task '{task.task_id}' owns unplanned bundle path '{path}'"
                        )
                        continue
                    if planned_family not in declared_families:
                        errors.append(
                            f"build task '{task.task_id}' declares families "
                            f"{sorted(family.value for family in declared_families)} but path "
                            f"'{path}' belongs to '{planned_family.value}'"
                        )

            missing_assignments = sorted(set(bundle_plan.all_paths()) - assigned_paths)
            if missing_assignments:
                errors.append(
                    "bundle plan paths without build task ownership: "
                    + ", ".join(missing_assignments)
                )

            if self.impact_set is not None:
                for capability_id in self.impact_set.affected_capability_ids:
                    if capability_id not in decomposition_ids:
                        errors.append(
                            f"impact_set references unknown capability '{capability_id}'"
                        )
                for provision_id in self.impact_set.affected_provision_ids:
                    if provision_id not in provision_ids:
                        errors.append(
                            f"impact_set references unknown platform provision '{provision_id}'"
                        )
                for workflow_name in self.impact_set.affected_workflows:
                    if workflow_name not in workflow_names:
                        errors.append(
                            f"impact_set references unknown workflow '{workflow_name}'"
                        )

                known_paths = set(bundle_plan.all_paths())
                for provision in self.platform_provision_plan.provisions:
                    known_paths.update(provision.config_paths)
                    known_paths.update(provision.stub_paths)
                for path in self.impact_set.affected_bundle_paths:
                    if path not in known_paths:
                        errors.append(
                            f"impact_set references unknown bundle path '{path}'"
                        )

        if errors:
            raise ValueError("; ".join(errors))
        return self


def build_decomposition_package(payload: Dict[str, Any]) -> DecompositionPackage:
    return DecompositionPackage.model_validate(payload)


def build_builder_blueprint(payload: Dict[str, Any]) -> BuilderBlueprint:
    return BuilderBlueprint.model_validate(payload)


__all__ = [
    "PrimarySurface",
    "ViewType",
    "ActionType",
    "AutomationEffectKind",
    "DeclarativeFamily",
    "BuilderWorkflowRole",
    "BuilderIntentMode",
    "PlatformProvisionMode",
    "IntentBrief",
    "ConceptBlueprint",
    "PlatformProvisionSpec",
    "PlatformProvisionPlan",
    "ImpactSet",
    "CapabilityMapItem",
    "CapabilityMap",
    "AppSpec",
    "Capability",
    "EntityFieldSpec",
    "EntitySpec",
    "ViewSpec",
    "ActionSpec",
    "ModuleSpec",
    "DomainEventSpec",
    "AutomationEffectSpec",
    "AutomationRouteSpec",
    "WorkflowSpec",
    "PolicySpec",
    "BundlePlan",
    "BuildTask",
    "BuildGraph",
    "DecompositionPackage",
    "BuilderBlueprint",
    "build_decomposition_package",
    "build_builder_blueprint",
]
