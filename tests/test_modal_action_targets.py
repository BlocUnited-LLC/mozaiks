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
