"""A plan that cannot be repaired must end saying so.

A live run ended like this:

    review_app_build_plan returned failure: Plan ownership errors:
    - habit_management: the approved app-owned module requires exactly one
      module capability ... (found 0)
    - 1: module task capability_pack_id='habit_registry', surface_id=
      'habit_management' ... must resolve to one declared module capability
    ...
    [TASK_BATCH] app_build_tasks produced no task items from app_task_batch_items
    [APPGENERATOR] Run failed: workflow_failed

Review had already produced four named errors. AppPlanAgent had a route for
`needs_revision` and none for `blocked`, so a spent plan fell through to the
task batch, which triggers on that agent regardless, found an empty
app_task_batch_items, logged it at INFO and continued. The run then died as a
generic workflow_failed with the reason three thousand lines upstream.

Two separate faults, one symptom: nothing routed the blocked outcome, and the
batch treated "nothing to build" as unremarkable.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / "factory_app/workflows/AppGenerator/transition_graph.yaml"
BATCHES = ROOT / "mozaiksai/core/workflow/task_batches.py"


def _plan_routes() -> dict[str, str]:
    rules = yaml.safe_load(GRAPH.read_text(encoding="utf-8"))["transition_rules"]
    return {
        rule["condition_value"]: rule["target_agent"]
        for rule in rules
        if rule.get("source_agent") == "AppPlanAgent"
        and rule.get("condition_key") == "app_plan_outcome"
    }


def test_a_blocked_plan_has_a_route_of_its_own() -> None:
    assert _plan_routes().get("blocked") == "terminate"


def test_a_revisable_plan_is_still_sent_back_for_revision() -> None:
    """The repair path must survive the addition of the blocked path."""
    assert _plan_routes().get("needs_revision") == "AppPlanAgent"


def test_the_blocked_route_is_a_condition_not_the_fallback() -> None:
    """Relying on the unconditional terminate is what lost the reason."""
    rules = yaml.safe_load(GRAPH.read_text(encoding="utf-8"))["transition_rules"]
    blocked = [
        rule
        for rule in rules
        if rule.get("source_agent") == "AppPlanAgent"
        and rule.get("condition_value") == "blocked"
    ]

    assert len(blocked) == 1
    assert blocked[0]["transition_type"] == "condition"
    assert blocked[0]["termination_reason"] == "workflow_failed"


def test_an_empty_task_batch_is_a_warning_not_an_aside() -> None:
    """Its own comment calls it the difference between building and not."""
    source = BATCHES.read_text(encoding="utf-8")

    assert "wf_logger.warning(\n" in source
    assert "produced no task items from %s; " in source
    assert "nothing will be built" in source


def test_the_empty_batch_still_continues_rather_than_raising() -> None:
    """Other batches in the run are not this batch's business to fail."""
    source = BATCHES.read_text(encoding="utf-8")
    marker = source.index("produced no task items")
    tail = source[marker : marker + 400]

    assert "continue" in tail
    assert "raise" not in tail
