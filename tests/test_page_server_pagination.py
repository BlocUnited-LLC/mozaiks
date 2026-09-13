"""Server paging crosses typed generation, materialization, and module GET input."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from factory_app.workflows.AppGenerator.tools.save_app_schema import save_app_schema
from mozaiksai.core.runtime.app.page_schema import (
    AppDataTableConfig,
    PageSchemaValidationError,
    validate_page_schema,
)
from mozaiksai.core.workflow.outputs.structured import (
    get_provider_response_model,
    load_workflow_structured_outputs,
)
from mozaiksai.core.workflow.ui_primitives import format_generated_page_ui_primitive_guidance
from mozaiksai.hosts.routers import modules
from tests.factory_context import factory_context


def _page(**overrides):
    return {
        "schema_version": "mozaiks.app_page.v1", "name": "books", "title": "Books",
        "route": "/books", "page_type": "record_list", "layout": "full-width",
        "sections": [{"id": "books", "primitive": "DataTable", "config": {
            "columns": [{"key": "title"}], "api_endpoint": "/api/modules/books/list",
            "data_key": "items", "pagination": True, "pagination_mode": "server",
            "page_size": 20, "total_key": "total", "search": True, **overrides,
        }}],
    }


@pytest.mark.parametrize("overrides", [
    {"total_key": None}, {"total_key": ""}, {"total_key": "eval(total)"},
    {"data_key": None}, {"api_endpoint": None}, {"api_endpoint": "/api/other"},
    {"pagination": False}, {"page_size": None}, {"page_size": 0}, {"page_size": -1},
    {"page_size": 101}, {"page_size": True}, {"page_size": 2.5}, {"page_size": "20"},
    {"pagination_mode": None, "total_key": None},
    {"pagination_mode": "remote"}, {"pagination_mode": "client", "total_key": "total"},
])
def test_invalid_server_paging_fails_before_materialization(overrides):
    with pytest.raises(PageSchemaValidationError):
        validate_page_schema(_page(**overrides))


def test_resource_table_server_mode_is_explicitly_unsupported():
    page = _page()
    page["sections"][0]["primitive"] = "ResourceTable"
    with pytest.raises(PageSchemaValidationError):
        validate_page_schema(page)


def test_server_paging_preserves_known_module_and_action_gates():
    for index in ({}, {"books": frozenset({"create"})}):
        with pytest.raises(PageSchemaValidationError):
            validate_page_schema(_page(), action_index=index)
    validate_page_schema(_page(), action_index={"books": frozenset({"list"})})


@pytest.mark.parametrize("primitive,overrides", [
    ("DataTable", {"total_key": None}), ("DataTable", {"page_size": 0}), ("ResourceTable", {}),
])
def test_materializer_rejects_invalid_server_contract_before_writing(primitive, overrides, tmp_path, monkeypatch):
    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path))
    page = _page(**overrides)
    page["sections"][0]["primitive"] = primitive
    models, _ = load_workflow_structured_outputs("AppGenerator")
    typed = models["AppPageSchema"].model_validate(page)
    with pytest.raises(ValueError):
        save_app_schema(manifest={"app_name": "Books", "pages": ["books"], "default_route": "/books"},
                        pages=[typed], context_variables=factory_context())
    assert not list(tmp_path.rglob("*"))


def test_typed_server_page_materializes_and_keeps_client_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path))
    models, _ = load_workflow_structured_outputs("AppGenerator")
    typed = models["AppPageSchema"].model_validate(_page())
    save_app_schema(manifest={"app_name": "Books", "pages": ["books"], "default_route": "/books"},
                    pages=[typed], context_variables=factory_context())
    output = tmp_path / "apps/generated-app/build-test/app/ui/pages/books.yaml"
    page = validate_page_schema(yaml.safe_load(output.read_text()))
    assert page.sections[0].config["pagination_mode"] == "server"
    assert page.sections[0].config["total_key"] == "total"
    for name in ("AppDataTableConfig", "AppResourceTableConfig"):
        model = models[name]
        config = model.model_validate({"columns": [{"key": "title"}]})
        assert config.pagination_mode == "client"
        assert config.page_size is None
        with pytest.raises(ValidationError, match="pagination_mode"):
            model.model_validate({"columns": [{"key": "title"}], "pagination_mode": None})
        canonical_schema = model.model_json_schema()
        assert canonical_schema["properties"]["pagination_mode"]["default"] == "client"
        assert "pagination_mode" not in canonical_schema["required"]
        provider_schema = get_provider_response_model(model).model_json_schema()
        assert "pagination_mode" in provider_schema["required"]
        assert model.model_json_schema() == canonical_schema
        client_page = _page(total_key=None)
        del client_page["sections"][0]["config"]["pagination_mode"]
        client_page["sections"][0]["primitive"] = name.removeprefix("App").removesuffix("Config")
        save_app_schema(manifest={"app_name": "Books", "pages": ["books"], "default_route": "/books"},
                        pages=[models["AppPageSchema"].model_validate(client_page)], context_variables=factory_context())
        assert validate_page_schema(yaml.safe_load(output.read_text())).sections[0].config["pagination_mode"] == "client"
    assert AppDataTableConfig(columns=["title"], page_size=200).pagination_mode == "client"
    guidance = format_generated_page_ui_primitive_guidance()
    assert "pagination_mode" in guidance
    assert "page/page_size/search" in guidance


def test_module_get_decodes_only_declared_integer_inputs(monkeypatch):
    captured = []

    async def scope(**kwargs):
        return {**kwargs["requested_scope"], "permissions": []}

    from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor

    class Handler:
        async def list(self, ctx, **params):
            captured.append((ctx, params))
            return {"items": [], "total": 0}

    executor = ModuleExecutor()
    executor.register("books", Handler(), action_method_map={"list": "list"}, action_schemas={"list": {"input": {
        "type": "object", "additionalProperties": False, "properties": {
            "page": {"type": "integer", "minimum": 1},
            "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
            "search": {"type": "string"},
        }, "required": ["page", "page_size", "search"],
    }}})
    monkeypatch.setattr(executor, "_build_persistence_context", lambda _request: None)
    monkeypatch.setattr(executor, "_finalize_dispatch_audit", AsyncMock())

    monkeypatch.setattr(modules, "is_auth_enabled", lambda: False)
    monkeypatch.setattr(modules, "get_platform_hooks", lambda: SimpleNamespace(call_module_scope=scope))
    app = FastAPI()
    app.include_router(modules.router)
    app.state.executor_registry = SimpleNamespace(module_executor=executor)
    app.state.module_action_surfaces = {}
    app.state.failed_module_names = []
    with TestClient(app) as client:
        response = client.get("/api/modules/books/list", params={
            "page": "2", "page_size": "20", "search": "001+[x].* &?", "user_id": "foreign",
        })
        for bad in ["two", "2.5", "0", "-1", "1e2", "true", "02", "+2", " 2", "10000000000000000"]:
            rejected = client.get("/api/modules/books/list", params={"page": bad, "page_size": "20", "search": ""})
            assert rejected.status_code == 400
        assert client.get("/api/modules/books/list", params={"page": "1", "page_size": "101", "search": ""}).status_code == 400
        assert client.post("/api/modules/books/list", json={"page": "2", "page_size": "20", "search": ""}).status_code == 400
        # The transport's 16-character decode limit is not a JSON integer limit.
        assert client.get("/api/modules/books/list", params={
            "page": "9999999999999999", "page_size": "20", "search": "10000000000000000",
        }).status_code == 200
        assert client.post("/api/modules/books/list", json={
            "page": 10000000000000000, "page_size": 20, "search": "10000000000000000",
        }).status_code == 200
    assert response.status_code == 200
    assert len(captured) == 3
    assert captured[0][1] == {"page": 2, "page_size": 20, "search": "001+[x].* &?"}
    assert captured[0][0].user_id != "foreign"
    assert captured[1][1] == {"page": 9999999999999999, "page_size": 20, "search": "10000000000000000"}
    assert captured[2][1] == {"page": 10000000000000000, "page_size": 20, "search": "10000000000000000"}
