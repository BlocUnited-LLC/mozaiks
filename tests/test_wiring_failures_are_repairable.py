"""Orphaned page endpoints reach their approved owner with the real action IDs."""

from __future__ import annotations

from factory_app.workflows.AppGenerator.tools.app_validation import (
    _wiring_repair_errors,
)
from factory_app.workflows.AppGenerator.tools.repair_policy import prepare_bundle_repair

FILES = {
    "modules/habit_registry/module.yaml": (
        "module:\n  id: habit_registry\n"
        "actions:\n- id: create_habit\n- id: list_habits\n"
    )
}


def _wiring(*orphans: tuple[str, str, str]) -> dict:
    return {
        "passed": False,
        "orphaned_pages": [
            {"page": page, "section": section, "endpoint": endpoint}
            for page, section, endpoint in orphans
        ],
    }


def _repair_context() -> dict:
    contract_path = "modules/habit_registry/module.yaml"
    page_path = "ui/pages/habits.yaml"
    return {
        "app_build_plan": {"build_tasks": [
            {"task_id": "habit_contract", "task_type": "module_contract",
             "initial_agent": "ConfigMiddlewareAgent", "owned_paths": [contract_path], "depends_on": []},
            {"task_id": "habit_page", "task_type": "page_bundle",
             "initial_agent": "AppSchemaAgent", "owned_paths": [page_path], "depends_on": ["habit_contract"]},
        ]},
        "app_task_batch_results": {
            "habit_contract": {"code_files": [{"filename": contract_path, "content": FILES[contract_path]}]},
            "habit_page": {"code_files": [{"filename": page_path, "content": "page_id: habits\n"}]},
        },
    }


def test_a_passing_wiring_result_produces_nothing() -> None:
    assert _wiring_repair_errors({"passed": True, "orphaned_pages": []}, FILES) == []


def test_each_orphan_becomes_one_repairable_error() -> None:
    errors = _wiring_repair_errors(
        _wiring(
            ("habits", "habit-list", "/api/habits"),
            ("dashboard", "stats", "/api/habits/stats"),
        ),
        FILES,
    )

    assert len(errors) == 2
    assert errors[0].startswith("ui/pages/habits.yaml:")
    assert errors[1].startswith("ui/pages/dashboard.yaml:")


def test_the_error_routes_to_the_agent_that_owns_pages() -> None:
    errors = _wiring_repair_errors(_wiring(("habits", "habit-list", "/api/habits")), FILES)

    result = prepare_bundle_repair({"passed": False, "errors": errors}, _repair_context())

    assert result["status"] == "needs_revision"
    assert result["target_agent"] == "AppSchemaAgent"
    assert result["active"]["task_id"] == "habit_page"
    assert result["active"]["allowed_paths"] == ["ui/pages/habits.yaml"]
    assert result["target_errors"] == errors
    assert result["attempt"] == 1


def test_an_unowned_page_diagnostic_cannot_infer_repair_authority_from_its_path() -> None:
    errors = _wiring_repair_errors(_wiring(("dashboard", "stats", "/api/habits")), FILES)

    result = prepare_bundle_repair({"passed": False, "errors": errors}, _repair_context())

    assert result["status"] == "blocked"
    assert result["target_agent"] is None
    assert result["diagnostics"][0]["block_reason"] == "missing_or_ambiguous_owner"
    assert result["attempt"] == 0


def test_a_page_repair_cannot_bypass_its_missing_prerequisite_output() -> None:
    context = _repair_context()
    context["app_task_batch_results"].pop("habit_contract")
    errors = _wiring_repair_errors(_wiring(("habits", "habit-list", "/api/habits")), FILES)

    result = prepare_bundle_repair({"passed": False, "errors": errors}, context)

    assert result["status"] == "blocked"
    assert result["target_agent"] is None
    assert result["diagnostics"][0]["block_reason"] == "prerequisite_not_accepted"
    assert result["attempt"] == 0


def test_the_declared_actions_are_named_so_the_fix_is_not_a_guess() -> None:
    errors = _wiring_repair_errors(_wiring(("habits", "habit-list", "/api/habits")), FILES)

    assert "/api/modules/habit_registry/list_habits" in errors[0]
    assert "/api/modules/habit_registry/create_habit" in errors[0]
    assert "Do not invent an action id." in errors[0]


def test_an_invented_action_under_a_real_module_is_still_reported() -> None:
    """The live case: right module id, action that was never declared."""
    errors = _wiring_repair_errors(
        _wiring(("habits", "row/delete", "/api/modules/habit_registry/delete_habit")), FILES
    )

    assert len(errors) == 1
    assert "delete_habit" in errors[0]
    assert "/api/modules/habit_registry/list_habits" in errors[0]


def test_a_bundle_with_no_module_contract_says_so_rather_than_listing_nothing() -> None:
    errors = _wiring_repair_errors(_wiring(("habits", "habit-list", "/api/habits")), {})

    assert "none declared" in errors[0]


def test_an_orphan_without_a_page_is_skipped_not_mislabelled() -> None:
    errors = _wiring_repair_errors(_wiring(("", "section", "/api/habits")), FILES)

    assert errors == []
