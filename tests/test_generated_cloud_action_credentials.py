"""Cloud facade actions bind provider credentials to the host app context."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from mozaiksai.core import secrets
from mozaiksai.core.data.persistence.connector_store import ConnectorStore

TEMPLATES = Path(__file__).resolve().parents[1] / "factory_app/build_context/mozaiks_cloud/templates"
HOST_APP = "host-app"
TARGET_APP = "provider-target-app"
_ACTIONS = [
    ("cloud_deployment", "submit_deployment", {"idempotency_key": "deployment-test"}),
    ("cloud_deployment", "get_deployment_status", {"operation_id": "operation-test"}),
    ("cloud_deployment", "get_environment_endpoints", {}),
    ("cloud_deployment", "get_deployment_health", {"deployment_id": "deployment-test"}),
    ("cloud_deployment", "request_rollback", {
        "target_release_id": "release-test", "approval_reference": "approval-test",
        "idempotency_key": "rollback-test",
    }),
    ("cloud_domain", "connect_domain", {"domain": "app.example.test", "idempotency_key": "connect-test"}),
    ("cloud_domain", "get_domain_verification", {"binding_id": "binding-test", "tenant_id": "provider-tenant"}),
    ("cloud_domain", "get_dns_instructions", {"binding_id": "binding-test", "tenant_id": "provider-tenant"}),
    ("cloud_domain", "request_domain_activation", {
        "binding_id": "binding-test", "tenant_id": "provider-tenant", "idempotency_key": "activate-test",
    }),
    ("cloud_domain", "get_domain_status", {"binding_id": "binding-test", "tenant_id": "provider-tenant"}),
    ("cloud_domain", "disconnect_domain", {
        "binding_id": "binding-test", "tenant_id": "provider-tenant", "idempotency_key": "disconnect-test",
    }),
]
_JSON_APP_ACTIONS = {"submit_deployment", "connect_domain", "request_domain_activation", "disconnect_domain"}
_QUERY_APP_ACTIONS = {"get_domain_verification", "get_dns_instructions", "get_domain_status"}
_REQUIRES_TARGET_APP = _JSON_APP_ACTIONS | {"get_environment_endpoints"}


def _load_module(monkeypatch, name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def generated_handlers(monkeypatch):
    # Generated services use absolute app-local imports. Every package/module
    # substituted here is restored by monkeypatch, including an existing app's
    # services namespace; no generated module is imported at collection time.
    for name, path in (
        ("services", TEMPLATES / "services"),
        ("services.integrations", TEMPLATES / "services/integrations"),
        ("_test_cloud_deployment", TEMPLATES / "modules/cloud_deployment/backend"),
        ("_test_cloud_domain", TEMPLATES / "modules/cloud_domain/backend"),
    ):
        package = ModuleType(name)
        package.__path__ = [str(path)]
        monkeypatch.setitem(sys.modules, name, package)
    clients = {}
    for name in ("mozaiks_cloud_client", "mozaiks_cloud_deployment_client", "mozaiks_cloud_domain_client"):
        clients[name] = _load_module(
            monkeypatch, f"services.integrations.{name}", TEMPLATES / f"services/integrations/{name}.py",
        )
    handlers = {}
    for module_id, class_name in (
        ("cloud_deployment", "CloudDeploymentHandler"), ("cloud_domain", "CloudDomainHandler"),
    ):
        backend = TEMPLATES / "modules" / module_id / "backend"
        _load_module(monkeypatch, f"_test_{module_id}.service", backend / "service.py")
        handler = _load_module(monkeypatch, f"_test_{module_id}.handler", backend / "handler.py")
        handlers[module_id] = getattr(handler, class_name)()
    return SimpleNamespace(
        handlers=handlers, error=clients["mozaiks_cloud_client"].MozaiksCloudConfigurationError,
    )


@pytest.fixture(params=_ACTIONS, ids=[row[1] for row in _ACTIONS])
def action(request, generated_handlers):
    module_id, name, params = request.param
    return SimpleNamespace(
        name=name, run=getattr(generated_handlers.handlers[module_id], name),
        params={**params, "app_id": TARGET_APP}, error=generated_handlers.error,
    )


@pytest.fixture
def connector_backend(monkeypatch):
    records = {HOST_APP: {"public_config": {"api_base": "https://scoped.example.test"}}}
    lookup = AsyncMock(side_effect=lambda **scope: records.get(scope["scope_id"]))
    secret = AsyncMock(return_value={"success": True, "status": "found", "secret_value": "scoped-test-key"})
    monkeypatch.setattr(ConnectorStore, "__init__", lambda self: None)
    monkeypatch.setattr(ConnectorStore, "get", lookup)
    monkeypatch.setattr(secrets, "get_connector_vault_backend", lambda: SimpleNamespace(get_secret=secret))
    return SimpleNamespace(records=records, lookup=lookup, secret=secret)


@pytest.fixture
def captured_requests(monkeypatch):
    monkeypatch.setenv("MOZAIKS_CLOUD_API_BASE", "https://process.example.test")
    monkeypatch.setenv("MOZAIKS_CLOUD_API_KEY", "process-test-key")
    monkeypatch.setenv("MOZAIKS_CLOUD_APP_ID", "process-app")
    requests = []
    original_client = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"success": True})

    def client(*args, **kwargs):
        return original_client(*args, transport=httpx.MockTransport(respond), trust_env=False, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    return requests


async def test_action_credentials_follow_context_while_target_app_remains_provider_data(
    action, connector_backend, captured_requests,
):
    result = await action.run(SimpleNamespace(app_id=HOST_APP), **action.params)
    assert result == {"success": True}
    connector_backend.lookup.assert_awaited_once_with(
        scope="app", scope_id=HOST_APP, service="mozaiks_cloud",
    )
    connector_backend.secret.assert_awaited_once_with(
        scope="app", scope_id=HOST_APP, service="mozaiks_cloud",
    )
    assert len(captured_requests) == 1
    request = captured_requests[0]
    assert request.url.host == "scoped.example.test"
    assert request.headers["Authorization"] == "Bearer scoped-test-key"
    if action.name in _JSON_APP_ACTIONS:
        assert json.loads(request.content)["app_id"] == TARGET_APP
    elif action.name in _QUERY_APP_ACTIONS:
        assert request.url.params["app_id"] == TARGET_APP
        assert request.url.params["tenant_id"] == "provider-tenant"
    elif action.name == "get_environment_endpoints":
        assert request.url.path == f"/api/mozaiks-cloud/v1/apps/{TARGET_APP}/endpoints"


async def test_missing_request_app_id_never_selects_environment_credentials(
    action, connector_backend, captured_requests,
):
    params = {key: value for key, value in action.params.items() if key != "app_id"}
    if action.name in _REQUIRES_TARGET_APP:
        with pytest.raises(ValueError, match="app_id is required"):
            await action.run(SimpleNamespace(app_id=HOST_APP), **params)
        assert captured_requests == []
        connector_backend.lookup.assert_not_awaited()
        return

    # Five read actions and rollback require no optional target app parameter.
    assert await action.run(SimpleNamespace(app_id=HOST_APP), **params) == {"success": True}
    connector_backend.lookup.assert_awaited_once_with(
        scope="app", scope_id=HOST_APP, service="mozaiks_cloud",
    )
    connector_backend.secret.assert_awaited_once_with(
        scope="app", scope_id=HOST_APP, service="mozaiks_cloud",
    )
    assert len(captured_requests) == 1
    assert captured_requests[0].url.host == "scoped.example.test"
    assert captured_requests[0].headers["Authorization"] == "Bearer scoped-test-key"
    assert "app_id" not in captured_requests[0].url.params


@pytest.mark.parametrize("context", [{}, {"app_id": None}, {"app_id": " \t "}], ids=["missing", "none", "blank"])
async def test_missing_context_app_cannot_be_replaced_by_request_app_or_environment(
    action, connector_backend, captured_requests, context,
):
    with pytest.raises(ValueError, match="ctx.app_id is required"):
        await action.run(SimpleNamespace(**context), **action.params)
    connector_backend.lookup.assert_not_awaited()
    connector_backend.secret.assert_not_awaited()
    assert captured_requests == []


@pytest.mark.parametrize("action, omit_target_app", [
    pytest.param(row, False, id=f"{row[1]}-with-target-app") for row in _ACTIONS
] + [
    pytest.param(row, True, id=f"{row[1]}-without-target-app")
    for row in _ACTIONS if row[1] not in _REQUIRES_TARGET_APP
], indirect=["action"])
async def test_unreadable_context_connector_cannot_be_bypassed_with_target_app(
    action, connector_backend, captured_requests, omit_target_app,
):
    connector_backend.secret.return_value = {"success": True, "status": "not_found"}
    params = dict(action.params)
    if omit_target_app:
        params.pop("app_id")
    with pytest.raises(action.error):
        await action.run(SimpleNamespace(app_id=HOST_APP), **params)
    connector_backend.lookup.assert_awaited_once_with(
        scope="app", scope_id=HOST_APP, service="mozaiks_cloud",
    )
    connector_backend.secret.assert_awaited_once_with(
        scope="app", scope_id=HOST_APP, service="mozaiks_cloud",
    )
    assert captured_requests == []


async def test_absent_context_connector_allows_documented_environment_setup(
    action, connector_backend, captured_requests,
):
    connector_backend.records.clear()
    assert await action.run(SimpleNamespace(app_id=HOST_APP), **action.params) == {"success": True}
    connector_backend.lookup.assert_awaited_once_with(
        scope="app", scope_id=HOST_APP, service="mozaiks_cloud",
    )
    connector_backend.secret.assert_not_awaited()
    assert [(request.url.host, request.headers["Authorization"]) for request in captured_requests] == [
        ("process.example.test", "Bearer process-test-key"),
    ]
