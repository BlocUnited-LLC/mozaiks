"""
mozaiksai/core/workflow/pack/schema.py Pydantic validator unit tests.

Covers validators tested via model construction:

  TransitionUIBinding._validate_component:
    - empty/whitespace → ValidationError
    - path with "/" → ValidationError
    - ends with .jsx/.tsx/.js/.ts → ValidationError
    - starts with non-alpha char → ValidationError
    - valid component name → accepted, returned

  TransitionOption._non_empty (id):
    - empty/whitespace id → ValidationError
    - valid id → accepted, stripped

  TransitionOption._normalize_route_to / _normalize_sequence:
    - None → None
    - whitespace-only → None
    - valid value → stripped string

  TransitionOption._validate_context_variables:
    - empty-string key → ValidationError
    - valid dict → accepted

  ConditionRoute._normalize_optional_id:
    - None → None
    - whitespace → None
    - valid → stripped

  WorkflowTransition._validate_id:
    - empty/whitespace → ValidationError

  WorkflowTransition._validate_type_fields:
    - user_choice without options → ValidationError
    - user_choice without ui → ValidationError
    - user_choice with top-level route_to → ValidationError
    - user_choice option without route_to → ValidationError
    - silent without route_to → ValidationError
    - condition without context_key → ValidationError
    - condition without routes or default_route → ValidationError
    - confirm without confirm_route → ValidationError
    - chat_session without route_to → ValidationError
    - chat_session with ui → ValidationError
    - chat_session with options → ValidationError
    - valid silent → accepted
    - valid user_choice → accepted
    - valid condition → accepted
    - valid confirm → accepted
    - valid chat_session → accepted

  WorkflowDependency._validate_id:
    - empty → ValidationError
    - valid → accepted

  WorkflowEntry._validate_id:
    - empty → ValidationError
    - valid → accepted

  WorkflowEntrypoint._validate_id / _validate_path:
    - empty id → ValidationError
    - path without leading slash → ValidationError

  WorkflowEntrypoint._normalize_optional_id:
    - whitespace transition/workflow/sequence → None

  WorkflowEntrypoint._validate_target:
    - both transition and workflow → ValidationError
    - neither → ValidationError
    - transition only → accepted
    - workflow only → accepted

  JourneyStepGroup._validate_workflows:
    - empty string in workflows list → ValidationError
    - non-list workflows → ValidationError

  JourneyStepGroup._validate_step_shape:
    - both workflows and transition → ValidationError
    - neither → ValidationError
    - workflows only → accepted
    - transition only → accepted

  GlobalJourney._validate_id:
    - empty → ValidationError

  GlobalJourney._validate_steps:
    - empty list → ValidationError

  GlobalJourney._validate_affected_declarative_families:
    - empty strings filtered out
    - whitespace-only families filtered

  GlobalPackGraph._validate_artifact_dependency_graph:
    - non-dict → ValidationError
    - empty family key → ValidationError
    - non-list dependencies → ValidationError
    - unknown dependency reference → ValidationError
    - valid → accepted, normalized

  GlobalPackGraph._validate_uniqueness_and_refs:
    - duplicate workflow ids → ValidationError
    - duplicate entrypoint ids → ValidationError
    - duplicate transition ids → ValidationError
    - workflow dependency referencing unknown workflow → ValidationError
    - entrypoint transition not in registry → ValidationError
    - BackendOnly workflow in journey → ValidationError
    - BackendOnly workflow in entrypoints → ValidationError

  normalize_step_groups:
    - steps with workflows → list of workflow id lists
    - transition step → empty list for that position

  parse_global_pack_graph:
    - valid raw dict → GlobalPackGraph
    - 'journeys' key → raises ValueError
    - 'workflow_sequences' key → converted to journeys
    - invalid schema → raises ValueError
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from mozaiksai.core.workflow.pack.schema import (
    ConditionRoute,
    GlobalJourney,
    GlobalPackGraph,
    JourneyStepGroup,
    TransitionOption,
    TransitionUIBinding,
    WorkflowDependency,
    WorkflowEntry,
    WorkflowEntrypoint,
    WorkflowTransition,
    normalize_step_groups,
    parse_global_pack_graph,
)

# ---------------------------------------------------------------------------
# Helpers for building minimal valid objects
# ---------------------------------------------------------------------------

def _ui(component: str = "LauncherScreen") -> TransitionUIBinding:
    return TransitionUIBinding(component=component)


def _option(id: str = "opt1", route_to: str = "wf1") -> TransitionOption:
    return TransitionOption(id=id, route_to=route_to)


def _silent(id: str = "t1", route_to: str = "wf1") -> WorkflowTransition:
    return WorkflowTransition(id=id, transition_type="silent", route_to=route_to)


def _minimal_pack(**kwargs) -> GlobalPackGraph:
    defaults = {"version": 3}
    defaults.update(kwargs)
    return GlobalPackGraph(**defaults)


# ---------------------------------------------------------------------------
# 1. TransitionUIBinding._validate_component
# ---------------------------------------------------------------------------

class TestTransitionUIBindingComponent:
    def test_valid_component_accepted(self):
        binding = TransitionUIBinding(component="LauncherScreen")
        assert binding.component == "LauncherScreen"

    def test_empty_string_raises(self):
        with pytest.raises(ValidationError, match="non-empty"):
            TransitionUIBinding(component="")

    def test_whitespace_raises(self):
        with pytest.raises(ValidationError, match="non-empty"):
            TransitionUIBinding(component="   ")

    def test_path_with_slash_raises(self):
        with pytest.raises(ValidationError, match="registry key"):
            TransitionUIBinding(component="ui/LauncherScreen")

    def test_path_with_backslash_raises(self):
        with pytest.raises(ValidationError, match="registry key"):
            TransitionUIBinding(component="ui\\LauncherScreen")

    def test_ends_with_jsx_raises(self):
        with pytest.raises(ValidationError, match="registry key"):
            TransitionUIBinding(component="LauncherScreen.jsx")

    def test_ends_with_tsx_raises(self):
        with pytest.raises(ValidationError, match="registry key"):
            TransitionUIBinding(component="LauncherScreen.tsx")

    def test_ends_with_js_raises(self):
        with pytest.raises(ValidationError, match="registry key"):
            TransitionUIBinding(component="launcher.js")

    def test_ends_with_ts_raises(self):
        with pytest.raises(ValidationError, match="registry key"):
            TransitionUIBinding(component="launcher.ts")

    def test_non_alpha_start_raises(self):
        with pytest.raises(ValidationError):
            TransitionUIBinding(component="123Screen")

    def test_underscore_in_name_raises(self):
        # regex is [A-Za-z][A-Za-z0-9_]* — leading alpha required
        with pytest.raises(ValidationError):
            TransitionUIBinding(component="_Screen")

    def test_alphanumeric_with_underscore_accepted(self):
        binding = TransitionUIBinding(component="My_Component1")
        assert binding.component == "My_Component1"

    def test_whitespace_stripped(self):
        # validator does str(value or "").strip() first
        # but if value is " MyComponent " → stripped to "MyComponent" → valid
        # However Pydantic may also validate the raw type
        # Let me check - the validator does strip, so " MyComponent " should work
        binding = TransitionUIBinding(component=" MyComponent ")
        assert binding.component == "MyComponent"


# ---------------------------------------------------------------------------
# 2. TransitionOption — id, route_to, sequence, context_variables
# ---------------------------------------------------------------------------

class TestTransitionOptionId:
    def test_valid_id_accepted(self):
        opt = TransitionOption(id="option1")
        assert opt.id == "option1"

    def test_empty_id_raises(self):
        with pytest.raises(ValidationError, match="non-empty"):
            TransitionOption(id="")

    def test_whitespace_id_raises(self):
        with pytest.raises(ValidationError, match="non-empty"):
            TransitionOption(id="   ")

    def test_id_stripped(self):
        opt = TransitionOption(id="  opt1  ")
        assert opt.id == "opt1"


class TestTransitionOptionRouteTo:
    def test_none_stays_none(self):
        opt = TransitionOption(id="opt", route_to=None)
        assert opt.route_to is None

    def test_whitespace_normalised_to_none(self):
        opt = TransitionOption(id="opt", route_to="   ")
        assert opt.route_to is None

    def test_valid_route_to_stripped(self):
        opt = TransitionOption(id="opt", route_to="  wf1  ")
        assert opt.route_to == "wf1"


class TestTransitionOptionSequence:
    def test_none_stays_none(self):
        opt = TransitionOption(id="opt", sequence=None)
        assert opt.sequence is None

    def test_whitespace_normalised_to_none(self):
        opt = TransitionOption(id="opt", sequence="   ")
        assert opt.sequence is None

    def test_valid_sequence_stripped(self):
        opt = TransitionOption(id="opt", sequence="  seq1  ")
        assert opt.sequence == "seq1"


class TestTransitionOptionContextVariables:
    def test_valid_dict_accepted(self):
        opt = TransitionOption(id="opt", context_variables={"key": "value"})
        assert opt.context_variables == {"key": "value"}

    def test_empty_dict_accepted(self):
        opt = TransitionOption(id="opt", context_variables={})
        assert opt.context_variables == {}

    def test_empty_string_key_raises(self):
        with pytest.raises(ValidationError, match="non-empty strings"):
            TransitionOption(id="opt", context_variables={"": "value"})

    def test_whitespace_key_raises(self):
        with pytest.raises(ValidationError, match="non-empty strings"):
            TransitionOption(id="opt", context_variables={"  ": "value"})


# ---------------------------------------------------------------------------
# 3. ConditionRoute._normalize_optional_id
# ---------------------------------------------------------------------------

class TestConditionRouteNormalizeOptionalId:
    def test_valid_route_to_accepted(self):
        route = ConditionRoute(match="value", route_to="wf1")
        assert route.route_to == "wf1"

    def test_route_to_stripped(self):
        route = ConditionRoute(match="value", route_to="  wf1  ")
        assert route.route_to == "wf1"

    def test_none_sequence_stays_none(self):
        route = ConditionRoute(match="value", route_to="wf1", sequence=None)
        assert route.sequence is None

    def test_whitespace_sequence_normalised_to_none(self):
        route = ConditionRoute(match="value", route_to="wf1", sequence="   ")
        assert route.sequence is None

    def test_valid_sequence_stripped(self):
        route = ConditionRoute(match="value", route_to="wf1", sequence="  seq1  ")
        assert route.sequence == "seq1"


# ---------------------------------------------------------------------------
# 4. WorkflowTransition._validate_id
# ---------------------------------------------------------------------------

class TestWorkflowTransitionId:
    def test_empty_id_raises(self):
        with pytest.raises(ValidationError, match="non-empty"):
            WorkflowTransition(id="", transition_type="silent", route_to="wf1")

    def test_whitespace_id_raises(self):
        with pytest.raises(ValidationError, match="non-empty"):
            WorkflowTransition(id="   ", transition_type="silent", route_to="wf1")

    def test_valid_id_accepted(self):
        t = WorkflowTransition(id="t1", transition_type="silent", route_to="wf1")
        assert t.id == "t1"

    def test_id_stripped(self):
        t = WorkflowTransition(id="  t1  ", transition_type="silent", route_to="wf1")
        assert t.id == "t1"


# ---------------------------------------------------------------------------
# 5. WorkflowTransition._normalize_sequence
# ---------------------------------------------------------------------------

class TestWorkflowTransitionSequence:
    def test_none_sequence_stays_none(self):
        t = WorkflowTransition(id="t1", transition_type="silent", route_to="wf1", sequence=None)
        assert t.sequence is None

    def test_whitespace_sequence_normalised_to_none(self):
        t = WorkflowTransition(id="t1", transition_type="silent", route_to="wf1", sequence="   ")
        assert t.sequence is None


# ---------------------------------------------------------------------------
# 6. WorkflowTransition._validate_type_fields — user_choice
# ---------------------------------------------------------------------------

class TestWorkflowTransitionUserChoice:
    def test_user_choice_without_options_raises(self):
        with pytest.raises(ValidationError, match="at least one option"):
            WorkflowTransition(
                id="t1",
                transition_type="user_choice",
                ui=_ui(),
                options=[],
            )

    def test_user_choice_without_ui_raises(self):
        with pytest.raises(ValidationError, match="requires ui"):
            WorkflowTransition(
                id="t1",
                transition_type="user_choice",
                ui=None,
                options=[_option()],
            )

    def test_user_choice_with_top_level_route_to_raises(self):
        with pytest.raises(ValidationError, match="must use options"):
            WorkflowTransition(
                id="t1",
                transition_type="user_choice",
                ui=_ui(),
                options=[_option()],
                route_to="wf1",
            )

    def test_user_choice_option_without_route_to_raises(self):
        with pytest.raises(ValidationError, match="require route_to"):
            WorkflowTransition(
                id="t1",
                transition_type="user_choice",
                ui=_ui(),
                options=[TransitionOption(id="opt1")],
            )

    def test_valid_user_choice_accepted(self):
        t = WorkflowTransition(
            id="t1",
            transition_type="user_choice",
            ui=_ui(),
            options=[_option("opt1", "wf1"), _option("opt2", "wf2")],
        )
        assert t.id == "t1"
        assert len(t.options) == 2


# ---------------------------------------------------------------------------
# 7. WorkflowTransition._validate_type_fields — silent/progress_view/prerequisite_redirect
# ---------------------------------------------------------------------------

class TestWorkflowTransitionSilent:
    def test_silent_without_route_to_raises(self):
        with pytest.raises(ValidationError, match="requires route_to"):
            WorkflowTransition(id="t1", transition_type="silent")

    def test_silent_with_route_to_accepted(self):
        t = WorkflowTransition(id="t1", transition_type="silent", route_to="wf1")
        assert t.route_to == "wf1"

    def test_progress_view_without_route_to_raises(self):
        with pytest.raises(ValidationError, match="requires route_to"):
            WorkflowTransition(id="t1", transition_type="progress_view")

    def test_prerequisite_redirect_without_route_to_raises(self):
        with pytest.raises(ValidationError, match="requires route_to"):
            WorkflowTransition(id="t1", transition_type="prerequisite_redirect")


# ---------------------------------------------------------------------------
# 8. WorkflowTransition._validate_type_fields — condition
# ---------------------------------------------------------------------------

class TestWorkflowTransitionCondition:
    def test_condition_without_context_key_raises(self):
        with pytest.raises(ValidationError, match="requires context_key"):
            WorkflowTransition(
                id="t1",
                transition_type="condition",
                routes=[ConditionRoute(match="x", route_to="wf1")],
            )

    def test_condition_without_routes_or_default_raises(self):
        with pytest.raises(ValidationError, match="requires routes or default_route"):
            WorkflowTransition(
                id="t1",
                transition_type="condition",
                context_key="some_key",
            )

    def test_valid_condition_with_routes_accepted(self):
        t = WorkflowTransition(
            id="t1",
            transition_type="condition",
            context_key="step",
            routes=[ConditionRoute(match="done", route_to="wf_next")],
        )
        assert t.context_key == "step"

    def test_valid_condition_with_default_route_accepted(self):
        t = WorkflowTransition(
            id="t1",
            transition_type="condition",
            context_key="step",
            default_route="wf_fallback",
        )
        assert t.default_route == "wf_fallback"


# ---------------------------------------------------------------------------
# 9. WorkflowTransition._validate_type_fields — confirm
# ---------------------------------------------------------------------------

class TestWorkflowTransitionConfirm:
    def test_confirm_without_confirm_route_raises(self):
        with pytest.raises(ValidationError, match="requires confirm_route"):
            WorkflowTransition(
                id="t1",
                transition_type="confirm",
                ui=_ui("ConfirmScreen"),
                options=[],
            )

    def test_valid_confirm_with_confirm_route_accepted(self):
        t = WorkflowTransition(
            id="t1",
            transition_type="confirm",
            ui=_ui("ConfirmScreen"),
            confirm_route="wf_confirm",
        )
        assert t.confirm_route == "wf_confirm"

    def test_valid_confirm_with_options_accepted(self):
        t = WorkflowTransition(
            id="t1",
            transition_type="confirm",
            ui=_ui("ConfirmScreen"),
            options=[
                TransitionOption(id="confirm", route_to="wf1"),
                TransitionOption(id="cancel", route_to="wf2"),
            ],
        )
        assert len(t.options) == 2


# ---------------------------------------------------------------------------
# 10. WorkflowTransition._validate_type_fields — chat_session
# ---------------------------------------------------------------------------

class TestWorkflowTransitionChatSession:
    def test_chat_session_without_route_to_raises(self):
        with pytest.raises(ValidationError, match="requires route_to"):
            WorkflowTransition(id="t1", transition_type="chat_session")

    def test_chat_session_with_ui_raises(self):
        with pytest.raises(ValidationError, match="must not declare a ui"):
            WorkflowTransition(
                id="t1",
                transition_type="chat_session",
                route_to="wf1",
                ui=_ui(),
            )

    def test_chat_session_with_options_raises(self):
        with pytest.raises(ValidationError, match="must not declare options"):
            WorkflowTransition(
                id="t1",
                transition_type="chat_session",
                route_to="wf1",
                options=[_option()],
            )

    def test_valid_chat_session_accepted(self):
        t = WorkflowTransition(id="t1", transition_type="chat_session", route_to="wf1")
        assert t.route_to == "wf1"


# ---------------------------------------------------------------------------
# 11. WorkflowDependency._validate_id
# ---------------------------------------------------------------------------

class TestWorkflowDependencyId:
    def test_empty_id_raises(self):
        with pytest.raises(ValidationError, match="non-empty"):
            WorkflowDependency(id="")

    def test_whitespace_id_raises(self):
        with pytest.raises(ValidationError, match="non-empty"):
            WorkflowDependency(id="   ")

    def test_valid_id_accepted(self):
        dep = WorkflowDependency(id="prereq_workflow")
        assert dep.id == "prereq_workflow"


# ---------------------------------------------------------------------------
# 12. WorkflowEntry._validate_id
# ---------------------------------------------------------------------------

class TestWorkflowEntryId:
    def test_empty_id_raises(self):
        with pytest.raises(ValidationError, match="non-empty"):
            WorkflowEntry(id="")

    def test_valid_id_accepted(self):
        entry = WorkflowEntry(id="AppGenerator")
        assert entry.id == "AppGenerator"


# ---------------------------------------------------------------------------
# 13. WorkflowEntrypoint validators
# ---------------------------------------------------------------------------

class TestWorkflowEntrypointId:
    def test_empty_id_raises(self):
        with pytest.raises(ValidationError, match="non-empty"):
            WorkflowEntrypoint(id="", path="/start", workflow="wf1")

    def test_whitespace_id_raises(self):
        with pytest.raises(ValidationError, match="non-empty"):
            WorkflowEntrypoint(id="   ", path="/start", workflow="wf1")


class TestWorkflowEntrypointPath:
    def test_path_without_slash_raises(self):
        with pytest.raises(ValidationError, match="start with '/'"):
            WorkflowEntrypoint(id="ep1", path="start", workflow="wf1")

    def test_valid_path_accepted(self):
        ep = WorkflowEntrypoint(id="ep1", path="/start", workflow="wf1")
        assert ep.path == "/start"


class TestWorkflowEntrypointNormalizeOptionalId:
    def test_whitespace_transition_normalised_to_none(self):
        ep = WorkflowEntrypoint(id="ep1", path="/start", workflow="wf1", transition="   ")
        assert ep.transition is None

    def test_whitespace_sequence_normalised_to_none(self):
        ep = WorkflowEntrypoint(id="ep1", path="/start", workflow="wf1", sequence="   ")
        assert ep.sequence is None


class TestWorkflowEntrypointTarget:
    def test_both_transition_and_workflow_raises(self):
        with pytest.raises(ValidationError, match="exactly one"):
            WorkflowEntrypoint(id="ep1", path="/start", transition="t1", workflow="wf1")

    def test_neither_transition_nor_workflow_raises(self):
        with pytest.raises(ValidationError, match="exactly one"):
            WorkflowEntrypoint(id="ep1", path="/start")

    def test_transition_only_accepted(self):
        ep = WorkflowEntrypoint(id="ep1", path="/start", transition="t1")
        assert ep.transition == "t1"
        assert ep.workflow is None

    def test_workflow_only_accepted(self):
        ep = WorkflowEntrypoint(id="ep1", path="/start", workflow="wf1")
        assert ep.workflow == "wf1"
        assert ep.transition is None


# ---------------------------------------------------------------------------
# 14. JourneyStepGroup validators
# ---------------------------------------------------------------------------

class TestJourneyStepGroupWorkflows:
    def test_non_list_workflows_raises(self):
        with pytest.raises(ValidationError):
            JourneyStepGroup(workflows="wf1")

    def test_empty_string_in_workflows_raises(self):
        with pytest.raises(ValidationError, match="non-empty strings"):
            JourneyStepGroup(workflows=[""])

    def test_whitespace_only_item_raises(self):
        with pytest.raises(ValidationError, match="non-empty strings"):
            JourneyStepGroup(workflows=["  "])

    def test_valid_workflows_accepted(self):
        group = JourneyStepGroup(workflows=["wf1", "wf2"])
        assert group.workflows == ["wf1", "wf2"]

    def test_workflows_items_stripped(self):
        group = JourneyStepGroup(workflows=["  wf1  "])
        assert group.workflows == ["wf1"]


class TestJourneyStepGroupTransition:
    def test_whitespace_transition_normalised_to_none(self):
        with pytest.raises(ValidationError, match="exactly one"):
            # no workflows and transition is None after normalization
            JourneyStepGroup(workflows=[], transition="   ")

    def test_valid_transition_accepted(self):
        group = JourneyStepGroup(transition="t1")
        assert group.transition == "t1"


class TestJourneyStepGroupShape:
    def test_both_workflows_and_transition_raises(self):
        with pytest.raises(ValidationError, match="exactly one"):
            JourneyStepGroup(workflows=["wf1"], transition="t1")

    def test_neither_raises(self):
        with pytest.raises(ValidationError, match="exactly one"):
            JourneyStepGroup(workflows=[])

    def test_workflows_only_accepted(self):
        group = JourneyStepGroup(workflows=["wf1"])
        assert group.workflows == ["wf1"]
        assert group.transition is None

    def test_transition_only_accepted(self):
        group = JourneyStepGroup(transition="t1")
        assert group.transition == "t1"
        assert group.workflows == []


# ---------------------------------------------------------------------------
# 15. GlobalJourney validators
# ---------------------------------------------------------------------------

class TestGlobalJourneyId:
    def test_empty_id_raises(self):
        with pytest.raises(ValidationError, match="non-empty"):
            GlobalJourney(id="", steps=[JourneyStepGroup(workflows=["wf1"])])

    def test_valid_id_accepted(self):
        journey = GlobalJourney(id="main_journey", steps=[JourneyStepGroup(workflows=["wf1"])])
        assert journey.id == "main_journey"


class TestGlobalJourneySteps:
    def test_empty_steps_raises(self):
        with pytest.raises(ValidationError, match="non-empty list"):
            GlobalJourney(id="j1", steps=[])

    def test_valid_steps_accepted(self):
        journey = GlobalJourney(id="j1", steps=[JourneyStepGroup(workflows=["wf1"])])
        assert len(journey.steps) == 1


class TestGlobalJourneyAffectedFamilies:
    def test_empty_strings_filtered(self):
        journey = GlobalJourney(
            id="j1",
            steps=[JourneyStepGroup(workflows=["wf1"])],
            affected_declarative_families=["app_schema", "", "ui_contract"],
        )
        assert "" not in journey.affected_declarative_families
        assert "app_schema" in journey.affected_declarative_families
        assert "ui_contract" in journey.affected_declarative_families

    def test_whitespace_families_filtered(self):
        journey = GlobalJourney(
            id="j1",
            steps=[JourneyStepGroup(workflows=["wf1"])],
            affected_declarative_families=["   ", "valid_family"],
        )
        assert "valid_family" in journey.affected_declarative_families
        assert len(journey.affected_declarative_families) == 1


# ---------------------------------------------------------------------------
# 16. GlobalPackGraph._validate_artifact_dependency_graph
# ---------------------------------------------------------------------------

class TestArtifactDependencyGraph:
    def test_empty_graph_accepted(self):
        pack = _minimal_pack(artifact_dependency_graph={})
        assert pack.artifact_dependency_graph == {}

    def test_valid_graph_accepted(self):
        pack = _minimal_pack(artifact_dependency_graph={"a": ["b"], "b": []})
        assert pack.artifact_dependency_graph == {"a": ["b"], "b": []}

    def test_empty_family_key_raises(self):
        with pytest.raises((ValidationError, ValueError)):
            _minimal_pack(artifact_dependency_graph={"": ["b"]})

    def test_non_list_dependencies_raises(self):
        with pytest.raises((ValidationError, ValueError)):
            _minimal_pack(artifact_dependency_graph={"a": "not_a_list"})

    def test_unknown_dependency_raises(self):
        with pytest.raises((ValidationError, ValueError)):
            _minimal_pack(artifact_dependency_graph={"a": ["unknown_family"]})

    def test_duplicate_dependencies_deduplicated(self):
        pack = _minimal_pack(artifact_dependency_graph={"a": ["b", "b"], "b": []})
        # duplicates should be deduplicated
        assert pack.artifact_dependency_graph["a"] == ["b"]


# ---------------------------------------------------------------------------
# 17. GlobalPackGraph._validate_uniqueness_and_refs
# ---------------------------------------------------------------------------

class TestGlobalPackGraphUniqueness:
    def test_duplicate_workflow_ids_raises(self):
        with pytest.raises((ValidationError, ValueError), match="duplicate workflow"):
            _minimal_pack(workflows=[WorkflowEntry(id="wf1"), WorkflowEntry(id="wf1")])

    def test_duplicate_entrypoint_ids_raises(self):
        with pytest.raises((ValidationError, ValueError), match="duplicate entrypoint"):
            _minimal_pack(
                workflows=[WorkflowEntry(id="wf1")],
                entrypoints=[
                    WorkflowEntrypoint(id="ep1", path="/a", workflow="wf1"),
                    WorkflowEntrypoint(id="ep1", path="/b", workflow="wf1"),
                ],
            )

    def test_duplicate_entrypoint_paths_raises(self):
        with pytest.raises((ValidationError, ValueError), match="duplicate entrypoint"):
            _minimal_pack(
                workflows=[WorkflowEntry(id="wf1")],
                entrypoints=[
                    WorkflowEntrypoint(id="ep1", path="/start", workflow="wf1"),
                    WorkflowEntrypoint(id="ep2", path="/start", workflow="wf1"),
                ],
            )

    def test_duplicate_transition_ids_raises(self):
        with pytest.raises((ValidationError, ValueError), match="duplicate transition"):
            _minimal_pack(
                transitions=[
                    _silent("t1", "wf1"),
                    _silent("t1", "wf1"),
                ],
                workflows=[WorkflowEntry(id="wf1")],
            )

    def test_workflow_dependency_unknown_raises(self):
        with pytest.raises((ValidationError, ValueError)):
            _minimal_pack(
                workflows=[
                    WorkflowEntry(id="wf1", dependencies=["unknown_wf"]),
                ],
            )

    def test_entrypoint_transition_not_in_registry_raises(self):
        with pytest.raises((ValidationError, ValueError)):
            _minimal_pack(
                entrypoints=[
                    WorkflowEntrypoint(id="ep1", path="/start", transition="missing_t"),
                ],
            )

    def test_valid_pack_accepted(self):
        pack = _minimal_pack(
            workflows=[WorkflowEntry(id="wf1")],
            transitions=[_silent("t1", "wf1")],
            entrypoints=[WorkflowEntrypoint(id="ep1", path="/start", workflow="wf1")],
        )
        assert len(pack.workflows) == 1
        assert len(pack.transitions) == 1


class TestGlobalPackGraphBackendOnly:
    def test_backend_only_in_journey_raises(self):
        with pytest.raises((ValidationError, ValueError), match="BackendOnly"):
            _minimal_pack(
                workflows=[WorkflowEntry(id="wf1", startup_mode="BackendOnly")],
                journeys=[
                    GlobalJourney(
                        id="j1",
                        steps=[JourneyStepGroup(workflows=["wf1"])],
                    )
                ],
            )

    def test_backend_only_in_entrypoints_raises(self):
        with pytest.raises((ValidationError, ValueError), match="BackendOnly"):
            _minimal_pack(
                workflows=[WorkflowEntry(id="wf1", startup_mode="BackendOnly")],
                entrypoints=[WorkflowEntrypoint(id="ep1", path="/start", workflow="wf1")],
            )

    def test_backend_only_without_entrypoint_accepted(self):
        pack = _minimal_pack(
            workflows=[WorkflowEntry(id="wf1", startup_mode="BackendOnly")],
        )
        assert pack.workflows[0].startup_mode == "BackendOnly"


# ---------------------------------------------------------------------------
# 18. normalize_step_groups
# ---------------------------------------------------------------------------

class TestNormalizeStepGroups:
    def test_workflows_step_returns_workflow_ids(self):
        steps = [JourneyStepGroup(workflows=["wf1", "wf2"])]
        result = normalize_step_groups(steps)
        assert result == [["wf1", "wf2"]]

    def test_transition_step_returns_empty_list(self):
        steps = [JourneyStepGroup(transition="t1")]
        result = normalize_step_groups(steps)
        assert result == [[]]

    def test_mixed_steps(self):
        steps = [
            JourneyStepGroup(workflows=["wf1"]),
            JourneyStepGroup(transition="t1"),
            JourneyStepGroup(workflows=["wf2", "wf3"]),
        ]
        result = normalize_step_groups(steps)
        assert result == [["wf1"], [], ["wf2", "wf3"]]

    def test_empty_steps_returns_empty_list(self):
        result = normalize_step_groups([])
        assert result == []


# ---------------------------------------------------------------------------
# 19. parse_global_pack_graph
# ---------------------------------------------------------------------------

class TestParseGlobalPackGraph:
    def test_valid_raw_dict_accepted(self):
        raw = {"version": 3}
        pack = parse_global_pack_graph(raw)
        assert isinstance(pack, GlobalPackGraph)
        assert pack.version == 3

    def test_journeys_key_raises(self):
        with pytest.raises(ValueError, match="'journeys' is no longer supported"):
            parse_global_pack_graph({"version": 3, "journeys": []})

    def test_workflow_sequences_converted_to_journeys(self):
        raw = {
            "version": 3,
            "workflows": [{"id": "wf1"}],
            "workflow_sequences": [
                {
                    "id": "j1",
                    "steps": [{"workflows": ["wf1"]}],
                }
            ],
        }
        pack = parse_global_pack_graph(raw)
        assert len(pack.journeys) == 1
        assert pack.journeys[0].id == "j1"

    def test_invalid_schema_raises(self):
        with pytest.raises(ValueError, match="Invalid global pack graph"):
            parse_global_pack_graph({"version": 3, "workflows": [{"id": ""}]})

    def test_none_raw_treated_as_empty(self):
        # empty dict after `raw or {}` → version missing → ValidationError wrapped
        with pytest.raises(ValueError):
            parse_global_pack_graph(None)
