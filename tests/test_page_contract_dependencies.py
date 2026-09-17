"""A page_bundle task must be able to see the contracts it copies from.

The page agent is told: "Copy every module action id exactly from dependency
module.yaml outputs." A task receives only the outputs of its direct depends_on
entries, and a live plan wired both page bundles to data_models and
business_services and not to module_contract - the task owning module.yaml:

    module_contract  module_contract_habit_management  ['persistence_contract_habit']
    page_bundle      page_bundle_habit_dashboard       ['data_models_habit_management',
                                                        'business_services_habit_management']
    page_bundle      page_bundle_habit_management      ['data_models_habit_management',
                                                        'business_services_habit_management']

So the agent was told to copy from a set that was always empty, and did the only
other thing available: it guessed. It emitted /api/modules/habits/create_habit
for a module whose id is habits_registry.

The canonical FORM was right - that rule is stated four times and was followed.
The identity was invented because the contract never arrived. This is the root
cause; rewriting the emitted endpoint afterwards is a safety net over it.
"""

from __future__ import annotations

from typing import Any

from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    _repair_page_contract_dependencies,
)


def _plan(page_depends_on: list[str] | None = None) -> dict[str, Any]:
    return {
        "build_tasks": [
            {"task_id": "contract_a", "task_type": "module_contract"},
            {"task_id": "services_a", "task_type": "business_services"},
            {
                "task_id": "page_dashboard",
                "task_type": "page_bundle",
                "depends_on": list(page_depends_on if page_depends_on is not None else ["services_a"]),
            },
        ]
    }


def _page(plan: dict[str, Any]) -> dict[str, Any]:
    return next(t for t in plan["build_tasks"] if t["task_type"] == "page_bundle")


def test_the_live_gap_is_closed() -> None:
    plan = _plan(["data_models_a", "services_a"])

    repairs = _repair_page_contract_dependencies(plan, None)

    assert len(repairs) == 1 and "contract_a" in repairs[0]
    assert _page(plan)["depends_on"] == ["data_models_a", "services_a", "contract_a"]


def test_a_page_that_already_declared_it_is_left_alone() -> None:
    plan = _plan(["services_a", "contract_a"])

    assert _repair_page_contract_dependencies(plan, None) == []
    assert _page(plan)["depends_on"] == ["services_a", "contract_a"]


def test_every_module_contract_is_named_not_a_guessed_subset() -> None:
    """Which modules a page binds to is the page agent's call, not this repair's."""
    plan = _plan([])
    plan["build_tasks"].append({"task_id": "contract_b", "task_type": "module_contract"})

    _repair_page_contract_dependencies(plan, None)

    assert _page(plan)["depends_on"] == ["contract_a", "contract_b"]


def test_the_planners_own_dependencies_are_preserved() -> None:
    plan = _plan(["services_a", "data_models_a"])

    _repair_page_contract_dependencies(plan, None)

    kept = _page(plan)["depends_on"]
    assert kept[:2] == ["services_a", "data_models_a"]


def test_a_plan_with_no_module_contract_is_untouched() -> None:
    plan = {
        "build_tasks": [
            {"task_id": "page_dashboard", "task_type": "page_bundle", "depends_on": []},
        ]
    }

    assert _repair_page_contract_dependencies(plan, None) == []
    assert _page(plan)["depends_on"] == []


def test_non_page_tasks_are_not_rewired() -> None:
    plan = _plan()

    _repair_page_contract_dependencies(plan, None)

    services = next(t for t in plan["build_tasks"] if t["task_type"] == "business_services")
    assert "depends_on" not in services


def test_the_repair_is_idempotent() -> None:
    plan = _plan(["services_a"])

    assert _repair_page_contract_dependencies(plan, None)
    assert _repair_page_contract_dependencies(plan, None) == []


def test_no_cycle_is_introduced() -> None:
    """module_contract never depends on a page bundle, so this cannot cycle."""
    plan = _plan(["services_a"])

    _repair_page_contract_dependencies(plan, None)

    contract = next(t for t in plan["build_tasks"] if t["task_type"] == "module_contract")
    assert "page_dashboard" not in (contract.get("depends_on") or [])


def test_the_planner_is_told_the_rule_so_repair_is_the_fallback() -> None:
    text = open("factory_app/workflows/AppGenerator/agents.yaml", encoding="utf-8").read()

    assert "Every `page_bundle` task must list every `module_contract` task" in text
    assert "invents the module id" in text
