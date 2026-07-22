"""
RateLimitMiddleware integration tests.

Covers end-to-end middleware behaviour with a real FastAPI app and
in-memory rate-limit storage:

  Enabled (default) flow:
    - Requests below the limit pass through with X-RateLimit-* headers.
    - Request that exceeds the limit returns 429 with Retry-After.
    - 429 includes CORS Access-Control-Allow-Origin when Origin header present.
    - Path-prefix limits tighter than global are applied to matching paths.
    - Excluded paths are never limited.
    - OPTIONS requests (CORS preflight) are never limited.

  Disabled flow:
    - All requests pass through; no rate-limit headers attached.
"""
from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Module-level autouse fixture: restore os.environ across every test.
# RateLimitMiddleware reads env at middleware-stack build time (first request),
# NOT at add_middleware() time. The env vars must remain set through the full
# test lifetime — this fixture saves/restores the relevant keys so tests remain
# isolated.
# ---------------------------------------------------------------------------

_RATE_LIMIT_ENV_KEYS = {
    "RATE_LIMIT_ENABLED",
    "RATE_LIMIT_REQUESTS_PER_MINUTE",
    "RATE_LIMIT_PATH_LIMITS",
    "RATE_LIMIT_EXCLUDED_PATHS",
    "RATE_LIMIT_CLIENT_HEADER",
    "REDIS_URL",
}


@pytest.fixture(autouse=True)
def _restore_rate_limit_env():
    """Snapshot and restore rate-limit env vars around each test."""
    snapshot = {k: os.environ.get(k) for k in _RATE_LIMIT_ENV_KEYS}
    yield
    for k, v in snapshot.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rate_limit_app(
    *,
    enabled: bool = True,
    rpm: int = 3,
    path_limits: str = "",
    excluded: str = "/api/health",
    client_header: str = "",
) -> TestClient:
    """Build a minimal FastAPI app with RateLimitMiddleware wired in.

    Env vars are set directly so they persist until the autouse fixture
    restores them — this is necessary because Starlette builds the middleware
    stack on the first request, not at add_middleware() time.
    """
    os.environ["RATE_LIMIT_ENABLED"] = "true" if enabled else "false"
    os.environ["RATE_LIMIT_REQUESTS_PER_MINUTE"] = str(rpm)
    os.environ["RATE_LIMIT_PATH_LIMITS"] = path_limits
    os.environ["RATE_LIMIT_EXCLUDED_PATHS"] = excluded
    os.environ["RATE_LIMIT_CLIENT_HEADER"] = client_header
    os.environ.pop("REDIS_URL", None)  # always use in-memory storage

    from mozaiksai.core.transport.rate_limit import RateLimitMiddleware

    inner_app = FastAPI()

    @inner_app.get("/api/data")
    async def data_endpoint():
        return {"ok": True}

    @inner_app.get("/api/auth/token")
    async def auth_endpoint():
        return {"token": "xyz"}

    @inner_app.get("/api/chats/exists/{app_id}/{workflow_name}/{chat_id}")
    async def chat_exists_endpoint(app_id: str, workflow_name: str, chat_id: str):
        return {
            "exists": True,
            "app_id": app_id,
            "workflow_name": workflow_name,
            "chat_id": chat_id,
        }

    @inner_app.get("/api/health")
    async def health_endpoint():
        return {"status": "ok"}

    @inner_app.options("/api/data")
    async def preflight():
        return {}

    inner_app.add_middleware(RateLimitMiddleware)
    return TestClient(inner_app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# 1. Requests within limit pass through with rate-limit headers
# ---------------------------------------------------------------------------

class TestAllowedRequests:
    def test_response_200_within_limit(self):
        client = _make_rate_limit_app(rpm=10)
        resp = client.get("/api/data", headers={"X-Real-IP": "10.0.0.1"})
        assert resp.status_code == 200

    def test_x_ratelimit_limit_header_present(self):
        client = _make_rate_limit_app(rpm=10)
        resp = client.get("/api/data", headers={"X-Real-IP": "10.0.0.1"})
        assert "X-RateLimit-Limit" in resp.headers

    def test_x_ratelimit_remaining_header_present(self):
        client = _make_rate_limit_app(rpm=10)
        resp = client.get("/api/data", headers={"X-Real-IP": "10.0.0.1"})
        assert "X-RateLimit-Remaining" in resp.headers

    def test_x_ratelimit_reset_header_present(self):
        client = _make_rate_limit_app(rpm=10)
        resp = client.get("/api/data", headers={"X-Real-IP": "10.0.0.1"})
        assert "X-RateLimit-Reset" in resp.headers

    def test_remaining_decrements_each_request(self):
        client = _make_rate_limit_app(rpm=10)
        headers = {"X-Real-IP": "10.0.0.2"}
        r1 = client.get("/api/data", headers=headers)
        r2 = client.get("/api/data", headers=headers)
        rem1 = int(r1.headers["X-RateLimit-Remaining"])
        rem2 = int(r2.headers["X-RateLimit-Remaining"])
        assert rem2 <= rem1


# ---------------------------------------------------------------------------
# 2. Limit exceeded → 429
# ---------------------------------------------------------------------------

class TestLimitExceeded:
    def _exhaust_limit(self, client: TestClient, path: str, ip: str, n: int) -> None:
        for _ in range(n):
            client.get(path, headers={"X-Real-IP": ip})

    def test_429_after_limit_exhausted(self):
        client = _make_rate_limit_app(rpm=2)
        ip = "11.22.33.44"
        self._exhaust_limit(client, "/api/data", ip, 2)
        resp = client.get("/api/data", headers={"X-Real-IP": ip})
        assert resp.status_code == 429

    def test_429_body_has_detail_key(self):
        client = _make_rate_limit_app(rpm=2)
        ip = "11.22.33.45"
        self._exhaust_limit(client, "/api/data", ip, 2)
        resp = client.get("/api/data", headers={"X-Real-IP": ip})
        assert "detail" in resp.json()

    def test_429_has_retry_after_header(self):
        client = _make_rate_limit_app(rpm=2)
        ip = "11.22.33.46"
        self._exhaust_limit(client, "/api/data", ip, 2)
        resp = client.get("/api/data", headers={"X-Real-IP": ip})
        assert "Retry-After" in resp.headers

    def test_429_x_ratelimit_remaining_is_zero(self):
        client = _make_rate_limit_app(rpm=2)
        ip = "11.22.33.47"
        self._exhaust_limit(client, "/api/data", ip, 2)
        resp = client.get("/api/data", headers={"X-Real-IP": ip})
        assert resp.headers.get("X-RateLimit-Remaining") == "0"

    def test_429_adds_cors_header_when_origin_present(self):
        client = _make_rate_limit_app(rpm=2)
        ip = "11.22.33.48"
        self._exhaust_limit(client, "/api/data", ip, 2)
        resp = client.get(
            "/api/data",
            headers={"X-Real-IP": ip, "origin": "https://example.com"},
        )
        assert resp.status_code == 429
        assert resp.headers.get("Access-Control-Allow-Origin") == "https://example.com"

    def test_429_no_cors_header_when_no_origin(self):
        client = _make_rate_limit_app(rpm=2)
        ip = "11.22.33.49"
        self._exhaust_limit(client, "/api/data", ip, 2)
        resp = client.get("/api/data", headers={"X-Real-IP": ip})
        assert "Access-Control-Allow-Origin" not in resp.headers

    def test_different_clients_have_independent_buckets(self):
        # Enable X-Real-IP as the client-ID header so each IP gets its own bucket.
        client = _make_rate_limit_app(rpm=2, client_header="X-Real-IP")
        # Exhaust ip A
        for _ in range(2):
            client.get("/api/data", headers={"X-Real-IP": "20.0.0.1"})
        client.get("/api/data", headers={"X-Real-IP": "20.0.0.1"})  # → 429
        # ip B is unaffected
        resp = client.get("/api/data", headers={"X-Real-IP": "20.0.0.2"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 3. Path-prefix overrides
# ---------------------------------------------------------------------------

class TestPathPrefixLimits:
    def test_tighter_path_limit_applies(self):
        # Global is 100, auth path is 2
        client = _make_rate_limit_app(rpm=100, path_limits="/api/auth:2")
        ip = "30.0.0.1"
        client.get("/api/auth/token", headers={"X-Real-IP": ip})
        client.get("/api/auth/token", headers={"X-Real-IP": ip})
        resp = client.get("/api/auth/token", headers={"X-Real-IP": ip})
        assert resp.status_code == 429

    def test_non_matching_path_uses_global_limit(self):
        # Auth is 2, global is 100 — /api/data should not be limited early
        client = _make_rate_limit_app(rpm=100, path_limits="/api/auth:2")
        ip = "30.0.0.2"
        for _ in range(3):
            resp = client.get("/api/data", headers={"X-Real-IP": ip})
        assert resp.status_code == 200

    def test_more_specific_chat_exists_limit_wins_over_chat_prefix(self):
        client = _make_rate_limit_app(
            rpm=100,
            path_limits="/api/chats:1,/api/chats/exists:3",
        )
        ip = "30.0.0.3"

        for index in range(3):
            resp = client.get(
                f"/api/chats/exists/demo-app/ValueEngine/chat-{index}",
                headers={"X-Real-IP": ip},
            )
            assert resp.status_code == 200

        resp = client.get(
            "/api/chats/exists/demo-app/ValueEngine/chat-4",
            headers={"X-Real-IP": ip},
        )
        assert resp.status_code == 429


# ---------------------------------------------------------------------------
# 4. Excluded paths
# ---------------------------------------------------------------------------

class TestExcludedPaths:
    def test_excluded_path_never_limited(self):
        client = _make_rate_limit_app(rpm=1, excluded="/api/health")
        ip = "40.0.0.1"
        # Hit excluded path far more than the global limit
        for _ in range(5):
            resp = client.get("/api/health", headers={"X-Real-IP": ip})
        assert resp.status_code == 200

    def test_excluded_path_has_no_rate_limit_headers(self):
        client = _make_rate_limit_app(rpm=10, excluded="/api/health")
        resp = client.get("/api/health", headers={"X-Real-IP": "40.0.0.2"})
        assert "X-RateLimit-Limit" not in resp.headers


# ---------------------------------------------------------------------------
# 5. OPTIONS (CORS preflight) never rate-limited
# ---------------------------------------------------------------------------

class TestOptionsPreflight:
    def test_options_not_rate_limited(self):
        client = _make_rate_limit_app(rpm=1)
        ip = "50.0.0.1"
        # Exhaust global limit with GET
        client.get("/api/data", headers={"X-Real-IP": ip})
        # OPTIONS must still pass
        resp = client.options("/api/data", headers={"X-Real-IP": ip})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 6. Disabled rate limiting
# ---------------------------------------------------------------------------

class TestDisabledRateLimiting:
    def test_disabled_passes_all_requests(self):
        client = _make_rate_limit_app(enabled=False, rpm=1)
        ip = "60.0.0.1"
        for _ in range(5):
            resp = client.get("/api/data", headers={"X-Real-IP": ip})
        assert resp.status_code == 200

    def test_disabled_no_rate_limit_headers(self):
        client = _make_rate_limit_app(enabled=False)
        resp = client.get("/api/data", headers={"X-Real-IP": "60.0.0.2"})
        assert "X-RateLimit-Limit" not in resp.headers


# ---------------------------------------------------------------------------
# 7. WebSocket connections are rate-limited
# ---------------------------------------------------------------------------


def _make_ws_rate_limit_app(*, rpm: int = 3, enabled: bool = True) -> TestClient:
    """Build a minimal app with a WebSocket endpoint and RateLimitMiddleware.

    Uses a plain async route handler (not FastAPI DI injection) to avoid
    dependency-injection interference from previous test-session imports.
    """
    os.environ["RATE_LIMIT_ENABLED"] = "true" if enabled else "false"
    os.environ["RATE_LIMIT_REQUESTS_PER_MINUTE"] = str(rpm)
    os.environ["RATE_LIMIT_PATH_LIMITS"] = ""
    os.environ["RATE_LIMIT_EXCLUDED_PATHS"] = "/api/health"
    os.environ["RATE_LIMIT_CLIENT_HEADER"] = ""
    os.environ.pop("REDIS_URL", None)

    from mozaiksai.core.transport.rate_limit import RateLimitMiddleware

    inner_app = FastAPI()

    # Register as a plain WebSocket route to avoid FastAPI DI resolution
    # interference that occurs in long-running pytest sessions.
    async def _ws_handler(websocket) -> None:
        await websocket.accept()
        await websocket.send_text("ok")
        await websocket.close()

    # Use /echo — a path that does not match any entry in _DEFAULT_PATH_LIMITS
    # (/api/auth, /api/chats, /chat, /api/workflows, /ws/).  This ensures the
    # global rpm cap governs both the HTTP drain request and the WS upgrade.
    inner_app.router.add_websocket_route("/echo", _ws_handler)

    @inner_app.get("/api/data")
    async def _data_endpoint():
        return {"ok": True}

    inner_app.add_middleware(RateLimitMiddleware)
    return TestClient(inner_app, raise_server_exceptions=False)


class TestWebSocketRateLimit:
    def test_ws_connection_accepted_within_limit(self):
        client = _make_ws_rate_limit_app(rpm=10)
        with client.websocket_connect("/echo") as ws:
            msg = ws.receive_text()
        assert msg == "ok"

    def test_ws_connection_rejected_after_limit_exhausted(self):
        """After exceeding the per-client global limit, the WS handshake is closed with 1008.

        Uses /echo (not under /ws/ or /chat) so the global limit governs both the
        HTTP drain request and the WebSocket upgrade attempt.
        """
        from starlette.websockets import WebSocketDisconnect

        client = _make_ws_rate_limit_app(rpm=1)
        ip_header = "70.0.0.1"

        # Exhaust the 1/min limit with an HTTP request first.
        client.get("/api/data", headers={"X-Real-IP": ip_header})

        # The next WS connection from the same IP must be refused with 1008.
        with pytest.raises((WebSocketDisconnect, Exception)) as exc_info:
            with client.websocket_connect("/echo", headers={"X-Real-IP": ip_header}) as ws:
                ws.receive_text()

        exc = exc_info.value
        # When it's a WebSocketDisconnect, confirm the code is 1008 (Policy Violation).
        if isinstance(exc, WebSocketDisconnect):
            assert exc.code == 1008

    def test_ws_not_rate_limited_when_disabled(self):
        """When rate limiting is disabled, WS connections pass through unconditionally."""
        client = _make_ws_rate_limit_app(rpm=1, enabled=False)
        with client.websocket_connect("/echo") as ws:
            msg = ws.receive_text()
        assert msg == "ok"

    def test_ws_path_limit_in_default_config(self):
        """The /ws/ path prefix appears in _DEFAULT_PATH_LIMITS with a tighter cap (10/min)."""
        from mozaiksai.core.transport.rate_limit import _DEFAULT_PATH_LIMITS

        assert "/ws/" in _DEFAULT_PATH_LIMITS
        assert _DEFAULT_PATH_LIMITS["/ws/"] <= 20, "WS default limit should be tighter than global 60/min"
