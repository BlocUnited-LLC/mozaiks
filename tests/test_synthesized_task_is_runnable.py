"""A task the plan gate invents must be able to run.

_repair_coverage synthesizes a build task when an approved module has files no
task owns. It set task_id, type, agent and owned_paths — and no prompt.

task_batches rejects any task whose prompt field is empty:

    task {task_id!r} is missing worker prompt

So a synthesized task did not merely lack polish; it guaranteed the build would
die. A live tool-lending run produced six of them — three each for
borrow_requests and overdue_management, the two modules whose capabilities the
plan gate had just repaired — and the run ended 55 seconds in with 19 agents
started and nothing generated.

A repair that converts a coverage gap into a fatal error is worse than no
repair. These tests execute the real validator's requirement rather than
asserting on the repair's return strings.
"""

from __future__ import annotations

from typing import Any

import pytest

from factory_app.workflows.AppGenerator.tools.app_plan_review import _repair_coverage

# The field task_batches reads. AppGenerator's batch declares
# worker.prompt_field: initial_message.
PROMPT_FIELD = "initial_message"
AGENT_FIELD = "initial_agent"


class _Context:
    def __init__(self, values: dict[str, Any]) -> None:
        self._values = dict(values)

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._values[key] = value


def _context() -> _Context:
    return _Context({
        "design_surface_map": {
            "surfaces": [{
                "surface_id": "borrow_requests",
                "owner": "app",
                "surface_kind": "module",
                "primary_entities": ["BorrowRequest"],
            }]
        }
    })


def _plan_with_an_uncovered_module() -> dict[str, Any]:
    """The live shape: generic tasks exist, one approved module is uncovered.

    _repair_coverage returns early when build_tasks is empty, so a plan with no
    tasks at all never synthesizes and would make these tests vacuous.
    """
    return {
        "capability_packs": [{
            "capability_pack_id": "borrow_requests",
            "surface_id": "borrow_requests",
            "surface_kind": "module",
            "capability_source": "generated_module",
            "primary_entities": ["BorrowRequest"],
        }],
        "build_tasks": [{
            "task_id": "business_services",
            "task_type": "business_services",
            "capability_pack_id": "catalogue",
            "surface_id": "catalogue",
            "surface_kind": "module",
            "initial_agent": "ServiceAgent",
            "execution_target": "ServiceAgent",
            "initial_message": "Implement the services required for managing Tool operations.",
            "owned_paths": ["modules/catalogue/backend/service.py"],
            "depends_on": [],
        }],
    }


def _synthesized(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [t for t in plan["build_tasks"] if str(t.get("task_id", "")).startswith("task_")]


def test_every_synthesized_task_carries_a_prompt() -> None:
    """The exact condition task_batches rejects on."""
    plan = _plan_with_an_uncovered_module()

    _repair_coverage(plan, _context())

    tasks = _synthesized(plan)
    assert tasks, "the fixture must actually trigger synthesis"
    for task in tasks:
        prompt = str(task.get(PROMPT_FIELD) or "").strip()
        assert prompt, (
            f"{task.get('task_id')} has no {PROMPT_FIELD}; task_batches will "
            "reject it and end the build"
        )
        assert str(task.get(AGENT_FIELD) or "").strip(), (
            f"{task.get('task_id')} has no {AGENT_FIELD}"
        )


def test_the_prompt_names_the_entity_the_module_owns() -> None:
    """A generic prompt would run and produce the wrong thing.

    Passing the validator is not the point; the worker has to know what it is
    building.
    """
    plan = _plan_with_an_uncovered_module()

    _repair_coverage(plan, _context())

    tasks = _synthesized(plan)
    assert tasks, "nothing was synthesized; this test would pass vacuously"
    for task in tasks:
        assert "BorrowRequest" in task[PROMPT_FIELD], (
            f"{task.get('task_id')} does not say what it is for: {task[PROMPT_FIELD]!r}"
        )


def test_a_pack_without_entities_still_gets_an_actionable_prompt() -> None:
    """Falling back to nothing would recreate the original failure."""
    plan = _plan_with_an_uncovered_module()
    plan["capability_packs"][0]["primary_entities"] = []
    context = _Context({
        "design_surface_map": {
            "surfaces": [{
                "surface_id": "borrow_requests",
                "owner": "app",
                "surface_kind": "module",
                "primary_entities": [],
            }]
        }
    })

    _repair_coverage(plan, context)

    tasks = _synthesized(plan)
    assert tasks, "nothing was synthesized; this test would pass vacuously"
    for task in tasks:
        prompt = str(task.get(PROMPT_FIELD) or "").strip()
        assert prompt, "a pack with no entity must still yield a prompt"
        assert "borrow requests" in prompt, f"prompt is not specific: {prompt!r}"


def test_a_synthesized_task_declares_its_execution_target() -> None:
    """The planner's own tasks carry it; a synthesized one looked different."""
    plan = _plan_with_an_uncovered_module()

    _repair_coverage(plan, _context())

    tasks = _synthesized(plan)
    assert tasks, "nothing was synthesized; this test would pass vacuously"
    for task in tasks:
        assert task.get("execution_target") == task.get(AGENT_FIELD)


@pytest.mark.parametrize("kind", ["business_services", "data_models", "module_contract"])
def test_each_task_type_describes_its_own_work(kind: str) -> None:
    """Three tasks for one module must not read identically."""
    plan = _plan_with_an_uncovered_module()

    _repair_coverage(plan, _context())

    typed = [t for t in _synthesized(plan) if t.get("task_type") == kind]
    if not typed:
        pytest.skip(f"fixture did not synthesize a {kind} task")
    prompts = {t[PROMPT_FIELD] for t in _synthesized(plan)}
    assert len(prompts) > 1, "every synthesized task got the same prompt"
