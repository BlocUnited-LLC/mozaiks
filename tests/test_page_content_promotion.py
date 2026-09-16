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


from mozaiksai.core.workflow.generator_support.page_plan_utils import (  # noqa: E402
    align_page_name_with_file,
)


def test_the_display_label_moves_to_title_and_the_name_follows_the_file() -> None:
    """The live failure: the human label was put in `name`.

        $.name: page_schema.name_mismatch: Page schema name must match the
        requested page. Runtime page name must match file identity 'habits';
        keep the display label in title.
    """
    document = {"name": "Habit Management", "route": "/habits", "sections": []}

    assert align_page_name_with_file(document, "ui/pages/habits.yaml") == "Habit Management"
    assert document["name"] == "habits"
    assert document["title"] == "Habit Management"


def test_an_existing_title_is_not_overwritten() -> None:
    document = {"name": "Habit Management", "title": "Your Habits", "route": "/habits"}

    align_page_name_with_file(document, "ui/pages/habits.yaml")

    assert document["name"] == "habits"
    assert document["title"] == "Your Habits"


def test_a_matching_name_is_left_alone() -> None:
    document = {"name": "habits", "route": "/habits"}

    assert align_page_name_with_file(document, "ui/pages/habits.yaml") is None
    assert "title" not in document


def test_the_rename_reaches_the_written_content() -> None:
    import yaml as _yaml

    content = _yaml.safe_dump({"name": "Habit Management", "route": "/habits", "sections": []})

    normalized = normalize_planned_page_content(content, path="ui/pages/habits.yaml")

    document = _yaml.safe_load(normalized)
    assert document["name"] == "habits"
    assert document["title"] == "Habit Management"
