"""The GitHub OAuth popup flow is bound to the user and browser that start it.

These tests drive the real router (``mozaiksai.hosts.routers.oauth_github``)
through FastAPI's TestClient with the real ``require_user`` dependency. Only
GitHub's HTTP endpoints and the auth adapter's token validation are faked.

Each TestClient is one browser: it keeps its own cookie jar, and sends the
bearer token of the person using it (or none). Browsers reach the host over
HTTPS unless a test says otherwise, because the binding cookie is ``Secure``
outside local development.

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
from mozaiksai.core.auth.anonymous_access import (
    ANONYMOUS_PROVENANCE,
    DEV_OVERRIDE_PROVENANCE,
    LOCAL_DEVELOPMENT_PROVENANCE,
)
from mozaiksai.core.auth.dependencies import UserPrincipal, require_user
from tests.factory_context import factory_context

ROOT = Path(__file__).resolve().parents[1]
AUTHORIZE_PREFIX = "https://github.com/login/oauth/authorize?"
SECURE_COOKIE = "__Host-mozaiks_github_oauth_binding"
LOCAL_COOKIE = "mozaiks_github_oauth_binding"


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
    for name in ("_PENDING_FLOWS", "_CONNECTIONS", "_FAILURES", "_STATE_STORE", "_TOKEN_STORE"):
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


def _browser(
    app: FastAPI,
    user: str | None = None,
    *,
    base_url: str = "https://testserver",
    client: tuple[str, int] = ("testclient", 50000),
    headers: dict[str, str] | None = None,
) -> TestClient:
    sent = {"Authorization": f"Bearer bearer-{user}"} if user else {}
    sent.update(headers or {})
    return TestClient(app, base_url=base_url, client=client, headers=sent, follow_redirects=False)


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


def _binding(browser: TestClient) -> tuple[str, str]:
    """The binding cookie this browser holds, as ``(name, value)``."""
    found = [(cookie.name, cookie.value) for cookie in browser.cookies.jar if cookie.name.endswith(LOCAL_COOKIE)]
    assert len(found) == 1, found
    name, value = found[0]
    assert value is not None
    return name, value


def _callback(browser: TestClient, code: str, state: str, **kwargs: Any) -> httpx.Response:
    return browser.get("/api/oauth/github/callback", params={"code": code, "state": state}, **kwargs)


def _ready(browser: TestClient, connection_id: str) -> Any:
    return browser.get("/api/oauth/github/ready", params={"connection_id": connection_id}).json()


def _set_cookie_attributes(response: httpx.Response) -> dict[str, str]:
    header = response.headers["set-cookie"]
    _, _, attributes = header.partition(";")
    parsed = {}
    for item in attributes.split(";"):
        key, _, value = item.strip().partition("=")
        parsed[key.lower()] = value
    return parsed


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
    assert cookie.startswith(f"{SECURE_COOKIE}=")
    attributes = _set_cookie_attributes(start)
    assert attributes["path"] == "/" and "secure" in attributes and "httponly" in attributes
    assert attributes["samesite"] == "lax" and "domain" not in attributes

    assert alice.get("/api/oauth/github/ready", params={"connection_id": connection_id}).json() == {"ready": False}

    callback = alice.get("/api/oauth/github/callback", params={"code": "alice-code", "state": state})
    assert callback.status_code == 200
    assert "GitHub connected" in callback.text
    assert "window.close" in callback.text  # success closes the popup
    cleared = callback.headers["set-cookie"]  # binding cleared, with the attributes __Host- requires
    assert cleared.startswith(f'{SECURE_COOKIE}=""') and "Path=/;" in cleared and "Secure" in cleared
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
    assert oauth.BINDING_REFUSED_MESSAGE in callback.text
    assert github.exchanges == []  # the victim's code is never exchanged
    # The attacker learns only that their own flow failed.
    assert _ready(mallory, connection_id) == {"ready": False, "error": oauth.BINDING_REFUSED_MESSAGE}
    assert mallory.get("/api/oauth/github/repos", params={"connection_id": connection_id}).status_code == 404
    assert oauth.consume_github_token(connection_id, user_id="mallory") is None
    # The state was spent: not even the attacker's own browser can use it now.
    replay = mallory.get("/api/oauth/github/callback", params={"code": "mallory-code", "state": state})
    assert replay.status_code == 400
    assert github.exchanges == []


# ---------------------------------------------------------------------------
# The binding cookie cannot be planted, repeated or borrowed
# ---------------------------------------------------------------------------


def test_victim_with_their_own_flow_cannot_complete_an_attackers_flow(app: FastAPI, github: _GitHub) -> None:
    """The victim's own binding cookie binds only the victim's own flow.

    The victim has started a flow, so their browser holds a valid binding
    cookie and their flow is still pending when GitHub redirects them with the
    attacker's state. Neither "a binding cookie is present" nor "it matches
    some pending flow" is enough.
    """
    victim = _browser(app, "victim")
    victim_state, victim_connection = _start(victim)
    mallory = _browser(app, "mallory")
    mallory_state, mallory_connection = _start(mallory)

    callback = _callback(victim, "victim-code", mallory_state)

    assert callback.status_code == 400
    assert oauth.BINDING_REFUSED_MESSAGE in callback.text
    assert github.exchanges == []
    assert oauth.consume_github_token(mallory_connection, user_id="mallory") is None
    assert mallory.get("/api/oauth/github/repos", params={"connection_id": mallory_connection}).status_code == 404
    # The refusal leaves the victim's own cookie and flow in place: they can still finish theirs.
    assert "set-cookie" not in callback.headers
    finished = _callback(victim, "victim-code", victim_state)
    assert finished.status_code == 200, finished.text
    assert oauth.consume_github_token(victim_connection, user_id="victim") == "token-for-victim-code"


@pytest.mark.parametrize("planted_path", ["/api/oauth/github", "/"])
def test_binding_planted_under_the_unprefixed_name_is_refused(
    app: FastAPI, github: _GitHub, planted_path: str,
) -> None:
    """A sibling subdomain, or an injected plain-http response, can set an
    unprefixed cookie in the victim's browser but not a ``__Host-`` one.

    The attacker starts a flow, plants its binding value under the unprefixed
    name, and sends the victim GitHub's authorize URL.
    """
    mallory = _browser(app, "mallory")
    state, connection_id = _start(mallory)
    _, planted = _binding(mallory)
    victim = _browser(app, "victim")
    victim.cookies.set(LOCAL_COOKIE, planted, path=planted_path)

    callback = _callback(victim, "victim-code", state)

    assert callback.status_code == 400
    assert github.exchanges == []
    assert oauth.consume_github_token(connection_id, user_id="mallory") is None
    assert _ready(mallory, connection_id) == {"ready": False, "error": oauth.BINDING_REFUSED_MESSAGE}


def test_a_repeated_binding_cookie_is_refused(app: FastAPI, github: _GitHub) -> None:
    """Starlette keeps only the last of several same-name cookies.

    A second cookie of the binding's name (under another Path, which is sent
    first, or another Domain, sent after it as the newer cookie) makes the
    request ambiguous, so the callback refuses it instead of picking one.
    """
    # The attacker's value after the victim's own: last-value-wins would pick the attacker's.
    victim = _browser(app, "victim")
    _start(victim)
    name, own = _binding(victim)
    mallory = _browser(app, "mallory")
    mallory_state, mallory_connection = _start(mallory)
    _, planted = _binding(mallory)

    callback = _callback(
        _browser(app), "victim-code", mallory_state, headers={"Cookie": f"{name}={own}; {name}={planted}"},
    )

    assert callback.status_code == 400
    assert github.exchanges == []
    assert oauth.consume_github_token(mallory_connection, user_id="mallory") is None

    # A same-name cookie under a more specific path beside the owner's own: refused although one matches.
    alice = _browser(app, "alice")
    state, connection_id = _start(alice)
    name, _ = _binding(alice)
    alice.cookies.set(name, "planted-value", path="/api/oauth/github/callback")

    refused = _callback(alice, "alice-code", state)

    assert refused.status_code == 400
    assert github.exchanges == []
    assert oauth.consume_github_token(connection_id, user_id="alice") is None


def test_a_refused_binding_is_reported_to_the_page_that_started_the_flow(app: FastAPI, github: _GitHub) -> None:
    """The likely honest cause: the page and the callback are on different
    addresses (page on localhost, callback on 127.0.0.1), so the cookie stayed
    behind. The popup keeps the reason on screen, and the page that polls
    /ready learns it; nobody else does.
    """
    alice = _browser(app, "alice")
    state, connection_id = _start(alice)
    other_address = _browser(app)  # alice's browser, but a host that never received the cookie

    callback = _callback(other_address, "alice-code", state)

    assert callback.status_code == 400
    assert oauth.BINDING_REFUSED_MESSAGE in callback.text
    assert "different address" in oauth.BINDING_REFUSED_MESSAGE and "127.0.0.1" in oauth.BINDING_REFUSED_MESSAGE
    assert "window.close" not in callback.text
    assert _ready(alice, connection_id) == {"ready": False, "error": oauth.BINDING_REFUSED_MESSAGE}
    assert _ready(_browser(app, "mallory"), connection_id) == {"ready": False}


def _start_as(app: FastAPI, provenance: str, **browser: Any) -> httpx.Response:
    if provenance == "token":
        client = _browser(app, "alice", **browser)
    else:
        app.dependency_overrides[require_user] = lambda: _principal("anonymous", provenance)
        client = _browser(app, **browser)
    response = client.post("/api/oauth/github/start")
    assert response.status_code == 200, response.text
    return response


_LOCAL: dict[str, Any] = {"base_url": "http://localhost:8000", "client": ("127.0.0.1", 51000)}


@pytest.mark.parametrize(
    ("provenance", "browser"),
    [
        # A signed-in user behind a TLS-terminating proxy: the app itself sees http.
        pytest.param("token", {"base_url": "http://app.example.test"}, id="token-behind-tls-proxy"),
        # A signed-in user on this machine: request facts alone never select the plain cookie.
        pytest.param("token", _LOCAL, id="token-from-this-machine"),
        # Development access for a client that is not this machine (AUTH_ANON_ACCESS=open, a container).
        pytest.param(
            LOCAL_DEVELOPMENT_PROVENANCE, {**_LOCAL, "client": ("172.17.0.1", 51000)}, id="open-remote-client",
        ),
        # Development access, but the request came through a proxy.
        pytest.param(
            LOCAL_DEVELOPMENT_PROVENANCE, {**_LOCAL, "headers": {"X-Forwarded-For": "203.0.113.9"}}, id="forwarded",
        ),
        # A dev persona, but a page on another site sent the request.
        pytest.param(
            DEV_OVERRIDE_PROVENANCE, {**_LOCAL, "headers": {"Origin": "https://attacker.example"}}, id="foreign-origin",
        ),
    ],
)
def test_the_binding_cookie_is_host_prefixed_outside_local_development(
    app: FastAPI, github: _GitHub, provenance: str, browser: dict[str, Any],
) -> None:
    start = _start_as(app, provenance, **browser)

    assert start.headers["set-cookie"].startswith(f"{SECURE_COOKIE}=")
    attributes = _set_cookie_attributes(start)
    assert attributes["path"] == "/" and "secure" in attributes and "domain" not in attributes


def test_local_development_uses_the_unprefixed_cookie_over_http(app: FastAPI, github: _GitHub) -> None:
    """WebKit can drop a Secure cookie set over http://localhost, so a flow
    started in local development (authentication off, development access, a
    request from this machine, here through the Vite proxy) uses the
    unprefixed cookie, scoped to the OAuth routes. GitHub's redirect is a
    cross-site navigation, so the callback is not judged local itself: the
    flow remembers which cookie it set.
    """
    app.dependency_overrides[require_user] = lambda: _principal("anonymous", LOCAL_DEVELOPMENT_PROVENANCE)
    developer = _browser(app, **_LOCAL, headers={"Origin": "http://localhost:3000"})

    start = developer.post("/api/oauth/github/start")
    assert start.status_code == 200, start.text
    assert start.headers["set-cookie"].startswith(f"{LOCAL_COOKIE}=")
    attributes = _set_cookie_attributes(start)
    assert attributes["path"] == "/api/oauth/github" and "secure" not in attributes and "httponly" in attributes
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(start.json()["authorize_url"]).query)

    callback = _callback(developer, "dev-code", query["state"][0], headers={"Sec-Fetch-Site": "cross-site"})

    assert callback.status_code == 200, callback.text
    assert callback.headers["set-cookie"].startswith(f'{LOCAL_COOKIE}=""')
    assert oauth.consume_github_token(start.json()["connection_id"], user_id="anonymous") == "token-for-dev-code"


def test_each_flow_reads_only_the_cookie_name_it_set(app: FastAPI, github: _GitHub) -> None:
    app.dependency_overrides[require_user] = lambda: _principal("anonymous", LOCAL_DEVELOPMENT_PROVENANCE)
    developer = _browser(app, **_LOCAL)
    start = developer.post("/api/oauth/github/start")
    assert start.status_code == 200, start.text
    state = urllib.parse.parse_qs(urllib.parse.urlsplit(start.json()["authorize_url"]).query)["state"][0]
    _, value = _binding(developer)

    renamed = _callback(_browser(app, **_LOCAL), "dev-code", state, headers={"Cookie": f"{SECURE_COOKIE}={value}"})

    assert renamed.status_code == 400
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
    name, cookie = _binding(alice)
    assert alice.get("/api/oauth/github/callback", params={"code": "alice-code", "state": state}).status_code == 200

    alice.cookies.set(name, cookie, path="/")
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
    assert (oauth._STATE_TTL, oauth._TOKEN_TTL) == (600, 300)  # the documented lifetimes

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

    assert "window.close" not in denied.text  # an error stays readable

    github.exchange_status = 500
    state, connection_id = _start(alice)
    failed = alice.get("/api/oauth/github/callback", params={"code": "alice-code", "state": state})
    assert failed.status_code == 502
    assert "window.close" not in failed.text
    assert oauth._CONNECTIONS == {}
    assert alice.get("/api/oauth/github/ready", params={"connection_id": connection_id}).json() == {
        "ready": False,
        "error": "GitHub token exchange failed. Start the connection again.",
    }


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
        assert response.headers["content-security-policy"] == "default-src 'none'; style-src 'unsafe-inline'"
        assert "<script" not in response.text, params


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

    def _run(user_id: str, discovery_inputs: dict[str, Any] | None = None) -> dict[str, Any]:
        context = factory_context({
            "user_id": user_id,
            "github_repo": "alice/private-repo",
            "github_oauth_session": connection_id,
            "discovery_inputs": discovery_inputs or {},
        })
        asyncio.run(module.collect_prechat_discovery_context(context_variables=context))
        return context

    mallory_run = _run("mallory")
    assert set(seen_tokens) == {None}
    assert mallory_run["github_oauth_session"] is None
    assert connection_id in oauth._CONNECTIONS  # still the owner's

    # discovery_inputs is caller-supplied: a user id named there is not the run's identity.
    _run("mallory", {"user_id": "alice", "github_oauth_session": connection_id})
    assert set(seen_tokens) == {None}
    assert connection_id in oauth._CONNECTIONS

    seen_tokens.clear()
    alice_run = _run("alice")
    assert seen_tokens and set(seen_tokens) == {"token-for-alice-code"}
    assert alice_run["github_oauth_session"] is None
    assert connection_id not in oauth._CONNECTIONS
