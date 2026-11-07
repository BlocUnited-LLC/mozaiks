"""Connected accounts: a user's own grant, the app admins' offering, and plugin tokens.

The service, store, OAuth client and declarations are real. Google's HTTP
endpoints, the database and the vault are in-memory fakes
(tests/connected_accounts_fakes.py), so each test can see exactly what was
requested, stored and revoked.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mozaiksai.core.audit.audit_logger import AuditEventKind
from mozaiksai.core.connected_accounts import oauth
from mozaiksai.core.connected_accounts import service as service_module
from mozaiksai.core.connected_accounts.definitions import (
    AccountConnectionsConfigError,
    load_account_connections,
    load_module_account_connections,
)
from mozaiksai.core.connected_accounts.errors import (
    AccountConnectionRequired,
    UnknownAccountConnection,
)
from mozaiksai.core.connected_accounts.providers import (
    GOOGLE_EMULATOR_ENV,
    AccountProviderError,
    get_provider,
    google_api_base_url,
)
from mozaiksai.core.connected_accounts.service import ConnectedAccountsService
from tests.connected_accounts_fakes import (
    CALENDAR_CONNECTION,
    CALENDAR_SCOPE,
    GMAIL_SEND_SCOPE,
    FakeDatabase,
    FakeGoogle,
    FakeVault,
    plugin_app,
    write_plugin_module,
)

APP_ID = "app-one"
REDIRECT = "https://app.example.test/api/connected-accounts/callback"
COOKIE = "__Host-mozaiks_account_connect_binding"


class _Audit:
    def __init__(self) -> None:
        self.records: list[Any] = []

    async def log(self, record: Any) -> None:
        self.records.append(record)

    def kinds(self) -> list[AuditEventKind]:
        return [record.kind for record in self.records]


@pytest.fixture
def google(monkeypatch: pytest.MonkeyPatch) -> FakeGoogle:
    fake = FakeGoogle()
    monkeypatch.setattr(oauth, "http_client", fake.client)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv(GOOGLE_EMULATOR_ENV, "https://google.test")
    monkeypatch.setenv("GOOGLE_ACCOUNTS_CLIENT_ID", "client-123")
    monkeypatch.setenv("GOOGLE_ACCOUNTS_CLIENT_SECRET", "client-secret-xyz")
    return fake


@pytest.fixture
def audit(monkeypatch: pytest.MonkeyPatch) -> _Audit:
    recorder = _Audit()
    monkeypatch.setattr(service_module, "get_audit_logger", lambda: recorder)
    return recorder


@pytest.fixture
def db() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture
def vault() -> FakeVault:
    return FakeVault()


@pytest.fixture
def make_service(tmp_path: Path, db: FakeDatabase, vault: FakeVault, google: FakeGoogle, audit: _Audit):
    root = plugin_app(tmp_path / "app")

    def build(app_id: str = APP_ID) -> ConnectedAccountsService:
        return ConnectedAccountsService(app_id=app_id, app_root=root, store=db.store(), vault=vault)

    return build


async def _connect(
    service: ConnectedAccountsService,
    google: FakeGoogle,
    *,
    user: str = "alice",
    connection_id: str = "google_calendar",
    granted: set[str] | None = None,
) -> Any:
    started = await service.start(user_id=user, connection_id=connection_id, redirect_uri=REDIRECT, binding_cookie=COOKIE)
    code, state = google.approve(started.authorize_url, user=user, granted=granted)
    result = await service.complete(
        state=state, code=code, error="", read_cookie=lambda name: [started.binding] if name == COOKIE else [],
    )
    return started, result


# ---------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------

def test_declarations_come_from_the_modules_the_host_loaded(tmp_path: Path) -> None:
    calendar = write_plugin_module(tmp_path, "google_calendar", [CALENDAR_CONNECTION])
    write_plugin_module(tmp_path, "not_loaded", [{**CALENDAR_CONNECTION, "id": "other"}])
    loaded = load_module_account_connections([("google_calendar", calendar)])
    assert [connection.id for connection in loaded] == ["google_calendar"]
    assert loaded[0].module_id == "google_calendar"
    assert loaded[0].scopes == (CALENDAR_SCOPE,)
    # Without the host's list, every module under app_root/modules is read.
    assert [connection.id for connection in load_account_connections(tmp_path)] == ["google_calendar", "other"]


@pytest.mark.parametrize(
    ("connection", "message"),
    [
        ({**CALENDAR_CONNECTION, "id": "Bad-Id"}, "lowercase identifier"),
        ({**CALENDAR_CONNECTION, "provider": "dropbox"}, "provider must be one of"),
        ({**CALENDAR_CONNECTION, "scopes": []}, "non-empty list of scope strings"),
        ({**CALENDAR_CONNECTION, "scopes": ["has space"]}, "non-empty list of scope strings"),
        ({**CALENDAR_CONNECTION, "label": "x" * 81}, "label is required"),
        ({**CALENDAR_CONNECTION, "client_secret": "nope"}, "unsupported fields"),
    ],
)
def test_invalid_declarations_are_refused(tmp_path: Path, connection: dict[str, Any], message: str) -> None:
    module_dir = write_plugin_module(tmp_path, "plugin", [connection])
    with pytest.raises(AccountConnectionsConfigError, match=message):
        load_module_account_connections([("plugin", module_dir)])


def test_two_modules_cannot_declare_the_same_connection(tmp_path: Path) -> None:
    first = write_plugin_module(tmp_path, "first", [CALENDAR_CONNECTION])
    second = write_plugin_module(tmp_path, "second", [CALENDAR_CONNECTION])
    with pytest.raises(AccountConnectionsConfigError, match="already declared by module 'first'"):
        load_module_account_connections([("first", first), ("second", second)])


def test_a_broken_declaration_offers_nothing(tmp_path: Path, db: FakeDatabase, vault: FakeVault) -> None:
    module_dir = write_plugin_module(tmp_path, "plugin", [{**CALENDAR_CONNECTION, "provider": "unknown"}])
    service = ConnectedAccountsService(
        app_id=APP_ID, app_root=tmp_path, modules=[("plugin", module_dir)], store=db.store(), vault=vault,
    )
    assert service.declared == ()
    assert service.config_error and "provider must be one of" in service.config_error


# ---------------------------------------------------------------------------
# Provider endpoints
# ---------------------------------------------------------------------------

def test_google_endpoints_and_the_local_emulator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(GOOGLE_EMULATOR_ENV, raising=False)
    provider = get_provider("google")
    assert provider.authorize_url == "https://accounts.google.com/o/oauth2/v2/auth"
    assert provider.token_url == "https://oauth2.googleapis.com/token"
    assert google_api_base_url() == "https://www.googleapis.com"
    # Distinct from sign-in with Google, which uses GOOGLE_CLIENT_ID.
    assert (provider.client_id_env, provider.client_secret_env) == ("GOOGLE_ACCOUNTS_CLIENT_ID", "GOOGLE_ACCOUNTS_CLIENT_SECRET")

    monkeypatch.setenv(GOOGLE_EMULATOR_ENV, "http://127.0.0.1:9999/")
    monkeypatch.delenv("APP_ENV", raising=False)
    assert get_provider("google").token_url == "http://127.0.0.1:9999/token"
    assert google_api_base_url() == "http://127.0.0.1:9999"


def test_the_emulator_is_refused_in_a_deployed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GOOGLE_EMULATOR_ENV, "http://127.0.0.1:9999")
    monkeypatch.setattr(
        "mozaiksai.core.connected_accounts.providers.resolve_environment",
        lambda: type("Env", (), {"is_deployed": True})(),
    )
    with pytest.raises(AccountProviderError, match="honored only in local development"):
        get_provider("google")
    with pytest.raises(AccountProviderError):
        google_api_base_url()


# ---------------------------------------------------------------------------
# Connecting
# ---------------------------------------------------------------------------

async def test_connecting_stores_the_grant_in_the_vault_and_only_a_record_in_the_database(
    make_service, google: FakeGoogle, db: FakeDatabase, vault: FakeVault, audit: _Audit,
) -> None:
    service = make_service()
    started, result = await _connect(service, google)
    assert (result.status, result.clear_cookie) == ("connected", COOKIE)

    # The authorize request: PKCE S256, offline access, consent, incremental scopes.
    query = started.authorize_url.split("?", 1)[1]
    for expected in ("code_challenge_method=S256", "access_type=offline", "prompt=consent", "include_granted_scopes=true"):
        assert expected in query
    assert "client-secret-xyz" not in started.authorize_url

    # The refresh token is in the vault under an opaque scope id, never in the database.
    [(scope, scope_id, service_name)] = vault.secrets
    assert (scope, service_name) == ("user", "google")
    assert "alice" not in scope_id and APP_ID not in scope_id
    refresh_token = vault.refresh_tokens()[0]
    database_dump = json.dumps([c.documents for c in db.collections.values()], default=str)
    assert refresh_token not in database_dump
    assert "access-alice" not in database_dump
    # The PKCE verifier does not outlive the flow.
    assert "code_verifier" not in database_dump

    [record] = db["ConnectedAccounts"].documents
    assert record["user_id"] == "alice" and record["app_id"] == APP_ID
    assert record["account_email"] == "alice@gmail.test"
    assert CALENDAR_SCOPE in record["granted_scopes"]
    assert audit.kinds() == [AuditEventKind.ACCOUNT_CONNECTED]
    assert refresh_token not in json.dumps([vars(r) for r in audit.records], default=str)

    listing = await service.list_for_user("alice")
    statuses = {row["id"]: row["status"] for row in listing["connections"]}
    assert statuses == {"gmail_send": "not_connected", "google_calendar": "connected"}
    assert listing["providers"][0]["account_email"] == "alice@gmail.test"
    # Another user sees nothing of Alice's.
    assert (await service.list_for_user("bob"))["providers"] == []


async def test_a_flow_completes_once_and_only_from_the_browser_that_started_it(make_service, google: FakeGoogle) -> None:
    service = make_service()
    started = await service.start(user_id="alice", connection_id="google_calendar", redirect_uri=REDIRECT, binding_cookie=COOKIE)
    code, state = google.approve(started.authorize_url, user="alice")

    # Another browser (no cookie, or a different one) cannot complete Alice's flow.
    refused = await service.complete(state=state, code=code, error="", read_cookie=lambda name: ["someone-else"])
    assert refused.status == "failed" and refused.clear_cookie is None
    assert google.token_requests == []
    # And the flow is spent: even the right browser cannot replay the state.
    replay = await service.complete(state=state, code=code, error="", read_cookie=lambda name: [started.binding])
    assert replay.status == "failed"
    assert (await service.flow_status(user_id="alice", flow_id=started.flow_id))["status"] == "failed"
    # The flow's outcome is Alice's alone.
    assert (await service.flow_status(user_id="bob", flow_id=started.flow_id)) == {"status": "unknown"}


async def test_a_repeated_binding_cookie_is_refused(make_service, google: FakeGoogle) -> None:
    service = make_service()
    started = await service.start(user_id="alice", connection_id="google_calendar", redirect_uri=REDIRECT, binding_cookie=COOKIE)
    code, state = google.approve(started.authorize_url, user="alice")
    result = await service.complete(state=state, code=code, error="", read_cookie=lambda name: [started.binding, started.binding])
    assert result.status == "failed"


async def test_a_partial_grant_is_not_kept(make_service, google: FakeGoogle, vault: FakeVault, db: FakeDatabase) -> None:
    service = make_service()
    _, result = await _connect(service, google, granted={"openid", "email"})
    assert result.status == "failed" and "every permission" in result.message
    assert vault.secrets == {} and db["ConnectedAccounts"].documents == []
    # A brand-new partial grant is revoked at the provider.
    assert len(google.revoked) == 1


async def test_declining_a_new_permission_keeps_the_access_already_granted(
    make_service, google: FakeGoogle, vault: FakeVault,
) -> None:
    service = make_service()
    await _connect(service, google, connection_id="google_calendar")
    calendar_refresh = vault.refresh_tokens()[0]
    # Alice tries Gmail but unticks gmail.send: revoking would end her Calendar grant too.
    _, result = await _connect(service, google, connection_id="gmail_send", granted={"openid", "email", CALENDAR_SCOPE})
    assert result.status == "failed"
    assert google.revoked == []
    assert vault.refresh_tokens() == [calendar_refresh]
    assert google.grants[calendar_refresh].revoked is False


async def test_a_grant_without_lasting_access_is_not_kept(make_service, google: FakeGoogle, vault: FakeVault) -> None:
    google.issue_refresh_token = False
    _, result = await _connect(make_service(), google)
    assert result.status == "failed" and "lasting access" in result.message
    assert vault.secrets == {}


async def test_a_grant_that_cannot_be_saved_is_revoked(make_service, google: FakeGoogle, vault: FakeVault, db: FakeDatabase) -> None:
    vault.fail_writes = True
    _, result = await _connect(make_service(), google)
    assert result.status == "failed"
    assert db["ConnectedAccounts"].documents == []
    assert len(google.revoked) == 1


async def test_the_user_declining_consent_ends_the_flow(make_service, google: FakeGoogle) -> None:
    service = make_service()
    started = await service.start(user_id="alice", connection_id="google_calendar", redirect_uri=REDIRECT, binding_cookie=COOKIE)
    _, state = google.approve(started.authorize_url, user="alice")
    result = await service.complete(state=state, code="", error="access_denied", read_cookie=lambda name: [started.binding])
    assert result.status == "failed" and "not granted" in result.message
    assert google.token_requests == []


async def test_without_the_apps_oauth_client_nothing_can_be_connected(
    make_service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOOGLE_ACCOUNTS_CLIENT_SECRET")
    service = make_service()
    with pytest.raises(AccountConnectionRequired) as raised:
        await service.start(user_id="alice", connection_id="google_calendar", redirect_uri=REDIRECT, binding_cookie=COOKIE)
    assert raised.value.reason == "unavailable"
    listing = await service.list_for_user("alice")
    assert {row["status"] for row in listing["connections"]} == {"unavailable"}


async def test_an_undeclared_connection_cannot_be_started(make_service) -> None:
    with pytest.raises(UnknownAccountConnection):
        await make_service().start(user_id="alice", connection_id="dropbox", redirect_uri=REDIRECT, binding_cookie=COOKIE)


# ---------------------------------------------------------------------------
# Plugin tokens
# ---------------------------------------------------------------------------

async def test_plugin_tokens_are_per_user_and_limited_to_the_connections_scopes(make_service, google: FakeGoogle) -> None:
    service = make_service()
    await _connect(service, google, user="alice", connection_id="google_calendar")
    await _connect(service, google, user="alice", connection_id="gmail_send")

    calendar_token = await service.access_token(user_id="alice", connection_id="google_calendar")
    gmail_token = await service.access_token(user_id="alice", connection_id="gmail_send")
    assert google.access_tokens[calendar_token][1] == frozenset({CALENDAR_SCOPE})
    assert google.access_tokens[gmail_token][1] == frozenset({GMAIL_SEND_SCOPE})
    # The refresh asked Google for exactly the connection's scopes.
    refreshes = [form for form in google.token_requests if form["grant_type"] == "refresh_token"]
    assert [form["scope"] for form in refreshes] == [CALENDAR_SCOPE, GMAIL_SEND_SCOPE]
    # Cached per connection: no second refresh, no thrashing between connections.
    assert await service.access_token(user_id="alice", connection_id="google_calendar") == calendar_token
    assert await service.access_token(user_id="alice", connection_id="gmail_send") == gmail_token
    assert len([form for form in google.token_requests if form["grant_type"] == "refresh_token"]) == 2

    with pytest.raises(AccountConnectionRequired) as raised:
        await service.access_token(user_id="bob", connection_id="google_calendar")
    assert raised.value.reason == "not_connected"
    assert raised.value.to_error_metadata()["connect_route"] == "/me?tab=connected-accounts&connect=google_calendar"


async def test_connecting_one_plugin_does_not_count_as_connecting_another(make_service, google: FakeGoogle, db: FakeDatabase) -> None:
    service = make_service()
    await _connect(service, google, connection_id="google_calendar")
    with pytest.raises(AccountConnectionRequired) as raised:
        await service.access_token(user_id="alice", connection_id="gmail_send")
    assert raised.value.reason == "not_connected"
    assert str(raised.value) == "Connect your Gmail account to use this."
    # A connection whose declaration grew since the user connected asks for the rest.
    record = db["ConnectedAccounts"].documents[0]
    record["granted_scopes"] = [scope for scope in record["granted_scopes"] if scope != CALENDAR_SCOPE] + ["partial"]
    service._declared = tuple(
        type(c)(**{**c.__dict__, "scopes": (CALENDAR_SCOPE, "partial")}) if c.id == "google_calendar" else c
        for c in service._declared
    )
    with pytest.raises(AccountConnectionRequired) as partial:
        await service.access_token(user_id="alice", connection_id="google_calendar")
    assert (partial.value.reason, partial.value.missing_scopes) == ("missing_scopes", (CALENDAR_SCOPE,))


async def test_a_grant_revoked_at_google_asks_the_user_to_reconnect(make_service, google: FakeGoogle, db: FakeDatabase) -> None:
    service = make_service()
    await _connect(service, google)
    token = await service.access_token(user_id="alice", connection_id="google_calendar")
    for grant in google.grants.values():
        grant.revoked = True  # the user removed the app in their Google account
    # The cached token still looks valid until the caller reports a refusal.
    assert await service.access_token(user_id="alice", connection_id="google_calendar") == token
    with pytest.raises(AccountConnectionRequired) as raised:
        await service.access_token(user_id="alice", connection_id="google_calendar", refresh=True)
    assert raised.value.reason == "reconnect_required"
    assert db["ConnectedAccounts"].documents[0]["status"] == "needs_reconnect"
    listing = await service.list_for_user("alice")
    assert {row["id"]: row["status"] for row in listing["connections"]}["google_calendar"] == "reconnect_required"


async def test_reconnecting_replaces_the_grant_and_clears_stale_tokens(make_service, google: FakeGoogle, vault: FakeVault) -> None:
    service = make_service()
    await _connect(service, google)
    old = await service.access_token(user_id="alice", connection_id="google_calendar")
    await _connect(service, google)
    new = await service.access_token(user_id="alice", connection_id="google_calendar")
    assert new != old
    assert len(vault.secrets) == 1


async def test_withheld_by_the_apps_admins_means_no_new_connections_and_no_tokens(
    make_service, google: FakeGoogle, audit: _Audit,
) -> None:
    service = make_service()
    await _connect(service, google)
    await service.access_token(user_id="alice", connection_id="google_calendar")

    assert await service.set_offered(connection_id="google_calendar", offered=False, actor_id="admin-1") == {
        "id": "google_calendar", "offered": False,
    }
    admin_write = audit.records[-1]
    assert admin_write.kind == AuditEventKind.ADMIN_WRITE
    assert (admin_write.actor_id, admin_write.resource, admin_write.action) == ("admin-1", "connected_accounts.offering", "google_calendar")
    assert admin_write.inputs_hash and admin_write.extra == {"offered": False}

    with pytest.raises(AccountConnectionRequired) as raised:
        await service.access_token(user_id="alice", connection_id="google_calendar")
    assert raised.value.reason == "unavailable"
    assert "connect_route" not in raised.value.to_error_metadata()
    with pytest.raises(UnknownAccountConnection):
        await service.start(user_id="alice", connection_id="google_calendar", redirect_uri=REDIRECT, binding_cookie=COOKIE)
    # Alice can still see and disconnect the grant she gave.
    listing = await service.list_for_user("alice")
    assert [row["id"] for row in listing["connections"]] == ["gmail_send"]
    assert [row["provider"] for row in listing["providers"]] == ["google"]

    # Another replica of the app sees the change too (no shared memory).
    replica = make_service()
    assert [connection.id for connection in await replica.offered()] == ["gmail_send"]
    await service.set_offered(connection_id="google_calendar", offered=True, actor_id="admin-1")
    assert await service.access_token(user_id="alice", connection_id="google_calendar")


async def test_an_admin_cannot_offer_what_no_plugin_declares(make_service) -> None:
    with pytest.raises(UnknownAccountConnection):
        await make_service().set_offered(connection_id="dropbox", offered=True, actor_id="admin-1")


async def test_offering_is_per_app(make_service, google: FakeGoogle) -> None:
    first, second = make_service("app-one"), make_service("app-two")
    await first.set_offered(connection_id="google_calendar", offered=False, actor_id="admin-1")
    assert [c.id for c in await first.offered()] == ["gmail_send"]
    assert [c.id for c in await second.offered()] == ["gmail_send", "google_calendar"]


async def test_grants_are_per_app(make_service, google: FakeGoogle) -> None:
    first, second = make_service("app-one"), make_service("app-two")
    await _connect(first, google)
    with pytest.raises(AccountConnectionRequired):
        await second.access_token(user_id="alice", connection_id="google_calendar")


# ---------------------------------------------------------------------------
# Admin view, disconnecting, account lifecycle
# ---------------------------------------------------------------------------

async def test_the_admin_summary_has_counts_and_setting_names_but_no_identities(make_service, google: FakeGoogle) -> None:
    service = make_service()
    await _connect(service, google, user="alice")
    await _connect(service, google, user="bob")
    summary = await service.admin_summary(redirect_uri=REDIRECT)
    dump = json.dumps(summary)
    for private in ("alice", "bob", "gmail.test", "client-secret-xyz", "client-123", "refresh-", "access-"):
        assert private not in dump
    calendar = next(row for row in summary["connections"] if row["id"] == "google_calendar")
    assert calendar["offered"] and calendar["client_configured"]
    assert calendar["client_settings"] == ["GOOGLE_ACCOUNTS_CLIENT_ID", "GOOGLE_ACCOUNTS_CLIENT_SECRET"]
    assert summary["providers"] == [{"provider": "google", "connected": 2, "needs_reconnect": 0}]
    assert summary["redirect_uri"] == REDIRECT


async def test_disconnecting_revokes_at_google_and_forgets_everything(
    make_service, google: FakeGoogle, vault: FakeVault, db: FakeDatabase, audit: _Audit,
) -> None:
    service = make_service()
    await _connect(service, google)
    refresh_token = vault.refresh_tokens()[0]
    await service.access_token(user_id="alice", connection_id="google_calendar")

    assert await service.disconnect(user_id="alice", provider_id="google") == {
        "disconnected": True, "revoked_at_provider": True,
    }
    assert google.revoked == [refresh_token]
    assert vault.secrets == {} and db["ConnectedAccounts"].documents == []
    assert audit.kinds()[-1] == AuditEventKind.ACCOUNT_DISCONNECTED
    with pytest.raises(AccountConnectionRequired):
        await service.access_token(user_id="alice", connection_id="google_calendar")
    # Idempotent.
    assert (await service.disconnect(user_id="alice", provider_id="google"))["disconnected"] is False


async def test_bob_cannot_disconnect_alice(make_service, google: FakeGoogle, vault: FakeVault) -> None:
    service = make_service()
    await _connect(service, google, user="alice")
    assert (await service.disconnect(user_id="bob", provider_id="google"))["disconnected"] is False
    assert len(vault.secrets) == 1 and google.revoked == []


async def test_deleting_an_account_revokes_every_grant_and_exports_none(
    make_service, google: FakeGoogle, vault: FakeVault, db: FakeDatabase,
) -> None:
    service = make_service()
    await _connect(service, google, user="alice")
    await _connect(service, google, user="bob")
    export = await service.export_user_data(user_id="alice")
    assert [row["provider"] for row in export] == ["google"]
    assert "refresh" not in json.dumps(export) and "access-" not in json.dumps(export)

    result = await service.delete_user_data(user_id="alice")
    assert result["revoked_at_provider"] == 1 and result["deleted_count"] >= 1
    assert [doc["user_id"] for doc in db["ConnectedAccounts"].documents] == ["bob"]
    assert len(vault.secrets) == 1
    assert db["ConnectedAccountFlows"].find({"owner_user_id": "alice"})._rows == []


async def test_an_expiring_vault_record_is_renewed_while_the_grant_is_used(
    make_service, google: FakeGoogle, vault: FakeVault,
) -> None:
    service = make_service()
    await _connect(service, google)
    [key] = vault.secrets
    vault.secrets[key]["expires_at"] = "2000-01-01T00:00:00+00:00"
    await service.access_token(user_id="alice", connection_id="google_calendar")
    assert vault.secrets[key]["expires_at"] > "2026"


async def test_an_unreadable_vault_fails_closed(make_service, google: FakeGoogle, vault: FakeVault) -> None:
    service = make_service()
    await _connect(service, google)
    vault.fail_reads = True
    with pytest.raises(service_module.AccountProviderUnavailable):
        await service.access_token(user_id="alice", connection_id="google_calendar")
