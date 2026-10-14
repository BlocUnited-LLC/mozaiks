"""The GitHub OAuth popup flow is bound to the user and browser that start it.

These tests drive the real router (``mozaiksai.hosts.routers.oauth_github``)
through FastAPI's TestClient with the real ``require_user`` dependency. Only
GitHub's HTTP endpoints and the auth adapter's token validation are faked.

Each TestClient is one browser: it keeps its own cookie jar, and sends the
bearer token of the person using it (or none).

The original defect: ``/connect`` took a caller-chosen ``session_key`` as the
OAuth state, and ``/callback``, ``/ready``, ``/repos`` and
``consume_github_token`` trusted whoever presented that key. An attacker could
pick the key, send a victim the connect link, and then read the victim's
repositories or run discovery with the victim's ``repo``-scoped token.
"""
from __future__ import annotations

import asyncio
import importlib.util
import urllib.parse
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import mozaiksai.hosts.routers.oauth_github as oauth
from mozaiksai.core.auth.adapters.base import AuthError, UserClaims
from mozaiksai.core.auth.anonymous_access import ANONYMOUS_PROVENANCE, LOCAL_DEVELOPMENT_PROVENANCE
from mozaiksai.core.auth.dependencies import UserPrincipal, require_user
from tests.factory_context import factory_context

ROOT = Path(__file__).resolve().parents[1]
AUTHORIZE_PREFIX = "https://github.com/login/oauth/authorize?"


class _Adapter:
    """Validates ``bearer-<user>`` tokens as that user; anything else is invalid."""

    name = "fixture"

    async def validate_token(self, token: str) -> UserClaims:
        if not token.startswith("bearer-"):
            raise AuthError("Invalid token", status_code=401)
        user_id = token.removeprefix("bearer-")
        return UserClaims(user_id=user_id, email=f"{user_id}@example.test", provider="fixture")

    def is_enabled(self) -> bool:
        return True


class _GitHub:
    """GitHub's token exchange and repo list, keyed by authorization code."""

    def __init__(self) -> None:
        self.exchanges: list[str] = []
        self.repo_tokens: list[str] = []
        self.exchange_status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            code = urllib.parse.parse_qs(request.content.decode())["code"][0]
            self.exchanges.append(code)
            if self.exchange_status != 200:
                return httpx.Response(self.exchange_status)
            return httpx.Response(200, json={"access_token": f"token-for-{code}"})
        if request.url.path == "/user/repos":
            token = request.headers["Authorization"].removeprefix("Bearer ")
            self.repo_tokens.append(token)
            owner = token.removeprefix("token-for-").removesuffix("-code")
            return httpx.Response(200, json=[{"full_name": f"{owner}/private-repo", "private": True}])
        return httpx.Response(404)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _reset_stores() -> None:
    # getattr keeps these tests runnable against the pre-fix router, whose
    # stores had other names, so there they fail on the defect, not in setup.
    for name in ("_PENDING_FLOWS", "_CONNECTIONS", "_STATE_STORE", "_TOKEN_STORE"):
        getattr(oauth, name, {}).clear()


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> Iterator[_GitHub]:
    fake = _GitHub()
    monkeypatch.setattr(
        oauth, "_github_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake.handler), timeout=5.0),
        raising=False,
    )
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "fixture-client")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "fixture-secret")
    monkeypatch.delenv("GITHUB_OAUTH_REDIRECT_URI", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    _reset_stores()
    with patch("mozaiksai.core.auth.dependencies.is_auth_enabled", return_value=True), \
         patch("mozaiksai.core.auth.dependencies.get_auth_adapter", return_value=_Adapter()):
        yield fake
    _reset_stores()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(oauth, "_clock", fake, raising=False)
    return fake


@pytest.fixture
def app() -> FastAPI:
    application = FastAPI()
    application.include_router(oauth.router)
    return application


def _browser(app: FastAPI, user: str | None = None) -> TestClient:
    headers = {"Authorization": f"Bearer bearer-{user}"} if user else {}
    return TestClient(app, headers=headers, follow_redirects=False)


def _start(browser: TestClient) -> tuple[str, str]:
    response = browser.post("/api/oauth/github/start")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["authorize_url"].startswith(AUTHORIZE_PREFIX)
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(body["authorize_url"]).query)
    return query["state"][0], body["connection_id"]


def _connect(browser: TestClient, code: str) -> str:
    state, connection_id = _start(browser)
    response = browser.get("/api/oauth/github/callback", params={"code": code, "state": state})
    assert response.status_code == 200, response.text
    assert "GitHub connected" in response.text
    return connection_id


# ---------------------------------------------------------------------------
# The owner's flow works end to end
# ---------------------------------------------------------------------------


def test_owner_connects_lists_repos_and_discovery_consumes_once(app: FastAPI, github: _GitHub) -> None:
    alice = _browser(app, "alice")
    assert alice.get("/api/oauth/github/status").json() == {"configured": True}

    start = alice.post("/api/oauth/github/start")
    assert start.status_code == 200
    body = start.json()
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(body["authorize_url"]).query)
    assert query["client_id"] == ["fixture-client"]
    assert query["scope"] == ["repo"]
    state = query["state"][0]
    connection_id = body["connection_id"]
    # Server-minted, unguessable, and distinct: the connection id never travels through GitHub.
    assert len(state) >= 40 and len(connection_id) >= 40 and state != connection_id
    cookie = start.headers["set-cookie"]
    assert cookie.startswith(f"{oauth.BINDING_COOKIE}=")
    assert "HttpOnly" in cookie and "Path=/api/oauth/github" in cookie and "SameSite=lax" in cookie

    assert alice.get("/api/oauth/github/ready", params={"connection_id": connection_id}).json() == {"ready": False}

    callback = alice.get("/api/oauth/github/callback", params={"code": "alice-code", "state": state})
    assert callback.status_code == 200
    assert "GitHub connected" in callback.text
    assert f'{oauth.BINDING_COOKIE}=""' in callback.headers["set-cookie"]  # binding cleared
    assert github.exchanges == ["alice-code"]

    assert alice.get("/api/oauth/github/ready", params={"connection_id": connection_id}).json() == {"ready": True}
    repos = alice.get("/api/oauth/github/repos", params={"connection_id": connection_id})
    assert repos.status_code == 200
    assert [repo["full_name"] for repo in repos.json()["repos"]] == ["alice/private-repo"]
    assert github.repo_tokens == ["token-for-alice-code"]

    assert oauth.consume_github_token(connection_id, user_id="alice") == "token-for-alice-code"
    assert oauth.consume_github_token(connection_id, user_id="alice") is None  # one-time
    assert alice.get("/api/oauth/github/ready", params={"connection_id": connection_id}).json() == {"ready": False}


# ---------------------------------------------------------------------------
# The reported attack: an attacker-chosen session key
# ---------------------------------------------------------------------------


def test_attacker_chosen_session_key_cannot_capture_a_victims_token(app: FastAPI, github: _GitHub) -> None:
    attacker = _browser(app)  # no authentication at all
    victim = _browser(app)    # the victim's browser following GitHub's redirect
    chosen = "attacker-chosen-session-key"

    connect = attacker.get("/api/oauth/github/connect", params={"session_key": chosen})
    assert connect.status_code in {404, 405}
    assert "github.com" not in connect.headers.get("location", "")

    callback = victim.get("/api/oauth/github/callback", params={"code": "victim-code", "state": chosen})
    assert callback.status_code == 400
    assert github.exchanges == []

    for path in ("/api/oauth/github/ready", "/api/oauth/github/repos"):
        response = attacker.get(path, params={"session_key": chosen, "connection_id": chosen})
        assert response.status_code == 401, path
    assert oauth.consume_github_token(chosen, user_id="mallory") is None
    assert oauth._CONNECTIONS == {}
    assert github.repo_tokens == []


def test_flow_started_by_attacker_cannot_be_completed_by_victims_consent(app: FastAPI, github: _GitHub) -> None:
    """The variant a user-bound state alone would leave open.

    The attacker starts a flow as themselves and sends the victim GitHub's
    authorize URL. GitHub redirects the victim's browser to the callback with
    the victim's code; that browser lacks the attacker's binding cookie.
    """
    mallory = _browser(app, "mallory")
    state, connection_id = _start(mallory)
    victim_browser = _browser(app, "victim")

    callback = victim_browser.get("/api/oauth/github/callback", params={"code": "victim-code", "state": state})

    assert callback.status_code == 400
    assert "different browser" in callback.text
    assert github.exchanges == []  # the victim's code is never exchanged
    assert mallory.get("/api/oauth/github/ready", params={"connection_id": connection_id}).json() == {"ready": False}
    assert mallory.get("/api/oauth/github/repos", params={"connection_id": connection_id}).status_code == 404
    assert oauth.consume_github_token(connection_id, user_id="mallory") is None
    # The state was spent: not even the attacker's own browser can use it now.
    replay = mallory.get("/api/oauth/github/callback", params={"code": "mallory-code", "state": state})
    assert replay.status_code == 400
    assert github.exchanges == []


def test_another_user_cannot_read_or_consume_a_connection(app: FastAPI, github: _GitHub) -> None:
    alice = _browser(app, "alice")
    connection_id = _connect(alice, "alice-code")
    mallory = _browser(app, "mallory")

    assert mallory.get("/api/oauth/github/ready", params={"connection_id": connection_id}).json() == {"ready": False}
    assert mallory.get("/api/oauth/github/repos", params={"connection_id": connection_id}).status_code == 404
    assert oauth.consume_github_token(connection_id, user_id="mallory") is None
    assert oauth.consume_github_token(connection_id, user_id="") is None
    assert github.repo_tokens == []
    # A refused consume does not spend the owner's connection.
    assert oauth.consume_github_token(connection_id, user_id="alice") == "token-for-alice-code"


def test_unauthenticated_calls_are_refused(app: FastAPI, github: _GitHub) -> None:
    alice = _browser(app, "alice")
    connection_id = _connect(alice, "alice-code")
    nobody = _browser(app)
    forged = TestClient(app, headers={"Authorization": "Bearer forged"})

    for client in (nobody, forged):
        assert client.get("/api/oauth/github/status").status_code == 401
        assert client.post("/api/oauth/github/start").status_code == 401
        assert client.get("/api/oauth/github/ready", params={"connection_id": connection_id}).status_code == 401
        assert client.get("/api/oauth/github/repos", params={"connection_id": connection_id}).status_code == 401
    assert github.repo_tokens == []
    assert len(oauth._PENDING_FLOWS) == 0


def _principal(user_id: str, provenance: str) -> UserPrincipal:
    return UserPrincipal(
        user_id=user_id, email=None, name=None, roles=[], scopes=[], raw_claims={},
        auth_provenance=provenance,
    )


def test_anonymous_visitor_cannot_connect_but_local_development_can(app: FastAPI, github: _GitHub) -> None:
    app.dependency_overrides[require_user] = lambda: _principal("anonymous", ANONYMOUS_PROVENANCE)
    visitor = _browser(app)
    assert visitor.get("/api/oauth/github/status").status_code == 403
    assert visitor.post("/api/oauth/github/start").status_code == 403
    assert oauth._PENDING_FLOWS == {}

    app.dependency_overrides[require_user] = lambda: _principal("anonymous", LOCAL_DEVELOPMENT_PROVENANCE)
    developer = _browser(app)
    connection_id = _connect(developer, "dev-code")
    assert oauth.consume_github_token(connection_id, user_id="anonymous") == "token-for-dev-code"


# ---------------------------------------------------------------------------
# State handling: forged, replayed, expired
# ---------------------------------------------------------------------------


def test_forged_state_is_refused(app: FastAPI, github: _GitHub) -> None:
    alice = _browser(app, "alice")
    _start(alice)  # alice holds a valid binding cookie for a real flow

    response = alice.get("/api/oauth/github/callback", params={"code": "alice-code", "state": "forged-state"})

    assert response.status_code == 400
    assert github.exchanges == []
    assert oauth._CONNECTIONS == {}


def test_state_is_single_use(app: FastAPI, github: _GitHub) -> None:
    alice = _browser(app, "alice")
    state, _ = _start(alice)
    cookie = alice.cookies.get(oauth.BINDING_COOKIE)
    assert alice.get("/api/oauth/github/callback", params={"code": "alice-code", "state": state}).status_code == 200

    alice.cookies.set(oauth.BINDING_COOKIE, cookie, path=oauth.BINDING_COOKIE_PATH)
    replay = alice.get("/api/oauth/github/callback", params={"code": "second-code", "state": state})

    assert replay.status_code == 400
    assert github.exchanges == ["alice-code"]


def test_expired_state_is_refused(app: FastAPI, github: _GitHub, clock: _Clock) -> None:
    alice = _browser(app, "alice")
    state, connection_id = _start(alice)

    clock.now += oauth._STATE_TTL
    response = alice.get("/api/oauth/github/callback", params={"code": "alice-code", "state": state})

    assert response.status_code == 400
    assert github.exchanges == []
    assert oauth.consume_github_token(connection_id, user_id="alice") is None


def test_connected_token_expires(app: FastAPI, github: _GitHub, clock: _Clock) -> None:
    alice = _browser(app, "alice")
    connection_id = _connect(alice, "alice-code")

    clock.now += oauth._TOKEN_TTL - 1
    assert alice.get("/api/oauth/github/ready", params={"connection_id": connection_id}).json() == {"ready": True}
    clock.now += 1
    assert alice.get("/api/oauth/github/ready", params={"connection_id": connection_id}).json() == {"ready": False}
    assert alice.get("/api/oauth/github/repos", params={"connection_id": connection_id}).status_code == 404
    assert oauth.consume_github_token(connection_id, user_id="alice") is None
    assert oauth._CONNECTIONS == {}


def test_each_user_holds_a_bounded_number_of_flows(app: FastAPI, github: _GitHub) -> None:
    alice = _browser(app, "alice")
    states = [_start(alice)[0] for _ in range(oauth._MAX_ENTRIES_PER_USER + 2)]
    _start(_browser(app, "bob"))

    owned = [state for state, flow in oauth._PENDING_FLOWS.items() if flow.owner_user_id == "alice"]
    assert owned == states[-oauth._MAX_ENTRIES_PER_USER:]
    assert sum(flow.owner_user_id == "bob" for flow in oauth._PENDING_FLOWS.values()) == 1


# ---------------------------------------------------------------------------
# GitHub denial and failure, configuration, removed routes, reflection
# ---------------------------------------------------------------------------


def test_denied_consent_and_failed_exchange_store_nothing(app: FastAPI, github: _GitHub) -> None:
    alice = _browser(app, "alice")
    state, _ = _start(alice)
    denied = alice.get("/api/oauth/github/callback", params={"error": "access_denied", "state": state})
    assert denied.status_code == 200
    assert "not granted" in denied.text

    github.exchange_status = 500
    state, connection_id = _start(alice)
    failed = alice.get("/api/oauth/github/callback", params={"code": "alice-code", "state": state})
    assert failed.status_code == 502
    assert oauth._CONNECTIONS == {}
    assert alice.get("/api/oauth/github/ready", params={"connection_id": connection_id}).json() == {"ready": False}


def test_start_requires_configured_credentials(
    app: FastAPI, github: _GitHub, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_OAUTH_CLIENT_SECRET")
    alice = _browser(app, "alice")

    assert alice.get("/api/oauth/github/status").json() == {"configured": False}
    assert alice.post("/api/oauth/github/start").status_code == 503
    assert oauth._PENDING_FLOWS == {}


def test_redirect_uri_is_encoded_into_the_authorize_url(
    app: FastAPI, github: _GitHub, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_OAUTH_REDIRECT_URI", "https://app.example.test/api/oauth/github/callback?x=1&y=2")
    body = _browser(app, "alice").post("/api/oauth/github/start").json()
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(body["authorize_url"]).query)
    assert query["redirect_uri"] == ["https://app.example.test/api/oauth/github/callback?x=1&y=2"]


@pytest.mark.parametrize("app_env", [None, "development", "production"])
def test_dev_token_seeding_route_is_gone(
    app: FastAPI, github: _GitHub, monkeypatch: pytest.MonkeyPatch, app_env: str | None,
) -> None:
    if app_env is None:
        monkeypatch.delenv("APP_ENV", raising=False)
    else:
        monkeypatch.setenv("APP_ENV", app_env)

    for browser in (_browser(app), _browser(app, "alice")):
        response = browser.get("/api/oauth/github/_dev/complete", params={"session_key": "seeded"})
        assert response.status_code == 404
    assert oauth._CONNECTIONS == {}
    assert oauth.consume_github_token("seeded", user_id="alice") is None


def test_callback_reflects_nothing_from_the_request(app: FastAPI, github: _GitHub) -> None:
    payload = "</script><script>alert(document.domain)</script>"
    browser = _browser(app)

    for params in (
        {"error": "access_denied", "error_description": payload},
        {"error": payload},
        {"code": "x", "state": payload},
    ):
        response = browser.get("/api/oauth/github/callback", params=params)
        assert "alert(document.domain)" not in response.text, params
        assert response.headers["content-security-policy"] == "default-src 'none'; script-src 'unsafe-inline'"


def test_platform_host_mounts_only_the_bound_flow() -> None:
    routes = {
        (method, route.path)
        for route in oauth.router.routes
        for method in getattr(route, "methods", set())
    }
    assert routes == {
        ("GET", "/api/oauth/github/status"),
        ("POST", "/api/oauth/github/start"),
        ("GET", "/api/oauth/github/callback"),
        ("GET", "/api/oauth/github/ready"),
        ("GET", "/api/oauth/github/repos"),
    }


# ---------------------------------------------------------------------------
# ExistingAppDiscovery hands the token only to the owner's run
# ---------------------------------------------------------------------------


def _load_preload_module() -> Any:
    path = ROOT / "factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py"
    spec = importlib.util.spec_from_file_location("tests.oauth_github_preload_discovery", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_discovery_preload_consumes_only_the_runs_own_connection(
    app: FastAPI, github: _GitHub, monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection_id = _connect(_browser(app, "alice"), "alice-code")
    module = _load_preload_module()
    seen_tokens: list[str | None] = []

    class _NotFound:
        status_code = 404

        def json(self) -> dict[str, str]:
            return {"message": "Not Found"}

    async def _fake_github_request(url: str, token: str | None, **_: Any) -> _NotFound:
        seen_tokens.append(token)
        return _NotFound()

    monkeypatch.setattr(module, "_github_request", _fake_github_request)

    def _run(user_id: str) -> dict[str, Any]:
        context = factory_context({
            "user_id": user_id,
            "github_repo": "alice/private-repo",
            "github_oauth_session": connection_id,
        })
        asyncio.run(module.collect_prechat_discovery_context(context_variables=context))
        return context

    mallory_run = _run("mallory")
    assert set(seen_tokens) == {None}
    assert mallory_run["github_oauth_session"] is None
    assert connection_id in oauth._CONNECTIONS  # still the owner's

    seen_tokens.clear()
    alice_run = _run("alice")
    assert seen_tokens and set(seen_tokens) == {"token-for-alice-code"}
    assert alice_run["github_oauth_session"] is None
    assert connection_id not in oauth._CONNECTIONS
