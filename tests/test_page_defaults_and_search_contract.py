"""Existing primitive behavior must survive generation and strict page loading."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from factory_app.workflows.AppGenerator.tools.save_app_schema import save_app_schema
from mozaiksai.core.runtime.app.page_schema import (
    AppDataTableConfig,
    AppFormConfig,
    AppFormField,
    AppResourceTableConfig,
    validate_page_schema,
)
from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs
from mozaiksai.core.workflow.ui_primitives import format_generated_page_ui_primitive_guidance
from tests.factory_context import factory_context


@pytest.fixture(scope="module")
def models():
    return load_workflow_structured_outputs("AppGenerator")[0]


@pytest.mark.parametrize("value", ["planned", "", 0, 4, 1.5, False, True, None])
def test_scalar_defaults_survive_runtime_and_structured_output(models, value):
    payload = {"name": "status", "label": "Status", "type": "text", "default_value": value}
    for model in (AppFormField, models["AppFormField"]):
        actual = model.model_validate(payload).model_dump()["default_value"]
        assert actual == value
        assert type(actual) is type(value)


@pytest.mark.parametrize("value", [[], ["planned"], {}, {"expression": "selected_row.status"}])
def test_defaults_reject_non_scalar_values(models, value):
    for model in (AppFormField, models["AppFormField"]):
        with pytest.raises(ValidationError):
            model.model_validate({"name": "status", "label": "Status", "type": "text", "default_value": value})


@pytest.mark.parametrize("alias", ["default", "defaults", "default_value"])
def test_form_has_no_top_level_default_contract(models, alias):
    for model in (AppFormConfig, models["AppFormConfig"]):
        with pytest.raises(ValidationError):
            model.model_validate({"fields": [], alias: "planned"})


@pytest.mark.parametrize("primitive", ["DataTable", "ResourceTable"])
@pytest.mark.parametrize("keys", [["title", "author"], ["hidden_search_field"], [], None])
def test_search_keys_are_shared_by_both_table_contracts(models, primitive, keys):
    runtime = AppDataTableConfig if primitive == "DataTable" else AppResourceTableConfig
    payload = {"columns": [{"key": "title"}], "search": True, "search_keys": keys}
    for model in (runtime, models[f"App{primitive}Config"]):
        assert model.model_validate(payload).model_dump()["search_keys"] == keys


def test_resource_table_inherits_search_keys_without_redeclaring():
    assert "search_keys" in AppDataTableConfig.__annotations__
    assert "search_keys" not in AppResourceTableConfig.__annotations__


@pytest.mark.parametrize("keys", ["title,author", [1], {"title": True}])
def test_search_keys_reject_non_string_arrays(models, keys):
    for model in (AppDataTableConfig, AppResourceTableConfig, models["AppDataTableConfig"], models["AppResourceTableConfig"]):
        with pytest.raises(ValidationError):
            model.model_validate({"columns": [{"key": "title"}], "search_keys": keys})


def test_materializer_preserves_typed_defaults_and_search_keys(models, tmp_path, monkeypatch):
    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path))
    fields = [
        {"name": "status", "label": "Status", "type": "select", "default_value": "planned",
         "options": [{"value": "planned", "label": "Planned"}]},
        {"name": "count", "label": "Count", "type": "number", "default_value": 0},
        {"name": "notify", "label": "Notify", "type": "checkbox", "default_value": False},
        {"name": "notes", "label": "Notes", "type": "textarea", "default_value": ""},
    ]
    page = models["AppPageSchema"].model_validate({
        "schema_version": "mozaiks.app_page.v1", "name": "books", "route": "/books",
        "title": "Books", "page_type": "record_list", "layout": "full-width",
        "sections": [
            {"id": "books", "primitive": "DataTable", "config": {
                "columns": [{"key": key} for key in ("title", "author", "notes")],
                "search": True, "search_keys": ["title", "author"],
            }},
            {"id": "create", "primitive": "Modal", "config": {"title": "Add book", "children": [
                {"id": "create-form", "primitive": "Form", "config": {"fields": fields}},
            ]}},
        ],
    })
    save_app_schema(
        manifest={"app_name": "Books", "default_route": "/books", "pages": ["books"]},
        pages=[page], context_variables=factory_context(),
    )
    output = tmp_path / "apps/generated-app/build-test/app/ui/pages/books.yaml"
    persisted = yaml.safe_load(output.read_text(encoding="utf-8"))
    validate_page_schema(persisted)
    assert persisted["sections"][0]["config"]["search_keys"] == ["title", "author"]
    saved_fields = persisted["sections"][1]["config"]["children"][0]["config"]["fields"]
    assert [(f["default_value"], type(f["default_value"])) for f in saved_fields] == [
        (f["default_value"], type(f["default_value"])) for f in fields
    ]


def test_generated_prompt_guidance_exposes_bounded_defaults_and_loaded_row_search():
    guidance = format_generated_page_ui_primitive_guidance()
    assert "default_value" in guidance
    assert "string, number, boolean, or null" in guidance
    assert "selected_row" in guidance
    assert "search_keys" in guidance
    assert "loaded rows" in guidance
    # The prompt catalog is generated from the registered JS schema, not a second prompt list.
    root = Path(__file__).resolve().parents[1]
    assert "default_value" in (root / "chat-ui/src/ui/page-renderer/primitive_schemas.json").read_text()
