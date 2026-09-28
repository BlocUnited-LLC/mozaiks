"""Module identities are explicit closed references, never URL guesses."""
from __future__ import annotations

from copy import deepcopy

import pytest
import yaml

from mozaiksai.core.workflow.generator_support.page_plan_utils import (
    compile_page_data_sources,
    module_action_index,
    normalize_planned_page_content,
)

MODULE_YAML = """
schema_version: mozaiks.module.v1
module:
  id: habits_registry
actions:
- id: list_habits
  output_schema:
    type: array
    items:
      type: object
      properties:
        name:
          type: string
- id: create_habit
"""


def _index():
    return module_action_index({"modules/habits_registry/module.yaml": MODULE_YAML})


def _page(module_id="habits_registry", action_id="list_habits"):
    return {
        "name": "dashboard", "route": "/dashboard",
        "sections": [{"id": "habits", "primitive": "DataTable", "config": {
            "columns": [{"key": "name"}],
            "data_source": {"module_id": module_id, "action_id": action_id},
        }}],
    }


def test_index_reads_generated_contract():
    assert set(_index()["habits_registry"]) == {"list_habits", "create_habit"}
    assert _index()["habits_registry"]["list_habits"]["output_schema"]["type"] == "array"


def test_explicit_pair_compiles_to_runtime_endpoint():
    document = _page()
    assert compile_page_data_sources(document, _index(), reject_api_endpoints=True) == 1
    assert document["sections"][0]["config"]["api_endpoint"] == "/api/modules/habits_registry/list_habits"
    assert "data_source" not in document["sections"][0]["config"]
    baseline = deepcopy(document)
    assert compile_page_data_sources(document, _index()) == 0
    assert document == baseline


@pytest.mark.parametrize("module_id,action_id", [("habits", "list_habits"), ("habits_registry", "unknown")])
def test_unknown_pairs_fail_without_guessing_an_owner(module_id, action_id):
    with pytest.raises(ValueError, match="unknown module/action"):
        compile_page_data_sources(_page(module_id, action_id), _index())


@pytest.mark.parametrize("source", ["/api/tasks", {}, {"module_id": "habits_registry"},
                                     {"module_id": "habits_registry", "action_id": "list_habits", "url": "/api/tasks"},
                                     {"module_id": "../habits_registry", "action_id": "list_habits"}])
def test_data_source_has_one_closed_shape(source):
    document = _page()
    document["sections"][0]["config"]["data_source"] = source
    with pytest.raises(ValueError, match="data_source"):
        compile_page_data_sources(document, _index())


def test_no_inventory_is_not_permission_to_guess():
    with pytest.raises(ValueError, match="unknown module/action"):
        compile_page_data_sources(_page(), {})


@pytest.mark.parametrize("endpoint", ["/api/modules/dashboard", "/api/tasks", "/api/modules/habits_registry/list_habits"])
def test_model_written_urls_are_rejected_even_if_they_would_resolve(endpoint):
    page = _page()
    page["sections"][0]["config"] = {"api_endpoint": endpoint}
    with pytest.raises(ValueError, match="model-authored endpoint URLs"):
        compile_page_data_sources(page, _index(), reject_api_endpoints=True)


def test_normalization_compiles_nested_sections_and_actions():
    page = _page()
    form = {"id": "new-habit", "primitive": "Form", "config": {
        "fields": [], "submit_action": {"action_type": "submit", "label": "Create", "data_source": {
            "module_id": "habits_registry", "action_id": "create_habit",
        }},
    }}
    page["sections"].append({"id": "grid", "primitive": "Grid", "config": {"columns": 2, "children": [form]}})
    out = normalize_planned_page_content(yaml.safe_dump(page), path="ui/pages/dashboard.yaml", modules=_index(), reject_api_endpoints=True)
    assert "data_source" not in out
    assert "api_endpoint: /api/modules/habits_registry/list_habits" in out
    assert "href: /api/modules/habits_registry/create_habit" in out


def test_assembly_never_retargets_a_compiled_url():
    page = _page()
    page["sections"][0]["config"] = {"api_endpoint": "/api/modules/habits/list_habits"}
    content = yaml.safe_dump(page)
    assert normalize_planned_page_content(content, path="ui/pages/dashboard.yaml", modules=_index()) == content
