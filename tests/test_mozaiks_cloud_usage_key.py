"""Credential isolation for the generated Mozaiks Cloud usage reporter."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from factory_app.build_context.mozaiks_cloud.templates.services.integrations import (
    mozaiks_cloud_client,
)
from factory_app.build_context.mozaiks_cloud.templates.services.integrations.mozaiks_cloud_deployment_client import (
    MozaiksCloudDeploymentClient,
)
from factory_app.build_context.mozaiks_cloud.templates.services.integrations.mozaiks_cloud_usage_client import (
    MozaiksCloudUsageClient,
)


def test_usage_key_is_used_only_for_usage_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOZAIKS_CLOUD_API_BASE", "https://cloud.example.test")
    monkeypatch.setenv("MOZAIKS_CLOUD_API_KEY", "general-key")
    monkeypatch.setenv("MOZAIKS_CLOUD_USAGE_API_KEY", "usage-key")
    requests: list[httpx.Request] = []
    original_async_client = httpx.AsyncClient

    def mock_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"success": True})

        return original_async_client(*args, transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(mozaiks_cloud_client.httpx, "AsyncClient", mock_client)

    async def send_requests() -> None:
        await MozaiksCloudUsageClient().post_usage_rollups([], idempotency_key="usage-1")
        await MozaiksCloudDeploymentClient(
            mozaiks_cloud_client.MozaiksCloudTransport()
        ).get_operation_status(operation_id="operation-1")

    asyncio.run(send_requests())

    assert [request.headers["Authorization"] for request in requests] == [
        "Bearer usage-key",
        "Bearer general-key",
    ]
    assert [request.url.path for request in requests] == [
        "/api/mozaiks-cloud/v1/usage/rollups",
        "/api/mozaiks-cloud/v1/operations/operation-1",
    ]


@pytest.mark.parametrize("usage_key", [None, " \t "])
def test_usage_client_falls_back_to_general_key(
    monkeypatch: pytest.MonkeyPatch, usage_key: str | None
) -> None:
    monkeypatch.setenv("MOZAIKS_CLOUD_API_BASE", "https://cloud.example.test")
    monkeypatch.setenv("MOZAIKS_CLOUD_API_KEY", "general-key")
    if usage_key is None:
        monkeypatch.delenv("MOZAIKS_CLOUD_USAGE_API_KEY", raising=False)
    else:
        monkeypatch.setenv("MOZAIKS_CLOUD_USAGE_API_KEY", usage_key)

    settings = asyncio.run(MozaiksCloudUsageClient()._transport.settings())
    assert settings.api_key == "general-key"


def test_usage_key_does_not_configure_general_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOZAIKS_CLOUD_API_BASE", "https://cloud.example.test")
    monkeypatch.setenv("MOZAIKS_CLOUD_USAGE_API_KEY", "usage-key")
    monkeypatch.delenv("MOZAIKS_CLOUD_API_KEY", raising=False)

    assert asyncio.run(MozaiksCloudUsageClient().is_configured())
    with pytest.raises(mozaiks_cloud_client.MozaiksCloudConfigurationError):
        asyncio.run(mozaiks_cloud_client.MozaiksCloudTransport().settings())


def test_usage_key_ignores_conflicting_connector_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOZAIKS_CLOUD_API_BASE", "https://usage.example.test")
    monkeypatch.setenv("MOZAIKS_CLOUD_USAGE_API_KEY", "usage-key")
    connector_calls: list[str | None] = []

    async def conflicting_connector(
        app_id: str | None,
    ) -> mozaiks_cloud_client.MozaiksCloudConnectorSettings:
        connector_calls.append(app_id)
        return mozaiks_cloud_client.MozaiksCloudConnectorSettings(
            api_base="https://other-operator.example.test",
            api_key="connector-key",
            source="connector",
        )

    monkeypatch.setattr(mozaiks_cloud_client, "_load_connector_settings", conflicting_connector)

    settings = asyncio.run(MozaiksCloudUsageClient()._transport.settings())

    assert settings.api_base == "https://usage.example.test"
    assert settings.api_key == "usage-key"
    assert connector_calls == []


def test_usage_key_without_explicit_base_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MOZAIKS_CLOUD_API_BASE", raising=False)
    monkeypatch.setenv("MOZAIKS_CLOUD_USAGE_API_KEY", "usage-key")
    connector_calls: list[str | None] = []

    async def conflicting_connector(
        app_id: str | None,
    ) -> mozaiks_cloud_client.MozaiksCloudConnectorSettings:
        connector_calls.append(app_id)
        return mozaiks_cloud_client.MozaiksCloudConnectorSettings(
            api_base="https://other-operator.example.test",
            api_key="connector-key",
            source="connector",
        )

    monkeypatch.setattr(mozaiks_cloud_client, "_load_connector_settings", conflicting_connector)

    client = MozaiksCloudUsageClient()
    assert asyncio.run(client.is_configured()) is False
    with pytest.raises(mozaiks_cloud_client.MozaiksCloudConfigurationError):
        asyncio.run(client.post_usage_rollups([], idempotency_key="usage-1"))
    assert connector_calls == []


def test_explicit_usage_transport_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOZAIKS_CLOUD_USAGE_API_KEY", "usage-key")
    transport = mozaiks_cloud_client.MozaiksCloudTransport(
        api_base="https://cloud.example.test", api_key="injected-key"
    )

    client = MozaiksCloudUsageClient(transport)

    assert client._transport is transport
    assert asyncio.run(client._transport.settings()).api_key == "injected-key"
