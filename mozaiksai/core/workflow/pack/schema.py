from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

# ---------------------------------------------------------------------------
# Workflow routing transitions — user-choice and non-chat routing moments that
# live in the global pack graph (extension_registry.json), NOT in individual
# workflow orchestrator.yaml files. Transitions are the workflow-routing layer;
# transition_graph.yaml is the agent-routing layer. These are distinct concerns.
# ---------------------------------------------------------------------------


class TransitionUIBinding(BaseModel):
    """Registered shell surface used to render a transition."""

    model_config = ConfigDict(extra="forbid")

    component: str
    mode: Literal["screen"] = "screen"
    shell_mode: Literal["standard", "workspace", "conversation", "focused", "immersive", "public"] | None = None
    props: dict[str, Any] = Field(default_factory=dict)

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
    def _validate_props(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("transition ui props must be an object")
        return value


class TransitionOption(BaseModel):
    """A single selectable route option in a user_choice transition."""

    model_config = ConfigDict(extra="forbid")

    id: str
    route_to: str | None = None  # workflow id OR transition id
    route_type: Literal["transition", "workflow"] = "workflow"  # stamped at load time
    sequence: str | None = None
    context_variables: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        val = str(value or "").strip()
        if not val:
            raise ValueError("TransitionOption id must be non-empty")
        return val

    @field_validator("route_to")
    @classmethod
    def _normalize_route_to(cls, value: str | None) -> str | None:
        if value is None:
            return None
        val = str(value or "").strip()
        return val or None

    @field_validator("sequence")
    @classmethod
    def _normalize_sequence(cls, value: str | None) -> str | None:
        if value is None:
            return None
        val = str(value or "").strip()
        return val or None

    @field_validator("context_variables")
    @classmethod
    def _validate_context_variables(cls, value: dict[str, Any]) -> dict[str, Any]:
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
    sequence: str | None = None

    @field_validator("route_to", "sequence")
    @classmethod
    def _normalize_optional_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        val = str(value or "").strip()
        return val or None


class WorkflowTransition(BaseModel):
    """A router-driven transition point between workflows.

    transition_type values:
      user_choice           — mounts a registered React component; user picks a path
      condition             — auto-routes based on a context variable (no UI)
      confirm               — mounts a registered React component for yes/cancel prompt
      silent                — router continues with no UI surface
      progress_view         — optional progress UI before continuing
      prerequisite_redirect — optional UI explaining a prerequisite redirect
      chat_session          — launches route_to workflow in the current chat surface
                               with no blocking overlay; user can type freely
      workflow_complete     — terminal: shell fires this client-side when a workflow
                               run finishes and no server transition is pending.
                               Renders the registered completion component; resolves
                               client-side only — no /api/transitions/resolve call.

    UI resolution (shell responsibility):
      1. Look up transition.ui.component in the component registry.
      2. If not found, fall back to the built-in "LauncherScreen" (user_choice)
         or "ConfirmScreen" (confirm).
      3. Mount the component with props: { transition, onResolve }
         - transition: the full WorkflowTransition object
         - onResolve: (option_id: str) => void
              fires routing.transition.resolve event, shell executes the routing

    chat_session resolution (shell responsibility):
      TransitionScreen detects chat_session, calls onResolve(null) immediately
      without mounting UI. Shell calls /api/transitions/resolve and receives
      resolution_type: "chat_session" with chat_id, workflow_id, and
      context_variables. Shell switches active chat session in-place.
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
        "chat_session",
        "workflow_complete",
    ]
    ui: TransitionUIBinding | None = None

    # user_choice / confirm routing data
    options: list[TransitionOption] = Field(default_factory=list)

    # single-route transition data
    route_to: str | None = None
    route_type: Literal["transition", "workflow"] | None = None
    sequence: str | None = None

    # condition transition data
    context_key: str | None = None  # context_variable key to evaluate
    routes: list[ConditionRoute] = Field(default_factory=list)
    default_route: str | None = None  # fallback workflow/transition id

    # confirm transition data
    confirm_route: str | None = None  # workflow/transition on confirm
    cancel_route: str | None = None  # workflow/transition on cancel (optional)

    # optional transitions may be skipped by the shell without user interaction
    optional: bool = False

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        val = str(value or "").strip()
        if not val:
            raise ValueError("transition id must be non-empty")
        return val

    @field_validator("sequence")
    @classmethod
    def _normalize_sequence(cls, value: str | None) -> str | None:
        if value is None:
            return None
        val = str(value or "").strip()
        return val or None

    @model_validator(mode="after")
    def _validate_type_fields(self) -> WorkflowTransition:
        if self.transition_type in {"user_choice", "user_choice_context", "user_choice_route"} and not self.options:
            raise ValueError(f"{self.transition_type} transition must have at least one option")
        if self.transition_type in {"user_choice", "user_choice_context", "user_choice_route", "confirm"} and self.ui is None:
            raise ValueError(f"{self.transition_type} transition requires ui")
        if self.transition_type in {"user_choice", "user_choice_context", "user_choice_route"}:
            if isinstance(self.route_to, str) and self.route_to.strip():
                raise ValueError(
                    f"{self.transition_type} transition must use options[].route_to instead of top-level route_to"
                )
            missing = [
                opt.id
                for opt in self.options
                if not (isinstance(opt.route_to, str) and opt.route_to.strip())
            ]
            if missing:
                raise ValueError(
                    f"user_choice transition options require route_to: {', '.join(missing)}"
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
        if self.transition_type == "chat_session":
            if not isinstance(self.route_to, str) or not self.route_to.strip():
                raise ValueError("chat_session transition requires route_to pointing to a workflow id")
            if self.ui is not None:
                raise ValueError("chat_session transition must not declare a ui block — it never renders an overlay")
            if self.options:
                raise ValueError("chat_session transition must not declare options")
            if self.confirm_route or self.cancel_route:
                raise ValueError("chat_session transition must not declare confirm_route or cancel_route")
            if self.context_key or self.routes:
                raise ValueError("chat_session transition must not declare context_key or routes")
        return self


class WorkflowDependency(BaseModel):
    """Explicit dependency edge in the global pack graph."""

    model_config = ConfigDict(extra="forbid")

    id: str
    scope: Literal["app", "user"] = "app"
    reason: str | None = None
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
    description: str | None = None
    startup_mode: Literal["UserDriven", "AgentDriven", "BackendOnly"] | None = None
    dependencies: list[str | WorkflowDependency] = Field(
        default_factory=list,
        description=(
            "Workflow execution-gating dependencies. Declares which workflows must "
            "complete before this one can start. Enforced by the session router at "
            "route_trigger() time. Distinct from artifact_dependency_graph, which "
            "propagates artifact staleness after a refinement change — not execution order."
        ),
    )

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
    transition: str | None = None
    workflow: str | None = None
    sequence: str | None = None
    requiresAuth: bool = True
    order: int = 100
    meta: dict[str, Any] = Field(default_factory=dict)

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
    def _normalize_optional_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        val = str(value or "").strip()
        return val or None

    @field_validator("meta")
    @classmethod
    def _validate_meta(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("entrypoint meta must be an object")
        return value

    @model_validator(mode="after")
    def _validate_target(self) -> WorkflowEntrypoint:
        if bool(self.transition) == bool(self.workflow):
            raise ValueError("entrypoint must declare exactly one of transition or workflow")
        return self


class JourneyStepGroup(BaseModel):
    """A single serial journey phase containing one or more workflows or one transition."""

    model_config = ConfigDict(extra="forbid")

    workflows: list[str] = Field(default_factory=list)
    transition: str | None = None

    @field_validator("workflows")
    @classmethod
    def _validate_workflows(cls, value: list[str]) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("journey step group workflows must be a list")
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("journey step group workflows must be non-empty strings")
            normalized.append(item.strip())
        return normalized

    @field_validator("transition")
    @classmethod
    def _validate_transition(cls, value: str | None) -> str | None:
        if value is None:
            return None
        transition = str(value or "").strip()
        return transition or None

    @model_validator(mode="after")
    def _validate_step_shape(self) -> JourneyStepGroup:
        has_workflows = bool(self.workflows)
        has_transition = bool(self.transition)
        if has_workflows == has_transition:
            raise ValueError("journey step must declare exactly one of workflows or transition")
        return self


class GlobalJourney(BaseModel):
    """Across-workflow sequencing definition used for runtime auto-advance."""

    model_config = ConfigDict(extra="forbid")

    id: str
    description: str | None = None
    affected_declarative_families: list[str] = Field(default_factory=list)
    steps: list[JourneyStepGroup] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        val = str(value or "").strip()
        if not val:
            raise ValueError("journey id must be a non-empty string")
        return val

    @field_validator("steps")
    @classmethod
    def _validate_steps(cls, value: list[JourneyStepGroup]) -> list[JourneyStepGroup]:
        if not isinstance(value, list) or not value:
            raise ValueError("journey steps must be a non-empty list")
        return value

    @field_validator("affected_declarative_families")
    @classmethod
    def _validate_affected_declarative_families(cls, value: list[str]) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("journey affected_declarative_families must be a list")
        normalized: list[str] = []
        for item in value:
            family = str(item or "").strip()
            if family:
                normalized.append(family)
        return normalized


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
    pack_name: str | None = None
    description: str | None = None
    workflows: list[WorkflowEntry] = Field(default_factory=list)
    entrypoints: list[WorkflowEntrypoint] = Field(default_factory=list)
    journeys: list[GlobalJourney] = Field(default_factory=list)
    transitions: list[WorkflowTransition] = Field(default_factory=list)
    artifact_dependency_graph: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Declarative artifact family dependency graph. Each key is an artifact family; "
            "the value lists upstream families it directly depends on. Used by the control "
            "plane to propagate downstream staleness when an upstream family is written."
        ),
    )

    @field_validator("artifact_dependency_graph")
    @classmethod
    def _validate_artifact_dependency_graph(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        if not isinstance(value, dict):
            raise ValueError("artifact_dependency_graph must be an object")
        normalized: dict[str, list[str]] = {}
        for raw_family, raw_dependencies in value.items():
            family = str(raw_family or "").strip()
            if not family:
                raise ValueError("artifact_dependency_graph family ids must be non-empty strings")
            if not isinstance(raw_dependencies, list):
                raise ValueError(
                    f"artifact_dependency_graph family '{family}' dependencies must be a list"
                )
            dependencies: list[str] = []
            for raw_dependency in raw_dependencies:
                dependency = str(raw_dependency or "").strip()
                if not dependency:
                    raise ValueError(
                        f"artifact_dependency_graph family '{family}' dependencies must be non-empty strings"
                    )
                if dependency not in dependencies:
                    dependencies.append(dependency)
            normalized[family] = dependencies
        return normalized

    @model_validator(mode="after")
    def _validate_uniqueness_and_refs(self) -> GlobalPackGraph:
        wf_ids: list[str] = [w.id for w in self.workflows]
        if len(wf_ids) != len(set(wf_ids)):
            raise ValueError("global pack graph contains duplicate workflow ids")
        entrypoint_ids: list[str] = [e.id for e in self.entrypoints]
        if len(entrypoint_ids) != len(set(entrypoint_ids)):
            raise ValueError("global pack graph contains duplicate entrypoint ids")
        entrypoint_paths: list[str] = [e.path for e in self.entrypoints]
        if len(entrypoint_paths) != len(set(entrypoint_paths)):
            raise ValueError("global pack graph contains duplicate entrypoint paths")
        transition_ids: list[str] = [t.id for t in self.transitions]
        if len(transition_ids) != len(set(transition_ids)):
            raise ValueError("global pack graph contains duplicate transition ids")
        journey_ids: list[str] = [j.id for j in self.journeys]
        if len(journey_ids) != len(set(journey_ids)):
            raise ValueError("global pack graph contains duplicate journey ids")
        artifact_families = set(self.artifact_dependency_graph.keys())
        for family, dependencies in self.artifact_dependency_graph.items():
            for dependency in dependencies:
                if dependency not in artifact_families:
                    raise ValueError(
                        f"artifact_dependency_graph family '{family}' references unknown family '{dependency}'"
                    )
        valid_targets = set(wf_ids) | set(transition_ids)
        transition_id_set = set(transition_ids)
        journey_id_set = set(journey_ids)
        for entry in self.workflows:
            for dependency in entry.dependencies:  # type: ignore[assignment]
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
            if transition.sequence and transition.sequence not in journey_id_set:
                raise ValueError(
                    f"transition '{transition.id}' sequence '{transition.sequence}' "
                    "is not a known workflow_sequence id"
                )
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
                if opt.sequence and opt.sequence not in journey_id_set:
                    raise ValueError(
                        f"transition '{transition.id}' option '{opt.id}' sequence '{opt.sequence}' "
                        "is not a known workflow_sequence id"
                    )
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
                if route.sequence and route.sequence not in journey_id_set:
                    raise ValueError(
                        f"transition '{transition.id}' condition route sequence '{route.sequence}' "
                        "is not a known workflow_sequence id"
                    )
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
            group_index_by_workflow: dict[str, int] = {}
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
                for dependency in entry.dependencies:  # type: ignore[assignment]
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
        backend_only_ids = {w.id for w in self.workflows if w.startup_mode == "BackendOnly"}
        if backend_only_ids:
            for journey in self.journeys:
                for group in journey.steps:
                    for wf_id in group.workflows:
                        if wf_id in backend_only_ids:
                            raise ValueError(
                                f"journey '{journey.id}' includes BackendOnly workflow '{wf_id}' — "
                                "BackendOnly workflows are triggered by domain events and must not "
                                "appear in workflow_sequences"
                            )
            for entrypoint in self.entrypoints:
                if entrypoint.workflow and entrypoint.workflow in backend_only_ids:
                    raise ValueError(
                        f"entrypoint '{entrypoint.id}' points to BackendOnly workflow "
                        f"'{entrypoint.workflow}' — BackendOnly workflows are not "
                        "user-reachable and must not have entrypoints"
                    )
        return self


def normalize_step_groups(steps: list[JourneyStepGroup]) -> list[list[str]]:
    """Normalize GlobalJourney steps to grouped execution layers.

    Transition checkpoint steps are represented as empty groups so journey
    positions remain aligned with the authored step index.
    """
    return [list(step.workflows) for step in steps]


def parse_global_pack_graph(raw: dict[str, Any]) -> GlobalPackGraph:
    normalized: dict[str, Any] = dict(raw or {})
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
    # Helpers
    "normalize_step_groups",
    "parse_global_pack_graph",
]
