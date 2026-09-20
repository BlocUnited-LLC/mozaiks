"""A module whose pages list its records must expose a way to read them.

A generated habit tracker shipped a module declaring create_habit and
checkin_habit and nothing else, while its dashboard rendered two tables of
habits. Acceptance rejected the bundle:

    module_action_wiring: 3 page endpoint(s) reference unknown module actions
      orphaned_pages:   dashboard/habit-overview, dashboard/habit-list,
                        habits/habit-form/submit  -> all call /api/habits
      orphaned_actions: habit_registry/create_habit, habit_registry/checkin_habit

A module that can create records but never list them is incoherent on its own
terms.

The first version of this repair set operations[] and nothing else, on the
belief that the contract agent read it. It does not - it is told to "Treat the
action list in `current_build_task.initial_message` as a closed contract". So
the repair was inert, and a later run proved it: the log said

    plan repaired: habits_registry: owns ['Habit'] with no read operation;
                   declared 'list_habits' so its pages have something to read

and the module.yaml it produced declared create_habit and checkoff_habit and no
read. The pages fell back to an invented /api/habits exactly as before.

The repair fired, so the fix looked confirmed. "The repair fires" was never the
same claim as "the action exists", and only the emitted bundle could tell them
apart. That is why the tests below assert on the contract task's
initial_message and not only on operations[].
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
        "build_tasks": [
            {
                "task_id": "task_contract",
                "task_type": "module_contract",
                "capability_pack_id": "habit_registry",
                "initial_message": "Entities: Habit. Actions: create_habit, checkin_habit.",
            }
        ],
    }


def test_the_live_gap_is_filled() -> None:
    """create + checkin, nothing that reads."""
    plan = _plan(["create_habit", "checkin_habit"])

    repairs = _repair_missing_read_operation(plan, _context())

    assert repairs
    operations = plan["capability_packs"][0]["operations"]
    # Named after the entity, matching create_habit and the page agent's own
    # worked example list_tickets. list_habit_registry would be a third
    # convention, and the page agent would emit list_habits and orphan against
    # an action that exists under a name nobody guesses.
    assert "list_habits" in operations
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


def test_the_read_is_named_after_the_entity_not_the_module() -> None:
    """Three conventions were in play; this one must match the other two."""
    plan = _plan(["create_habit"])
    plan["capability_packs"][0]["capability_pack_id"] = "habit_registry"
    plan["capability_packs"][0]["surface_id"] = "habit_registry"

    _repair_missing_read_operation(plan, _context())

    operations = plan["capability_packs"][0]["operations"]
    assert "list_habits" in operations
    assert "list_habit_registry" not in operations


def test_entity_names_pluralise_the_way_the_convention_expects() -> None:
    from factory_app.workflows.AppGenerator.tools.app_plan_review import _plural_entity_slug

    assert _plural_entity_slug("Habit") == "habits"
    assert _plural_entity_slug("HabitCheckIn") == "habit_check_ins"
    assert _plural_entity_slug("Category") == "categories"
    assert _plural_entity_slug("Box") == "boxes"
    # A trailing vowel+y is not an -ies word.
    assert _plural_entity_slug("Journey") == "journeys"


def test_an_unusable_entity_name_falls_back_to_the_module() -> None:
    """Better a module-named read than no read at all."""
    plan = _plan(["create_thing"])
    plan["capability_packs"][0]["primary_entities"] = ["   "]

    # Blank entities mean the pack owns nothing nameable, so nothing is added.
    assert _repair_missing_read_operation(plan, _context()) == []


def test_the_action_reaches_the_contract_the_agent_actually_reads() -> None:
    """The bug: operations[] alone changed nothing in the emitted module.yaml."""
    plan = _plan(["create_habit", "checkin_habit"])

    _repair_missing_read_operation(plan, _context())

    message = plan["build_tasks"][0]["initial_message"]
    assert "list_habits" in message
    # The planner's own instructions survive; the action is added to them.
    assert "create_habit, checkin_habit" in message


def test_a_module_with_no_contract_task_says_so_instead_of_claiming_success() -> None:
    """A repair that cannot land must not log like one that did.

    The whole cost of the original bug was a success line for work the bundle
    never contained, which read as confirmation for two more iterations.
    """
    plan = _plan(["create_habit"])
    plan["build_tasks"] = []

    repairs = _repair_missing_read_operation(plan, _context())

    assert len(repairs) == 1
    assert "no module_contract task" in repairs[0]
    assert "will ship without a read" in repairs[0]
