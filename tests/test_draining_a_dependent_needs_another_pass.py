"""A drained dependent must not make a resolvable graph look cyclic.

A live build failed twice in eight runs with:

    task batch 'app_build_tasks' has unresolved or cyclic dependencies:
    {'business_services_habit': ['module_contract_habit', 'data_models_habit']}

All three tasks were in the batch, and the graph was acyclic and well-formed.

The scheduler classifies every pending task, then drains the ones whose
dependency failed. A task that depends on BOTH a failed task and a task drained
in the same pass was read from `pending` before that drain, so it was neither
resolved nor blocked - and with nothing resolved, the loop raised instead of
re-classifying with the updated failed set.

Two-level chains hit it every time: module_contract fails, data_models is
drained for depending on it, business_services depends on both.

This was unreachable until #648 made task failures non-fatal. With fail_batch
the first failure raised before any drain, so the path never ran. My change
exposed it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SOURCE = (
    Path(__file__).resolve().parents[1] / "mozaiksai/core/workflow/task_batches.py"
).read_text(encoding="utf-8")


def _scheduling_loop() -> str:
    start = SOURCE.index("    while pending:")
    end = SOURCE.index("        semaphore = asyncio.Semaphore", start)
    return SOURCE[start:end]


def test_a_pass_that_drained_something_tries_again() -> None:
    loop = _scheduling_loop()

    assert "if blocked_by_failed:" in loop
    assert re.search(r"if blocked_by_failed:.*?\n(.*\n)*?\s+continue\n", loop), (
        "a pass that drained dependents must re-classify, not raise"
    )


def test_the_cyclic_error_still_exists_for_a_genuine_cycle() -> None:
    """The guard is narrowed, not removed."""
    loop = _scheduling_loop()

    assert "has unresolved or cyclic dependencies" in SOURCE
    assert "if pending:" in loop


def test_the_continue_precedes_the_raise() -> None:
    """Order is the whole fix: re-classify before concluding it is cyclic."""
    loop = _scheduling_loop()
    ready_at = loop.index("ready = resolved")
    drained_at = loop.index("if blocked_by_failed:", ready_at)
    pending_at = loop.index("if pending:", ready_at)

    assert drained_at < pending_at


def test_draining_still_records_which_dependency_failed() -> None:
    """The drained task's error must still name its cause."""
    assert "f\"dependency '{failed_dep}' failed\"" in SOURCE


def test_the_regression_is_attributed_where_it_came_from() -> None:
    """The comment has to say why this was unreachable before, or it reads as dead code."""
    loop = _scheduling_loop()

    assert "fail_batch" in loop


# ---------------------------------------------------------------------------
# Behavioural proof. The assertions above read source text, which today proved
# insufficient on its own: a regex containing an invisible  passed a grep
# and matched nothing at runtime. These run the scheduler.
# ---------------------------------------------------------------------------


def _spec():
    from mozaiksai.core.workflow.task_batches import parse_task_batches_config

    config = parse_task_batches_config(
        {
            "version": 1,
            "batches": [
                {
                    "id": "b",
                    "trigger_agent": "T",
                    "source": {"kind": "context_variable", "path": "items", "task_model": "AppBuildTask"},
                    "worker": {
                        "mode": "ag2_agent",
                        "agent_field": "agent",
                        "prompt_field": "message",
                    },
                    "execution": {
                        "concurrency": 4,
                        "dependency_field": "depends_on",
                        "failure_policy": "continue_with_available",
                    },
                    "result": {"context_key": "out", "status_key": "status"},
                }
            ],
        }
    )
    return config.batches[0]


def _chain() -> list[dict]:
    """The live shape: a task depending on a failure AND on its drained dependent."""
    return [
        {"task_id": "module_contract_habit", "agent": "A", "message": "m", "depends_on": []},
        {
            "task_id": "data_models_habit",
            "agent": "A",
            "message": "m",
            "depends_on": ["module_contract_habit"],
        },
        {
            "task_id": "business_services_habit",
            "agent": "A",
            "message": "m",
            "depends_on": ["module_contract_habit", "data_models_habit"],
        },
    ]


async def _run(monkeypatch, failing: set[str], items: list[dict]) -> dict:
    from mozaiksai.core.workflow import task_batches as tb

    async def fake_run_one_task(*, task, **kwargs):
        if str(task["task_id"]) in failing:
            raise ValueError(f"{task['task_id']} failed on purpose")
        return {"_task_id": str(task["task_id"])}

    monkeypatch.setattr(tb, "_run_one_task", fake_run_one_task)
    return await tb._execute_one_batch(
        workflow_name="W",
        batch=_spec(),
        task_items=items,
        agents={},
        context_variables={},
        chat_id=None,
        app_id=None,
        user_id=None,
        wf_logger=None,
        fresh_agents_per_task=False,
        agents_factory=None,
        context_authority_policy=None,
    )


@pytest.mark.asyncio
async def test_the_live_chain_no_longer_reports_a_cycle(monkeypatch) -> None:
    result = await _run(monkeypatch, {"module_contract_habit"}, _chain())

    assert result["status"] == "partial"
    assert set(result["failed_tasks"]) == {
        "module_contract_habit",
        "data_models_habit",
        "business_services_habit",
    }
    assert result["completed_tasks"] == []


@pytest.mark.asyncio
async def test_an_independent_task_still_runs_alongside_the_drained_chain(monkeypatch) -> None:
    """continue_with_available means the rest of the build survives."""
    items = _chain() + [{"task_id": "page_bundle", "agent": "A", "message": "m", "depends_on": []}]

    result = await _run(monkeypatch, {"module_contract_habit"}, items)

    assert result["completed_tasks"] == ["page_bundle"]


@pytest.mark.asyncio
async def test_a_genuine_cycle_is_still_rejected(monkeypatch) -> None:
    """Narrowing the guard must not blind it."""
    items = [
        {"task_id": "a", "agent": "A", "message": "m", "depends_on": ["b"]},
        {"task_id": "b", "agent": "A", "message": "m", "depends_on": ["a"]},
    ]

    with pytest.raises(ValueError, match="unresolved or cyclic"):
        await _run(monkeypatch, set(), items)


@pytest.mark.asyncio
async def test_nothing_failing_still_completes_everything(monkeypatch) -> None:
    result = await _run(monkeypatch, set(), _chain())

    assert result["status"] == "completed"
    assert len(result["completed_tasks"]) == 3
