"""The contract agent must be told the actions, not left to infer them.

ConfigMiddlewareAgent is instructed: "Treat the action list in
current_build_task.initial_message as a closed contract. Emit every named
action exactly once." Nothing guaranteed that message named any actions.

A live plan declared operations on the pack:

    operations: ['create_habit', 'list_habits', 'record_checkin']

and gave the contract task this entire initial_message:

    Module to manage habits including creation, check-in recording,
    and retrieval.

The closed contract was prose. The agent inferred create_habit and
record_checkin from "creation, check-in recording", missed "retrieval", and
emitted a module with no read action. Its pages then bound to
/api/modules/habits/list_habits, which no module declared, and acceptance
rejected the bundle on module_wiring, functional_completeness and
app_runtime_load.

This is the case #626 was wrongly assumed to cover. That fix concluded the
planner had omitted the read, because the repair fired only when operations[]
lacked one. Here the planner declared it and the agent never saw it - the
opposite story, and the one left unguarded.
"""

from __future__ import annotations

from typing import Any

from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    _repair_contract_task_operations,
)

LIVE_MESSAGE = "Module to manage habits including creation, check-in recording, and retrieval."


def _plan(message: str = LIVE_MESSAGE, operations: list[str] | None = None) -> dict[str, Any]:
    return {
        "capability_packs": [
            {
                "capability_pack_id": "habits_module",
                "surface_id": "habits_module",
                "surface_kind": "module",
                "capability_source": "generated_module",
                "primary_entities": ["Habit"],
                "operations": list(
                    operations
                    if operations is not None
                    else ["create_habit", "list_habits", "record_checkin"]
                ),
            }
        ],
        "build_tasks": [
            {
                "task_id": "task_contract",
                "task_type": "module_contract",
                "capability_pack_id": "habits_module",
                "initial_message": message,
            }
        ],
    }


def _message(plan: dict[str, Any]) -> str:
    return plan["build_tasks"][0]["initial_message"]


def test_the_live_case_now_names_every_operation() -> None:
    plan = _plan()

    repairs = _repair_contract_task_operations(plan, None)

    assert len(repairs) == 1
    message = _message(plan)
    for operation in ("create_habit", "list_habits", "record_checkin"):
        assert f"`{operation}`" in message


def test_the_planners_prose_is_preserved_not_replaced() -> None:
    """The description carries intent the id list does not."""
    plan = _plan()

    _repair_contract_task_operations(plan, None)

    assert _message(plan).startswith(LIVE_MESSAGE)


def test_a_message_that_already_names_them_is_left_alone() -> None:
    plan = _plan(message="Emit create_habit, list_habits and record_checkin.")

    assert _repair_contract_task_operations(plan, None) == []


def test_a_partially_naming_message_is_still_repaired() -> None:
    """Naming two of three is the dangerous case - it looks authoritative."""
    plan = _plan(message="Emit create_habit and record_checkin.")

    repairs = _repair_contract_task_operations(plan, None)

    assert repairs and "list_habits" in repairs[0]
    assert "`list_habits`" in _message(plan)


def test_a_substring_match_does_not_count_as_named() -> None:
    """record_checkin must not be considered named by the word checkin."""
    plan = _plan(message="Handles checkin flows.", operations=["record_checkin"])

    assert _repair_contract_task_operations(plan, None)
    assert "`record_checkin`" in _message(plan)


def test_a_pack_with_no_operations_is_not_given_a_list() -> None:
    plan = _plan(operations=[])

    assert _repair_contract_task_operations(plan, None) == []


def test_a_provider_pack_is_not_touched() -> None:
    plan = _plan()
    plan["capability_packs"][0]["capability_source"] = "framework_pack"

    assert _repair_contract_task_operations(plan, None) == []


def test_the_repair_is_idempotent() -> None:
    plan = _plan()

    assert _repair_contract_task_operations(plan, None)
    assert _repair_contract_task_operations(plan, None) == []


def test_it_composes_with_the_missing_read_repair() -> None:
    """#634 appends a synthesized read; both repairs must agree on the result.

    #634 names its synthesized action in its own note, so this repair sees it as
    already named. The planner's other operations are not, so the authoritative
    list is still emitted - and it lists every operation including the
    synthesized one, which is the point of calling it authoritative. One
    complete list beats a list that omits the action the other repair just
    added.
    """
    from factory_app.workflows.AppGenerator.tools.app_plan_review import (
        _repair_missing_read_operation,
    )

    class _Ctx:
        def get(self, key: str, default: Any = None) -> Any:
            return {
                "design_surface_map": {
                    "surfaces": [
                        {
                            "surface_id": "habits_module",
                            "owner": "app",
                            "surface_kind": "module",
                            "primary_entities": ["Habit"],
                        }
                    ]
                }
            }.get(key, default)

        def set(self, key: str, value: Any) -> None:
            pass

    plan = _plan(operations=["create_habit"])
    _repair_missing_read_operation(plan, _Ctx())
    _repair_contract_task_operations(plan, None)

    message = _message(plan)
    # Exactly one authoritative list, and it is complete.
    assert message.count("Actions for this module (authoritative") == 1
    assert "`create_habit`" in message and "`list_habits`" in message
