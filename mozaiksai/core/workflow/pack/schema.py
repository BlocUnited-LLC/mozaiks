from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

# ---------------------------------------------------------------------------
# Workflow routing transitions — user-choice and non-chat routing moments that
# live in the global pack graph (extension_registry.json), NOT in individual
# workflow orchestrator.yaml files. Transitions are the workflow-routing layer;
# handoffs.yaml is the agent-routing layer. These are distinct concerns.
# ---------------------------------------------------------------------------


class TransitionUIBinding(BaseModel):
    """Registered shell surface used to render a transition."""

    model_config = ConfigDict(extra="forbid")

    component: str
    mode: Literal["screen"] = "screen"
    props: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("component")
    @classmethod
    def _validate_component(cls, value: str) -> str:
        component = str(value or "").strip()
        if not component:
            raise ValueError("transition ui component must be non-empty")
        lowered = component.lower()
        if any(token in component for token in ("/", "\\")) or lowered.endswith(
            (".jsx", ".tsx", ".js", ".ts")
        ):
            raise ValueError(
                "transition ui component must be a registry key, not a file path"
            )
        if not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", component):
            raise ValueError(
                "transition ui component must be a simple component registry key"
            )
        return component

    @field_validator("props")
    @classmethod
    def _validate_props(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("transition ui props must be an object")
        return value


class TransitionOption(BaseModel):
    """A single selectable route option in a user_choice transition."""

    model_config = ConfigDict(extra="forbid")

    id: str
    route_to: Optional[str] = None  # workflow id OR transition id
    route_type: Literal["transition", "workflow"] = "workflow"  # stamped at load time
    context_variables: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        val = str(value or "").strip()
        if not val:
            raise ValueError("TransitionOption id must be non-empty")
        return val

    @field_validator("route_to")
    @classmethod
    def _normalize_route_to(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        val = str(value or "").strip()
        return val or None

    @field_validator("context_variables")
    @classmethod
    def _validate_context_variables(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("TransitionOption context_variables must be an object")
        for key in value:
            if not isinstance(key, str) or not key.strip():
                raise ValueError("TransitionOption context variable keys must be non-empty strings")
        return value


class ConditionRoute(BaseModel):
    """A single branch in a condition transition."""

    model_config = ConfigDict(extra="forbid")

    match: Any  # value to compare against context_key
    route_to: str  # workflow id OR transition id
    route_type: Literal["transition", "workflow"] = "workflow"  # stamped at load time


class WorkflowTransition(BaseModel):
    """A router-driven transition point between workflows.

    transition_type values:
      user_choice           — mounts a registered React component; user picks a path
      condition             — auto-routes based on a context variable (no UI)
      confirm               — mounts a registered React component for yes/cancel prompt
      silent                — router continues with no UI surface
      progress_view         — optional progress UI before continuing
      prerequisite_redirect — optional UI explaining a prerequisite redirect

    UI resolution (shell responsibility):
      1. Look up transition.ui.component in the component registry.
      2. If not found, fall back to the built-in "LauncherScreen" (user_choice)
         or "ConfirmScreen" (confirm).
      3. Mount the component with props: { transition, onResolve }
         - transition: the full WorkflowTransition object
         - onResolve: (option_id: str) => void
              fires routing.transition.resolve event, shell executes the routing
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    transition_type: Literal[
        "user_choice",
        "user_choice_context",
        "user_choice_route",
        "condition",
        "confirm",
        "silent",
        "progress_view",
        "prerequisite_redirect",
    ]
    ui: Optional[TransitionUIBinding] = None

    # user_choice / confirm routing data
    options: List[TransitionOption] = Field(default_factory=list)

    # single-route transition data
    route_to: Optional[str] = None
    route_type: Optional[Literal["transition", "workflow"]] = None

    # condition transition data
    context_key: Optional[str] = None  # context_variable key to evaluate
    routes: List[ConditionRoute] = Field(default_factory=list)
    default_route: Optional[str] = None  # fallback workflow/transition id

    # confirm transition data
    confirm_route: Optional[str] = None  # workflow/transition on confirm
    cancel_route: Optional[str] = None  # workflow/transition on cancel (optional)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        val = str(value or "").strip()
        if not val:
            raise ValueError("transition id must be non-empty")
        return val

    @model_validator(mode="after")
    def _validate_type_fields(self) -> "WorkflowTransition":
        if self.transition_type in {"user_choice", "user_choice_context", "user_choice_route"} and not self.options:
            raise ValueError(f"{self.transition_type} transition must have at least one option")
        if self.transition_type in {"user_choice", "user_choice_context", "user_choice_route", "confirm"} and self.ui is None:
            raise ValueError(f"{self.transition_type} transition requires ui")
        if self.transition_type == "user_choice":
            missing = [
                opt.id
                for opt in self.options
                if not (isinstance(opt.route_to, str) and opt.route_to.strip())
            ]
            if missing and not (isinstance(self.route_to, str) and self.route_to.strip()):
                raise ValueError(
                    f"user_choice transition options require route_to: {', '.join(missing)}"
                )
        if self.transition_type == "user_choice_context":
            if not isinstance(self.route_to, str) or not self.route_to.strip():
                if not any(isinstance(opt.route_to, str) and opt.route_to.strip() for opt in self.options):
                    raise ValueError("user_choice_context transition requires route_to or per-option route_to")
        if self.transition_type == "user_choice_route":
            missing = [opt.id for opt in self.options if not (isinstance(opt.route_to, str) and opt.route_to.strip())]
            if missing:
                raise ValueError(
                    f"user_choice_route transition options require route_to: {', '.join(missing)}"
                )
        if self.transition_type == "condition":
            if not self.context_key:
                raise ValueError("condition transition requires context_key")
            if not self.routes and not self.default_route:
                raise ValueError("condition transition requires routes or default_route")
        if self.transition_type == "confirm" and not (
            self.confirm_route or any(opt.id in {"confirm", "cancel"} for opt in self.options)
        ):
            raise ValueError("confirm transition requires confirm_route or confirm/cancel options")
        if self.transition_type in {"silent", "progress_view", "prerequisite_redirect"}:
            if not isinstance(self.route_to, str) or not self.route_to.strip():
                raise ValueError(f"{self.transition_type} transition requires route_to")
        return self


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


def _slugify_identifier(value: Optional[str], default: str) -> str:
    """Create a stable lowercase identifier from free-form text."""
    if not isinstance(value, str) or not value.strip():
        return default
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip()).strip("_").lower()
    return slug or default


def _normalize_inject_key(value: Optional[str], *, fallback: str) -> str:
    """
    Normalize inject keys to the required mfj_* shape.

    If omitted, a deterministic fallback key is derived from journey/stage ids.
    """
    raw = str(value or "").strip() or fallback
    if raw.startswith("mfj_"):
        return raw
    return f"mfj_{_slugify_identifier(raw, 'results')}"


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


class WorkflowEntrypoint(BaseModel):
    """Shell route owned by the workflow registry.

    Use this for routes that enter a workflow journey or transition. Persistent
    product pages are owned by page/UI manifests, not this registry.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    path: str
    label: str = ""
    transition: Optional[str] = None
    workflow: Optional[str] = None
    sequence: Optional[str] = None
    requiresAuth: bool = True
    order: int = 100
    meta: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        val = str(value or "").strip()
        if not val:
            raise ValueError("entrypoint id must be non-empty")
        return val

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        path = str(value or "").strip()
        if not path.startswith("/"):
            raise ValueError("entrypoint path must start with '/'")
        return path

    @field_validator("transition", "workflow", "sequence")
    @classmethod
    def _normalize_optional_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        val = str(value or "").strip()
        return val or None

    @field_validator("meta")
    @classmethod
    def _validate_meta(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("entrypoint meta must be an object")
        return value

    @model_validator(mode="after")
    def _validate_target(self) -> "WorkflowEntrypoint":
        if bool(self.transition) == bool(self.workflow):
            raise ValueError("entrypoint must declare exactly one of transition or workflow")
        return self


class JourneyStepGroup(BaseModel):
    """A single serial journey phase containing one or more workflows or one transition."""

    model_config = ConfigDict(extra="forbid")

    workflows: List[str] = Field(default_factory=list)
    transition: Optional[str] = None

    @field_validator("workflows")
    @classmethod
    def _validate_workflows(cls, value: List[str]) -> List[str]:
        if not isinstance(value, list):
            raise ValueError("journey step group workflows must be a list")
        normalized: List[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("journey step group workflows must be non-empty strings")
            normalized.append(item.strip())
        return normalized

    @field_validator("transition")
    @classmethod
    def _validate_transition(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        transition = str(value or "").strip()
        return transition or None

    @model_validator(mode="after")
    def _validate_step_shape(self) -> "JourneyStepGroup":
        has_workflows = bool(self.workflows)
        has_transition = bool(self.transition)
        if has_workflows == has_transition:
            raise ValueError("journey step must declare exactly one of workflows or transition")
        return self


class GlobalJourney(BaseModel):
    """Across-workflow sequencing definition used for runtime auto-advance."""

    model_config = ConfigDict(extra="forbid")

    id: str
    description: Optional[str] = None
    steps: List[JourneyStepGroup] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        val = str(value or "").strip()
        if not val:
            raise ValueError("journey id must be a non-empty string")
        return val

    @field_validator("steps")
    @classmethod
    def _validate_steps(cls, value: List[JourneyStepGroup]) -> List[JourneyStepGroup]:
        if not isinstance(value, list) or not value:
            raise ValueError("journey steps must be a non-empty list")
        return value


class GlobalPackGraph(BaseModel):
    """Canonical global pack graph.

    Three registries:
      workflows — workflow entries with dependency declarations
      workflow_sequences
                  — ordered workflow step groups used only for auto-advance.
                    Entry UI belongs to entrypoints[] routes that reference transitions directly.
      transitions — routing decision points between workflows and phases.
                    Individual workflows must NOT declare their own launcher UI.
    """

    model_config = ConfigDict(extra="forbid")

    version: Literal[3]
    pack_name: Optional[str] = None
    description: Optional[str] = None
    workflows: List[WorkflowEntry] = Field(default_factory=list)
    entrypoints: List[WorkflowEntrypoint] = Field(default_factory=list)
    journeys: List[GlobalJourney] = Field(default_factory=list)
    transitions: List[WorkflowTransition] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_uniqueness_and_refs(self) -> "GlobalPackGraph":
        wf_ids: List[str] = [w.id for w in self.workflows]
        if len(wf_ids) != len(set(wf_ids)):
            raise ValueError("global pack graph contains duplicate workflow ids")
        entrypoint_ids: List[str] = [e.id for e in self.entrypoints]
        if len(entrypoint_ids) != len(set(entrypoint_ids)):
            raise ValueError("global pack graph contains duplicate entrypoint ids")
        entrypoint_paths: List[str] = [e.path for e in self.entrypoints]
        if len(entrypoint_paths) != len(set(entrypoint_paths)):
            raise ValueError("global pack graph contains duplicate entrypoint paths")
        transition_ids: List[str] = [t.id for t in self.transitions]
        if len(transition_ids) != len(set(transition_ids)):
            raise ValueError("global pack graph contains duplicate transition ids")
        journey_ids: List[str] = [j.id for j in self.journeys]
        if len(journey_ids) != len(set(journey_ids)):
            raise ValueError("global pack graph contains duplicate journey ids")
        valid_targets = set(wf_ids) | set(transition_ids)
        transition_id_set = set(transition_ids)
        journey_id_set = set(journey_ids)
        for entry in self.workflows:
            for dependency in entry.dependencies:
                dep_id = dependency.id if isinstance(dependency, WorkflowDependency) else str(dependency)
                dep_id = dep_id.strip()
                if dep_id and dep_id not in wf_ids:
                    raise ValueError(
                        f"workflow '{entry.id}' dependency '{dep_id}' is not a known workflow id"
                    )
        for entrypoint in self.entrypoints:
            if entrypoint.transition and entrypoint.transition not in transition_id_set:
                raise ValueError(
                    f"entrypoint '{entrypoint.id}' transition '{entrypoint.transition}' "
                    "is not a known transition id"
                )
            if entrypoint.workflow and entrypoint.workflow not in wf_ids:
                raise ValueError(
                    f"entrypoint '{entrypoint.id}' workflow '{entrypoint.workflow}' "
                    "is not a known workflow id"
                )
            if entrypoint.sequence and entrypoint.sequence not in journey_id_set:
                raise ValueError(
                    f"entrypoint '{entrypoint.id}' sequence '{entrypoint.sequence}' "
                    "is not a known workflow_sequence id"
                )
        for transition in self.transitions:
            if transition.route_to and transition.route_to not in valid_targets:
                raise ValueError(
                    f"transition '{transition.id}' route_to '{transition.route_to}' "
                    "is not a known workflow or transition id"
                )
            if transition.route_to:
                object.__setattr__(
                    transition,
                    "route_type",
                    "transition" if transition.route_to in transition_id_set else "workflow",
                )
            for opt in getattr(transition, "options", []):
                if not opt.route_to:
                    continue
                if opt.route_to not in valid_targets:
                    raise ValueError(
                        f"transition '{transition.id}' option '{opt.id}' route_to "
                        f"'{opt.route_to}' is not a known workflow or transition id"
                    )
                object.__setattr__(
                    opt,
                    "route_type",
                    "transition" if opt.route_to in transition_id_set else "workflow",
                )
            for route in getattr(transition, "routes", []):
                if route.route_to not in valid_targets:
                    raise ValueError(
                        f"transition '{transition.id}' condition route '{route.route_to}' "
                        "is not a known workflow or transition id"
                    )
                object.__setattr__(
                    route,
                    "route_type",
                    "transition" if route.route_to in transition_id_set else "workflow",
                )
            if transition.default_route and transition.default_route not in valid_targets:
                raise ValueError(
                    f"transition '{transition.id}' default_route '{transition.default_route}' "
                    "is not a known workflow or transition id"
                )
            if transition.confirm_route and transition.confirm_route not in valid_targets:
                raise ValueError(
                    f"transition '{transition.id}' confirm_route '{transition.confirm_route}' "
                    "is not a known workflow or transition id"
                )
            if transition.cancel_route and transition.cancel_route not in valid_targets:
                raise ValueError(
                    f"transition '{transition.id}' cancel_route '{transition.cancel_route}' "
                    "is not a known workflow or transition id"
                )
        workflow_entries = {workflow.id: workflow for workflow in self.workflows}
        for journey in self.journeys:
            group_index_by_workflow: Dict[str, int] = {}
            for index, group in enumerate(journey.steps):
                if group.transition and group.transition not in transition_id_set:
                    raise ValueError(
                        f"journey '{journey.id}' references unknown transition '{group.transition}'"
                    )
                for workflow_id in group.workflows:
                    if workflow_id not in workflow_entries:
                        raise ValueError(
                            f"journey '{journey.id}' references unknown workflow '{workflow_id}'"
                        )
                    if workflow_id in group_index_by_workflow:
                        raise ValueError(
                            f"journey '{journey.id}' references workflow '{workflow_id}' more than once"
                        )
                    group_index_by_workflow[workflow_id] = index
            for workflow_id, group_index in group_index_by_workflow.items():
                entry = workflow_entries[workflow_id]
                for dependency in entry.dependencies:
                    if isinstance(dependency, WorkflowDependency):
                        if dependency.gating != "required":
                            continue
                        dep_id = dependency.id
                    else:
                        dep_id = str(dependency)
                    dep_id = dep_id.strip()
                    if not dep_id or dep_id not in group_index_by_workflow:
                        continue
                    dep_group_index = group_index_by_workflow[dep_id]
                    if dep_group_index >= group_index:
                        raise ValueError(
                            f"journey '{journey.id}' places workflow '{workflow_id}' "
                            f"before or with required dependency '{dep_id}'"
                        )
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
    # Defaults to resume_agent when omitted — only set explicitly when a dedicated
    # router agent (e.g. ResumeRouterAgent) should receive control first.
    resume_entry_agent: Optional[str] = None
    aggregation_strategy: str = "collect_all"
    inject_as: Optional[str] = None
    on_partial_failure: Literal[
        "resume_with_available",
        "fail_all",
        "retry_failed",
        "prompt_user",
    ] = "resume_with_available"
    timeout_seconds: int = 60

    @model_validator(mode="after")
    def _coerce_resume_entry_agent(self) -> "MFJFanInConfig":
        if not self.resume_entry_agent:
            self.resume_entry_agent = self.resume_agent
        if self.inject_as is not None:
            self.inject_as = _normalize_inject_key(self.inject_as, fallback="results")
        return self

    @field_validator("resume_agent")
    @classmethod
    def _validate_resume_agent(cls, value: str) -> str:
        val = str(value or "").strip()
        if not val:
            raise ValueError("fan_in.resume_agent must be non-empty")
        return val

    @field_validator("inject_as", mode="before")
    @classmethod
    def _validate_inject_as(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("fan_in.inject_as must be a string when provided")
        val = value.strip()
        return val or None

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


class MFJJourneyStage(BaseModel):
    """A single fan-out/fan-in phase inside a multi-stage MFJ journey.

    Stage 0 is triggered by the parent journey's decomposition_agent.
    Subsequent stages require a gate_agent — the agent whose output signals
    that this stage should begin (e.g. after user approval).
    Shared spawn config (spawn_mode, max_children) lives on the parent journey's fan_out.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    gate_agent: Optional[str] = None
    child_initial_agent: str
    resume_agent: str
    inject_as: Optional[str] = None

    @field_validator("id", "child_initial_agent", "resume_agent")
    @classmethod
    def _validate_non_empty(cls, value: str) -> str:
        val = str(value or "").strip()
        if not val:
            raise ValueError("stage string fields must be non-empty")
        return val

    @field_validator("inject_as", mode="before")
    @classmethod
    def _validate_inject_as(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("stage.inject_as must be a string when provided")
        val = value.strip()
        return val or None


class MidFlightJourney(BaseModel):
    """Canonical per-workflow MFJ trigger config.

    Two authoring modes:
    - Flat (fan_out + fan_in required): one fan-out/fan-in cycle per journey.
    - Staged (stages required, fan_in absent): N fan-out/fan-in cycles driven by a
      single decomposition_agent decomposition. The schema expands staged journeys into
      flat journeys internally — the coordinator always sees the flat format.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    description: Optional[str] = None
    decomposition_agent: str
    trigger_on: Literal["decomposition_event"] = "decomposition_event"
    requires: List[str] = Field(default_factory=list)
    fan_out: Optional[MFJFanOutConfig] = None
    fan_in: Optional[MFJFanInConfig] = None
    stages: Optional[List[MFJJourneyStage]] = None
    output_contract: MFJContract = Field(default_factory=MFJContract)

    @field_validator("id", "decomposition_agent")
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

    @model_validator(mode="after")
    def _validate_mode(self) -> "MidFlightJourney":
        has_stages = bool(self.stages)
        if has_stages:
            if self.fan_in is not None:
                raise ValueError(
                    "staged journey cannot have a top-level fan_in — "
                    "each stage declares its own resume_agent and inject_as"
                )
            if self.fan_out is None:
                raise ValueError(
                    "staged journey requires fan_out for shared spawn config (spawn_mode, max_children)"
                )
            for i, stage in enumerate(self.stages):
                if i > 0 and not stage.gate_agent:
                    raise ValueError(
                        f"stage '{stage.id}' (position {i}) requires gate_agent — "
                        "only the first stage can omit it"
                    )
        else:
            if self.fan_out is None or self.fan_in is None:
                raise ValueError("non-staged journey requires both fan_out and fan_in")
            self.fan_in.inject_as = _normalize_inject_key(
                self.fan_in.inject_as,
                fallback=f"{self.id}_results",
            )
        return self


def _expand_staged_journeys(journeys: List[MidFlightJourney]) -> List[MidFlightJourney]:
    """Expand staged journeys into flat MidFlightJourney objects.

    The coordinator always processes the flat format. staged journeys are a
    DX-only concept that compiles away at schema-load time. Each stage becomes
    its own flat journey with the correct decomposition_agent and requires chain.
    """
    result: List[MidFlightJourney] = []
    for journey in journeys:
        if not journey.stages:
            result.append(journey)
            continue

        base = journey.fan_out  # shared spawn config
        prev_id: Optional[str] = None

        for i, stage in enumerate(journey.stages):
            trigger = journey.decomposition_agent if i == 0 else stage.gate_agent
            stage_full_id = f"{journey.id}.{stage.id}"
            requires = [prev_id] if prev_id else list(journey.requires)
            stage_inject = _normalize_inject_key(
                stage.inject_as,
                fallback=f"{journey.id}_{stage.id}_results",
            )

            stage_fan_out = MFJFanOutConfig(
                spawn_mode=base.spawn_mode,
                child_initial_agent=stage.child_initial_agent,
                max_children=base.max_children,
                authoring_workflow=base.authoring_workflow,
            )
            stage_fan_in = MFJFanInConfig(
                resume_agent=stage.resume_agent,
                inject_as=stage_inject,
            )

            result.append(
                MidFlightJourney(
                    id=stage_full_id,
                    description=journey.description,
                    decomposition_agent=trigger,
                    fan_out=stage_fan_out,
                    fan_in=stage_fan_in,
                    requires=requires,
                )
            )
            prev_id = stage_full_id

    return result


class WorkflowPackGraph(BaseModel):
    """Canonical per-workflow pack graph."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[3]
    mid_flight_journeys: List[MidFlightJourney] = Field(default_factory=list)

    @model_validator(mode="after")
    def _expand_and_validate(self) -> "WorkflowPackGraph":
        self.mid_flight_journeys = _expand_staged_journeys(self.mid_flight_journeys)
        ids: List[str] = [j.id for j in self.mid_flight_journeys]
        if len(ids) != len(set(ids)):
            raise ValueError("workflow pack graph contains duplicate mid_flight_journey ids")
        return self


def normalize_step_groups(steps: List[JourneyStepGroup]) -> List[List[str]]:
    """Normalize GlobalJourney steps to grouped execution layers.

    Transition checkpoint steps are represented as empty groups so journey
    positions remain aligned with the authored step index.
    """
    return [list(step.workflows) for step in steps]


def parse_global_pack_graph(raw: Dict[str, Any]) -> GlobalPackGraph:
    normalized: Dict[str, Any] = dict(raw or {})
    if "journeys" in normalized:
        raise ValueError(
            "Invalid global pack graph: 'journeys' is no longer supported; use "
            "'workflow_sequences'."
        )

    if "workflow_sequences" in normalized:
        normalized["journeys"] = normalized.pop("workflow_sequences")

    try:
        return GlobalPackGraph.model_validate(normalized)
    except ValidationError as exc:
        raise ValueError(f"Invalid global pack graph: {exc}") from exc


def parse_workflow_pack_graph(raw: Dict[str, Any]) -> WorkflowPackGraph:
    try:
        return WorkflowPackGraph.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid workflow pack graph: {exc}") from exc


__all__ = [
    # Routing transitions
    "TransitionUIBinding",
    "TransitionOption",
    "ConditionRoute",
    "WorkflowTransition",
    # Global pack graph
    "WorkflowDependency",
    "WorkflowEntry",
    "WorkflowEntrypoint",
    "JourneyStepGroup",
    "GlobalJourney",
    "GlobalPackGraph",
    # Per-workflow MFJ pack graph
    "MFJContract",
    "MFJJourneyStage",
    "MFJFanOutConfig",
    "MFJFanInConfig",
    "MidFlightJourney",
    "WorkflowPackGraph",
    # Helpers
    "normalize_step_groups",
    "parse_global_pack_graph",
    "parse_workflow_pack_graph",
]
