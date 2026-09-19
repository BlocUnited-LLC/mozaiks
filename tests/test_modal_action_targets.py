"""Modal actions must point at a Modal that exists on the page.

A live build generated a form whose cancel action closed a modal nothing
declared:

    $.sections[1].config.children[0].config.cancel_action.payload.modal_id:
    page_schema.unknown_modal: Modal actions require modal_id referencing a
    Modal on this page.

Two readings are unambiguous: an action inside a Modal means that Modal, and a
page with exactly one Modal has only one candidate. Anything else is left to
fail, because guessing between several modals would silently wire the wrong one
- a page that loads and does the wrong thing is worse than one that is rejected.
"""

from __future__ import annotations

from typing import Any

from mozaiksai.core.workflow.generator_support.page_plan_utils import (
    resolve_modal_action_targets,
)


def _cancel_action(modal_id: str) -> dict[str, Any]:
    return {
        "action_type": "event",
        "event_type": "ui.modal.close",
        "payload": {"modal_id": modal_id},
    }


def _page_with_modal(modal_id: str, action_modal_id: str) -> dict[str, Any]:
    return {
        "name": "habits",
        "route": "/habits",
        "sections": [
            {"id": "header", "primitive": "PageHeader", "config": {"title": "Habits"}},
            {
                "id": modal_id,
                "primitive": "Modal",
                "config": {
                    "children": [
                        {
                            "id": "habit_form",
                            "primitive": "Form",
                            "config": {"fields": [], "cancel_action": _cancel_action(action_modal_id)},
                        }
                    ]
                },
            },
        ],
    }


def test_an_action_inside_a_modal_targets_that_modal() -> None:
    page = _page_with_modal("habit_modal", "does_not_exist")

    assert resolve_modal_action_targets(page) == 1

    form = page["sections"][1]["config"]["children"][0]
    assert form["config"]["cancel_action"]["payload"]["modal_id"] == "habit_modal"


def test_a_correct_reference_is_left_alone() -> None:
    page = _page_with_modal("habit_modal", "habit_modal")

    assert resolve_modal_action_targets(page) == 0


def test_a_page_with_one_modal_resolves_an_action_outside_it() -> None:
    page = {
        "name": "habits",
        "route": "/habits",
        "sections": [
            {
                "id": "open_button",
                "primitive": "Button",
                "config": {
                    "action": {
                        "action_type": "event",
                        "event_type": "ui.modal.open",
                        "payload": {"modal_id": "wrong"},
                    }
                },
            },
            {"id": "the_modal", "primitive": "Modal", "config": {"children": []}},
        ],
    }

    assert resolve_modal_action_targets(page) == 1
    assert page["sections"][0]["config"]["action"]["payload"]["modal_id"] == "the_modal"


def test_ambiguity_between_several_modals_is_left_to_the_validator() -> None:
    """Guessing here would silently wire the wrong modal."""
    page = {
        "name": "habits",
        "route": "/habits",
        "sections": [
            {
                "id": "open_button",
                "primitive": "Button",
                "config": {
                    "action": {
                        "action_type": "event",
                        "event_type": "ui.modal.open",
                        "payload": {"modal_id": "wrong"},
                    }
                },
            },
            {"id": "modal_a", "primitive": "Modal", "config": {"children": []}},
            {"id": "modal_b", "primitive": "Modal", "config": {"children": []}},
        ],
    }

    assert resolve_modal_action_targets(page) == 0
    assert page["sections"][0]["config"]["action"]["payload"]["modal_id"] == "wrong"


def test_a_page_with_no_modals_is_untouched() -> None:
    page = {
        "name": "habits",
        "route": "/habits",
        "sections": [
            {
                "id": "b",
                "primitive": "Button",
                "config": {
                    "action": {
                        "action_type": "event",
                        "event_type": "ui.modal.open",
                        "payload": {"modal_id": "ghost"},
                    }
                },
            }
        ],
    }

    assert resolve_modal_action_targets(page) == 0


from mozaiksai.core.runtime.app.page_schema import AppPageSection  # noqa: E402
from mozaiksai.core.workflow.generator_support.page_plan_utils import (  # noqa: E402
    declare_the_intended_modal,
)


def _page_wiring_an_undeclared_modal(modal_id: str = "habit_form_modal") -> dict[str, Any]:
    """The live failure: open and close wired, no Modal declared."""
    return {
        "name": "habits",
        "route": "/habits",
        "sections": [
            {
                "id": "toolbar",
                "primitive": "PageHeader",
                "config": {
                    "title": "Habits",
                    "actions": [
                        {
                            "label": "New habit",
                            "action_type": "event",
                            "event_type": "ui.modal.open",
                            "payload": {"modal_id": modal_id},
                        }
                    ],
                },
            },
            {
                "id": "editor_card",
                "primitive": "Card",
                "title": "Habit",
                "config": {
                    "variant": "outlined",
                    "children": [
                        {
                            "id": "habit_form",
                            "primitive": "Form",
                            "config": {
                                "fields": [],
                                "cancel_action": {
                                    "label": "Cancel",
                                    "action_type": "event",
                                    "event_type": "ui.modal.close",
                                    "payload": {"modal_id": modal_id},
                                },
                            },
                        }
                    ],
                },
            },
        ],
    }


def test_the_container_holding_the_form_becomes_the_modal() -> None:
    page = _page_wiring_an_undeclared_modal()

    assert declare_the_intended_modal(page) == "habit_form_modal"

    section = page["sections"][1]
    assert section["primitive"] == "Modal"
    assert section["id"] == "habit_form_modal"
    # The form it held is still inside it.
    assert section["config"]["children"][0]["id"] == "habit_form"
    # And the real schema accepts the result.
    AppPageSection.model_validate(section)


def test_config_modal_cannot_accept_is_dropped_not_smuggled() -> None:
    """Unknown runtime-affecting fields are forbidden, so they cannot survive."""
    page = _page_wiring_an_undeclared_modal()

    declare_the_intended_modal(page)

    assert "variant" not in page["sections"][1]["config"]
    assert page["sections"][1]["config"]["title"] == "Habit"


def test_a_page_that_already_declares_a_modal_is_untouched() -> None:
    page = _page_wiring_an_undeclared_modal()
    page["sections"].append({"id": "other", "primitive": "Modal", "config": {"children": []}})

    assert declare_the_intended_modal(page) is None


def test_two_different_modal_ids_are_refused() -> None:
    """Not determined - which container is which dialog?"""
    page = _page_wiring_an_undeclared_modal()
    page["sections"][0]["config"]["actions"][0]["payload"]["modal_id"] = "a_different_modal"

    assert declare_the_intended_modal(page) is None


def test_two_candidate_containers_are_refused() -> None:
    page = _page_wiring_an_undeclared_modal()
    page["sections"].append(dict(page["sections"][1], id="second_card"))

    assert declare_the_intended_modal(page) is None


def _close_action(payload: Any) -> dict[str, Any]:
    """A close action carrying whatever payload the model actually emitted."""
    action: dict[str, Any] = {"action_type": "event", "event_type": "ui.modal.close"}
    if payload is not _ABSENT:
        action["payload"] = payload
    return action


_ABSENT = object()


def _modal_page(payload: Any) -> dict[str, Any]:
    return {
        "name": "dashboard",
        "route": "/dashboard",
        "sections": [
            {
                "id": "create-task-modal",
                "primitive": "Modal",
                "config": {"actions": [_close_action(payload)]},
            }
        ],
    }


def test_an_action_with_payload_none_still_gets_its_enclosing_modal() -> None:
    """The commonest shape of this defect, and the one that used to be skipped.

    A live build (chat 61db4014) lost its entire two-attempt repair budget to
    four such actions. Each sat inside a Modal this function could already see;
    only the absent payload stopped it resolving them.
    """
    page = _modal_page(None)

    assert resolve_modal_action_targets(page) == 1

    action = page["sections"][0]["config"]["actions"][0]
    assert action["payload"] == {"modal_id": "create-task-modal"}


def test_an_action_with_no_payload_key_at_all_is_resolved() -> None:
    page = _modal_page(_ABSENT)

    assert resolve_modal_action_targets(page) == 1
    assert page["sections"][0]["config"]["actions"][0]["payload"] == {"modal_id": "create-task-modal"}


def test_an_empty_payload_dict_is_resolved() -> None:
    page = _modal_page({})

    assert resolve_modal_action_targets(page) == 1
    assert page["sections"][0]["config"]["actions"][0]["payload"] == {"modal_id": "create-task-modal"}


def test_a_populated_payload_keeps_its_other_keys() -> None:
    """Filling in modal_id must not discard what the model already wrote."""
    page = _modal_page({"source": "toolbar"})

    assert resolve_modal_action_targets(page) == 1
    assert page["sections"][0]["config"]["actions"][0]["payload"] == {
        "source": "toolbar",
        "modal_id": "create-task-modal",
    }


def test_an_unresolvable_action_is_left_exactly_as_found() -> None:
    """No enclosing Modal and no sole candidate: do not invent a payload."""
    page = {
        "name": "dashboard",
        "route": "/dashboard",
        "sections": [
            {"id": "a-modal", "primitive": "Modal", "config": {}},
            {"id": "b-modal", "primitive": "Modal", "config": {}},
            {"id": "toolbar", "primitive": "Toolbar", "config": {"actions": [_close_action(None)]}},
        ],
    }

    assert resolve_modal_action_targets(page) == 0
    # Ambiguous between two modals, so the action must reach the validator
    # exactly as written rather than carrying an empty payload we invented.
    assert page["sections"][2]["config"]["actions"][0]["payload"] is None


def test_the_four_actions_from_the_live_run_all_resolve() -> None:
    """Distilled from chat 61db4014: two Modals, each with a close action and a
    nested Form cancel_action, every one of them emitted with payload: None.

    save_app_schema rejected the page four times with page_schema.unknown_modal
    and the run exhausted its repair budget one step short of a valid page.
    """
    def modal(modal_id: str) -> dict[str, Any]:
        return {
            "id": modal_id,
            "primitive": "Modal",
            "config": {
                "actions": [{"action_type": "navigate", "href": "/dashboard"}, _close_action(None)],
                "children": [
                    {
                        "id": f"{modal_id}-form",
                        "primitive": "Form",
                        "config": {"fields": [], "cancel_action": _close_action(None)},
                    }
                ],
            },
        }

    page = {
        "name": "Dashboard",
        "route": "/dashboard",
        "sections": [
            {"id": "summary", "primitive": "SummaryStrip", "config": {}},
            {"id": "task-list", "primitive": "DataTable", "config": {}},
            modal("create-task-modal"),
            modal("edit-task-modal"),
        ],
    }

    assert resolve_modal_action_targets(page) == 4

    for section_index, modal_id in ((2, "create-task-modal"), (3, "edit-task-modal")):
        config = page["sections"][section_index]["config"]
        assert config["actions"][1]["payload"]["modal_id"] == modal_id
        assert config["children"][0]["config"]["cancel_action"]["payload"]["modal_id"] == modal_id
