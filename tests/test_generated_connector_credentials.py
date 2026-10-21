"""Generated provider clients never substitute host credentials for app connectors."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import httpx
import pytest

from factory_app.build_context.mozaiks_cloud.templates.services.integrations import (
    mozaiks_cloud_client as cloud,
)
from factory_app.build_context.mozaiks_cloud.templates.services.integrations import (
    mozaiks_cloud_usage_client as usage,
)
from factory_app.build_context.mozaikspay.templates.services.integrations import (
    mozaikspay_client as pay,
)
from mozaiksai.core import secrets
from mozaiksai.core.data.persistence.connector_store import ConnectorStore


@pytest.fixture(params=["mozaiks_cloud", "mozaikspay"])
def provider(request):
    if request.param == "mozaiks_cloud":
        return SimpleNamespace(
            service=request.param, client=cloud.MozaiksCloudTransport,
            settings=cloud.MozaiksCloudConnectorSettings,
            error=cloud.MozaiksCloudConfigurationError,
        )
    return SimpleNamespace(
        service=request.param, client=pay.MozaiksPayClient,
        settings=pay.MozaiksPayConnectorSettings,
        error=pay.MozaiksPayConfigurationError,
    )


@pytest.fixture(autouse=True)
def synthetic_provider_environment(monkeypatch):
    for name, value in {
        "MOZAIKS_CLOUD_API_BASE": "https://process.example.test",
        "MOZAIKS_CLOUD_API_KEY": "process-cloud-test-key",
        "MOZAIKS_CLOUD_APP_ID": "process-cloud-app",
        "MOZAIKSPAY_API_BASE": "https://process.example.test",
        "MOZAIKSPAY_API_KEY": "process-pay-test-key",
        "MOZAIKSPAY_CLIENT_ID": "process-pay-client",
        "MOZAIKSPAY_CLIENT_SECRET": "process-pay-test-secret",
        "MOZAIKS_APP_URL": "https://runtime.example.test",
    }.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def connector_backend(monkeypatch):
    lookup = AsyncMock(return_value={"public_config": {"api_base": "https://scoped.example.test"}})
    secret = AsyncMock(return_value={"success": True, "status": "found", "secret_value": "scoped-test-key"})
    backend = Mock(return_value=SimpleNamespace(get_secret=secret))
    # Metadata lookup and secret retrieval stay at their real loader boundaries;
    # constructing this test store must not initialize a process Mongo client.
    monkeypatch.setattr(ConnectorStore, "__init__", lambda self: None)
    monkeypatch.setattr(ConnectorStore, "get", lookup)
    monkeypatch.setattr(secrets, "get_connector_vault_backend", backend)
    return SimpleNamespace(lookup=lookup, secret=secret, backend=backend)


@pytest.fixture
def captured_requests(monkeypatch):
    requests = []
    original_client = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"success": True})

    def client(*args, **kwargs):
        return original_client(*args, transport=httpx.MockTransport(respond), trust_env=False, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    return requests


async def provider_request(client):
    if isinstance(client, cloud.MozaiksCloudTransport):
        return await client.request("GET", "/health")
    return await client.readiness()


async def provider_settings(client):
    if isinstance(client, cloud.MozaiksCloudTransport):
        return await client.settings()
    return await client._settings()


@pytest.mark.parametrize("vault_result", [
    pytest.param({"success": True, "status": "not_found"}, id="not-found"),
    pytest.param({"success": False, "status": "error"}, id="error"),
    pytest.param({"success": False, "status": "error", "secret_value": "untrusted-test-key"}, id="error-with-key"),
    pytest.param({"success": False, "status": "found", "secret_value": "untrusted-test-key"}, id="unsuccessful-found"),
    pytest.param({"status": "found", "secret_value": "untrusted-test-key"}, id="missing-success"),
    pytest.param({"success": True, "status": "found"}, id="missing-key"),
    pytest.param({"success": True, "status": "found", "secret_value": " \t "}, id="blank-key"),
    pytest.param(None, id="missing-result"),
    pytest.param("invalid result", id="invalid-result"),
])
async def test_saved_connector_with_unreadable_secret_refuses_http(
    provider, connector_backend, captured_requests, vault_result,
):
    connector_backend.secret.return_value = vault_result
    with pytest.raises(provider.error):
        await provider_request(provider.client(app_id="app-a"))
    assert captured_requests == []
    connector_backend.secret.assert_awaited_once_with(
        scope=ConnectorStore.SCOPE_APP, scope_id="app-a", service=provider.service,
    )


@pytest.mark.parametrize("failure", ["metadata", "vault", "vault-factory"])
async def test_connector_lookup_failure_is_not_connector_absence(
    provider, connector_backend, captured_requests, failure,
):
    target = {"metadata": connector_backend.lookup, "vault": connector_backend.secret,
              "vault-factory": connector_backend.backend}[failure]
    target.side_effect = RuntimeError("synthetic backend unavailable")
    with pytest.raises(provider.error):
        await provider_request(provider.client(app_id="app-a"))
    assert captured_requests == []


@pytest.mark.parametrize("record", [
    pytest.param([], id="invalid-record"),
    pytest.param({}, id="missing-public-config"),
    pytest.param({"public_config": None}, id="null-public-config"),
    pytest.param({"public_config": []}, id="invalid-public-config"),
    pytest.param({"public_config": {}}, id="missing-base"),
    pytest.param({"public_config": {"api_base": " \t "}}, id="blank-base"),
])
async def test_existing_connector_cannot_borrow_process_endpoint(
    provider, connector_backend, captured_requests, record,
):
    connector_backend.lookup.return_value = record
    with pytest.raises(provider.error):
        await provider_request(provider.client(app_id="app-a"))
    assert captured_requests == []


async def test_found_connector_uses_scoped_key_and_endpoint(
    provider, connector_backend, captured_requests,
):
    client = provider.client(app_id="app-a")
    assert await provider_request(client) == {"success": True}
    settings = await provider_settings(client)
    assert settings.source == "connector"
    assert settings.api_key == "scoped-test-key"
    assert settings.api_base == "https://scoped.example.test"
    connector_backend.lookup.assert_awaited_once_with(
        scope=ConnectorStore.SCOPE_APP, scope_id="app-a", service=provider.service,
    )
    connector_backend.secret.assert_awaited_once_with(
        scope=ConnectorStore.SCOPE_APP, scope_id="app-a", service=provider.service,
    )
    assert len(captured_requests) == 1
    assert captured_requests[0].url.host == "scoped.example.test"
    assert captured_requests[0].headers["Authorization"] == "Bearer scoped-test-key"
    if provider.service == "mozaikspay":
        assert settings.client_id is None
        assert settings.client_secret is None
        assert "X-MozaiksPay-Client-Id" not in captured_requests[0].headers


async def test_absent_connector_allows_documented_environment_configuration(
    provider, connector_backend, captured_requests,
):
    connector_backend.lookup.return_value = None
    client = provider.client(app_id="app-a")
    await provider_request(client)
    assert (await provider_settings(client)).source == "env"
    connector_backend.lookup.assert_awaited_once_with(
        scope=ConnectorStore.SCOPE_APP, scope_id="app-a", service=provider.service,
    )
    connector_backend.secret.assert_not_awaited()
    assert len(captured_requests) == 1
    assert captured_requests[0].url.host == "process.example.test"
    key = "process-cloud-test-key" if provider.service == "mozaiks_cloud" else "process-pay-test-key"
    assert captured_requests[0].headers["Authorization"] == f"Bearer {key}"


async def test_two_apps_never_share_process_credentials_after_vault_denial(
    provider, connector_backend, captured_requests,
):
    async def lookup(*, scope, scope_id, service):
        return {"public_config": {"api_base": f"https://{scope_id}.example.test"}}

    async def secret(*, scope, scope_id, service):
        if scope_id == "app-a":
            return {"success": True, "status": "found", "secret_value": "app-a-test-key"}
        return {"success": True, "status": "not_found"}

    connector_backend.lookup.side_effect = lookup
    connector_backend.secret.side_effect = secret
    await provider_request(provider.client(app_id="app-a"))
    with pytest.raises(provider.error):
        await provider_request(provider.client(app_id="app-b"))
    assert [(request.url.host, request.headers["Authorization"]) for request in captured_requests] == [
        ("app-a.example.test", "Bearer app-a-test-key"),
    ]
    expected = [call(scope="app", scope_id=app_id, service=provider.service) for app_id in ("app-a", "app-b")]
    assert connector_backend.lookup.await_args_list == expected
    assert connector_backend.secret.await_args_list == expected


@pytest.mark.parametrize("missing", ["api_base", "api_key"])
async def test_incomplete_custom_connector_loader_does_not_mix_environment(
    provider, connector_backend, captured_requests, missing,
):
    fields = {"api_base": "https://custom.example.test", "api_key": "custom-test-key", "source": "connector"}
    fields[missing] = None
    loader = AsyncMock(return_value=provider.settings(**fields))
    with pytest.raises(provider.error):
        await provider_request(provider.client(app_id="app-a", connector_settings_loader=loader))
    assert captured_requests == []
    loader.assert_awaited_once_with("app-a")
    connector_backend.lookup.assert_not_awaited()


async def test_pay_connector_client_pair_does_not_use_process_api_key(connector_backend, captured_requests):
    connector_backend.lookup.return_value = {
        "public_config": {"api_base": "https://scoped.example.test", "client_id": "app-client"},
    }
    client = pay.MozaiksPayClient(app_id="app-a")
    await client.readiness()
    settings = await client._settings()
    assert settings.api_key is None
    assert settings.client_id == "app-client"
    assert settings.client_secret == "scoped-test-key"
    assert len(captured_requests) == 1
    assert captured_requests[0].headers["Authorization"] == "Bearer scoped-test-key"
    assert captured_requests[0].headers["X-MozaiksPay-Client-Id"] == "app-client"


async def test_environment_configuration_without_app_lookup_remains_supported(
    provider, connector_backend, captured_requests,
):
    await provider_request(provider.client())
    connector_backend.lookup.assert_not_awaited()
    connector_backend.secret.assert_not_awaited()
    assert len(captured_requests) == 1
    assert captured_requests[0].url.host == "process.example.test"


async def test_explicit_endpoint_and_key_remain_supported(provider, connector_backend, captured_requests):
    client = provider.client(api_base="https://explicit.example.test", api_key="explicit-test-key")
    await provider_request(client)
    assert (await provider_settings(client)).source == "explicit"
    connector_backend.lookup.assert_not_awaited()
    connector_backend.secret.assert_not_awaited()
    assert [(request.url.host, request.headers["Authorization"]) for request in captured_requests] == [
        ("explicit.example.test", "Bearer explicit-test-key"),
    ]


async def test_explicit_overrides_cannot_rescue_unreadable_saved_connector(
    provider, connector_backend, captured_requests,
):
    connector_backend.secret.return_value = {"success": True, "status": "not_found"}
    with pytest.raises(provider.error):
        await provider_request(provider.client(
            app_id="app-a", api_base="https://explicit.example.test", api_key="explicit-test-key",
        ))
    assert captured_requests == []


@pytest.fixture(params=["status", "report_once"])
def usage_caller(request, monkeypatch):
    from factory_app.build_context.mozaiks_cloud.templates.modules.cloud_usage_reporter.backend.handler import (
        CloudUsageReporterHandler,
    )
    from factory_app.build_context.mozaiks_cloud.templates.modules.cloud_usage_reporter.backend.reporter import (
        UsageReporterService,
    )
    from mozaiksai.core.metrics import app_metrics

    monkeypatch.delenv("MOZAIKS_CLOUD_USAGE_API_KEY", raising=False)
    monkeypatch.setenv("MOZAIKS_CLOUD_USAGE_REPORTING", "1")
    monkeypatch.setenv("MOZAIKS_CLOUD_APP_ID", "app-a")
    monkeypatch.setenv("MOZAIKS_APP_ID", "app-a")
    monkeypatch.setitem(sys.modules, "app.services.integrations.mozaiks_cloud_usage_client", usage)
    metrics = Mock(return_value=SimpleNamespace(usage_rollup=AsyncMock(return_value=[{"page_views": 2}])))
    monkeypatch.setattr(app_metrics, "AppMetrics", metrics)

    async def invoke(*, app_id="app-a"):
        if not str(app_id or "").strip():
            monkeypatch.delenv("MOZAIKS_CLOUD_APP_ID", raising=False)
            monkeypatch.setenv("MOZAIKS_APP_ID", "")
        if request.param == "status":
            return await CloudUsageReporterHandler().get_reporter_status(SimpleNamespace(app_id=app_id))
        monkeypatch.setenv("MOZAIKS_APP_ID", app_id or "")
        return await UsageReporterService().report_once()

    return SimpleNamespace(invoke=invoke, kind=request.param, metrics=metrics)


@pytest.mark.parametrize("connector_state", ["found", "missing-secret", "absent"])
async def test_usage_callers_keep_app_identity(
    usage_caller, connector_backend, captured_requests, connector_state,
):
    if connector_state == "missing-secret":
        connector_backend.secret.return_value = {"success": True, "status": "not_found"}
    elif connector_state == "absent":
        connector_backend.lookup.return_value = None
    result = await usage_caller.invoke()
    connector_backend.lookup.assert_awaited_once_with(scope="app", scope_id="app-a", service="mozaiks_cloud")
    if connector_state == "absent":
        connector_backend.secret.assert_not_awaited()
    else:
        connector_backend.secret.assert_awaited_once_with(scope="app", scope_id="app-a", service="mozaiks_cloud")
    if connector_state == "missing-secret":
        assert captured_requests == []
        usage_caller.metrics.assert_not_called()
        assert result == ({"sent": 0, "reason": "unconfigured"} if usage_caller.kind == "report_once"
                          else {"enabled": True, "configured": False, "reporting": False})
    elif usage_caller.kind == "report_once":
        assert result["sent"] == 1
        assert usage_caller.metrics.call_args.args[0].app_id == "app-a"
        host = "process.example.test" if connector_state == "absent" else "scoped.example.test"
        key = "process-cloud-test-key" if connector_state == "absent" else "scoped-test-key"
        assert [(r.url.host, r.headers["Authorization"]) for r in captured_requests] == [(host, f"Bearer {key}")]
    else:
        assert result == {"enabled": True, "configured": True, "reporting": True}
        assert captured_requests == []


@pytest.mark.parametrize("app_id", [None, "", " \t "])
async def test_usage_callers_without_identity_refuse_environment_fallback(
    usage_caller, connector_backend, captured_requests, app_id,
):
    result = await usage_caller.invoke(app_id=app_id)
    if usage_caller.kind == "report_once":
        assert result == {"sent": 0, "reason": "app_identity_missing"}
    else:
        assert result == {
            "enabled": True,
            "configured": False,
            "reporting": False,
            "reason": "app_identity_missing",
        }
    connector_backend.lookup.assert_not_awaited()
    connector_backend.secret.assert_not_awaited()
    usage_caller.metrics.assert_not_called()
    assert captured_requests == []


async def test_dedicated_usage_configuration_stays_explicit_with_app_context(
    usage_caller, connector_backend, captured_requests, monkeypatch,
):
    monkeypatch.setenv("MOZAIKS_CLOUD_USAGE_API_KEY", "usage-test-key")
    connector_backend.secret.return_value = {"success": True, "status": "not_found"}
    result = await usage_caller.invoke()
    connector_backend.lookup.assert_not_awaited()
    connector_backend.secret.assert_not_awaited()
    if usage_caller.kind == "report_once":
        assert result["sent"] == 1
        assert [(r.url.host, r.headers["Authorization"]) for r in captured_requests] == [
            ("process.example.test", "Bearer usage-test-key"),
        ]
    else:
        assert result["configured"] is True
        assert captured_requests == []
