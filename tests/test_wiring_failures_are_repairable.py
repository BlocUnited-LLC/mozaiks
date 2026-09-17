"""A wiring failure should be repaired, not just reported.

A wiring failure already blocked acceptance - it is in the acceptance
subresults - but it was never handed to _prepare_bundle_repair, whose errors
come only from the bundle scan, runtime load, runtime quality and module
implementation results. So a build with orphaned endpoints failed without ever
attempting a fix.

The loop it was missing out on already does the right things: it routes
ui/pages/* to AppSchemaAgent, bounds itself with max_attempts, hands each agent
only its own files, and stops when a retry produces the same failure
fingerprint. The errors simply never arrived.

Each message names the declared actions. A page agent told only "unknown
action" would guess again - which is how /api/habits, and a delete_habit that
was never declared, reached a bundle whose module contract was right there in
the same generated_files.
"""

from __future__ import annotations

from factory_app.workflows.AppGenerator.tools.app_validation import (
    _bundle_repair_target_for_error,
    _wiring_repair_errors,
)

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
    """The routing already existed; only the errors were missing."""
    errors = _wiring_repair_errors(_wiring(("habits", "habit-list", "/api/habits")), FILES)

    assert _bundle_repair_target_for_error(errors[0]) == "AppSchemaAgent"


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
