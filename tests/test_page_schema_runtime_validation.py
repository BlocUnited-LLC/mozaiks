from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mozaiksai.core.runtime.app.loader import AppLoader, AppLoadError
from mozaiksai.core.runtime.app.page_schema import (
    PAGE_SCHEMA_VERSION,
    PageSchemaValidationError,
    load_and_validate_page_schema,
    validate_page_schema,
)
from mozaiksai.core.workflow.generator_support.page_plan_utils import _page_from_plan
from mozaiksai.hosts.routers import shell


def _valid_page(**overrides: Any) -> dict[str, Any]:
    page: dict[str, Any] = {
        "schema_version": PAGE_SCHEMA_VERSION,
        "name": "home",
        "route": "/home",
        "title": "Home",
        "page_type": "record_list",
        "layout": "full-width",
        "roles": [],
        "sections": [
            {
                "id": "home-header",
                "primitive": "PageHeader",
                "title": None,
                "config": {
                    "title": "Home",
                    "subtitle": "A valid runtime page.",
                },
            }
        ],
    }
    page.update(overrides)
    return page


def _write_app(root: Path, page: dict[str, Any] | str, *, page_name: str = "home") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.json").write_text(json.dumps({"appName": "Test App"}), encoding="utf-8")
    pages_dir = root / "ui" / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    content = page if isinstance(page, str) else yaml.safe_dump(page, sort_keys=False)
    (pages_dir / f"{page_name}.yaml").write_text(content, encoding="utf-8")


def test_valid_page_schema_serves() -> None:
    page = validate_page_schema(_valid_page())

    assert page.name == "home"
    assert page.layout == "full-width"
    assert page.sections[0].primitive == "PageHeader"


@pytest.mark.parametrize(
    ("field", "value", "code_fragment"),
    [
        ("layout", "stack", "literal_error"),
        ("page_type", "dashboard", "literal_error"),
    ],
)
def test_invalid_page_schema_closed_taxonomy_fails(
    field: str,
    value: str,
    code_fragment: str,
) -> None:
    with pytest.raises(PageSchemaValidationError) as exc_info:
        validate_page_schema(_valid_page(**{field: value}))

    assert any(code_fragment in diagnostic.code for diagnostic in exc_info.value.diagnostics)


def test_unknown_primitive_fails() -> None:
    page = _valid_page()
    page["sections"][0]["primitive"] = "UnknownPrimitive"

    with pytest.raises(PageSchemaValidationError) as exc_info:
        validate_page_schema(page)

    assert any("value_error" in diagnostic.code for diagnostic in exc_info.value.diagnostics)


def test_malformed_primitive_configuration_fails() -> None:
    page = _valid_page()
    page["sections"][0]["config"] = {"subtitle": "missing required title"}

    with pytest.raises(PageSchemaValidationError) as exc_info:
        validate_page_schema(page)

    assert any("missing" in diagnostic.code for diagnostic in exc_info.value.diagnostics)


def test_unknown_runtime_field_fails() -> None:
    page = _valid_page()
    page["runtime"] = {"unsafe": True}

    with pytest.raises(PageSchemaValidationError) as exc_info:
        validate_page_schema(page)

    assert any(diagnostic.code == "page_schema.extra_forbidden" for diagnostic in exc_info.value.diagnostics)


def test_module_action_bindings_close_when_runtime_context_is_available() -> None:
    page = _valid_page(
        sections=[
            {
                "id": "home-table",
                "primitive": "DataTable",
                "title": None,
                "config": {
                    "columns": [{"key": "id", "label": "ID"}],
                    "api_endpoint": "/api/modules/accounts/missing_action",
                },
            }
        ]
    )

    with pytest.raises(PageSchemaValidationError) as exc_info:
        validate_page_schema(page, action_index={"accounts": frozenset({"list_accounts"})})

    assert any(diagnostic.code == "page_schema.unknown_action" for diagnostic in exc_info.value.diagnostics)


def test_invalid_yaml_fails_safely(tmp_path: Path) -> None:
    page_path = tmp_path / "bad.yaml"
    page_path.write_text("name: [unterminated", encoding="utf-8")

    with pytest.raises(PageSchemaValidationError) as exc_info:
        load_and_validate_page_schema(page_path, expected_name="bad")

    details = [diagnostic.model_dump() for diagnostic in exc_info.value.diagnostics]
    assert details == [
        {
            "code": "page_schema.invalid_yaml",
            "location": "$",
            "message": "Page schema YAML could not be parsed.",
        }
    ]


@pytest.mark.asyncio
async def test_boot_rejects_invalid_pages(tmp_path: Path) -> None:
    page = _valid_page(layout="stack")
    _write_app(tmp_path, page)

    with pytest.raises(AppLoadError, match="Invalid page schema"):
        await AppLoader.load(str(tmp_path))


def test_shell_endpoint_serves_validated_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_app(tmp_path, _valid_page())
    monkeypatch.setenv("PLATFORM_PATH", str(tmp_path))
    app = FastAPI()
    app.include_router(shell.router)

    response = TestClient(app).get("/api/pages/home")

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == PAGE_SCHEMA_VERSION
    assert body["layout"] == "full-width"


def test_shell_endpoint_rejects_invalid_page_without_internal_leaks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_app(tmp_path, _valid_page(sections=[{"id": "bad", "primitive": "Nope", "config": {}}]))
    monkeypatch.setenv("PLATFORM_PATH", str(tmp_path))
    app = FastAPI()
    app.include_router(shell.router)

    response = TestClient(app).get("/api/pages/home")

    assert response.status_code == 500
    text = response.text
    assert "Traceback" not in text
    assert str(tmp_path) not in text
    assert "Nope" not in text
    assert response.json()["detail"]["error"] == "Invalid page schema"


def test_traversal_fails_before_page_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_resolver(_name: str) -> Path:
        raise AssertionError("page resolver must not run for invalid page names")

    monkeypatch.setattr(shell, "_resolve_page_schema_path", fail_resolver)
    app = FastAPI()
    app.include_router(shell.router)

    response = TestClient(app).get("/api/pages/..secret")

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid page name"


@pytest.mark.asyncio
async def test_factory_page_validates_boots_serves_and_matches_renderer_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _page_from_plan(
        {
            "name": "Orders",
            "route": "/orders",
            "title": "Orders",
            "page_type_hint": "record_list",
            "layout": "full-width",
            "sections_hint": [
                {
                    "primitive": "PageHeader",
                    "section_id_hint": "orders-header",
                    "title_hint": "Orders",
                    "config_hint": json.dumps({"title": "Orders"}),
                },
                {
                    "primitive": "ResourceTable",
                    "section_id_hint": "orders-table",
                    "title_hint": "Orders",
                    "config_hint": json.dumps(
                        {
                            "columns": [{"key": "id", "label": "ID"}],
                            "api_endpoint": "/api/modules/orders/list_orders",
                        }
                    ),
                },
            ],
        },
        "orders",
    )
    _write_app(tmp_path, page, page_name="orders")
    module_dir = tmp_path / "modules" / "orders"
    module_dir.mkdir(parents=True)
    module_dir.joinpath("module.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "mozaiks.module.v1",
                "module": {
                    "id": "orders",
                    "name": "Orders",
                    "handler": "backend.handler:OrdersHandler",
                },
                "actions": [
                    {"id": "list_orders", "description": "List orders", "api_surface": "public_readonly"}
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    load_result = await AppLoader.load(str(tmp_path))
    assert "orders" in load_result.page_schemas

    monkeypatch.setenv("PLATFORM_PATH", str(tmp_path))
    app = FastAPI()
    app.state.page_schemas = {
        name: schema.model_dump(mode="json", exclude_none=True)
        for name, schema in load_result.page_schemas.items()
    }
    app.include_router(shell.router)
    response = TestClient(app).get("/api/pages/orders")

    assert response.status_code == 200
    served = response.json()
    assert served == app.state.page_schemas["orders"]

    renderer_schema_path = (
        Path(__file__).resolve().parents[1]
        / "chat-ui"
        / "src"
        / "ui"
        / "page-renderer"
        / "primitive_schemas.json"
    )
    renderer_contract = json.loads(renderer_schema_path.read_text(encoding="utf-8"))
    renderer_primitives = {key for key in renderer_contract if not key.startswith("_")}
    assert {section["primitive"] for section in served["sections"]} <= renderer_primitives

    schema_page_source = (
        Path(__file__).resolve().parents[1]
        / "chat-ui"
        / "src"
        / "ui"
        / "screens"
        / "SchemaPage.jsx"
    ).read_text(encoding="utf-8")
    assert "<PageRenderer schema={schema}" in schema_page_source
