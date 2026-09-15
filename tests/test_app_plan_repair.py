"""The plan gate must fix what it can already derive, not reject and re-ask.

A live build of a habit tracker reached AppPlanAgent, failed review three times,
and killed the run in 38 seconds. The stored context says exactly what happened:

    app_plan_outcome    = 'blocked'
    app_plan_attempts   = 3
    app_task_batch_status = None

    Plan ownership errors:
    - habits_module: generated module capability_pack_id must match its
      approved surface_id, not the source product category
    - t1: module task capability_pack_id='crud_pack',
      surface_id='habits_module', path modules=['habits'] must resolve to one
      declared module capability
    - t2: ... same
    - t3: ... same

Four errors, one mistake: the planner named the module after the product
category it came from rather than the approved surface, and every ownership
check downstream keyed off the wrong name. The validator's own message states
the correct value. Asking a model to guess a name the system already knows
cost three rounds and then the run.

These tests execute the real validators. Asserting on the repair's return
strings would pass while the plan still failed review - which is the failure
mode that has cost the most time on this workflow.
"""

from __future__ import annotations

from typing import Any

import pytest

from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    _repair_plan,
    validate_plan_origins,
)


class _Context:
    """Minimal stand-in for the runtime context the validators read."""

    def __init__(self, values: dict[str, Any]) -> None:
        self._values = dict(values)

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._values[key] = value


def _design_surface_map() -> dict[str, Any]:
    return {
        "surfaces": [
            {
                "surface_id": "habits_module",
                "owner": "app",
                "surface_kind": "module",
                "primary_entities": ["Habit", "HabitCheckIn"],
            }
        ]
    }


def _plan_as_the_model_wrote_it() -> dict[str, Any]:
    """The exact shape that failed the live run.

    The product category ("crud_pack") stands in for the approved surface
    ("habits_module") in the pack id, in every task, and in the directory the
    owned paths sit under.
    """
    return {
        "capability_packs": [
            {
                "capability_pack_id": "crud_pack",
                "surface_id": "habits_module",
                "surface_kind": "module",
                "capability_source": "generated_module",
                "primary_entities": ["Habit"],
            }
        ],
        "build_tasks": [
            {
                "task_id": f"t{index}",
                "capability_pack_id": "crud_pack",
                "surface_id": "habits_module",
                "owned_paths": [f"modules/habits/backend/{name}.py"],
            }
            for index, name in enumerate(("schemas", "services", "handler"), start=1)
        ],
    }


def _context() -> _Context:
    return _Context({"design_surface_map": _design_surface_map(), "capability_packs": []})


def test_the_live_failure_is_reproduced_before_repair() -> None:
    """Without repair this plan fails exactly as the real run did."""
    with pytest.raises(ValueError) as excinfo:
        validate_plan_origins(_plan_as_the_model_wrote_it(), _context())

    message = str(excinfo.value)
    assert "must match its approved surface_id" in message
    assert "must resolve to one declared module capability" in message
    # One mistake, many errors - which is why re-asking the model never helped.
    # The live run produced exactly four; this fixture also drifts
    # primary_entities, so pin the shape rather than an exact count.
    assert message.count("\n- ") >= 4
    # Every one of them names the same module.
    assert message.count("habits_module") >= 4


def test_repair_makes_the_same_plan_pass() -> None:
    plan = _plan_as_the_model_wrote_it()
    context = _context()

    repairs = _repair_plan(plan, context)
    assert repairs, "the derivable mismatch must be repaired, not passed through"

    # The assertion that matters: the real validator now accepts it.
    validate_plan_origins(plan, context)


def test_repair_adopts_the_approved_identity_everywhere() -> None:
    plan = _plan_as_the_model_wrote_it()
    _repair_plan(plan, _context())

    assert plan["capability_packs"][0]["capability_pack_id"] == "habits_module"
    assert plan["capability_packs"][0]["primary_entities"] == ["Habit", "HabitCheckIn"]
    for task in plan["build_tasks"]:
        assert task["capability_pack_id"] == "habits_module"
        assert task["surface_id"] == "habits_module"
        # The ownership rule requires module_ids == {pack_id}, so the directory
        # has to move with the name or the plan still fails.
        assert all(path.startswith("modules/habits_module/") for path in task["owned_paths"])


def test_repair_leaves_a_correct_plan_alone() -> None:
    plan = _plan_as_the_model_wrote_it()
    _repair_plan(plan, _context())
    already_correct = {
        "capability_packs": [dict(p) for p in plan["capability_packs"]],
        "build_tasks": [dict(t) for t in plan["build_tasks"]],
    }

    assert _repair_plan(already_correct, _context()) == []
    validate_plan_origins(already_correct, _context())


def test_repair_does_not_invent_an_unapproved_module() -> None:
    """Only surfaces the approved design declares may be adopted."""
    plan = _plan_as_the_model_wrote_it()
    plan["capability_packs"][0]["surface_id"] = "not_in_the_design"
    plan["build_tasks"][0]["surface_id"] = "not_in_the_design"

    assert _repair_plan(plan, _context()) == []
    # Still rejected, which is correct - this one is not derivable.
    with pytest.raises(ValueError):
        validate_plan_origins(plan, _context())


def _plan_with_an_invented_capability() -> dict[str, Any]:
    """The second live failure: a capability nobody approved.

    After the identity repair landed, a rerun failed on exactly one error:

        notifications_pack: managed_capability requires a registered provider
        pack; a product category is not a managed service

    The concept was a habit tracker. Nothing asked for notifications, the
    approved design declares no notifications surface, and no provider pack is
    registered to supply one. All three attempts produced it, so feedback does
    not remove it - and one unapproved capability fails the entire plan.
    """
    plan = _plan_as_the_model_wrote_it()
    plan["capability_packs"].append(
        {
            "capability_pack_id": "notifications_pack",
            "surface_id": "notifications",
            "surface_kind": "module",
            "capability_source": "managed_capability",
        }
    )
    plan["build_tasks"].append(
        {
            "task_id": "t_notify",
            "capability_pack_id": "notifications_pack",
            "surface_id": "notifications",
            "owned_paths": ["modules/notifications/backend/services.py"],
        }
    )
    plan["build_tasks"][0]["depends_on"] = ["t_notify"]
    return plan


def test_invented_capability_is_dropped_and_the_plan_passes() -> None:
    plan = _plan_with_an_invented_capability()
    context = _context()

    repairs = _repair_plan(plan, context)

    assert any("dropped 'notifications_pack'" in r for r in repairs)
    assert [p["capability_pack_id"] for p in plan["capability_packs"]] == ["habits_module"]
    assert all(t["task_id"] != "t_notify" for t in plan["build_tasks"])
    # A task that depended on the removed one must not keep a dangling edge.
    assert plan["build_tasks"][0].get("depends_on") == []

    # The assertion that matters: the real validator accepts what is left.
    validate_plan_origins(plan, context)


def test_a_registered_managed_capability_is_kept() -> None:
    """Dropping is for capabilities with no provider, not for managed ones."""
    plan = _plan_with_an_invented_capability()
    context = _Context(
        {
            "design_surface_map": _design_surface_map(),
            # A real provider pack exists for it, so it is legitimate.
            "capability_packs": [
                {"id": "notifications_pack", "capability_source": "managed_capability"}
            ],
        }
    )

    repairs = _repair_plan(plan, context)

    assert not any("dropped" in r for r in repairs)
    assert any(p["capability_pack_id"] == "notifications_pack" for p in plan["capability_packs"])
