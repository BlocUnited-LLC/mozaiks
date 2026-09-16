"""A module whose pages list its records must expose a way to read them.

A generated habit tracker shipped a module declaring create_habit and
checkin_habit and nothing else, while its dashboard rendered two tables of
habits. Acceptance rejected the bundle:

    module_action_wiring: 3 page endpoint(s) reference unknown module actions
      orphaned_pages:   dashboard/habit-overview, dashboard/habit-list,
                        habits/habit-form/submit  -> all call /api/habits
      orphaned_actions: habit_registry/create_habit, habit_registry/checkin_habit

A module that can create records but never list them is incoherent on its own
terms. The contract agent treats the planner's operations[] as a closed
contract, so an operation added at plan time reaches module.yaml and
ServiceAgent implements it with the handlers it already writes.
"""

from __future__ import annotations

from typing import Any

from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    _repair_missing_read_operation,
)


class _Context:
    def __init__(self, values: dict[str, Any]) -> None:
        self._values = dict(values)

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._values[key] = value


def _context() -> _Context:
    return _Context(
        {
            "design_surface_map": {
                "surfaces": [
                    {
                        "surface_id": "habit_registry",
                        "owner": "app",
                        "surface_kind": "module",
                        "primary_entities": ["Habit"],
                    }
                ]
            }
        }
    )


def _plan(operations: list[str]) -> dict[str, Any]:
    return {
        "capability_packs": [
            {
                "capability_pack_id": "habit_registry",
                "surface_id": "habit_registry",
                "surface_kind": "module",
                "capability_source": "generated_module",
                "primary_entities": ["Habit"],
                "operations": list(operations),
            }
        ],
        "build_tasks": [],
    }


def test_the_live_gap_is_filled() -> None:
    """create + checkin, nothing that reads."""
    plan = _plan(["create_habit", "checkin_habit"])

    repairs = _repair_missing_read_operation(plan, _context())

    assert repairs
    operations = plan["capability_packs"][0]["operations"]
    assert "list_habit_registry" in operations
    # The planner's own operations are preserved, not replaced.
    assert "create_habit" in operations and "checkin_habit" in operations


def test_an_existing_read_is_left_alone() -> None:
    for read in ("list_habits", "get_habit", "search_habits", "fetch_habit_summary"):
        plan = _plan(["create_habit", read])
        assert _repair_missing_read_operation(plan, _context()) == []
        assert plan["capability_packs"][0]["operations"] == ["create_habit", read]


def test_a_pack_owning_no_entities_is_not_given_a_reader() -> None:
    """Nothing to read means nothing to declare."""
    plan = _plan(["do_something"])
    plan["capability_packs"][0]["primary_entities"] = []

    assert _repair_missing_read_operation(plan, _context()) == []


def test_an_unapproved_surface_is_not_touched() -> None:
    """The approved design decides which modules exist; this only fills theirs."""
    plan = _plan(["create_habit"])
    plan["capability_packs"][0]["surface_id"] = "not_in_the_design"

    assert _repair_missing_read_operation(plan, _context()) == []


def test_a_provider_pack_is_not_given_a_reader() -> None:
    """An installed provider pack owns its own action surface."""
    plan = _plan(["create_habit"])
    plan["capability_packs"][0]["capability_source"] = "framework_pack"

    assert _repair_missing_read_operation(plan, _context()) == []


def test_the_repair_is_idempotent() -> None:
    plan = _plan(["create_habit"])
    context = _context()

    _repair_missing_read_operation(plan, context)
    assert _repair_missing_read_operation(plan, context) == []
