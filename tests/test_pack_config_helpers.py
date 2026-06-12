"""
Global pack config pure helper unit tests.

Covers:
  _normalize_dependency_spec:
    - WorkflowDependency input → returned as-is
    - string input → WorkflowDependency(id=...) created
    - empty string → None
    - whitespace-only string → None (stripped to empty)
    - non-string non-WorkflowDependency → None

  journey_next_step:
    - empty/None current_workflow → None
    - current workflow not in any group → None
    - current workflow is in last group → None
    - current workflow is in first group → returns first workflow of second group
    - current workflow is in middle group → returns first workflow of next group
    - groups with current in middle → next group's first entry returned

  list_workflow_ids:
    - empty pack → []
    - pack with one workflow → [workflow_id]
    - pack with multiple workflows → list of ids (order as defined)

  get_workflow_entry:
    - matching workflow id → WorkflowEntry returned
    - no match → None
    - empty pack → None

  list_entrypoints:
    - pack with entrypoints → list returned
    - empty pack → []

  list_transitions:
    - pack with transitions → list returned
    - empty pack → []

  get_workflow_sequence:
    - matching sequence id → GlobalJourney returned
    - no match → None
    - empty sequence_id → None

  get_transition:
    - matching transition id → WorkflowTransition returned
    - no match → None
    - empty transition_id → None

  infer_auto_workflow_sequence_for_start:
    - workflow in first step of sequence → sequence returned
    - workflow not in any sequence → None
    - workflow only in second step of sequence → None (not first)
    - empty workflow_name → None

  compute_required_dependencies:
    - workflow not in pack → []
    - workflow with no dependencies → []
    - workflow with optional dependency → not included
    - workflow with required dependency → included with from/to/scope/reason
    - required dependency with custom reason → reason preserved
    - duplicate dependency → deduplicated
    - empty workflow_name → []
"""
from __future__ import annotations

from mozaiksai.core.workflow.pack.config import (
    _normalize_dependency_spec,
    compute_required_dependencies,
    get_transition,
    get_workflow_entry,
    get_workflow_sequence,
    infer_auto_workflow_sequence_for_start,
    journey_next_step,
    list_entrypoints,
    list_transitions,
    list_workflow_ids,
)
from mozaiksai.core.workflow.pack.schema import (
    GlobalJourney,
    GlobalPackGraph,
    JourneyStepGroup,
    WorkflowDependency,
    WorkflowEntry,
    WorkflowEntrypoint,
    WorkflowTransition,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _step(workflows: list[str]) -> JourneyStepGroup:
    return JourneyStepGroup(workflows=workflows)


def _journey(*workflow_groups: list[str]) -> GlobalJourney:
    return GlobalJourney(
        id="test_journey",
        steps=[_step(grp) for grp in workflow_groups],
    )


def _empty_pack() -> GlobalPackGraph:
    return GlobalPackGraph(version=3, workflows=[], journeys=[], entrypoints=[], transitions=[])


# ---------------------------------------------------------------------------
# 1. _normalize_dependency_spec
# ---------------------------------------------------------------------------

class TestNormalizeDependencySpec:
    def test_workflow_dependency_returned_as_is(self):
        dep = WorkflowDependency(id="my_workflow")
        result = _normalize_dependency_spec(dep)
        assert result is dep

    def test_string_creates_dependency(self):
        result = _normalize_dependency_spec("my_workflow")
        assert isinstance(result, WorkflowDependency)
        assert result.id == "my_workflow"

    def test_string_stripped(self):
        result = _normalize_dependency_spec("  my_workflow  ")
        assert result is not None
        assert result.id == "my_workflow"

    def test_empty_string_returns_none(self):
        assert _normalize_dependency_spec("") is None

    def test_whitespace_only_returns_none(self):
        assert _normalize_dependency_spec("   ") is None

    def test_none_returns_none(self):
        assert _normalize_dependency_spec(None) is None  # type: ignore[arg-type]

    def test_int_returns_none(self):
        assert _normalize_dependency_spec(42) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 2. journey_next_step
# ---------------------------------------------------------------------------

class TestJourneyNextStep:
    def test_empty_current_workflow_returns_none(self):
        j = _journey(["wf_a"], ["wf_b"])
        assert journey_next_step(j, "") is None

    def test_none_current_workflow_returns_none(self):
        j = _journey(["wf_a"], ["wf_b"])
        assert journey_next_step(j, None) is None  # type: ignore[arg-type]

    def test_current_not_in_any_group_returns_none(self):
        j = _journey(["wf_a"], ["wf_b"])
        assert journey_next_step(j, "wf_c") is None

    def test_current_is_last_group_returns_none(self):
        j = _journey(["wf_a"], ["wf_b"])
        assert journey_next_step(j, "wf_b") is None

    def test_current_is_first_returns_second(self):
        j = _journey(["wf_a"], ["wf_b"])
        assert journey_next_step(j, "wf_a") == "wf_b"

    def test_middle_step_returns_next(self):
        j = _journey(["wf_a"], ["wf_b"], ["wf_c"])
        assert journey_next_step(j, "wf_b") == "wf_c"

    def test_next_group_first_workflow_returned(self):
        # When the next group has multiple workflows, return the first
        j = GlobalJourney(
            id="j",
            steps=[
                JourneyStepGroup(workflows=["wf_a"]),
                JourneyStepGroup(workflows=["wf_b", "wf_c"]),
            ],
        )
        assert journey_next_step(j, "wf_a") == "wf_b"


# ---------------------------------------------------------------------------
# 3. list_workflow_ids
# ---------------------------------------------------------------------------

class TestListWorkflowIds:
    def test_empty_pack_returns_empty(self):
        assert list_workflow_ids(_empty_pack()) == []

    def test_single_workflow(self):
        pack = GlobalPackGraph(
            version=3,
            workflows=[WorkflowEntry(id="app_generator")],
            journeys=[],
            entrypoints=[],
            transitions=[],
        )
        assert list_workflow_ids(pack) == ["app_generator"]

    def test_multiple_workflows_in_order(self):
        pack = GlobalPackGraph(
            version=3,
            workflows=[
                WorkflowEntry(id="wf_a"),
                WorkflowEntry(id="wf_b"),
                WorkflowEntry(id="wf_c"),
            ],
            journeys=[],
            entrypoints=[],
            transitions=[],
        )
        result = list_workflow_ids(pack)
        assert result == ["wf_a", "wf_b", "wf_c"]


# ---------------------------------------------------------------------------
# 4. get_workflow_entry
# ---------------------------------------------------------------------------

class TestGetWorkflowEntry:
    def test_matching_entry_returned(self):
        entry = WorkflowEntry(id="app_generator")
        pack = GlobalPackGraph(
            version=3,
            workflows=[entry],
            journeys=[],
            entrypoints=[],
            transitions=[],
        )
        result = get_workflow_entry(pack, "app_generator")
        assert result is entry

    def test_no_match_returns_none(self):
        pack = GlobalPackGraph(
            version=3,
            workflows=[WorkflowEntry(id="app_generator")],
            journeys=[],
            entrypoints=[],
            transitions=[],
        )
        assert get_workflow_entry(pack, "nonexistent") is None

    def test_empty_pack_returns_none(self):
        assert get_workflow_entry(_empty_pack(), "anything") is None


# ---------------------------------------------------------------------------
# 5. list_entrypoints
# ---------------------------------------------------------------------------

class TestListEntrypoints:
    def test_empty_pack_returns_empty(self):
        assert list_entrypoints(_empty_pack()) == []

    def test_entrypoints_returned(self):
        t = WorkflowTransition(id="launch", transition_type="workflow_complete")
        ep = WorkflowEntrypoint(id="ep1", path="/start", label="Start", transition="launch")
        pack = GlobalPackGraph(
            version=3,
            workflows=[],
            journeys=[],
            entrypoints=[ep],
            transitions=[t],
        )
        result = list_entrypoints(pack)
        assert result == [ep]


# ---------------------------------------------------------------------------
# 6. list_transitions
# ---------------------------------------------------------------------------

class TestListTransitions:
    def test_empty_pack_returns_empty(self):
        assert list_transitions(_empty_pack()) == []

    def test_transitions_returned(self):
        t = WorkflowTransition(id="t1", transition_type="workflow_complete")
        pack = GlobalPackGraph(
            version=3,
            workflows=[],
            journeys=[],
            entrypoints=[],
            transitions=[t],
        )
        result = list_transitions(pack)
        assert result == [t]


# ---------------------------------------------------------------------------
# 7. get_workflow_sequence
# ---------------------------------------------------------------------------

def _pack_with_journeys(*journey_ids: str) -> GlobalPackGraph:
    journeys = [
        GlobalJourney(id=jid, steps=[JourneyStepGroup(workflows=[f"wf_{jid}"])])
        for jid in journey_ids
    ]
    # Pack must declare the workflows referenced by journeys
    workflows = [WorkflowEntry(id=f"wf_{jid}") for jid in journey_ids]
    return GlobalPackGraph(version=3, workflows=workflows, journeys=journeys, entrypoints=[], transitions=[])


class TestGetWorkflowSequence:
    def test_matching_id_returned(self):
        pack = _pack_with_journeys("onboarding", "checkout")
        result = get_workflow_sequence(pack, "onboarding")
        assert result is not None
        assert result.id == "onboarding"

    def test_no_match_returns_none(self):
        pack = _pack_with_journeys("onboarding")
        assert get_workflow_sequence(pack, "nonexistent") is None

    def test_empty_sequence_id_returns_none(self):
        pack = _pack_with_journeys("onboarding")
        assert get_workflow_sequence(pack, "") is None

    def test_whitespace_id_returns_none(self):
        pack = _pack_with_journeys("onboarding")
        assert get_workflow_sequence(pack, "   ") is None

    def test_empty_journeys_returns_none(self):
        assert get_workflow_sequence(_empty_pack(), "anything") is None


# ---------------------------------------------------------------------------
# 8. get_transition
# ---------------------------------------------------------------------------

class TestGetTransition:
    def test_matching_id_returned(self):
        t = WorkflowTransition(id="launch", transition_type="workflow_complete")
        pack = GlobalPackGraph(
            version=3, workflows=[], journeys=[], entrypoints=[], transitions=[t]
        )
        result = get_transition(pack, "launch")
        assert result is t

    def test_no_match_returns_none(self):
        t = WorkflowTransition(id="launch", transition_type="workflow_complete")
        pack = GlobalPackGraph(
            version=3, workflows=[], journeys=[], entrypoints=[], transitions=[t]
        )
        assert get_transition(pack, "other") is None

    def test_empty_transition_id_returns_none(self):
        t = WorkflowTransition(id="launch", transition_type="workflow_complete")
        pack = GlobalPackGraph(
            version=3, workflows=[], journeys=[], entrypoints=[], transitions=[t]
        )
        assert get_transition(pack, "") is None

    def test_empty_transitions_returns_none(self):
        assert get_transition(_empty_pack(), "anything") is None


# ---------------------------------------------------------------------------
# 9. infer_auto_workflow_sequence_for_start
# ---------------------------------------------------------------------------

def _pack_with_journey(j: GlobalJourney, extra_wf_ids: list[str] | None = None) -> GlobalPackGraph:
    """Build a GlobalPackGraph with declared workflows for all ids referenced by j."""
    from mozaiksai.core.workflow.pack.schema import normalize_step_groups
    all_wf_ids: set[str] = set()
    for group in normalize_step_groups(j.steps):
        all_wf_ids.update(group)
    for wf_id in (extra_wf_ids or []):
        all_wf_ids.add(wf_id)
    workflows = [WorkflowEntry(id=wid) for wid in sorted(all_wf_ids)]
    return GlobalPackGraph(version=3, workflows=workflows, journeys=[j], entrypoints=[], transitions=[])


class TestInferAutoWorkflowSequenceForStart:
    def test_workflow_in_first_step_returns_sequence(self):
        j = _journey(["wf_a", "wf_b"], ["wf_c"])
        pack = _pack_with_journey(j)
        result = infer_auto_workflow_sequence_for_start(pack, "wf_a")
        assert result is j

    def test_workflow_not_in_any_sequence_returns_none(self):
        j = _journey(["wf_a"], ["wf_b"])
        pack = _pack_with_journey(j, extra_wf_ids=["wf_c"])
        assert infer_auto_workflow_sequence_for_start(pack, "wf_c") is None

    def test_workflow_only_in_second_step_returns_none(self):
        # infer_auto looks only at the FIRST group of each journey
        j = _journey(["wf_a"], ["wf_b"])
        pack = _pack_with_journey(j)
        assert infer_auto_workflow_sequence_for_start(pack, "wf_b") is None

    def test_empty_workflow_name_returns_none(self):
        j = _journey(["wf_a"], ["wf_b"])
        pack = _pack_with_journey(j)
        assert infer_auto_workflow_sequence_for_start(pack, "") is None

    def test_no_journeys_returns_none(self):
        assert infer_auto_workflow_sequence_for_start(_empty_pack(), "wf_a") is None


# ---------------------------------------------------------------------------
# 10. compute_required_dependencies
# ---------------------------------------------------------------------------

def _pack_with_workflow(wf_id: str, deps: list) -> GlobalPackGraph:
    """Build a pack declaring wf_id and any workflows referenced in its deps."""
    entry = WorkflowEntry(id=wf_id, dependencies=deps)
    # Collect dep ids to satisfy cross-validation
    dep_ids: set[str] = set()
    for d in deps:
        if isinstance(d, WorkflowDependency):
            dep_ids.add(d.id)
        elif isinstance(d, str) and d.strip():
            dep_ids.add(d.strip())
    dep_entries = [WorkflowEntry(id=did) for did in sorted(dep_ids)]
    return GlobalPackGraph(version=3, workflows=[entry] + dep_entries, journeys=[], entrypoints=[], transitions=[])


class TestComputeRequiredDependencies:
    def test_empty_workflow_name_returns_empty(self):
        assert compute_required_dependencies(_empty_pack(), "") == []

    def test_workflow_not_in_pack_returns_empty(self):
        assert compute_required_dependencies(_empty_pack(), "wf_a") == []

    def test_workflow_with_no_dependencies_returns_empty(self):
        pack = _pack_with_workflow("wf_a", [])
        assert compute_required_dependencies(pack, "wf_a") == []

    def test_optional_dependency_not_included(self):
        dep = WorkflowDependency(id="wf_prereq", gating="optional")
        pack = _pack_with_workflow("wf_a", [dep])
        assert compute_required_dependencies(pack, "wf_a") == []

    def test_required_dependency_included(self):
        dep = WorkflowDependency(id="wf_prereq", gating="required")
        pack = _pack_with_workflow("wf_a", [dep])
        result = compute_required_dependencies(pack, "wf_a")
        assert len(result) == 1
        assert result[0]["from"] == "wf_prereq"
        assert result[0]["to"] == "wf_a"
        assert result[0]["gating"] == "required"

    def test_required_dependency_default_scope_is_app(self):
        dep = WorkflowDependency(id="wf_prereq")
        pack = _pack_with_workflow("wf_a", [dep])
        result = compute_required_dependencies(pack, "wf_a")
        assert result[0]["scope"] == "app"

    def test_required_dependency_user_scope_preserved(self):
        dep = WorkflowDependency(id="wf_prereq", scope="user")
        pack = _pack_with_workflow("wf_a", [dep])
        result = compute_required_dependencies(pack, "wf_a")
        assert result[0]["scope"] == "user"

    def test_custom_reason_preserved(self):
        dep = WorkflowDependency(id="wf_prereq", reason="Must complete onboarding first.")
        pack = _pack_with_workflow("wf_a", [dep])
        result = compute_required_dependencies(pack, "wf_a")
        assert result[0]["reason"] == "Must complete onboarding first."

    def test_default_reason_generated_when_missing(self):
        dep = WorkflowDependency(id="wf_prereq")
        pack = _pack_with_workflow("wf_a", [dep])
        result = compute_required_dependencies(pack, "wf_a")
        assert "wf_prereq" in result[0]["reason"]
        assert "wf_a" in result[0]["reason"]

    def test_string_dependency_treated_as_required(self):
        pack = _pack_with_workflow("wf_a", ["wf_prereq"])
        result = compute_required_dependencies(pack, "wf_a")
        assert len(result) == 1
        assert result[0]["from"] == "wf_prereq"

    def test_duplicate_dependency_deduplicated(self):
        dep1 = WorkflowDependency(id="wf_prereq")
        dep2 = WorkflowDependency(id="wf_prereq")
        pack = _pack_with_workflow("wf_a", [dep1, dep2])
        result = compute_required_dependencies(pack, "wf_a")
        assert len(result) == 1

    def test_multiple_required_dependencies_all_included(self):
        deps = [
            WorkflowDependency(id="wf_step1"),
            WorkflowDependency(id="wf_step2"),
        ]
        pack = _pack_with_workflow("wf_final", deps)
        result = compute_required_dependencies(pack, "wf_final")
        assert len(result) == 2
        froms = {r["from"] for r in result}
        assert froms == {"wf_step1", "wf_step2"}
