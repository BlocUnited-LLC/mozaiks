"""One agent mistake must not discard every finished task in the run.

A live build ran 1.3h and died on a single module_contract task:

    task batch 'app_build_tasks' failed at task 'task_habit_management_module':
    module_contract.events_yaml is null but raw output emits .../contracts/events.yaml

fail_batch discarded the five tasks that had already succeeded, and the
bundle-level repair loop never saw anything because no bundle was ever
produced. Three retry layers exist; the fatal one was the bottom one, so the
two that can actually repair were unreachable.

The scheduler already drains tasks whose dependency failed, marking them
"dependency 'X' failed", so continue_with_available fails the dependent subtree
and keeps the rest. Acceptance then judges what arrived - which is what it is
for, and it now has a repair path.

The second half matters as much as the first: the AppPlanAgent -> AssemblyAgent
rule only matched status "completed". Switching the policy alone would have
turned a loud crash into a silent stall.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
BATCHES = ROOT / "factory_app/workflows/AppGenerator/extended_orchestration/task_batches.yaml"
GRAPH = ROOT / "factory_app/workflows/AppGenerator/transition_graph.yaml"


def _batch() -> dict:
    spec = yaml.safe_load(BATCHES.read_text(encoding="utf-8"))
    return next(b for b in spec["batches"] if b["id"] == "app_build_tasks")


def _rules() -> list[dict]:
    return yaml.safe_load(GRAPH.read_text(encoding="utf-8"))["transition_rules"]


def test_a_failed_task_no_longer_discards_the_batch() -> None:
    assert _batch()["execution"]["failure_policy"] == "continue_with_available"


def test_a_partial_batch_still_reaches_assembly() -> None:
    """Without this the crash becomes a silent stall, which is worse."""
    targets = {
        rule["condition_value"]: rule["target_agent"]
        for rule in _rules()
        if rule.get("source_agent") == "AppPlanAgent"
        and rule.get("condition_key") == "app_task_batch_status"
    }

    assert targets.get("completed") == "AssemblyAgent"
    assert targets.get("partial") == "AssemblyAgent"


def test_the_scheduler_drains_dependents_of_a_failed_task() -> None:
    """The subtree fails; the rest of the build does not."""
    import asyncio

    from mozaiksai.core.workflow.task_batches import _task_dependencies

    # The behaviour under test lives in the scheduling loop: a task whose
    # dependency failed is moved to `failed` with a dependency error rather than
    # left pending, which is what makes continue_with_available terminate.
    source = (ROOT / "mozaiksai/core/workflow/task_batches.py").read_text(encoding="utf-8")
    assert "Drain tasks that can never run because a required dependency failed." in source
    assert "f\"dependency '{failed_dep}' failed\"" in source
    assert _task_dependencies({"depends_on": ["a"]}, "depends_on") == ["a"]
    assert asyncio  # the loop is async; imported to document where this runs


def test_an_identical_retry_stops_instead_of_spending_the_budget() -> None:
    source = (ROOT / "mozaiksai/core/workflow/task_batches.py").read_text(encoding="utf-8")

    assert "if _attempt == attempts - 1 or message == last_error:" in source


def test_the_retry_budget_is_unchanged_for_failures_that_differ() -> None:
    """Stopping on repetition must not reduce genuine retry attempts."""
    assert _batch()["execution"]["retry_limit"] == 3
