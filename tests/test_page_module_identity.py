"""A page endpoint must name the module that actually declares the action.

A generated habit tracker emitted

    /api/modules/habits/create_habit
    /api/modules/habits/checkoff_habit

against a module whose id is `habits_registry`. Every call 404s, and acceptance
reports the actions as orphaned.

The canonical FORM was correct. The page agent is told four separate times to
use `/api/modules/{module_id}/{action_id}`, including "Do not preserve a
nonexistent API path to match design docs", and it followed that rule - it
invented the identity, not the shape. More prompt text was never going to fix
it, which is why this is a deterministic rewrite against the emitted contracts.
"""

from __future__ import annotations

import yaml

from mozaiksai.core.workflow.generator_support.page_plan_utils import (
    module_action_index,
    normalize_planned_page_content,
    retarget_page_module_ids,
)

MODULE_YAML = """
schema_version: mozaiks.module.v1
module:
  id: habits_registry
actions:
- id: create_habit
- id: checkoff_habit
"""


def _index() -> dict[str, set[str]]:
    return module_action_index({"modules/habits_registry/module.yaml": MODULE_YAML})


def _page(endpoint: str) -> dict:
    return {
        "name": "dashboard",
        "route": "/dashboard",
        "sections": [{"type": "Form", "actions": [{"kind": "submit", "href": endpoint}]}],
    }


def test_the_index_reads_the_generated_contract() -> None:
    assert _index() == {"habits_registry": {"create_habit", "checkoff_habit"}}


def test_the_live_case_is_retargeted() -> None:
    document = _page("/api/modules/habits/create_habit")

    rewrites = retarget_page_module_ids(document, _index())

    assert rewrites == ["habits/create_habit -> habits_registry/create_habit"]
    assert document["sections"][0]["actions"][0]["href"] == (
        "/api/modules/habits_registry/create_habit"
    )


def test_a_real_module_is_left_alone() -> None:
    """Even when the action is missing - module.yaml is the contract, not this."""
    for endpoint in (
        "/api/modules/habits_registry/create_habit",
        "/api/modules/habits_registry/list_habits",
    ):
        document = _page(endpoint)
        assert retarget_page_module_ids(document, _index()) == []
        assert document["sections"][0]["actions"][0]["href"] == endpoint


def test_an_ambiguous_action_is_not_guessed() -> None:
    """Two owners is a real ambiguity; picking one silently binds the wrong module."""
    modules = {"habits_registry": {"create_habit"}, "archive": {"create_habit"}}
    document = _page("/api/modules/habits/create_habit")

    assert retarget_page_module_ids(document, modules) == []
    assert document["sections"][0]["actions"][0]["href"] == "/api/modules/habits/create_habit"


def test_an_unknown_action_is_not_guessed() -> None:
    document = _page("/api/modules/habits/list_habits")

    assert retarget_page_module_ids(document, _index()) == []


def test_a_non_canonical_path_is_left_alone() -> None:
    """/api/habits is the read-action defect, not this one.

    Rewriting it would mean inventing an action id. The module gaining a read
    is what removes the reason the page wrote it.
    """
    document = _page("/api/habits")

    assert retarget_page_module_ids(document, _index()) == []
    assert document["sections"][0]["actions"][0]["href"] == "/api/habits"


def test_endpoints_buried_in_a_config_hint_string_are_retargeted() -> None:
    """Table endpoints live inside a JSON-encoded string, not a YAML field."""
    document = {
        "name": "dashboard",
        "route": "/dashboard",
        "sections": [
            {
                "type": "ResourceTable",
                "config_hint": '{"api_endpoint": "/api/modules/habits/checkoff_habit"}',
            }
        ],
    }

    assert retarget_page_module_ids(document, _index())
    assert "habits_registry/checkoff_habit" in document["sections"][0]["config_hint"]


def test_no_generated_modules_means_no_rewriting() -> None:
    document = _page("/api/modules/habits/create_habit")

    assert retarget_page_module_ids(document, {}) == []


def test_the_rewrite_is_idempotent() -> None:
    document = _page("/api/modules/habits/create_habit")
    index = _index()

    assert retarget_page_module_ids(document, index)
    assert retarget_page_module_ids(document, index) == []


def test_normalization_carries_the_rewrite_into_the_file_content() -> None:
    """The correction has to reach the content, not only the parsed document."""
    content = yaml.safe_dump(_page("/api/modules/habits/create_habit"), sort_keys=False)

    out = normalize_planned_page_content(content, path="ui/pages/dashboard.yaml", modules=_index())

    assert "/api/modules/habits_registry/create_habit" in out
    assert "/api/modules/habits/create_habit" not in out


def test_a_page_with_nothing_to_fix_is_returned_unchanged() -> None:
    content = yaml.safe_dump(_page("/api/modules/habits_registry/create_habit"), sort_keys=False)

    assert normalize_planned_page_content(
        content, path="ui/pages/dashboard.yaml", modules=_index()
    ) == content
