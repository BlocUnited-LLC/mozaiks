from __future__ import annotations

import json
from pathlib import Path
from typing import Any, get_args

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mozaiksai.core.runtime.app.loader import AppLoader, AppLoadError
from mozaiksai.core.runtime.app.page_schema import (
    _TOP_LEVEL_CONFIG_MODELS,
    PAGE_SCHEMA_VERSION,
    AppPageChildSection,
    AppPageSection,
    PageSchemaValidationError,
    load_and_validate_page_schema,
    validate_page_schema,
)
from mozaiksai.hosts.routers import shell
from tests.page_plan_fixtures import _page_from_plan


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


@pytest.mark.parametrize("payload,valid", [
    (None, True),
    ({"record_id": "{selected_row.id}", "name": "{form.name}", "notes": "{form.notes}"}, True),
    ({"record_id": "{selected_row.id}"}, False),
])
def test_explicit_form_payload_must_include_editable_values(payload, valid):
    page = _valid_page(sections=[{
        "id": "edit", "primitive": "Form", "config": {
            "fields": [{"name": "name", "label": "Name", "type": "text"},
                       {"name": "notes", "label": "Notes", "type": "textarea"}],
            "submit_action": {"label": "Save", "action_type": "submit",
                              "href": "/api/modules/records/update", "payload": payload},
        },
    }])
    if valid:
        validate_page_schema(page)
    else:
        with pytest.raises(PageSchemaValidationError) as exc_info:
            validate_page_schema(page)
        assert any(d.code == "page_schema.incomplete_form_payload" for d in exc_info.value.diagnostics)


@pytest.mark.parametrize("primitive,config", [
    ("SummaryStrip", {"items": [{"label": "Total", "value_key": "total"}]}),
    ("Metric", {"label": "Total", "value_key": "total"}),
])
def test_live_metrics_preserve_and_validate_api_binding(primitive, config):
    page = _valid_page(sections=[{"id": "total", "primitive": primitive,
                                "config": {**config, "api_endpoint": "/api/modules/records/summary"}}])
    result = validate_page_schema(page)
    assert result.sections[0].config["api_endpoint"] == "/api/modules/records/summary"
    page["sections"][0]["config"]["api_endpoint"] = "/api/modules/records/summary?bad=true"
    with pytest.raises(PageSchemaValidationError):
        validate_page_schema(page)


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


def test_structured_action_maps_compile_and_modal_targets_resolve():
    page = _valid_page()
    page["sections"][0]["config"]["actions"] = [{
        "label": "Edit", "action_type": "event", "event_type": "ui.modal.open",
        "payload": [{"key": "modal_id", "value": "edit"}],
    }]
    page["sections"].append({"id": "edit", "primitive": "Modal", "config": {"title": "Edit"}})
    result = validate_page_schema(page).model_dump()
    assert result["sections"][0]["config"]["actions"][0]["payload"] == {"modal_id": "edit"}
    page["sections"][0]["config"]["actions"][0]["payload"][0]["key"] = "modalId"
    with pytest.raises(PageSchemaValidationError, match="Invalid page schema"):
        validate_page_schema(page)


def test_duplicate_action_mapping_keys_are_rejected():
    from mozaiksai.core.runtime.app.page_schema import AppPageAction
    with pytest.raises(ValueError, match="keys must be unique"):
        AppPageAction.model_validate({"label": "Save", "action_type": "submit", "href": "/api/modules/items/save",
                                      "payload": [{"key": "id", "value": "a"}, {"key": "id", "value": "b"}]})


def test_form_initial_values_are_a_closed_selected_record_binding():
    from mozaiksai.core.runtime.app.page_schema import AppFormConfig
    assert AppFormConfig(fields=[], initial_values_key="selected_row").initial_values_key == "selected_row"
    with pytest.raises(ValueError):
        AppFormConfig(fields=[], initial_values_key="eval(user_input)")


@pytest.mark.parametrize("href", ["", "modal-create", "https://example.invalid/private-token"])
def test_invalid_action_href_has_actionable_input_free_feedback(href: str) -> None:
    page = _valid_page()
    page["sections"][0]["config"]["actions"] = [
        {"label": "Create", "action_type": "event", "event_type": "ui.modal.open", "href": href}
    ]
    with pytest.raises(PageSchemaValidationError) as exc_info:
        validate_page_schema(page)
    diagnostic = exc_info.value.diagnostics[0]
    assert diagnostic.code == "page_schema.page_href"
    assert "use null" in diagnostic.message
    assert "private-token" not in diagnostic.message


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


def _pricing_catalog_config() -> dict[str, Any]:
    return {
        "plans": [
            {
                "plan_id": "pro",
                "label": "Pro",
                "description": "For growing teams.",
                "price_display": "$29",
                "cta_label": "Choose Pro",
                "highlights": ["Unlimited projects"],
                "managed_ai": {"display": "100K AI tokens"},
                "usage_limits": [
                    {"label": "AI tokens", "monthly_limit_display": "100K/month"}
                ],
                "pricing": {"display": "$29", "interval": "month"},
                "is_default": False,
            }
        ],
        "groups": [
            {
                "group_id": "platform",
                "label": "Platform",
                "description": "Core plans.",
                "kind": "subscription",
                "plan_ids": ["pro"],
                "capability_groups": ["platform"],
                "add_on_ids": ["priority_review"],
            }
        ],
        "add_ons": [
            {
                "id": "priority",
                "add_on_id": "priority_review",
                "label": "Priority review",
                "description": "Faster review.",
                "price_display": "$25",
                "price": {"display": "$25", "interval": "one_time"},
                "cta_label": "Add review",
                "highlights": ["One-time purchase"],
            }
        ],
    }


def test_pricing_catalog_inline_entries_are_closed_and_preserve_valid_shapes() -> None:
    page = _valid_page(
        sections=[
            {
                "id": "pricing",
                "primitive": "PricingCatalog",
                "config": _pricing_catalog_config(),
            }
        ]
    )

    validated = validate_page_schema(page)

    assert validated.sections[0].config == _pricing_catalog_config()
    for value in ([], None):
        config = {"plans": value, "groups": value, "add_ons": value}
        validated = validate_page_schema(
            _valid_page(
                sections=[
                    {"id": "pricing", "primitive": "PricingCatalog", "config": config}
                ]
            )
        )
        expected = config if value == [] else {}
        assert validated.sections[0].config == expected


@pytest.mark.parametrize(
    "malformed_config",
    [
        {"plans": [{"plan_id": "pro", "label": "Pro", "agent_id": "live-agent"}]},
        {
            "plans": [
                {
                    "plan_id": "pro",
                    "label": "Pro",
                    "managed_ai": {
                        "display": "100K",
                        "channel": {"websocket": {"connected": True}},
                    },
                }
            ]
        },
        {
            "groups": [
                {
                    "group_id": "platform",
                    "label": "Platform",
                    "checkpoint": {"path": "/tmp/runtime.chk"},
                }
            ]
        },
        {
            "add_ons": [
                {
                    "label": "Priority",
                    "price": {"display": "$25", "envelope": {"wal_id": "wal-1"}},
                }
            ]
        },
        {"plans": [{"plan_id": "pro", "label": "Pro", "price_display": {"usd": 29}}]},
    ],
)
def test_pricing_catalog_rejects_open_runtime_state(malformed_config: dict[str, Any]) -> None:
    with pytest.raises(PageSchemaValidationError):
        validate_page_schema(
            _valid_page(
                sections=[
                    {
                        "id": "pricing",
                        "primitive": "PricingCatalog",
                        "config": malformed_config,
                    }
                ]
            )
        )


def test_primitive_config_model_graph_has_no_terminal_any_escape_hatch() -> None:
    models = {AppPageSection, AppPageChildSection, *_TOP_LEVEL_CONFIG_MODELS.values()}
    visited: set[type] = set()
    any_fields: set[tuple[str, str]] = set()

    def contains_any(annotation: Any) -> bool:
        return annotation is Any or any(contains_any(arg) for arg in get_args(annotation))

    def visit(model: type) -> None:
        if model in visited or not hasattr(model, "model_fields"):
            return
        visited.add(model)
        for name, field in model.model_fields.items():
            if contains_any(field.annotation):
                any_fields.add((model.__name__, name))
            visit_annotation(field.annotation)

    def visit_annotation(annotation: Any) -> None:
        candidate = getattr(annotation, "__origin__", None) or annotation
        if isinstance(candidate, type) and hasattr(candidate, "model_fields"):
            visit(candidate)
        for argument in get_args(annotation):
            visit_annotation(argument)

    for model in models:
        visit(model)

    assert any_fields == {
        ("AppPageSection", "config"),
        ("AppPageChildSection", "config"),
    }


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


@pytest.mark.parametrize("folder", [False, True])
def test_shell_route_uses_exact_discovered_page_key(tmp_path, monkeypatch, folder):
    from mozaiksai.core.runtime.app.page_schema import load_app_page_schemas
    from mozaiksai.hosts.platform import _load_page_schema_routes

    _write_app(tmp_path, _valid_page(name="CustomerList", route="/customers"), page_name="customer_list")
    if folder:
        page_dir = tmp_path / "ui/pages/customer_list"
        page_dir.mkdir()
        (page_dir.parent / "customer_list.yaml").rename(page_dir / "page.yaml")
    pages = load_app_page_schemas(tmp_path)
    route = _load_page_schema_routes(tmp_path)[0]
    assert route["schema"] == "customer_list"
    assert route["schema"] in pages

    # A cache miss must not pass only because Windows ignores filename case.
    monkeypatch.setattr(shell, "_resolve_page_schema_path", lambda name: tmp_path / "missing")
    app = FastAPI()
    app.state.page_schemas = {name: page.model_dump(mode="json", exclude_none=True) for name, page in pages.items()}
    app.include_router(shell.router)
    response = TestClient(app).get(f"/api/pages/{route['schema']}")
    assert response.status_code == 200
    assert response.json()["name"] == "CustomerList"


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
