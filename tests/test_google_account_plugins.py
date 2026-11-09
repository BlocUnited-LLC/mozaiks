"""The Google Calendar and Gmail plugin packs, end to end through the real module route.

The pack templates are copied into an app, loaded by the real ModuleLoader,
registered with the real ModuleExecutor and called over HTTP through the real
``/api/modules`` route. A user connects their own Google account through the
real connected-accounts service; only Google, the database, the vault and the
auth adapter's token validation are faked.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mozaiksai.core.auth.adapters.base import AuthError, UserClaims
from mozaiksai.core.connected_accounts import oauth, runtime
from mozaiksai.core.connected_accounts import service as service_module
from mozaiksai.core.connected_accounts.definitions import load_account_connections
from mozaiksai.core.connected_accounts.providers import GOOGLE_EMULATOR_ENV
from mozaiksai.core.connected_accounts.service import (
    ConnectedAccountsService,
    configure_connected_accounts,
)
from mozaiksai.core.runtime.app.module_loader import ModuleLoader
from mozaiksai.core.runtime.composition.module_authority import ModuleDispatchAuthority
from mozaiksai.core.runtime.composition.module_context import ModuleContext
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
from mozaiksai.hosts.routers import modules as module_router
from tests.connected_accounts_fakes import (
    CALENDAR_SCOPE,
    GMAIL_SEND_SCOPE,
    FakeDatabase,
    FakeGoogle,
    FakeVault,
)

ROOT = Path(__file__).resolve().parents[1]
PACKS = ROOT / "factory_app" / "build_context"
APP_ID = "plugin-app"
REDIRECT = "https://testserver/api/connected-accounts/callback"


class _Adapter:
    name = "fixture"

    async def validate_token(self, token: str) -> UserClaims:
        if not token.startswith("bearer-"):
            raise AuthError("Invalid token", status_code=401)
        user_id = token.removeprefix("bearer-")
        return UserClaims(user_id=user_id, email=f"{user_id}@example.test", provider="fixture", app_id=APP_ID)

    def is_enabled(self) -> bool:
        return True


class _Audit:
    async def log(self, record: Any) -> None:
        return None


def _install_packs(app_root: Path) -> None:
    (app_root / "modules").mkdir(parents=True)
    (app_root / "app.json").write_text('{"appName": "Plugin App"}', encoding="utf-8")
    for pack, module_id in (("google_calendar", "google_calendar"), ("gmail", "gmail")):
        shutil.copytree(PACKS / pack / "templates" / "modules" / module_id, app_root / "modules" / module_id)


@pytest.fixture
def google(monkeypatch: pytest.MonkeyPatch) -> FakeGoogle:
    fake = FakeGoogle()
    monkeypatch.setattr(oauth, "http_client", fake.client)
    monkeypatch.setattr(runtime, "api_http_client", fake.client)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv(GOOGLE_EMULATOR_ENV, "https://google.test")
    monkeypatch.setenv("GOOGLE_ACCOUNTS_CLIENT_ID", "client-123")
    monkeypatch.setenv("GOOGLE_ACCOUNTS_CLIENT_SECRET", "client-secret-xyz")
    monkeypatch.setattr(service_module, "get_audit_logger", lambda: _Audit())
    return fake


@pytest.fixture
async def host(tmp_path: Path, google: FakeGoogle, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    app_root = tmp_path / "app"
    _install_packs(app_root)
    loaded, failed = await ModuleLoader(base_path=str(app_root)).load_all(["google_calendar", "gmail"])
    assert failed == []
    service = ConnectedAccountsService(
        app_id=APP_ID,
        app_root=app_root,
        modules=[(module.name, module.path) for module in loaded],
        store=FakeDatabase().store(),
        vault=FakeVault(),
    )
    configure_connected_accounts(service)

    hooks = PlatformHookRegistry()
    monkeypatch.setattr(module_router, "get_platform_hooks", lambda: hooks)
    monkeypatch.setattr("mozaiksai.core.runtime.composition.module_executor.get_platform_hooks", lambda: hooks)
    monkeypatch.setattr(module_router, "record_action_invocation", lambda **kwargs: None)
    monkeypatch.setattr(ModuleExecutor, "_emit_dispatch_audit", AsyncMock())
    executor = ModuleExecutor()
    for module in loaded:
        executor.register_loaded_module(module)
    app = FastAPI()
    app.state.loaded_app_id = APP_ID
    app.state.module_action_surfaces = {module.name: module.action_api_surface_map for module in loaded}
    app.state.executor_registry = SimpleNamespace(module_executor=executor)
    app.include_router(module_router.router)
    with patch("mozaiksai.core.auth.dependencies.is_auth_enabled", return_value=True), \
         patch("mozaiksai.hosts.routers.modules.is_auth_enabled", return_value=True), \
         patch("mozaiksai.core.auth.dependencies.get_auth_adapter", return_value=_Adapter()):
        yield SimpleNamespace(app=app, service=service, google=google, modules={m.name: m for m in loaded})
    configure_connected_accounts(None)


async def _connect(host: SimpleNamespace, user: str, connection_id: str) -> None:
    started = await host.service.start(
        user_id=user, connection_id=connection_id, redirect_uri=REDIRECT, binding_cookie="cookie",
    )
    code, state = host.google.approve(started.authorize_url, user=user)
    result = await host.service.complete(state=state, code=code, error="", read_cookie=lambda name: [started.binding])
    assert result.status == "connected", result.message


def _client(host: SimpleNamespace, user: str | None) -> TestClient:
    return TestClient(host.app, headers={"Authorization": f"Bearer bearer-{user}"} if user else {})


def test_the_packs_declare_one_connection_each_with_the_narrowest_scopes(tmp_path: Path) -> None:
    _install_packs(tmp_path / "app")
    connections = {connection.id: connection for connection in load_account_connections(tmp_path / "app")}
    assert set(connections) == {"google_calendar", "gmail_send"}
    assert connections["google_calendar"].scopes == (CALENDAR_SCOPE,)
    # Send-only: no Gmail read, modify or metadata scope (restricted by Google).
    assert connections["gmail_send"].scopes == (GMAIL_SEND_SCOPE,)


async def test_a_user_who_has_not_connected_is_told_where_to_connect(host: SimpleNamespace) -> None:
    response = _client(host, "alice").get("/api/modules/google_calendar/list_upcoming_events")
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["error_code"] == "ACCOUNT_CONNECTION_REQUIRED"
    assert detail["extra_data"] == {
        "error_code": "ACCOUNT_CONNECTION_REQUIRED",
        "connection_id": "google_calendar",
        "provider": "google",
        "label": "Google Calendar",
        "reason": "not_connected",
        "recovery_message": "Connect your Google Calendar account to use this.",
        "connect_route": "/me?tab=connected-accounts&connect=google_calendar",
    }
    assert host.google.api_requests == []


async def test_calendar_reads_and_writes_the_users_own_calendar(host: SimpleNamespace) -> None:
    await _connect(host, "alice", "google_calendar")
    alice = _client(host, "alice")
    listed = alice.get("/api/modules/google_calendar/list_upcoming_events", params={"max_results": 5})
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["total"] == 1
    assert body["events"][0]["summary"] == "alice's standup"
    assert "attendees" not in body["events"][0]
    request = host.google.api_requests[-1]
    assert request.url.params["maxResults"] == "5" and request.url.params["singleEvents"] == "true"

    created = alice.post("/api/modules/google_calendar/create_event", json={
        "summary": "Dentist", "start": "2026-11-20T15:00:00", "end": "2026-11-20T16:00:00", "time_zone": "Europe/Paris",
    })
    assert created.status_code == 200, created.text
    assert created.json()["event"]["summary"] == "Dentist"
    sent = host.google.api_requests[-1]
    assert sent.method == "POST"
    assert b'"timeZone": "Europe/Paris"' in sent.content or b'"timeZone":"Europe/Paris"' in sent.content

    # Bob has not connected: Alice's grant is never used for him.
    assert _client(host, "bob").get("/api/modules/google_calendar/list_upcoming_events").status_code == 403


@pytest.mark.parametrize(
    "event",
    [
        {"summary": "x", "start": "2026-11-20T16:00:00+00:00", "end": "2026-11-20T15:00:00+00:00"},
        {"summary": "x", "start": "tomorrow", "end": "2026-11-20T15:00:00+00:00"},
        {"summary": "x", "start": "2026-11-20T15:00:00", "end": "2026-11-20T16:00:00"},
        {"summary": "x", "start": "2026-11-20T15:00:00", "end": "2026-11-20T16:00:00", "time_zone": "Mars/Base"},
        {"summary": "   ", "start": "2026-11-20T15:00:00+00:00", "end": "2026-11-20T16:00:00+00:00"},
        {"summary": "x" * 201, "start": "2026-11-20T15:00:00+00:00", "end": "2026-11-20T16:00:00+00:00"},
        {"summary": "x", "start": "2026-11-20T15:00:00+00:00", "end": "2026-11-20T16:00:00+00:00", "location": "x" * 301},
    ],
)
async def test_calendar_refuses_invalid_events_before_calling_google(host: SimpleNamespace, event: dict[str, str]) -> None:
    await _connect(host, "alice", "google_calendar")
    before = len(host.google.api_requests)
    response = _client(host, "alice").post("/api/modules/google_calendar/create_event", json=event)
    assert response.status_code == 400, response.text
    assert len(host.google.api_requests) == before


@pytest.mark.parametrize("params", [{"max_results": 0}, {"max_results": 51}, {"days_ahead": 91}, {"days_ahead": 0}])
async def test_calendar_listing_is_bounded(host: SimpleNamespace, params: dict[str, int]) -> None:
    await _connect(host, "alice", "google_calendar")
    response = _client(host, "alice").get("/api/modules/google_calendar/list_upcoming_events", params=params)
    assert response.status_code == 400, response.text
    assert host.google.api_requests == []


async def test_a_refused_access_token_is_refreshed_once(host: SimpleNamespace) -> None:
    await _connect(host, "alice", "google_calendar")
    alice = _client(host, "alice")
    assert alice.get("/api/modules/google_calendar/list_upcoming_events").status_code == 200
    host.google.reject_access_tokens.update(host.google.access_tokens)
    assert alice.get("/api/modules/google_calendar/list_upcoming_events").status_code == 200
    # Revoked at Google: the user is asked to reconnect instead of seeing a failure.
    for grant in host.google.grants.values():
        grant.revoked = True
    response = alice.get("/api/modules/google_calendar/list_upcoming_events")
    assert response.status_code == 403
    assert response.json()["detail"]["extra_data"]["reason"] == "reconnect_required"


async def test_gmail_sends_from_the_users_own_address(host: SimpleNamespace) -> None:
    await _connect(host, "alice", "gmail_send")
    response = _client(host, "alice").post("/api/modules/gmail/send_email", json={
        "to": ["friend@example.test"], "cc": ["Friend@Example.test", "boss@example.test"],
        "subject": "Lunch", "body": "Tomorrow at noon?",
    })
    assert response.status_code == 200, response.text
    assert response.json() == {"success": True, "message_id": "msg-1"}
    [message] = host.google.sent_messages
    assert "To: friend@example.test" in message
    assert "Cc: boss@example.test" in message  # the duplicate of a To address is dropped
    assert "Subject: Lunch" in message and "Tomorrow at noon?" in message


@pytest.mark.parametrize(
    "payload",
    [
        {"to": ["not-an-address"], "subject": "Hi", "body": "x"},
        {"to": ["a@example.test, b@example.test"], "subject": "Hi", "body": "x"},
        {"to": ["a@example.test"], "subject": "Hi\nBcc: everyone@example.test", "body": "x"},
        {"to": [f"user{index}@example.test" for index in range(15)], "cc": [f"cc{index}@example.test" for index in range(6)], "subject": "Hi", "body": "x"},
        {"to": [f"user{index}@example.test" for index in range(21)], "subject": "Hi", "body": "x"},
        {"to": ["a@example.test"], "subject": "x" * 251, "body": "x"},
        {"to": ["a@example.test"], "subject": "Hi", "body": "x" * 50001},
        {"to": ["a@example.test"], "subject": "Hi", "body": "   "},
        {"to": [], "subject": "Hi", "body": "x"},
    ],
)
async def test_gmail_refuses_header_injection_and_bulk_mail(host: SimpleNamespace, payload: dict[str, Any]) -> None:
    await _connect(host, "alice", "gmail_send")
    response = _client(host, "alice").post("/api/modules/gmail/send_email", json=payload)
    assert response.status_code == 400, response.text
    assert host.google.sent_messages == []


async def test_server_owned_and_workflow_dispatches_cannot_send_mail(host: SimpleNamespace) -> None:
    await _connect(host, "alice", "gmail_send")
    gmail_handler = host.modules["gmail"].handler

    def context(kind: str, permission_mode: str = "enforce") -> ModuleContext:
        return ModuleContext(
            app_id=APP_ID, user_id="alice",
            dispatch_authority=ModuleDispatchAuthority(
                kind=kind, permission_mode=permission_mode, reason="test", actor_id="alice",
            ),
        )

    params = {"to": ["friend@example.test"], "subject": "Hi", "body": "x"}
    with pytest.raises(PermissionError):
        await gmail_handler.send_email(context("workflow"), **params)
    with pytest.raises(PermissionError):
        await gmail_handler.send_email(context("event_reaction", "trusted_bypass"), **params)
    assert host.google.sent_messages == []
    assert (await gmail_handler.send_email(context("authenticated_user"), **params))["success"] is True


@pytest.mark.parametrize("pack_id", ["google_calendar", "gmail"])
async def test_a_generated_app_with_a_google_plugin_asks_for_its_own_google_client(
    pack_id: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting either pack declares the app's own Google OAuth client as runtime setup, deterministically."""
    import yaml

    from factory_app.workflows.AppGenerator.tools import (
        save_integration_manifest as manifest_module,
    )
    from factory_app.workflows.AppGenerator.tools.deployment_contract import (
        generate_deployment_artifacts,
    )
    from factory_app.workflows.AppGenerator.tools.generate_and_download import (
        _deployment_env_for_capability_packs,
    )
    from factory_app.workflows.AppGenerator.tools.integration_readiness import (
        check_integration_readiness,
    )
    from mozaiksai.core.session.build_context import normalize_pack_descriptor
    from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
    from mozaiksai.core.workflow.generator_support import connector_request
    from tests.factory_context import factory_context

    pack_dir = PACKS / pack_id
    descriptor = normalize_pack_descriptor(
        yaml.safe_load((pack_dir / "context.yaml").read_text(encoding="utf-8")), context_root=pack_dir,
    )
    context = ContextVariablesBridge(factory_context({
        "app_build_plan": {"capability_packs": [descriptor]},
        "capability_packs": [descriptor],
    }))
    monkeypatch.setattr(connector_request, "get_connector_inventory", AsyncMock(return_value={
        "required_services": [], "ready_services": [], "missing_required_services": [], "connectors": [],
    }))
    request = AsyncMock()
    monkeypatch.setattr(connector_request, "request_connector_bundle", request)
    declarations = AsyncMock()
    declarations.declare_app_integration_needs.return_value = {"saved": 1}
    monkeypatch.setattr(manifest_module, "WorkspaceIntegrationsService", lambda: declarations)

    readiness = await check_integration_readiness(context_variables=context)
    await manifest_module.save_integration_manifest(context_variables=context)

    # Runtime setup: generation is never blocked, and the app lists what to configure.
    assert readiness["status"] == "ready"
    request.assert_not_awaited()
    [need] = declarations.declare_app_integration_needs.await_args.kwargs["needs"]
    assert (need["service"], need["required_at"], need["connector_status"]) == (
        "google_account_connections", "runtime", "not_configured",
    )
    assert [field["name"] for field in need["required_fields"]] == ["client_id", "client_secret"]

    env = _deployment_env_for_capability_packs(context.snapshot()["capability_packs"])
    deployment = generate_deployment_artifacts(
        app_id="generated-app", deployment_profile="generic_container", include_dockerfiles=True,
        include_workflow=False, include_compose=False,
        extra_required_variables=env["required"], extra_optional_variables=env["optional"],
        extra_secret_variables=env["secret"], extra_public_variables=env["public"],
    )
    manifest = deployment["deployment_manifest"]
    assert "GOOGLE_ACCOUNTS_CLIENT_SECRET" in manifest["secret_env"]
    assert "GOOGLE_ACCOUNTS_CLIENT_SECRET" not in manifest["public_env"]
    # Optional, so an app without the client still deploys; its users see the plugin as not available.
    assert "GOOGLE_ACCOUNTS_CLIENT_ID" not in manifest["required_env"]
    example = deployment["artifacts"][".env.example"]
    for name in ("GOOGLE_ACCOUNTS_CLIENT_ID", "GOOGLE_ACCOUNTS_CLIENT_SECRET", "MOZAIKS_CONNECTED_ACCOUNTS_REDIRECT_URI"):
        assert f"{name}=" in example
