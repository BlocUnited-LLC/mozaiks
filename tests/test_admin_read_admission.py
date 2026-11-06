"""Automatic admin reads require loaded action metadata, never a name heuristic."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from mozaiksai.core.auth import UserPrincipal, optional_user
from mozaiksai.core.ports.entitlement import EntitlementResult
from mozaiksai.core.runtime.app.module_loader import ActionDef, ModuleAdminPanel, ModuleLoader
from mozaiksai.core.runtime.composition.executor_registry import ExecutorRegistry
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
from mozaiksai.hosts.routers import admin_modules, modules

_BASE = "/api/admin/modules/inventory"
_READ = f"{_BASE}/list_rows/read"
_EMPTY_INPUT = {"type": "object", "additionalProperties": False}


def _action(name="list_rows", **overrides):
    return {
        "id": name,
        "description": name,
        "handler_method": name,
        "api_surface": "admin_internal",
        "permissions": ["inventory.admin"],
        "input_schema": _EMPTY_INPUT,
        **overrides,
    }


def _operator(*, roles=None, scopes=None, provenance="token_validated", app_id=None):
    return UserPrincipal(
        user_id="operator-1", email=None, name=None,
        roles=["platform_operator"] if roles is None else roles,
        scopes=["inventory.admin"] if scopes is None else scopes,
        raw_claims={}, auth_provenance=provenance, app_id=app_id,
    )


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    monkeypatch.delenv("MOZAIKS_ADMIN_DISPATCH_ROLES", raising=False)
    root = tmp_path / "app"
    module_dir = root / "modules" / "inventory"
    (module_dir / "backend").mkdir(parents=True)
    actions = [
        _action(read_only=True, output_schema={
            "type": "object", "properties": {
                "items": {"type": "array", "items": {
                    "type": "object", "properties": {"name": {"type": "string"}},
                }},
            },
        }),
        _action("run_checks"),
        _action("explicit_false", read_only=False),
        _action("internal_read", api_surface="internal", read_only=True),
        _action("public_read", api_surface="public_readonly", read_only=True, permissions=[]),
        _action("gated_read", read_only=True, entitlement_gate="inventory.inspect"),
        _action("parameterized", read_only=True, input_schema={
            "type": "object", "properties": {"scope": {"type": "string"}}, "required": ["scope"],
        }),
    ]
    (module_dir / "module.yaml").write_text(yaml.safe_dump({
        "schema_version": "mozaiks.module.v1",
        "module": {
            "id": "inventory", "display_name": "Inventory", "version": "1.0.0",
            "description": "Read admission fixture", "handler": "backend.handler:InventoryModule",
        },
        "permissions": [{"id": "inventory.admin", "description": "Inspect inventory"}],
        "actions": actions,
    }), encoding="utf-8")
    (module_dir / "backend" / "handler.py").write_text('''
class InventoryModule:
    def __init__(self):
        self.reads = []
        self.history = []

    async def list_rows(self, ctx):
        self.reads.append("list_rows")
        return {"items": [{"name": "example"}], "actor": ctx.user_id,
                "authority": ctx.dispatch_authority.kind,
                "permission_mode": ctx.dispatch_authority.permission_mode,
                "surface": ctx.dispatch_provenance.surface}

    async def run_checks(self, ctx):
        self.history.append({"status": "created"})
        return {"history_count": len(self.history)}

    async def explicit_false(self, ctx):
        return await self.run_checks(ctx)

    async def internal_read(self, ctx):
        return await self.list_rows(ctx)

    async def public_read(self, ctx):
        return await self.list_rows(ctx)

    async def gated_read(self, ctx):
        return await self.list_rows(ctx)

    async def parameterized(self, ctx, *, scope):
        return await self.list_rows(ctx)
'''.lstrip(), encoding="utf-8")
    # This is the same manifest/handler load and metadata registration path
    # platform startup uses. Only identity and the external entitlement port
    # are supplied by the test host; no read-eligibility state is fabricated.
    loaded = ModuleLoader(str(root)).load("inventory")

    class DeniedEntitlement:
        async def check(self, capability_id, **scope):
            return EntitlementResult(granted=False, reason="no_grant")

    executor = ModuleExecutor(entitlement_checker=DeniedEntitlement())
    executor.register_loaded_module(loaded)
    registry = ExecutorRegistry()
    registry.register(executor)
    app = FastAPI()
    app.include_router(admin_modules.router)
    app.include_router(modules.router)
    app.state.loaded_app_id = "test-app"
    app.state.executor_registry = registry
    app.state.module_action_surfaces = {loaded.name: loaded.action_api_surface_map}
    app.state.failed_module_names = []
    app.dependency_overrides[optional_user] = _operator
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(app=app, client=client, loaded=loaded, executor=executor)


def test_loaded_read_metadata_reaches_actual_admin_dispatch(boundary):
    assert boundary.loaded.definition.action_read_only_map == boundary.loaded.action_read_only_map
    assert boundary.executor.is_read_only_action("inventory", "list_rows")
    assert not boundary.executor.is_read_only_action("inventory", "run_checks")
    assert not boundary.executor.is_read_only_action("inventory", "missing")
    assert not boundary.executor.is_read_only_action("missing", "list_rows")
    response = boundary.client.post(_READ, json={})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "items": [{"name": "example"}], "actor": "operator-1",
        "authority": "authenticated_user", "permission_mode": "enforce",
        "surface": "http_admin_module_dispatch",
    }
    assert boundary.loaded.handler.reads == ["list_rows"]


@pytest.mark.parametrize("action", ["run_checks", "explicit_false", "internal_read", "public_read", "missing"])
def test_ineligible_and_wrong_surface_actions_never_execute(boundary, action):
    assert boundary.client.post(f"{_BASE}/{action}/read", json={}).status_code == 404
    assert boundary.loaded.handler.reads == []
    assert boundary.loaded.handler.history == []


def test_registration_without_read_metadata_denies_automatic_load(boundary):
    loaded = boundary.loaded
    boundary.executor.register(
        loaded.name, loaded.handler, action_method_map=loaded.action_method_map,
        action_permissions=loaded.action_permissions_map, action_schemas=loaded.action_schemas_map,
    )
    assert boundary.client.post(_READ, json={}).status_code == 404
    assert boundary.loaded.handler.reads == []


@pytest.mark.parametrize("metadata", [{"list_rows": "true"}, {"list_rows": 1}, {"missing": True}])
def test_direct_registration_rejects_invalid_read_metadata(metadata):
    with pytest.raises((ValidationError, ValueError)):
        ModuleExecutor().register(
            "inventory", object(), action_method_map={"list_rows": "list_rows"}, action_read_only=metadata,
        )


def test_direct_registration_rejects_read_events():
    with pytest.raises(ValueError, match="emitted domain events"):
        ModuleExecutor().register(
            "inventory", object(), action_method_map={"list_rows": "list_rows"},
            action_read_only={"list_rows": True}, action_emits={"list_rows": ["domain.inventory.changed"]},
        )


@pytest.mark.parametrize("value", ["true", "false", 1, 0, None, [], {}])
def test_read_flag_requires_literal_boolean(value):
    with pytest.raises(ValidationError):
        ActionDef.model_validate(_action(read_only=value))


def test_read_flag_defaults_false_and_rejects_domain_events():
    assert ActionDef.model_validate(_action()).read_only is False
    assert ActionDef.model_validate(_action(read_only=True)).read_only is True
    with pytest.raises(ValidationError, match="emitted domain events"):
        ActionDef.model_validate(_action(read_only=True, emits=["domain.inventory.changed"]))


@pytest.mark.parametrize(("principal", "status"), [
    (None, 401),
    (_operator(provenance="dev_override"), 401),
    (_operator(roles=[]), 403),
    (_operator(roles=[], scopes=["platform_operator", "inventory.admin"]), 403),
    (_operator(scopes=[]), 403),
    (_operator(app_id="another-app"), 403),
])
def test_read_preserves_authentication_permissions_and_app_identity(boundary, principal, status):
    boundary.app.dependency_overrides[optional_user] = lambda: principal
    assert boundary.client.post(_READ, json={}).status_code == status
    assert boundary.loaded.handler.reads == []


def test_read_preserves_entitlement_gate(boundary):
    response = boundary.client.post(f"{_BASE}/gated_read/read", json={})
    assert response.status_code == 402
    assert response.json()["detail"]["error_code"] == "ENTITLEMENT_REQUIRED"
    assert boundary.loaded.handler.reads == []


@pytest.mark.parametrize("body", [
    {"scope": "private"}, {"params": {}}, {"context": {}}, {"user_id": "other"}, [], "", 0,
])
def test_read_rejects_all_nonempty_or_nonobject_bodies(boundary, body):
    assert boundary.client.post(_READ, json=body).status_code == 400
    assert boundary.loaded.handler.reads == []


@pytest.mark.parametrize(("content", "content_type"), [
    ("null", "application/json"), ("{", "application/json"), ("", "application/json"),
    ("{}", "text/plain"), ("{}", "application/json-invalid"),
])
def test_read_requires_valid_empty_json(boundary, content, content_type):
    response = boundary.client.post(_READ, content=content, headers={"Content-Type": content_type})
    assert response.status_code == 400
    assert boundary.loaded.handler.reads == []


@pytest.mark.parametrize(("query", "status"), [
    ("scope=private", 400), ("app_id=test-app", 400), ("access_token=private", 401),
])
def test_read_rejects_query_inputs(boundary, query, status):
    assert boundary.client.post(f"{_READ}?{query}", json={}).status_code == status
    assert boundary.loaded.handler.reads == []


def test_parameterized_read_still_fails_canonical_input_validation(boundary):
    response = boundary.client.post(f"{_BASE}/parameterized/read", json={})
    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == "INVALID_PARAMS"
    assert boundary.loaded.handler.reads == []


def test_explicit_writer_and_public_get_behavior_are_preserved(boundary):
    assert boundary.client.get(_READ).status_code == 405
    assert boundary.client.get("/api/modules/inventory/list_rows").status_code == 404
    assert boundary.client.post("/api/modules/inventory/run_checks", json={}).status_code == 404
    assert boundary.client.post(f"{_BASE}/run_checks", json={}).json() == {"history_count": 1}
    assert boundary.client.get("/api/modules/inventory/public_read").status_code == 200


def test_read_denial_audits_the_actual_route_without_query_values(boundary, monkeypatch):
    recorded = []

    class Recorder:
        async def log_admin_access(self, **kwargs):
            recorded.append(kwargs)

    monkeypatch.setattr(admin_modules, "get_audit_logger", lambda: Recorder())
    path = f"{_BASE}/run_checks/read"
    assert boundary.client.post(path, json={}).status_code == 404
    assert recorded[-1]["route"] == path
    assert recorded[-1]["outcome"] == "denied_not_read_only"
    assert boundary.client.post(f"{_READ}?access_token=private-token", json={}).status_code == 401
    assert recorded[-1]["route"] == _READ
    assert "private-token" not in str(recorded)
    assert boundary.loaded.handler.history == []


def test_admin_panel_page_preserves_registry_identity():
    panel = ModuleAdminPanel.model_validate({
        "id": "inventory", "label": "Inventory", "page": " infra_DNS ",
        "renderer": "custom_component", "component": "InventoryPanel",
    })
    assert panel.page == "infra_DNS"


def _write_panel(boundary, *, endpoint=_READ, data_key="items", column="name", sections=None):
    directory = boundary.loaded.path / "contracts"
    directory.mkdir(exist_ok=True)
    (directory / "admin.yaml").write_text(yaml.safe_dump({
        "schema_version": "mozaiks.admin.v2",
        "panels": [{
            "id": "inventory.snapshot", "label": "Inventory", "page": "infra_DNS",
            "renderer": "schema", "layout": "full-width",
            "sections": sections if sections is not None else [{
                "id": "rows", "primitive": "DataTable", "config": {
                    "api_endpoint": endpoint, "data_key": data_key,
                    "columns": [{"key": column, "label": "Name"}],
                },
            }],
        }],
    }), encoding="utf-8")
    return boundary.loaded.path.parent.parent


def test_admin_config_serves_actual_loaded_read_panel(boundary):
    from mozaiksai.core.admin.router import _load_module_admin_panels

    panels = _load_module_admin_panels(_write_panel(boundary))
    assert len(panels) == 1
    assert panels[0]["page"] == "infra_DNS"
    assert panels[0]["sections"][0]["config"]["api_endpoint"] == _READ
    assert boundary.loaded.handler.reads == []


@pytest.mark.parametrize("overrides", [
    {"endpoint": f"{_BASE}/missing/read"},
    {"endpoint": f"{_BASE}/run_checks/read"},
    {"endpoint": f"{_BASE}/parameterized/read"},
    {"endpoint": f"{_BASE}/list_rows"},
    {"endpoint": "/api/admin/modules"},
    {"endpoint": "/api/admin/modules?scope=other"},
    {"endpoint": f"{_READ}/extra"},
    {"endpoint": f"{_READ}?scope=other"},
    {"endpoint": f" {_READ}"},
    {"data_key": "missing"},
    {"column": "missing"},
])
def test_admin_config_reports_invalid_reads_instead_of_empty_success(boundary, overrides, monkeypatch):
    from mozaiksai.core.admin import router as admin_config

    root = _write_panel(boundary, **overrides)
    monkeypatch.setattr(admin_config, "_resolve_admin_app_root", lambda: root)
    app = FastAPI()
    app.include_router(admin_config.router)
    app.dependency_overrides[admin_config._require_admin] = _operator
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/admin/config")
    assert response.status_code == 500
    assert response.json() == {"detail": "Invalid admin panel configuration"}
    assert boundary.loaded.handler.reads == []
    assert boundary.loaded.handler.history == []


def test_admin_config_preserves_explicit_writer_button(boundary):
    from mozaiksai.core.admin.router import _load_module_admin_panels

    root = _write_panel(boundary, sections=[{
        "id": "run", "primitive": "Button", "config": {
            "label": "Run checks", "action": {
                "label": "Run checks", "action_type": "submit", "href": f"{_BASE}/run_checks",
            },
        },
    }])
    assert len(_load_module_admin_panels(root)) == 1
    assert boundary.loaded.handler.history == []


@pytest.mark.parametrize("public_action", ["public_read", "list_rows"])
def test_admin_config_preserves_legacy_public_panel_and_explicit_action(boundary, monkeypatch, public_action):
    from mozaiksai.core.admin import router as admin_config

    # ModuleAdminPanel has always accepted section dictionaries. This existing
    # public binding must not acquire a whole-panel migration gate merely
    # because the framework adds an opt-in automatic admin read operation.
    sections = [{"id": "legacy", "primitive": "DataTable", "config": {
        "api_endpoint": f"/api/modules/inventory/{public_action}", "api_params": {"limit": 10},
        "data_key": "items", "columns": [{"key": "name", "label": "Name"}],
        "actions": [{"label": "Run checks", "action_type": "submit", "href": f"{_BASE}/run_checks"}],
    }}]
    root = _write_panel(boundary, sections=sections)
    monkeypatch.setattr(admin_config, "_resolve_admin_app_root", lambda: root)
    app = FastAPI()
    app.include_router(admin_config.router)
    app.dependency_overrides[admin_config._require_admin] = _operator
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/admin/config")
    assert response.status_code == 200
    assert response.json()["module_panels"][0]["sections"] == sections
    assert boundary.loaded.handler.reads == []
    assert boundary.loaded.handler.history == []
    # Serving compatibility grants no additional dispatch authority: a legacy
    # public URL targeting an admin action remains unavailable through GET.
    expected_status = 200 if public_action == "public_read" else 404
    assert boundary.client.get(f"/api/modules/inventory/{public_action}").status_code == expected_status


def test_nested_admin_read_opts_entire_panel_into_strict_validation(boundary, monkeypatch):
    from mozaiksai.core.admin import router as admin_config

    root = _write_panel(boundary, sections=[{
        "id": "group", "primitive": "Grid", "config": {"children": [{
            "id": "rows", "primitive": "DataTable", "config": {
                "api_endpoint": _READ, "data_key": "items", "api_params": {"scope": "forbidden"},
                "columns": [{"key": "name", "label": "Name"}],
            },
        }]},
    }])
    monkeypatch.setattr(admin_config, "_resolve_admin_app_root", lambda: root)
    app = FastAPI()
    app.include_router(admin_config.router)
    app.dependency_overrides[admin_config._require_admin] = _operator
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/admin/config")
    assert response.status_code == 500
    assert response.json() == {"detail": "Invalid admin panel configuration"}
    assert boundary.loaded.handler.reads == []
    assert boundary.loaded.handler.history == []
