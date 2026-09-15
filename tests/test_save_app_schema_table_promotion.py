"""A section that asks for filtering must get the primitive that provides it.

A live build reached code generation for the first time and failed here:

    task batch 'app_build_tasks' failed at task 'page_bundle_task':
    ui/pages/habits.yaml: $.sections[0].default_filter:
      page_schema.extra_forbidden: Unknown runtime-affecting field is not allowed.
    $.sections[0].default_sort: ... $.sections[0].filters: ...

The fields are real. AppResourceTableConfig declares search, filters,
default_filter, sorts and default_sort; AppDataTableConfig does not. The agent
asked for a filterable, sortable table and reached for the wrong primitive, and
the schema itself says which one owns those fields.

These tests validate against the real page schema rather than asserting on the
normalizer's output shape.
"""

from __future__ import annotations

from typing import Any

import pytest

from factory_app.workflows.AppGenerator.tools.save_app_schema import _normalize_page_section
from mozaiksai.core.runtime.app.page_schema import AppPageSection


def _section(**config: Any) -> dict[str, Any]:
    return {
        "id": "habits_table",
        "primitive": "DataTable",
        "config": {
            "columns": [{"key": "name", "label": "Habit"}],
            "api_endpoint": "/api/modules/habits_module/list_habits",
            **config,
        },
    }


def test_the_live_failure_is_reproduced_without_promotion() -> None:
    raw = _section(filters=[], default_filter="active", default_sort="name")

    with pytest.raises(ValueError) as excinfo:
        AppPageSection.model_validate(raw)

    assert "default_filter" in str(excinfo.value) or "extra" in str(excinfo.value).lower()


@pytest.mark.parametrize(
    "field,value",
    [
        ("default_filter", "active"),
        ("default_sort", "name"),
        ("search", True),
        ("search_placeholder", "Search habits"),
    ],
)
def test_any_resource_table_field_promotes_the_primitive(field: str, value: Any) -> None:
    normalized = _normalize_page_section(_section(**{field: value}))

    assert normalized["primitive"] == "ResourceTable"
    # The assertion that matters: the real schema now accepts the section.
    AppPageSection.model_validate(normalized)


def test_a_plain_data_table_is_left_alone() -> None:
    normalized = _normalize_page_section(_section())

    assert normalized["primitive"] == "DataTable"
    AppPageSection.model_validate(normalized)


def test_server_pagination_is_not_promoted() -> None:
    """ResourceTable accepts client pagination only.

    Promoting a server-paginated table would trade one rejection for another,
    so it stays a DataTable and the field is left for the validator to reject.
    """
    normalized = _normalize_page_section(
        _section(default_sort="name", pagination_mode="server")
    )

    assert normalized["primitive"] == "DataTable"
