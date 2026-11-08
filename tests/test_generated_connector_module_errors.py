"""Connector configuration failures retain real HTTP role and app boundaries."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mozaiksai.core import secrets
from mozaiksai.core.auth import UserPrincipal, optional_user
from mozaiksai.core.data.persistence.connector_store import ConnectorStore
from mozaiksai.core.runtime.app.auth_contract import validate_app_auth_contract
from mozaiksai.core.runtime.composition import (
    ExecutorRegistry,
    ModuleExecutor,
    PlatformHookRegistry,
)
from mozaiksai.hosts.routers import modules
from tests.test_generated_cloud_action_credentials import _load_module
from tests.test_generated_cloud_action_credentials import generated_handlers as _generated_handlers

ROOT = Path(__file__).resolve().parents[1]
HOST_APP = "connector-proof-app"
CASES = [
    ("mozaiks_cloud", "cloud_deployment", "get_deployment_status", {"operation_id": "operation-test"}),
    ("mozaiks_cloud", "cloud_domain", "get_domain_status", {"binding_id": "binding-test"}),
    ("mozaikspay", "billing_portal", "get_subscription_status", {}),
    ("mozaikspay", "billing_portal", "get_usage_status", {}),
]
generated_handlers = _generated_handlers


@pytest.fixture(params=CASES, ids=[case[2] for case in CASES])
def facade(request, monkeypatch, generated_handlers):
    service, module_id, action, params = request.param
    template = ROOT / "factory_app/build_context" / service / "templates"
    if service == "mozaiks_cloud":
        handler = generated_handlers.handlers[module_id]
    else:
        backend = template / "modules/billing_portal/backend"
        for name, path in (("services", template / "services"),
                           ("services.integrations", template / "services/integrations"),
                           ("_test_billing_portal", backend)):
            package = ModuleType(name)
            package.__path__ = [str(path)]
            monkeypatch.setitem(sys.modules, name, package)
        _load_module(monkeypatch, "services.integrations.mozaikspay_client",
                     template / "services/integrations/mozaikspay_client.py")
        for name in ("schemas", "service", "base_handler", "handler"):
            module = _load_module(monkeypatch, f"_test_billing_portal.{name}", backend / f"{name}.py")
        handler = module.BillingPortalHandler()
    manifest = yaml.safe_load((template / "modules" / module_id / "module.yaml").read_text(encoding="utf-8"))
    declaration = next(item for item in manifest["actions"] if item["id"] == action)
    return SimpleNamespace(service=service, module=module_id, action=action, params=params,
                           handler=handler, declaration=declaration)


@pytest.fixture
def connector(monkeypatch):
    for name in ("MOZAIKS_CLOUD_API_BASE", "MOZAIKS_CLOUD_API_KEY", "MOZAIKS_CLOUD_APP_ID",
                 "MOZAIKSPAY_API_BASE", "MOZAIKSPAY_API_KEY", "MOZAIKSPAY_CLIENT_ID",
                 "MOZAIKSPAY_CLIENT_SECRET", "MOZAIKS_APP_URL"):
        monkeypatch.delenv(name, raising=False)
    records = {HOST_APP: {"public_config": {"api_base": "https://scoped.example.test"}}}
    lookup = AsyncMock(side_effect=lambda **scope: records.get(scope["scope_id"]))
    secret = AsyncMock(return_value={"success": True, "status": "found", "secret_value": "scoped-test-key"})
    monkeypatch.setattr(ConnectorStore, "__init__", lambda self: None)
    monkeypatch.setattr(ConnectorStore, "get", lookup)
    monkeypatch.setattr(secrets, "get_connector_vault_backend", lambda: SimpleNamespace(get_secret=secret))
    return SimpleNamespace(records=records, lookup=lookup, secret=secret)


@pytest.fixture
def provider_response():
    return {"status": 200}


@pytest.fixture
def provider_requests(monkeypatch, provider_response):
    requests = []
    client = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        return httpx.Response(provider_response["status"], json={"success": True, "found": True, "plan_id": "test-plan"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: client(
        *args, transport=httpx.MockTransport(respond), trust_env=False, **kwargs,
    ))
    return requests


@pytest.fixture
def http(facade, connector, provider_requests):
    document = yaml.safe_load((ROOT / "factory_app/app/config/auth.yaml").read_text(encoding="utf-8"))
    document["schema_version"] = "mozaiks.auth.v2"
    document["authorization"] = {"roles": [{
        "id": "connector_reader", "permissions": facade.declaration["permissions"],
    }]}
    assert document["authorization"]["roles"][0]["permissions"]
    app = FastAPI()
    app.state.auth_contract = validate_app_auth_contract(document)
    app.state.app_admin_emails = ()
    app.state.loaded_app_id = HOST_APP
    hooks = PlatformHookRegistry()
    executor = ModuleExecutor(platform_hooks=hooks, audit_logger=SimpleNamespace(log_module_action=AsyncMock()))
    executor.register(facade.module, facade.handler,
                      action_method_map={facade.action: facade.declaration["handler_method"]},
                      action_permissions={facade.action: facade.declaration["permissions"]},
                      action_schemas={facade.action: {
                          key: facade.declaration[f"{key}_schema"] for key in ("input", "output")
                          if f"{key}_schema" in facade.declaration
                      }})
    registry = ExecutorRegistry()
    registry.register(executor)
    app.state.executor_registry = registry
    app.state.module_action_surfaces = {facade.module: {facade.action: facade.declaration.get("api_surface")}}
    app.include_router(modules.router)
    principal = UserPrincipal(user_id="member-a", email=None, name=None, roles=["connector_reader"],
                              scopes=[], raw_claims={}, provider="jwt", auth_provenance="token_validated",
                              app_id=HOST_APP)
    app.dependency_overrides[optional_user] = lambda: principal
    app.dependency_overrides[modules.module_dispatch_environment] = lambda: modules.ModuleDispatchEnvironment(
        authentication_enabled=True, platform_hooks=hooks, record_invocation=lambda **kwargs: None,
        persistence_principal=lambda principal: None,
    )
    with TestClient(app) as client:
        def call():
            return client.post(f"/api/modules/{facade.module}/{facade.action}", json=facade.params,
                               headers={"Authorization": "Bearer synthetic-caller-token"})

        yield SimpleNamespace(call=call, principal=principal)


def test_roleless_caller_is_denied_before_connector_lookup(http, connector, provider_requests):
    http.principal.roles = []
    response = http.call()
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "PERMISSION_DENIED"
    connector.lookup.assert_not_awaited()
    connector.secret.assert_not_awaited()
    assert provider_requests == []


def test_configured_holder_uses_scoped_connector_or_forwarded_usage_bearer(
    http, facade, connector, provider_requests, monkeypatch,
):
    monkeypatch.setenv("MOZAIKS_APP_URL", "https://runtime.example.test")
    response = http.call()
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    connector.lookup.assert_awaited_once_with(scope="app", scope_id=HOST_APP, service=facade.service)
    connector.secret.assert_awaited_once_with(scope="app", scope_id=HOST_APP, service=facade.service)
    assert len(provider_requests) == 1
    request = provider_requests[0]
    if facade.action == "get_usage_status":
        assert request.url.host == "runtime.example.test"
        assert request.url.path == "/api/me/usage"
        assert request.headers["Authorization"] == "Bearer synthetic-caller-token"
    else:
        assert request.url.host == "scoped.example.test"
        assert request.headers["Authorization"] == "Bearer scoped-test-key"


def test_missing_connector_is_expected_unavailable_not_execution_error(http, connector, provider_requests):
    connector.records.clear()
    response = http.call()
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["error_code"] == "DEPENDENCY_UNAVAILABLE"
    assert response.json()["detail"]["error"] == "Required service is unavailable."
    connector.secret.assert_not_awaited()
    assert provider_requests == []


@pytest.mark.parametrize("failure", ["missing-secret", "vault-error", "lookup-error"])
def test_saved_connector_failure_never_borrows_environment(
    http, connector, provider_requests, monkeypatch, failure,
):
    for name in ("MOZAIKS_CLOUD_API_BASE", "MOZAIKSPAY_API_BASE", "MOZAIKS_APP_URL"):
        monkeypatch.setenv(name, "https://process.example.test")
    for name in ("MOZAIKS_CLOUD_API_KEY", "MOZAIKSPAY_API_KEY"):
        monkeypatch.setenv(name, "process-private-test-key")
    if failure == "missing-secret":
        connector.secret.return_value = {"success": True, "status": "not_found"}
    elif failure == "vault-error":
        connector.secret.side_effect = RuntimeError("private vault diagnostic")
    else:
        connector.lookup.side_effect = RuntimeError("private database diagnostic")
    response = http.call()
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["error_code"] == "DEPENDENCY_UNAVAILABLE"
    assert response.json()["detail"]["error"] == "Required service is unavailable."
    assert provider_requests == []


def test_foreign_token_app_is_denied_before_connector_lookup(http, connector, provider_requests):
    http.principal.app_id = "another-app"
    response = http.call()
    assert response.status_code == 403
    assert response.json()["detail"] == "App ID does not match the loaded app"
    connector.lookup.assert_not_awaited()
    connector.secret.assert_not_awaited()
    assert provider_requests == []


def test_provider_http_failure_is_not_configuration_or_caller_permission_denial(
    http, provider_requests, provider_response, monkeypatch,
):
    monkeypatch.setenv("MOZAIKS_APP_URL", "https://runtime.example.test")
    provider_response["status"] = 403
    response = http.call()
    assert response.status_code == 500
    assert response.json()["detail"]["error_code"] == "EXECUTION_ERROR"
    assert len(provider_requests) == 1
