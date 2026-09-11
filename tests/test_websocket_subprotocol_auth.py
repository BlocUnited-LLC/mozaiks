"""
Production-safe browser WebSocket authentication.

Browsers cannot set request headers on a WebSocket handshake, so the shared browser
client used to append `?access_token=<jwt>` to the URL — which the runtime rejects by
default (`MOZAIKS_WS_ALLOW_QUERY_TOKEN=false`) because URL-borne tokens leak into access
logs, browser history, and shared links. The credential now travels in the
`Sec-WebSocket-Protocol` handshake header instead.

Covers:
  Transport extraction:
    - base64url credential in the bearer subprotocol is decoded
    - marker without a credential value is rejected
    - malformed base64url is refused, not repaired
    - unrelated subprotocols are ignored
    - credential is never echoed as the selected subprotocol

  Strict credential encoding (the credential must be canonical unpadded base64url):
    - trailing garbage, illegal characters, whitespace, `=` padding, impossible
      lengths, standard-base64 `+`/`/`, empty values, invalid UTF-8, and
      non-canonical trailing bits are all refused before the auth adapter runs
    - refusal carries its own bounded reason, distinct from a missing credential
    - a malformed subprotocol never falls through to the query-token path
    - the same matrix is re-proven over a real FastAPI/Starlette handshake, with
      the adapter asserted never to have authenticated a recovered bearer

  Authentication through the configured adapter:
    - subprotocol token validates through get_auth_adapter() and binds WebSocketUser
    - missing credential closes 1008 before accept
    - invalid credential closes 1008 before accept
    - query-string token stays rejected by default
    - query-string token still honored under the development opt-in
    - auth-disabled local development still connects

  Scope binding preserved (authenticate_websocket_with_path_binding):
    - wrong user_id closes 1008
    - another app's bound identity does not become this app
    - wrong chat_id closes 1008
    - matching user/app/chat is accepted

  Log hygiene:
    - no token material in log records on success or failure

  Browser proof (real chat-ui source executed under Node):
    - websocketAuth.js encodes the credential the server decodes
    - api.js builds a WebSocket URL with no bearer token and passes the credential
      as a subprotocol instead
    - runtimeBridge.js emits a credential-free URL
"""
from __future__ import annotations

import base64
import json
import logging
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI, WebSocket

from mozaiksai.core.auth.adapters import AuthError, UserClaims
from mozaiksai.core.auth.adapters.base import BaseAuthAdapter
from mozaiksai.core.auth.adapters.registry import (
    register_adapter,
    reset_auth_adapter,
)
from mozaiksai.core.auth.websocket_auth import (
    WS_BEARER_SUBPROTOCOL,
    WS_CLOSE_POLICY_VIOLATION,
    WS_REASON_MALFORMED_CREDENTIAL,
    MalformedBearerCredential,
    accept_websocket,
    authenticate_websocket,
    authenticate_websocket_with_path_binding,
    extract_subprotocol_bearer_token,
    negotiated_subprotocol,
)

ROOT = Path(__file__).resolve().parents[1]

VALID_TOKEN = "header.payload.signature"
OTHER_APP_TOKEN = "other-app-token"


def encode_credential(token: str) -> str:
    """Mirror of the browser encoder: base64url, unpadded."""
    return base64.urlsafe_b64encode(token.encode("utf-8")).decode("ascii").rstrip("=")


class _FakeWebSocket:
    """Minimal WebSocket double exposing the surface the auth boundary touches."""

    def __init__(self, *, subprotocols: list[str] | None = None, query_params: dict | None = None) -> None:
        self.scope: dict = {"subprotocols": list(subprotocols or [])}
        self.query_params: dict = dict(query_params or {})
        self.state = type("_State", (), {})()
        self.accepted = False
        self.accepted_subprotocol: str | None = None
        self.closed: list[tuple[int | None, str | None]] = []

    async def accept(self, subprotocol: str | None = None) -> None:
        self.accepted = True
        self.accepted_subprotocol = subprotocol

    async def close(self, code: int | None = None, reason: str | None = None) -> None:
        self.closed.append((code, reason))


def browser_socket(token: str | None = VALID_TOKEN) -> _FakeWebSocket:
    """A handshake shaped exactly as the shared browser client sends it."""
    if token is None:
        return _FakeWebSocket()
    return _FakeWebSocket(subprotocols=[WS_BEARER_SUBPROTOCOL, encode_credential(token)])


class _StubAdapter(BaseAuthAdapter):
    """Stands in for a configured IdP adapter; only VALID_TOKEN/OTHER_APP_TOKEN verify."""

    name = "test-stub"

    async def validate_token(self, token: str) -> UserClaims:
        if token == VALID_TOKEN:
            return UserClaims(
                user_id="user-1",
                email="user-1@example.com",
                name="User One",
                roles=["member"],
                scopes=["access_as_user"],
                raw_claims={"sub": "user-1", "app_id": "app-1", "chat_id": "chat-1", "exp": 4102444800},
                provider="test-stub",
                app_id="app-1",
                chat_id="chat-1",
            )
        if token == OTHER_APP_TOKEN:
            return UserClaims(
                user_id="user-1",
                scopes=["access_as_user"],
                raw_claims={"sub": "user-1", "app_id": "app-2"},
                provider="test-stub",
                app_id="app-2",
                chat_id="chat-1",
            )
        raise AuthError("Invalid token", status_code=401, provider="test-stub")

    def validate_token_sync(self, token: str) -> UserClaims:  # pragma: no cover - unused
        raise NotImplementedError

    def is_enabled(self) -> bool:
        return True


@pytest.fixture
def auth_enabled(monkeypatch: pytest.MonkeyPatch):
    """Configure the runtime with a real registered adapter under the canonical registry."""
    register_adapter("test-stub", _StubAdapter, config_identity="test-stub-v1")
    monkeypatch.setenv("AUTH_PROVIDER", "test-stub")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("MOZAIKS_WS_ALLOW_QUERY_TOKEN", raising=False)
    reset_auth_adapter()
    yield
    reset_auth_adapter()


@pytest.fixture
def auth_disabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_PROVIDER", "none")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("MOZAIKS_WS_ALLOW_QUERY_TOKEN", raising=False)
    reset_auth_adapter()
    yield
    reset_auth_adapter()


# ---------------------------------------------------------------------------
# 1. Transport extraction
# ---------------------------------------------------------------------------

class TestSubprotocolExtraction:
    def test_decodes_base64url_credential(self):
        assert extract_subprotocol_bearer_token(browser_socket()) == VALID_TOKEN

    def test_decodes_credential_containing_url_unsafe_bytes(self):
        token = "a+b/c=d?e&f"
        socket = _FakeWebSocket(subprotocols=[WS_BEARER_SUBPROTOCOL, encode_credential(token)])
        assert extract_subprotocol_bearer_token(socket) == token

    def test_marker_without_credential_is_rejected(self):
        socket = _FakeWebSocket(subprotocols=[WS_BEARER_SUBPROTOCOL])
        assert extract_subprotocol_bearer_token(socket) is None

    def test_malformed_base64url_raises_rather_than_returning_none(self):
        """Malformed encoding is a hard refusal, not an absent credential."""
        socket = _FakeWebSocket(subprotocols=[WS_BEARER_SUBPROTOCOL, "!!!not-base64!!!"])
        with pytest.raises(MalformedBearerCredential):
            extract_subprotocol_bearer_token(socket)

    def test_unrelated_subprotocols_are_ignored(self):
        socket = _FakeWebSocket(subprotocols=["graphql-ws", "json"])
        assert extract_subprotocol_bearer_token(socket) is None
        assert negotiated_subprotocol(socket) is None

    def test_no_subprotocols_offered(self):
        assert extract_subprotocol_bearer_token(_FakeWebSocket()) is None

    def test_websocket_without_scope_attribute_is_tolerated(self):
        class _Bare:
            pass

        assert extract_subprotocol_bearer_token(_Bare()) is None  # type: ignore[arg-type]


class TestAcceptNegotiation:
    @pytest.mark.asyncio
    async def test_accept_echoes_marker_only_never_the_credential(self):
        socket = browser_socket()
        await accept_websocket(socket)  # type: ignore[arg-type]
        assert socket.accepted is True
        assert socket.accepted_subprotocol == WS_BEARER_SUBPROTOCOL
        assert encode_credential(VALID_TOKEN) != socket.accepted_subprotocol
        assert VALID_TOKEN not in str(socket.accepted_subprotocol)

    @pytest.mark.asyncio
    async def test_accept_selects_nothing_when_client_offered_nothing(self):
        socket = _FakeWebSocket()
        await accept_websocket(socket)  # type: ignore[arg-type]
        assert socket.accepted is True
        assert socket.accepted_subprotocol is None


# ---------------------------------------------------------------------------
# 2. Authentication through the configured adapter
# ---------------------------------------------------------------------------

class TestAuthenticateThroughAdapter:
    @pytest.mark.asyncio
    async def test_subprotocol_token_authenticates_and_binds_user(self, auth_enabled):
        socket = browser_socket()
        user = await authenticate_websocket(socket)  # type: ignore[arg-type]

        assert user is not None
        assert user.user_id == "user-1"
        assert user.provider == "test-stub"
        assert user.app_id == "app-1"
        assert socket.state.user_id == "user-1"
        assert socket.state.user is user
        assert socket.closed == []

    @pytest.mark.asyncio
    async def test_missing_credential_is_rejected(self, auth_enabled):
        socket = _FakeWebSocket()
        assert await authenticate_websocket(socket) is None  # type: ignore[arg-type]
        assert socket.accepted is False
        assert socket.closed == [(WS_CLOSE_POLICY_VIOLATION, "Missing access_token")]

    @pytest.mark.asyncio
    async def test_invalid_credential_is_rejected(self, auth_enabled):
        socket = browser_socket("forged-token")
        assert await authenticate_websocket(socket) is None  # type: ignore[arg-type]
        assert socket.accepted is False
        assert socket.closed[0][0] == WS_CLOSE_POLICY_VIOLATION

    @pytest.mark.asyncio
    async def test_malformed_credential_is_refused_with_its_own_reason(self, auth_enabled):
        socket = _FakeWebSocket(subprotocols=[WS_BEARER_SUBPROTOCOL, "!!!"])
        assert await authenticate_websocket(socket) is None  # type: ignore[arg-type]
        assert socket.accepted is False
        assert socket.closed == [
            (WS_CLOSE_POLICY_VIOLATION, WS_REASON_MALFORMED_CREDENTIAL)
        ]

    @pytest.mark.asyncio
    async def test_explicit_access_token_argument_still_wins(self, auth_enabled):
        socket = _FakeWebSocket()
        user = await authenticate_websocket(socket, access_token=VALID_TOKEN)  # type: ignore[arg-type]
        assert user is not None and user.user_id == "user-1"


class TestQueryTokenDisposition:
    @pytest.mark.asyncio
    async def test_query_token_still_rejected_by_default(self, auth_enabled):
        socket = _FakeWebSocket(query_params={"access_token": VALID_TOKEN})
        assert await authenticate_websocket(socket) is None  # type: ignore[arg-type]
        assert socket.closed == [(WS_CLOSE_POLICY_VIOLATION, "Missing access_token")]

    @pytest.mark.asyncio
    async def test_query_token_honored_under_development_opt_in(
        self, auth_enabled, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("MOZAIKS_WS_ALLOW_QUERY_TOKEN", "true")
        socket = _FakeWebSocket(query_params={"access_token": VALID_TOKEN})
        user = await authenticate_websocket(socket)  # type: ignore[arg-type]
        assert user is not None and user.user_id == "user-1"

    @pytest.mark.asyncio
    async def test_subprotocol_needs_no_opt_in(self, auth_enabled, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MOZAIKS_WS_ALLOW_QUERY_TOKEN", "false")
        user = await authenticate_websocket(browser_socket())  # type: ignore[arg-type]
        assert user is not None and user.user_id == "user-1"


class TestAuthDisabledDevelopment:
    @pytest.mark.asyncio
    async def test_local_development_without_auth_still_connects(self, auth_disabled):
        socket = _FakeWebSocket()
        user = await authenticate_websocket(socket)  # type: ignore[arg-type]
        assert user is not None
        assert socket.closed == []

    @pytest.mark.asyncio
    async def test_path_binding_bypass_intact_when_auth_disabled(self, auth_disabled):
        socket = _FakeWebSocket()
        user = await authenticate_websocket_with_path_binding(
            socket, path_user_id="dev-user", path_app_id="app-1", path_chat_id="chat-1"  # type: ignore[arg-type]
        )
        assert user is not None and user.user_id == "dev-user"
        assert socket.closed == []


# ---------------------------------------------------------------------------
# 3. Scope binding preserved
# ---------------------------------------------------------------------------

class TestScopeBinding:
    @pytest.mark.asyncio
    async def test_matching_user_app_chat_is_accepted(self, auth_enabled):
        socket = browser_socket()
        user = await authenticate_websocket_with_path_binding(
            socket, path_user_id="user-1", path_app_id="app-1", path_chat_id="chat-1"  # type: ignore[arg-type]
        )
        assert user is not None and user.user_id == "user-1"
        assert socket.closed == []

    @pytest.mark.asyncio
    async def test_wrong_user_is_rejected(self, auth_enabled):
        socket = browser_socket()
        user = await authenticate_websocket_with_path_binding(
            socket, path_user_id="user-2", path_app_id="app-1", path_chat_id="chat-1"  # type: ignore[arg-type]
        )
        assert user is None
        assert socket.closed == [(WS_CLOSE_POLICY_VIOLATION, "user_id mismatch")]

    @pytest.mark.asyncio
    async def test_another_apps_bound_identity_does_not_become_this_app(self, auth_enabled):
        """A token bound to app-2 cannot open a socket scoped to app-1."""
        socket = browser_socket(OTHER_APP_TOKEN)
        user = await authenticate_websocket_with_path_binding(
            socket, path_user_id="user-1", path_app_id="app-1", path_chat_id="chat-1"  # type: ignore[arg-type]
        )
        assert user is None
        assert socket.closed == [(WS_CLOSE_POLICY_VIOLATION, "app_id mismatch")]
        assert socket.accepted is False

    @pytest.mark.asyncio
    async def test_wrong_chat_is_rejected(self, auth_enabled):
        socket = browser_socket()
        user = await authenticate_websocket_with_path_binding(
            socket, path_user_id="user-1", path_app_id="app-1", path_chat_id="chat-9"  # type: ignore[arg-type]
        )
        assert user is None
        assert socket.closed == [(WS_CLOSE_POLICY_VIOLATION, "chat_id mismatch")]


# ---------------------------------------------------------------------------
# 4. Log hygiene
# ---------------------------------------------------------------------------

class TestLogHygiene:
    @pytest.mark.asyncio
    async def test_no_token_material_in_logs(self, auth_enabled, caplog: pytest.LogCaptureFixture):
        secret = "super.secret.jwt"
        encoded = encode_credential(secret)
        caplog.set_level(logging.DEBUG)

        await authenticate_websocket(browser_socket(secret))  # type: ignore[arg-type]
        await authenticate_websocket(_FakeWebSocket(subprotocols=[WS_BEARER_SUBPROTOCOL, "!!!"]))  # type: ignore[arg-type]
        await authenticate_websocket(browser_socket())  # type: ignore[arg-type]

        # Guard against a vacuous assertion: the boundary must actually have logged.
        assert caplog.records, "expected auth logging to be captured"

        emitted = "\n".join(record.getMessage() for record in caplog.records)
        assert secret not in emitted
        assert encoded not in emitted
        assert VALID_TOKEN not in emitted


# ---------------------------------------------------------------------------
# 5. Browser proof — real chat-ui source executed under Node
# ---------------------------------------------------------------------------

def run_node(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        cwd=ROOT, text=True, capture_output=True, check=False, timeout=60,
    )


WS_AUTH_MODULE = (ROOT / "chat-ui/src/adapters/websocketAuth.js").as_uri()


class TestBrowserTransport:
    def test_browser_encoder_round_trips_through_the_server_decoder(self):
        """The real browser module's output is what the server decodes."""
        script = f"""
import assert from 'node:assert/strict';
import {{ buildWebSocketAuthProtocols, WS_BEARER_SUBPROTOCOL }} from '{WS_AUTH_MODULE}';

const protocols = buildWebSocketAuthProtocols('{VALID_TOKEN}');
assert.equal(protocols.length, 2);
assert.equal(protocols[0], WS_BEARER_SUBPROTOCOL);
assert.notEqual(protocols[1], '{VALID_TOKEN}');
assert.match(protocols[1], /^[A-Za-z0-9_-]+$/, 'credential must be an unpadded base64url header token');

// No token means no protocols, so auth-disabled local dev still connects.
assert.deepEqual(buildWebSocketAuthProtocols(null), []);
assert.deepEqual(buildWebSocketAuthProtocols(''), []);
assert.deepEqual(buildWebSocketAuthProtocols(undefined), []);

console.log(JSON.stringify(protocols));
"""
        result = run_node(script)
        assert result.returncode == 0, result.stdout + result.stderr

        marker, credential = json.loads(result.stdout.strip().splitlines()[-1])
        assert marker == WS_BEARER_SUBPROTOCOL

        socket = _FakeWebSocket(subprotocols=[marker, credential])
        assert extract_subprotocol_bearer_token(socket) == VALID_TOKEN

    def test_api_adapter_puts_no_bearer_token_in_the_websocket_url(self):
        """Executes the production URL/socket construction lines from api.js."""
        source = (ROOT / "chat-ui/src/adapters/api.js").read_text(encoding="utf-8")
        start = source.index("    const wsBase = this.getWsBaseUrl();")
        end = source.index("openAuthenticatedWebSocket(wsUrl.toString(), getAccessToken(this.config));")
        body = source[start:end] + "openAuthenticatedWebSocket(wsUrl.toString(), getAccessToken(this.config));"

        script = f"""
import assert from 'node:assert/strict';
import {{ openAuthenticatedWebSocket, WS_BEARER_SUBPROTOCOL }} from '{WS_AUTH_MODULE}';

const opened = [];
globalThis.WebSocket = class {{
  constructor(url, protocols) {{ opened.push({{ url, protocols }}); this.readyState = 0; }}
  static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
}};

const getAccessToken = () => '{VALID_TOKEN}';

function construct(appId, userId, actualworkflowname, chatId, options) {{
  const self = {{
    getWsBaseUrl: () => 'wss://app.example.com',
    _chatConnections: new Map(),
    config: {{}},
  }};
  return (function () {{
{body}
    return socket;
  }}).call(self);
}}

construct('app-1', 'user-1', 'AppGenerator', 'chat-1', {{ suppressHistoryReplay: true }});
assert.equal(opened.length, 1);
const {{ url, protocols }} = opened[0];

// The credential is not in the URL, in any form.
assert.ok(!url.includes('access_token'), url);
assert.ok(!url.includes('{VALID_TOKEN}'), url);
assert.ok(!url.includes(encodeURIComponent('{VALID_TOKEN}')), url);
assert.equal(new URL(url).searchParams.get('access_token'), null);

// Non-credential query state is preserved.
assert.equal(new URL(url).searchParams.get('suppress_history_replay'), '1');
assert.equal(new URL(url).pathname, '/ws/AppGenerator/app-1/chat-1/user-1');
assert.equal(new URL(url).protocol, 'wss:');

// The credential rides the handshake subprotocol instead.
assert.equal(protocols[0], WS_BEARER_SUBPROTOCOL);
assert.equal(protocols.length, 2);
console.log(JSON.stringify(protocols[1]));
"""
        result = run_node(script)
        assert result.returncode == 0, result.stdout + result.stderr

        credential = json.loads(result.stdout.strip().splitlines()[-1])
        socket = _FakeWebSocket(subprotocols=[WS_BEARER_SUBPROTOCOL, credential])
        assert extract_subprotocol_bearer_token(socket) == VALID_TOKEN

    def test_runtime_bridge_url_carries_no_credential(self):
        source = (ROOT / "chat-ui/src/runtimeBridge.js").read_text(encoding="utf-8")
        assert "access_token" not in source
        assert "searchParams.set('access_token'" not in source

    def test_no_shared_browser_surface_puts_a_token_in_a_websocket_url(self):
        """Regression fence: the URL-token transport must not come back."""
        surfaces = [
            "chat-ui/src/adapters/api.js",
            "chat-ui/src/runtimeBridge.js",
            "chat-ui/src/adapters/websocketAuth.js",
            "factory_app/workflows/AppGenerator/ui/useSandbox.js",
        ]
        for relative in surfaces:
            source = (ROOT / relative).read_text(encoding="utf-8")
            assert "searchParams.set('access_token'" not in source, relative
            assert "access_token=" not in source, relative

    def test_studio_sandbox_import_resolves_through_the_chat_ui_alias(self):
        """`@mozaiks/chat-ui` aliases to chat-ui/src (web_shell/vite.config.js)."""
        source = (ROOT / "factory_app/workflows/AppGenerator/ui/useSandbox.js").read_text(encoding="utf-8")
        assert "from '@mozaiks/chat-ui/adapters/websocketAuth.js'" in source
        assert (ROOT / "chat-ui/src/adapters/websocketAuth.js").is_file()
        assert "'@mozaiks/chat-ui': chatUiSrcRoot" in (
            ROOT / "web_shell/vite.config.js"
        ).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 6. Real ASGI handshake — proves the scope/accept seam, not a double
# ---------------------------------------------------------------------------

class TestRealAsgiHandshake:
    """Runs the boundary against a real FastAPI app over a real WebSocket handshake.

    The doubles above assume `scope["subprotocols"]` carries the offer and that
    accepting with a subprotocol completes negotiation. This proves both against the
    actual server stack.
    """

    @staticmethod
    def _app():
        app = FastAPI()

        @app.websocket("/ws/{app_id}/{chat_id}/{user_id}")
        async def endpoint(websocket: WebSocket, app_id: str, chat_id: str, user_id: str):
            user = await authenticate_websocket_with_path_binding(
                websocket,
                path_user_id=user_id,
                path_app_id=app_id,
                path_chat_id=chat_id,
            )
            if user is None:
                return
            await accept_websocket(websocket)
            await websocket.send_json({"user_id": user.user_id, "app_id": user.app_id})
            await websocket.close()

        return app

    def test_browser_handshake_authenticates_and_negotiates(self, auth_enabled):
        from starlette.testclient import TestClient

        client = TestClient(self._app())
        with client.websocket_connect(
            "/ws/app-1/chat-1/user-1",
            subprotocols=[WS_BEARER_SUBPROTOCOL, encode_credential(VALID_TOKEN)],
        ) as websocket:
            assert websocket.accepted_subprotocol == WS_BEARER_SUBPROTOCOL
            assert websocket.receive_json() == {"user_id": "user-1", "app_id": "app-1"}

    def test_missing_credential_is_refused_by_the_real_server(self, auth_enabled):
        from starlette.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect

        client = TestClient(self._app())
        with pytest.raises(WebSocketDisconnect) as excinfo:  # noqa: PT012
            with client.websocket_connect("/ws/app-1/chat-1/user-1") as websocket:
                websocket.receive_json()
        assert excinfo.value.code == WS_CLOSE_POLICY_VIOLATION
        assert excinfo.value.reason == "Missing access_token"

    def test_invalid_credential_is_refused_by_the_real_server(self, auth_enabled):
        from starlette.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect

        client = TestClient(self._app())
        with pytest.raises(WebSocketDisconnect) as excinfo:  # noqa: PT012
            with client.websocket_connect(
                "/ws/app-1/chat-1/user-1",
                subprotocols=[WS_BEARER_SUBPROTOCOL, encode_credential("forged")],
            ) as websocket:
                websocket.receive_json()
        assert excinfo.value.code == WS_CLOSE_POLICY_VIOLATION
        assert excinfo.value.reason == "Invalid token"

    def test_another_apps_identity_is_refused_by_the_real_server(self, auth_enabled):
        from starlette.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect

        client = TestClient(self._app())
        with pytest.raises(WebSocketDisconnect) as excinfo:  # noqa: PT012
            with client.websocket_connect(
                "/ws/app-1/chat-1/user-1",
                subprotocols=[WS_BEARER_SUBPROTOCOL, encode_credential(OTHER_APP_TOKEN)],
            ) as websocket:
                websocket.receive_json()
        assert excinfo.value.code == WS_CLOSE_POLICY_VIOLATION
        assert excinfo.value.reason == "app_id mismatch"

    def test_query_token_is_refused_by_the_real_server_without_opt_in(self, auth_enabled):
        from starlette.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect

        client = TestClient(self._app())
        with pytest.raises(WebSocketDisconnect) as excinfo:  # noqa: PT012
            with client.websocket_connect(
                f"/ws/app-1/chat-1/user-1?access_token={VALID_TOKEN}"
            ) as websocket:
                websocket.receive_json()
        assert excinfo.value.code == WS_CLOSE_POLICY_VIOLATION
        assert excinfo.value.reason == "Missing access_token"


# ---------------------------------------------------------------------------
# 7. Strict credential encoding
# ---------------------------------------------------------------------------

# Malformed representations that must never reach the auth adapter. Each pairs a
# label with a factory turning a canonical encoding into the malformed form.
MALFORMED_CREDENTIALS = [
    ("trailing garbage", lambda c: c + "!!!!"),
    ("single illegal character", lambda c: c + "!"),
    ("leading illegal character", lambda c: "!" + c),
    ("embedded space", lambda c: c[:4] + " " + c[4:]),
    ("surrounding whitespace", lambda c: "  " + c + "  "),
    ("embedded tab", lambda c: c[:4] + "\t" + c[4:]),
    ("trailing newline", lambda c: c + "\n"),
    ("explicit padding", lambda c: c + "="),
    ("impossible length", lambda c: c + "A"),
    ("standard-base64 plus", lambda c: c[:4] + "+" + c[5:]),
    ("standard-base64 slash", lambda c: c[:4] + "/" + c[5:]),
    ("empty credential", lambda c: ""),
]

MALFORMED_IDS = [label for label, _ in MALFORMED_CREDENTIALS]

# HTTP list framing (RFC 7230) strips optional whitespace *around* each element of
# `Sec-WebSocket-Protocol`, so these two forms are unrepresentable on a real wire: the
# value the client actually transmits is already the canonical one. They are still
# refused at our boundary, which is what the _FakeWebSocket matrix above proves.
WIRE_UNREPRESENTABLE = {"surrounding whitespace", "trailing newline"}

ON_WIRE_MALFORMED = [
    (label, mangle)
    for label, mangle in MALFORMED_CREDENTIALS
    if label not in WIRE_UNREPRESENTABLE
]
ON_WIRE_MALFORMED_IDS = [label for label, _ in ON_WIRE_MALFORMED]


class TestStrictCredentialEncoding:
    """
    The credential after `mozaiks.bearer.v1` must be canonical unpadded base64url.

    Python's base64 silently *discards* characters outside the alphabet by default, so
    a lenient decoder turns base64url(token) + trailing garbage back into a usable
    token. That is a transport defect even though the recovered bearer still had to be
    valid: the transport must refuse a malformed representation, never repair one.
    """

    @pytest.mark.parametrize("label,mangle", MALFORMED_CREDENTIALS, ids=MALFORMED_IDS)
    def test_malformed_representation_is_refused(self, label: str, mangle) -> None:
        mangled = mangle(encode_credential(VALID_TOKEN))
        socket = _FakeWebSocket(subprotocols=[WS_BEARER_SUBPROTOCOL, mangled])
        with pytest.raises(MalformedBearerCredential):
            extract_subprotocol_bearer_token(socket)

    def test_noncanonical_trailing_bits_are_refused(self) -> None:
        # "QR" decodes to the same byte as canonical "QQ" only because the unused
        # trailing bits are ignored, so only the canonical form may be accepted.
        assert extract_subprotocol_bearer_token(
            _FakeWebSocket(subprotocols=[WS_BEARER_SUBPROTOCOL, "QQ"])
        ) == "A"
        with pytest.raises(MalformedBearerCredential):
            extract_subprotocol_bearer_token(
                _FakeWebSocket(subprotocols=[WS_BEARER_SUBPROTOCOL, "QR"])
            )

    def test_invalid_utf8_after_decoding_is_refused(self) -> None:
        undecodable = base64.urlsafe_b64encode(b"\xff\xfe\xfd").decode("ascii").rstrip("=")
        with pytest.raises(MalformedBearerCredential):
            extract_subprotocol_bearer_token(
                _FakeWebSocket(subprotocols=[WS_BEARER_SUBPROTOCOL, undecodable])
            )

    def test_canonical_encoding_round_trips_including_multibyte(self) -> None:
        for token in (VALID_TOKEN, "a+b/c=d?e&f", "tok-é中\U0001f600", "A"):
            socket = _FakeWebSocket(
                subprotocols=[WS_BEARER_SUBPROTOCOL, encode_credential(token)]
            )
            assert extract_subprotocol_bearer_token(socket) == token

    @pytest.mark.asyncio
    async def test_malformed_never_falls_through_to_the_query_token(
        self, auth_enabled, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Even with the dev opt-in on, a malformed subprotocol refuses outright."""
        monkeypatch.setenv("MOZAIKS_WS_ALLOW_QUERY_TOKEN", "true")
        socket = _FakeWebSocket(
            subprotocols=[WS_BEARER_SUBPROTOCOL, encode_credential(VALID_TOKEN) + "!!!!"],
            query_params={"access_token": VALID_TOKEN},
        )
        assert await authenticate_websocket(socket) is None  # type: ignore[arg-type]
        assert socket.accepted is False
        assert socket.closed == [(WS_CLOSE_POLICY_VIOLATION, WS_REASON_MALFORMED_CREDENTIAL)]

    def test_no_credential_material_in_logs_on_malformed_refusal(
        self, auth_enabled, caplog: pytest.LogCaptureFixture
    ) -> None:
        secret = "super.secret.jwt"
        encoded = encode_credential(secret)
        caplog.set_level(logging.DEBUG)

        socket = _FakeWebSocket(subprotocols=[WS_BEARER_SUBPROTOCOL, encoded + "!!!!"])
        with pytest.raises(MalformedBearerCredential):
            extract_subprotocol_bearer_token(socket)

        assert caplog.records, "expected a refusal to be logged"
        emitted = "\n".join(record.getMessage() for record in caplog.records)
        assert secret not in emitted
        assert encoded not in emitted


class TestStrictEncodingOverRealHandshake:
    """The same matrix over a real FastAPI/Starlette handshake, not a double."""

    @staticmethod
    def _app(seen: list[str]):
        app = FastAPI()

        @app.websocket("/ws/{app_id}/{chat_id}/{user_id}")
        async def endpoint(websocket: WebSocket, app_id: str, chat_id: str, user_id: str):
            user = await authenticate_websocket_with_path_binding(
                websocket,
                path_user_id=user_id,
                path_app_id=app_id,
                path_chat_id=chat_id,
            )
            if user is None:
                return
            seen.append(user.user_id)
            await accept_websocket(websocket)
            await websocket.send_json({"user_id": user.user_id})
            await websocket.close()

        return app

    def _connect(self, subprotocols, seen):
        from starlette.testclient import TestClient

        client = TestClient(self._app(seen))
        return client.websocket_connect("/ws/app-1/chat-1/user-1", subprotocols=subprotocols)

    def test_canonical_credential_is_accepted(self, auth_enabled) -> None:
        """Positive control: the canonical encoding still authenticates end to end."""
        seen: list[str] = []
        with self._connect([WS_BEARER_SUBPROTOCOL, encode_credential(VALID_TOKEN)], seen) as ws:
            assert ws.accepted_subprotocol == WS_BEARER_SUBPROTOCOL
            assert ws.receive_json() == {"user_id": "user-1"}
        assert seen == ["user-1"]

    def test_canonical_plus_trailing_garbage_is_refused(self, auth_enabled) -> None:
        """The exact reproduction: canonical encoding followed by trailing garbage."""
        from starlette.websockets import WebSocketDisconnect

        seen: list[str] = []
        mangled = encode_credential(VALID_TOKEN) + "!!!!"
        with pytest.raises(WebSocketDisconnect) as excinfo:  # noqa: PT012
            with self._connect([WS_BEARER_SUBPROTOCOL, mangled], seen) as ws:
                ws.receive_json()

        assert excinfo.value.code == WS_CLOSE_POLICY_VIOLATION
        assert excinfo.value.reason == WS_REASON_MALFORMED_CREDENTIAL
        # The adapter must never have authenticated the recovered underlying bearer.
        assert seen == []

    @pytest.mark.parametrize("label,mangle", ON_WIRE_MALFORMED, ids=ON_WIRE_MALFORMED_IDS)
    def test_malformed_matrix_is_refused_over_the_wire(
        self, auth_enabled, label: str, mangle
    ) -> None:
        from starlette.websockets import WebSocketDisconnect

        seen: list[str] = []
        mangled = mangle(encode_credential(VALID_TOKEN))
        with pytest.raises(WebSocketDisconnect) as excinfo:  # noqa: PT012
            with self._connect([WS_BEARER_SUBPROTOCOL, mangled], seen) as ws:
                ws.receive_json()

        assert excinfo.value.code == WS_CLOSE_POLICY_VIOLATION
        assert seen == [], label + " authenticated a recovered bearer"

    def test_invalid_utf8_is_refused_over_the_wire(self, auth_enabled) -> None:
        from starlette.websockets import WebSocketDisconnect

        seen: list[str] = []
        undecodable = base64.urlsafe_b64encode(b"\xff\xfe\xfd").decode("ascii").rstrip("=")
        with pytest.raises(WebSocketDisconnect) as excinfo:  # noqa: PT012
            with self._connect([WS_BEARER_SUBPROTOCOL, undecodable], seen) as ws:
                ws.receive_json()

        assert excinfo.value.code == WS_CLOSE_POLICY_VIOLATION
        assert excinfo.value.reason == WS_REASON_MALFORMED_CREDENTIAL
        assert seen == []

    @pytest.mark.parametrize("label", sorted(WIRE_UNREPRESENTABLE))
    def test_surrounding_whitespace_is_http_framing_not_a_credential(
        self, auth_enabled, label: str
    ) -> None:
        """
        OWS around a list element is removed by HTTP before our boundary sees it.

        The client therefore transmitted a canonical credential and is accepted. This
        is correct RFC 7230 framing, not the transport normalizing a malformed value —
        our boundary still refuses whitespace when it is presented directly, which
        TestStrictCredentialEncoding asserts.
        """
        mangle = dict(MALFORMED_CREDENTIALS)[label]
        canonical = encode_credential(VALID_TOKEN)
        seen: list[str] = []

        with self._connect([WS_BEARER_SUBPROTOCOL, mangle(canonical)], seen) as ws:
            assert ws.receive_json() == {"user_id": "user-1"}
        assert seen == ["user-1"]

        # Presented directly to the boundary, the same value is refused.
        with pytest.raises(MalformedBearerCredential):
            extract_subprotocol_bearer_token(
                _FakeWebSocket(subprotocols=[WS_BEARER_SUBPROTOCOL, mangle(canonical)])
            )

    def test_query_token_development_compatibility_is_unchanged(
        self, auth_enabled, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The dev opt-in still works over a real handshake, and stays off by default."""
        from starlette.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect

        seen: list[str] = []
        client = TestClient(self._app(seen))
        url = "/ws/app-1/chat-1/user-1?access_token=" + VALID_TOKEN

        with pytest.raises(WebSocketDisconnect) as excinfo:  # noqa: PT012
            with client.websocket_connect(url) as ws:
                ws.receive_json()
        assert excinfo.value.reason == "Missing access_token"
        assert seen == []

        monkeypatch.setenv("MOZAIKS_WS_ALLOW_QUERY_TOKEN", "true")
        with client.websocket_connect(url) as ws:
            assert ws.receive_json() == {"user_id": "user-1"}
        assert seen == ["user-1"]
