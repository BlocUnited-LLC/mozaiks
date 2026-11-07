"""The connected-accounts routes: a user's own connections, the provider callback, and the app admins' controls.

These tests drive the real router through FastAPI's TestClient with the real
``require_user`` and admin dependencies. Only the auth adapter's token
validation, Google's HTTP endpoints, the database and the vault are faked.
Each TestClient is one browser with its own cookie jar.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mozaiksai.core.audit.audit_logger import AuditEventKind
from mozaiksai.core.auth.adapters.base import AuthError, UserClaims
from mozaiksai.core.connected_accounts import oauth
from mozaiksai.core.connected_accounts import service as service_module
from mozaiksai.core.connected_accounts.providers import GOOGLE_EMULATOR_ENV
from mozaiksai.core.connected_accounts.service import (
    ConnectedAccountsService,
    configure_connected_accounts,
)
from mozaiksai.hosts.routers import connected_accounts as routes
from tests.connected_accounts_fakes import (
    CALENDAR_CONNECTION,
    FakeDatabase,
    FakeGoogle,
    FakeVault,
    plugin_app,
    write_plugin_module,
)

APP_ID = "app-one"
SECURE_COOKIE = "__Host-mozaiks_account_connect_binding"


class _Adapter:
    """``bearer-<user>`` is that user; ``bearer-admin-<user>`` also has the admin role."""

    name = "fixture"

    async def validate_token(self, token: str) -> UserClaims:
        if not token.startswith("bearer-"):
            raise AuthError("Invalid token", status_code=401)
        user_id = token.removeprefix("bearer-")
        roles = ["user", "admin"] if user_id.startswith("admin-") else ["user"]
        return UserClaims(
            user_id=user_id,
            email=f"{user_id}@example.test",
            email_verified=not user_id.startswith("unverified-"),
            roles=roles,
            provider="fixture",
        )

    def is_enabled(self) -> bool:
        return True


class _Audit:
    def __init__(self) -> None:
        self.records: list[Any] = []

    async def log(self, record: Any) -> None:
        self.records.append(record)


@pytest.fixture
def google(monkeypatch: pytest.MonkeyPatch) -> FakeGoogle:
    fake = FakeGoogle()
    monkeypatch.setattr(oauth, "http_client", fake.client)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv(routes.REDIRECT_URI_ENV, raising=False)
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
def vault() -> FakeVault:
    return FakeVault()


@pytest.fixture
def service(tmp_path: Path, google: FakeGoogle, audit: _Audit, vault: FakeVault) -> Iterator[ConnectedAccountsService]:
    configured = ConnectedAccountsService(
        app_id=APP_ID, app_root=plugin_app(tmp_path / "app"), store=FakeDatabase().store(), vault=vault,
    )
    configure_connected_accounts(configured)
    with patch("mozaiksai.core.auth.dependencies.is_auth_enabled", return_value=True), \
         patch("mozaiksai.core.auth.dependencies.get_auth_adapter", return_value=_Adapter()):
        yield configured
    configure_connected_accounts(None)


@pytest.fixture
def app(service: ConnectedAccountsService) -> FastAPI:
    application = FastAPI()
    application.include_router(routes.router)
    application.state.loaded_app_id = APP_ID
    return application


def _browser(app: FastAPI, user: str | None = None) -> TestClient:
    headers = {"Authorization": f"Bearer bearer-{user}"} if user else {}
    return TestClient(app, base_url="https://testserver", headers=headers, follow_redirects=False)


def _start(browser: TestClient, connection_id: str = "google_calendar") -> dict[str, Any]:
    response = browser.post(f"/api/me/connected-accounts/{connection_id}/connect")
    assert response.status_code == 200, response.text
    return response.json()


def _callback(browser: TestClient, code: str, state: str):
    # The popup is a plain navigation: it carries cookies, never the bearer token.
    return browser.get(
        "/api/connected-accounts/callback", params={"code": code, "state": state}, headers={"Authorization": ""},
    )


def test_a_user_connects_their_own_google_account_through_the_popup(app: FastAPI, google: FakeGoogle) -> None:
    alice = _browser(app, "alice")
    listing = alice.get("/api/me/connected-accounts")
    assert listing.status_code == 200 and listing.headers["cache-control"] == "no-store"
    assert {row["id"]: row["status"] for row in listing.json()["connections"]} == {
        "gmail_send": "not_connected", "google_calendar": "not_connected",
    }

    response = alice.post("/api/me/connected-accounts/google_calendar/connect")
    assert response.status_code == 200
    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith(f"{SECURE_COOKIE}=")
    for attribute in ("HttpOnly", "Path=/", "Secure", "SameSite=lax"):
        assert attribute in set_cookie
    assert "Domain" not in set_cookie
    started = response.json()
    assert "redirect_uri=https%3A%2F%2Ftestserver%2Fapi%2Fconnected-accounts%2Fcallback" in started["authorize_url"]

    code, state = google.approve(started["authorize_url"], user="alice")
    popup = _callback(alice, code, state)
    assert popup.status_code == 200
    assert "Google Calendar connected." in popup.text
    assert popup.headers["content-security-policy"].startswith("default-src 'none'")
    assert f'{SECURE_COOKIE}=""' in popup.headers["set-cookie"] or "Max-Age=0" in popup.headers["set-cookie"]

    status = alice.get(f"/api/me/connected-accounts/flows/{started['flow_id']}").json()
    assert status == {"status": "connected", "message": "Google Calendar connected."}
    after = alice.get("/api/me/connected-accounts").json()
    assert {row["id"]: row["status"] for row in after["connections"]}["google_calendar"] == "connected"
    assert after["providers"][0]["account_email"] == "alice@gmail.test"


def test_another_browser_cannot_complete_someone_elses_flow(app: FastAPI, google: FakeGoogle) -> None:
    alice, mallory = _browser(app, "alice"), _browser(app, "mallory")
    started = _start(alice)
    code, state = google.approve(started["authorize_url"], user="mallory")
    # Mallory consented with her own Google account and opens the callback in her browser.
    refused = _callback(mallory, code, state)
    assert refused.status_code == 400
    assert google.token_requests == []
    status = alice.get(f"/api/me/connected-accounts/flows/{started['flow_id']}").json()
    assert status["status"] == "failed" and "different browser" in status["message"]
    # Mallory's browser holds no cookie of this flow, so none is cleared for it.
    assert "set-cookie" not in refused.headers


def test_a_flow_and_its_outcome_belong_to_the_user_who_started_it(app: FastAPI) -> None:
    started = _start(_browser(app, "alice"))
    assert _browser(app, "bob").get(f"/api/me/connected-accounts/flows/{started['flow_id']}").json() == {"status": "unknown"}


def test_anonymous_visitors_cannot_connect_or_list(app: FastAPI) -> None:
    visitor = _browser(app)
    assert visitor.get("/api/me/connected-accounts").status_code in {401, 403}
    assert visitor.post("/api/me/connected-accounts/google_calendar/connect").status_code in {401, 403}
    assert visitor.delete("/api/me/connected-accounts/providers/google").status_code in {401, 403}


def test_disconnecting_revokes_the_users_grant(app: FastAPI, google: FakeGoogle, vault: FakeVault) -> None:
    alice = _browser(app, "alice")
    started = _start(alice)
    code, state = google.approve(started["authorize_url"], user="alice")
    _callback(alice, code, state)
    response = alice.delete("/api/me/connected-accounts/providers/google")
    assert response.json() == {"disconnected": True, "revoked_at_provider": True}
    assert vault.secrets == {}


def test_an_unoffered_or_unknown_connection_cannot_be_started(app: FastAPI, service: ConnectedAccountsService) -> None:
    assert _browser(app, "alice").post("/api/me/connected-accounts/dropbox/connect").status_code == 404


def test_without_the_apps_oauth_client_connecting_is_unavailable(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_ACCOUNTS_CLIENT_ID")
    response = _browser(app, "alice").post("/api/me/connected-accounts/google_calendar/connect")
    assert response.status_code == 503
    assert "set-cookie" not in response.headers


def test_a_configured_redirect_uri_is_used_exactly(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(routes.REDIRECT_URI_ENV, "https://app.example.com/api/connected-accounts/callback")
    started = _start(_browser(app, "alice"))
    assert "redirect_uri=https%3A%2F%2Fapp.example.com%2Fapi%2Fconnected-accounts%2Fcallback" in started["authorize_url"]


def test_the_routes_answer_only_for_the_loaded_app(app: FastAPI) -> None:
    app.state.loaded_app_id = "another-app"
    assert _browser(app, "alice").get("/api/me/connected-accounts").status_code == 404
    configure_connected_accounts(None)
    app.state.loaded_app_id = APP_ID
    assert _browser(app, "alice").get("/api/me/connected-accounts").status_code == 404
    popup = _browser(app).get("/api/connected-accounts/callback", params={"state": "x", "code": "y"})
    assert popup.status_code == 404 and "not available" in popup.text


def test_popup_messages_are_escaped(tmp_path: Path, google: FakeGoogle, audit: _Audit, vault: FakeVault) -> None:
    root = tmp_path / "escaping"
    write_plugin_module(root, "cal", [{**CALENDAR_CONNECTION, "label": "Cal <img src=x onerror=alert(1)>"}])
    configured = ConnectedAccountsService(app_id=APP_ID, app_root=root, store=FakeDatabase().store(), vault=vault)
    configure_connected_accounts(configured)
    application = FastAPI()
    application.include_router(routes.router)
    application.state.loaded_app_id = APP_ID
    try:
        with patch("mozaiksai.core.auth.dependencies.is_auth_enabled", return_value=True), \
             patch("mozaiksai.core.auth.dependencies.get_auth_adapter", return_value=_Adapter()):
            alice = _browser(application, "alice")
            started = _start(alice)
            code, state = google.approve(started["authorize_url"], user="alice")
            popup = _callback(alice, code, state)
    finally:
        configure_connected_accounts(None)
    assert "<img" not in popup.text
    assert "&lt;img src=x onerror=alert(1)&gt;" in popup.text


# ---------------------------------------------------------------------------
# The app's admins
# ---------------------------------------------------------------------------

def test_only_the_apps_admins_see_and_change_the_offering(app: FastAPI, audit: _Audit) -> None:
    alice, admin = _browser(app, "alice"), _browser(app, "admin-owner")
    assert alice.get("/api/admin/connected-accounts").status_code == 403
    assert alice.put("/api/admin/connected-accounts/google_calendar", json={"offered": False}).status_code == 403

    summary = admin.get("/api/admin/connected-accounts")
    assert summary.status_code == 200
    body = summary.json()
    assert {row["id"]: row["offered"] for row in body["connections"]} == {"gmail_send": True, "google_calendar": True}
    assert body["redirect_uri"] == "https://testserver/api/connected-accounts/callback"

    changed = admin.put("/api/admin/connected-accounts/google_calendar", json={"offered": False})
    assert changed.json() == {"id": "google_calendar", "offered": False}
    assert [record.kind for record in audit.records] == [AuditEventKind.ADMIN_WRITE]
    assert audit.records[0].actor_id == "admin-owner"

    assert [row["id"] for row in alice.get("/api/me/connected-accounts").json()["connections"]] == ["gmail_send"]
    assert alice.post("/api/me/connected-accounts/google_calendar/connect").status_code == 404


def test_a_verified_allowlisted_email_is_an_app_admin_and_an_unverified_one_is_not(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("mozaiksai.core.admin.router.is_admin_by_email", lambda email: email is not None)
    assert _browser(app, "owner").get("/api/admin/connected-accounts").status_code == 200
    assert _browser(app, "unverified-owner").get("/api/admin/connected-accounts").status_code == 403


@pytest.mark.parametrize("body", [{"offered": "false"}, {"offered": 0}, {"offered": True, "extra": 1}, {}])
def test_the_offering_update_is_strict(app: FastAPI, body: dict[str, Any]) -> None:
    assert _browser(app, "admin-owner").put("/api/admin/connected-accounts/google_calendar", json=body).status_code == 422


def test_admins_cannot_offer_an_undeclared_connection(app: FastAPI) -> None:
    response = _browser(app, "admin-owner").put("/api/admin/connected-accounts/dropbox", json={"offered": True})
    assert response.status_code == 404
