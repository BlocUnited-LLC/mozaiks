"""Materialized page content must be corrected, not only validated.

A live build reached code generation and failed twice on the same rule, at
ui/pages/habits.yaml and then ui/pages/dashboard.yaml:

    $.sections[1].default_filter: page_schema.extra_forbidden:
      Unknown runtime-affecting field is not allowed.

The first fix normalized sections inside save_app_schema, but the task batch
materializes pages through a different path - validate_planned_page, called from
assemble_app_tasks and task_batches - so the correction never reached the file
being validated. Those call sites write the page from the same map they validate
it from, so the fix has to change the content itself.
"""

from __future__ import annotations

import yaml

from mozaiksai.core.workflow.generator_support.page_plan_utils import (
    normalize_planned_page_content,
    promote_page_table_primitives,
)


def _page(**config) -> dict:
    return {
        "name": "habits",
        "route": "/habits",
        "sections": [
            {"id": "header", "primitive": "Heading", "config": {"text": "Habits"}},
            {
                "id": "habits_table",
                "primitive": "DataTable",
                "config": {
                    "columns": [{"key": "name", "label": "Habit"}],
                    "api_endpoint": "/api/modules/habits_module/list_habits",
                    **config,
                },
            },
        ],
    }


def test_content_is_rewritten_not_just_accepted() -> None:
    content = yaml.safe_dump(_page(default_filter="active", filters=[], default_sort="name"))

    normalized = normalize_planned_page_content(content, path="ui/pages/habits.yaml")

    document = yaml.safe_load(normalized)
    assert document["sections"][1]["primitive"] == "ResourceTable"
    # The rejected fields survive - they were never the problem.
    assert document["sections"][1]["config"]["default_filter"] == "active"


def test_a_page_needing_no_change_is_returned_untouched() -> None:
    content = yaml.safe_dump(_page())

    assert normalize_planned_page_content(content, path="ui/pages/habits.yaml") == content


def test_nested_sections_are_promoted_too() -> None:
    document = {
        "name": "dashboard",
        "route": "/dashboard",
        "sections": [
            {
                "id": "grid",
                "primitive": "Grid",
                "config": {
                    "children": [
                        {
                            "id": "inner",
                            "primitive": "DataTable",
                            "config": {"columns": [], "default_sort": "name"},
                        }
                    ]
                },
            }
        ],
    }

    assert promote_page_table_primitives(document) == 1
    assert document["sections"][0]["config"]["children"][0]["primitive"] == "ResourceTable"


def test_server_pagination_is_left_for_the_validator() -> None:
    content = yaml.safe_dump(_page(default_sort="name", pagination_mode="server"))

    normalized = normalize_planned_page_content(content, path="ui/pages/habits.yaml")

    assert yaml.safe_load(normalized)["sections"][1]["primitive"] == "DataTable"


def test_unparseable_content_is_passed_through() -> None:
    """Never swallow a YAML error - the validator reports it far better."""
    broken = "sections: [unclosed"

    assert normalize_planned_page_content(broken, path="ui/pages/x.yaml") == broken
