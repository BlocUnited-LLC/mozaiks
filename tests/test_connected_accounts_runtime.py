"""Who may use a user's connected account, and how the host wires connected accounts in.

A plugin obtains a token only inside the user's own dispatch: their HTTP
request, their own workflow session, or local development, and only for the
user the dispatch acts for. Every server-owned dispatch is refused.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mozaiksai.core.account.registry import AccountDataRegistry
from mozaiksai.core.connected_accounts import host, oauth
from mozaiksai.core.connected_accounts import service as service_module
from mozaiksai.core.connected_accounts.account_data import (
    ACCOUNT_DATA_HANDLER_ID,
    ConnectedAccountsDataHandler,
)
from mozaiksai.core.connected_accounts.errors import (
    AccountAccessDenied,
    AccountConnectionRequired,
)
from mozaiksai.core.connected_accounts.providers import GOOGLE_EMULATOR_ENV
from mozaiksai.core.connected_accounts.runtime import account_access_token
from mozaiksai.core.connected_accounts.service import (
    ConnectedAccountsService,
    configure_connected_accounts,
    get_connected_accounts_service,
)
from mozaiksai.core.runtime.composition import module_authority
from mozaiksai.core.runtime.composition.module_authority import ModuleDispatchAuthority
from mozaiksai.core.runtime.composition.module_context import ModuleContext
from tests.connected_accounts_fakes import FakeDatabase, FakeGoogle, FakeVault, plugin_app

APP_ID = "app-one"


class _Audit:
    async def log(self, record: Any) -> None:
        return None


@pytest.fixture
def connected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    google = FakeGoogle()
    monkeypatch.setattr(oauth, "http_client", google.client)
    monkeypatch.setattr(service_module, "get_audit_logger", lambda: _Audit())
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv(GOOGLE_EMULATOR_ENV, "https://google.test")
    monkeypatch.setenv("GOOGLE_ACCOUNTS_CLIENT_ID", "client-123")
    monkeypatch.setenv("GOOGLE_ACCOUNTS_CLIENT_SECRET", "client-secret-xyz")
    vault = FakeVault()
    service = ConnectedAccountsService(
        app_id=APP_ID, app_root=plugin_app(tmp_path / "app"), store=FakeDatabase().store(), vault=vault,
    )
    configure_connected_accounts(service)
    yield SimpleNamespace(service=service, google=google, vault=vault)
    configure_connected_accounts(None)


async def _connect(state: SimpleNamespace, user: str = "alice") -> None:
    started = await state.service.start(
        user_id=user, connection_id="google_calendar", redirect_uri="https://h/cb", binding_cookie="c",
    )
    code, flow_state = state.google.approve(started.authorize_url, user=user)
    result = await state.service.complete(state=flow_state, code=code, error="", read_cookie=lambda name: [started.binding])
    assert result.status == "connected"


def _context(kind: str, *, user_id: str | None = "alice", actor_id: str | None = "alice", app_id: str = APP_ID,
             monkeypatch: pytest.MonkeyPatch | None = None) -> ModuleContext:
    if monkeypatch is not None:
        # local_development authority exists only when the environment permits it.
        monkeypatch.setattr(module_authority, "_local_development_allowed", lambda: True)
    mode = "trusted_bypass" if kind in module_authority.TRUSTED_BYPASS_KINDS else "enforce"
    return ModuleContext(
        app_id=app_id,
        user_id=user_id,
        dispatch_authority=ModuleDispatchAuthority(kind=kind, permission_mode=mode, reason="test", actor_id=actor_id),
    )


@pytest.mark.parametrize("kind", ["authenticated_user", "workflow", "local_development"])
async def test_the_users_own_dispatch_gets_their_token(connected, kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    await _connect(connected)
    token = await account_access_token(_context(kind, monkeypatch=monkeypatch), "google_calendar")
    assert token.startswith("access-alice")


@pytest.mark.parametrize("kind", ["public_http", "framework_internal", "event_reaction", "app_internal", "operator_internal"])
async def test_server_owned_and_anonymous_dispatches_never_get_a_token(connected, kind: str) -> None:
    await _connect(connected)
    with pytest.raises(AccountAccessDenied):
        await account_access_token(_context(kind), "google_calendar")


async def test_a_dispatch_acting_for_another_user_gets_no_token(connected) -> None:
    await _connect(connected)
    with pytest.raises(AccountAccessDenied):
        await account_access_token(_context("authenticated_user", user_id="alice", actor_id="mallory"), "google_calendar")
    with pytest.raises(AccountAccessDenied):
        await account_access_token(_context("authenticated_user", user_id=None, actor_id=None), "google_calendar")
    with pytest.raises(AccountAccessDenied):
        await account_access_token(ModuleContext(app_id=APP_ID, user_id="alice"), "google_calendar")


async def test_tokens_are_bound_to_the_loaded_app(connected) -> None:
    await _connect(connected)
    # A dispatch for another app, or a host that never wired connected accounts, gets the
    # plugin's ordinary "not available" answer (403 ACCOUNT_CONNECTION_REQUIRED), not a 500.
    for context in (_context("authenticated_user", app_id="another-app"), None):
        if context is None:
            configure_connected_accounts(None)
            context = _context("authenticated_user")
        with pytest.raises(AccountConnectionRequired) as raised:
            await account_access_token(context, "google_calendar")
        assert raised.value.reason == "unavailable"
        assert str(raised.value) == "This app does not offer Google Calendar right now."
        assert "connect_route" not in raised.value.to_error_metadata()


async def test_the_user_is_asked_to_connect_rather_than_failing(connected) -> None:
    with pytest.raises(AccountConnectionRequired) as raised:
        await account_access_token(_context("workflow", user_id="bob", actor_id="bob"), "google_calendar")
    assert raised.value.reason == "not_connected"


# ---------------------------------------------------------------------------
# Host wiring
# ---------------------------------------------------------------------------

def test_the_host_installs_the_service_for_the_modules_it_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = AccountDataRegistry()
    monkeypatch.setattr(host, "account_data_registry", registry)
    root = plugin_app(tmp_path / "app")
    try:
        service = host.configure_for_app(
            app_id=APP_ID, app_root=root,
            modules=[SimpleNamespace(name="google_calendar", path=root / "modules" / "google_calendar")],
        )
        assert get_connected_accounts_service() is service
        assert [connection.id for connection in service.declared] == ["google_calendar"]  # gmail was not loaded
        assert registry.registered_module_ids() == [ACCOUNT_DATA_HANDLER_ID]
        page = host.profile_page()
        assert page is not None
        assert (page["id"], page["component"], page["visibility"], page["section"]) == (
            "connected-accounts", "ConnectedAccountsTab", "owner_only", "settings",
        )
        # Restarting the host does not register the handler twice.
        host.configure_for_app(app_id=APP_ID, app_root=root, modules=[])
        assert registry.registered_module_ids() == [ACCOUNT_DATA_HANDLER_ID]
        # No plugin declares a connection: no profile page, but deletion still revokes old grants.
        assert host.profile_page() is None
    finally:
        configure_connected_accounts(None)


async def test_account_deletion_revokes_the_users_grants_through_the_registry(connected) -> None:
    await _connect(connected, "alice")
    await _connect(connected, "bob")
    handler = ConnectedAccountsDataHandler()
    exported = await handler.export_user_data(app_id=APP_ID, user_id="alice")
    assert [row["provider"] for row in exported["connected_accounts"]] == ["google"]
    result = await handler.delete_user_data(app_id=APP_ID, user_id="alice")
    assert result["revoked_at_provider"] == 1
    with pytest.raises(AccountConnectionRequired):
        await connected.service.access_token(user_id="alice", connection_id="google_calendar")
    assert await connected.service.access_token(user_id="bob", connection_id="google_calendar")
    configure_connected_accounts(None)
    assert await handler.delete_user_data(app_id=APP_ID, user_id="alice") == {"deleted_count": 0}


async def test_the_profile_pages_include_connected_accounts_only_when_a_plugin_declares_one(
    connected, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mozaiksai.core.auth.dependencies import UserPrincipal
    from mozaiksai.hosts import platform as platform_app

    (tmp_path / "app.json").write_text('{"appName": "Test"}', encoding="utf-8")
    monkeypatch.setenv("PLATFORM_PATH", str(tmp_path))
    monkeypatch.setattr(platform_app, "executor_registry", SimpleNamespace(module_executor=None))
    principal = UserPrincipal(
        user_id="alice", email="alice@example.com", name="Alice", roles=[], scopes=[], raw_claims={}, app_id=APP_ID,
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    pages = (await platform_app.get_profile_pages(request=request, app_id=None, principal=principal))["pages"]
    assert [page["id"] for page in pages if page["id"] == "connected-accounts"] == ["connected-accounts"]
    configure_connected_accounts(None)
    pages = (await platform_app.get_profile_pages(request=request, app_id=None, principal=principal))["pages"]
    assert "connected-accounts" not in [page["id"] for page in pages]
