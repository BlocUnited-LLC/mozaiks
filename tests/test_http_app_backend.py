"""Offline HTTP behavior for the reusable app-backend transport."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from mozaiksai.core.adapters import circuit_breaker
from mozaiksai.core.adapters import http_app_backend as adapter_module
from mozaiksai.core.adapters.http_app_backend import HttpAppBackendAdapter


@pytest.fixture(autouse=True)
def isolated_breakers(monkeypatch):
    monkeypatch.setattr(circuit_breaker, "_breakers", {})
    monkeypatch.setenv("CIRCUIT_BREAKER_FAILURE_THRESHOLD", "2")
    monkeypatch.setenv("CIRCUIT_BREAKER_RECOVERY_TIMEOUT", "3600")
    monkeypatch.delenv("INTERNAL_API_KEY", raising=False)


@contextmanager
def _http_server(respond):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            request = {
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers),
                "body": self.rfile.read(int(self.headers.get("Content-Length", "0"))),
            }
            requests.append(request)
            status, headers, body = respond(request)
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_POST = do_GET
        do_DELETE = do_GET

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def http_server():
    with ExitStack() as stack:
        yield lambda respond: stack.enter_context(_http_server(respond))


def _json_response(status, data):
    return status, {"Content-Type": "application/json"}, json.dumps(data).encode()


def _proxy_env(monkeypatch, proxy_url):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, proxy_url)
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")


@pytest.mark.asyncio
async def test_private_transport_bypasses_environment_proxy(http_server, monkeypatch):
    origin, origin_requests = http_server(lambda _: _json_response(207, {"status": "degraded"}))
    proxy, proxy_requests = http_server(lambda _: _json_response(200, {"via": "proxy"}))
    _proxy_env(monkeypatch, proxy)
    adapter = HttpAppBackendAdapter(origin, "test-private-key", trust_env=False)

    response = await adapter.request("GET", "/health", headers={"X-Actor-Id": "operator-a"})

    assert response.success and response.status_code == 207
    assert response.data == {"status": "degraded"}
    assert proxy_requests == []
    assert len(origin_requests) == 1
    assert origin_requests[0]["headers"]["X-Internal-API-Key"] == "test-private-key"
    assert origin_requests[0]["headers"]["X-Actor-Id"] == "operator-a"


@pytest.mark.asyncio
async def test_environment_proxy_remains_the_default(http_server, monkeypatch):
    origin, origin_requests = http_server(lambda _: _json_response(200, {"via": "origin"}))
    proxy, proxy_requests = http_server(lambda _: _json_response(200, {"via": "proxy"}))
    _proxy_env(monkeypatch, proxy)

    response = await HttpAppBackendAdapter(origin).request("GET", "/health")

    assert response.data == {"via": "proxy"}
    assert origin_requests == []
    assert proxy_requests[0]["path"] == f"{origin}/health"


@pytest.mark.asyncio
async def test_redirect_cannot_forward_private_headers(http_server):
    destination, destination_requests = http_server(lambda _: _json_response(200, {}))
    origin, origin_requests = http_server(lambda _: (302, {"Location": destination}, b"redirect"))

    response = await HttpAppBackendAdapter(
        origin, "test-private-key", trust_env=False,
    ).request("GET", "/redirect", headers={"X-Actor-Id": "operator-a"})

    assert not response.success and response.status_code == 302
    assert len(origin_requests) == 1
    assert destination_requests == []


@pytest.mark.asyncio
async def test_failed_upstream_cannot_open_another_upstream_circuit(http_server):
    failing_url, failed_requests = http_server(lambda _: _json_response(503, {"error": "down"}))
    healthy_url, healthy_requests = http_server(lambda _: _json_response(200, {"ok": True}))
    failing = HttpAppBackendAdapter(failing_url, trust_env=False)
    healthy = HttpAppBackendAdapter(healthy_url, trust_env=False)

    for _ in range(2):
        assert (await failing.request("GET", "/health")).error == "backend_unavailable"
    assert (await failing.request("GET", "/health")).error == "backend_circuit_open"
    # A second adapter to that same upstream must not bypass its open circuit.
    same_upstream = HttpAppBackendAdapter(f"{failing_url}/", "another-key", trust_env=False)
    assert (await same_upstream.request("GET", "/different-path")).error == "backend_circuit_open"

    response = await healthy.request("GET", "/health")

    assert response.success and response.data == {"ok": True}
    assert len(failed_requests) == 2
    assert len(healthy_requests) == 1


def _mock_transport(monkeypatch, handler):
    client_type = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def client_factory(**kwargs):
        return client_type(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)


@pytest.mark.asyncio
async def test_circuit_identity_normalizes_origin_and_keeps_backend_prefix(monkeypatch):
    received = []

    def respond(request):
        received.append(request)
        return httpx.Response(503, json={"error": "down"})

    _mock_transport(monkeypatch, respond)
    first = HttpAppBackendAdapter("http://LOCALHOST:80/backend/", trust_env=False)
    same = HttpAppBackendAdapter("http://localhost/backend", trust_env=False)
    different = HttpAppBackendAdapter("http://localhost/other-backend", trust_env=False)
    for _ in range(2):
        await first.request("GET", "/health")

    assert (await same.request("GET", "/health")).error == "backend_circuit_open"
    assert (await different.request("GET", "/health")).error == "backend_unavailable"
    assert len(received) == 3


@pytest.mark.asyncio
async def test_private_timeout_and_caller_headers_remain_request_scoped(monkeypatch):
    received = []

    def respond(request):
        received.append(request)
        return httpx.Response(200, json={"ok": True})

    _mock_transport(monkeypatch, respond)
    monkeypatch.setenv("INTERNAL_API_KEY", "ambient-test-key")
    adapter = HttpAppBackendAdapter(
        "http://localhost:9999", "", timeout=15.0, connect_timeout=3.0, trust_env=False,
    )
    await asyncio.gather(
        adapter.request("POST", "/read", headers={"X-Actor-Id": "first"}, user_token="first-token"),
        adapter.request("POST", "/read", headers={"X-Actor-Id": "second"}),
    )
    await adapter.request("GET", "/health")

    by_actor = {request.headers.get("x-actor-id"): request for request in received}
    assert by_actor["first"].headers["authorization"] == "Bearer first-token"
    assert "authorization" not in by_actor["second"].headers
    assert "authorization" not in by_actor[None].headers
    for request in received:
        assert "x-internal-api-key" not in request.headers
        assert request.extensions["timeout"] == {"connect": 3.0, "read": 15.0, "write": 15.0, "pool": 15.0}


@pytest.mark.asyncio
async def test_default_timeout_and_ambient_key_remain_supported(monkeypatch):
    received = []

    def respond(request):
        received.append(request)
        return httpx.Response(200, json={})

    _mock_transport(monkeypatch, respond)
    monkeypatch.setenv("INTERNAL_API_KEY", "ambient-test-key")
    await HttpAppBackendAdapter("http://localhost:9999").request("GET", "/health")

    assert received[0].headers["x-internal-api-key"] == "ambient-test-key"
    assert received[0].extensions["timeout"] == {"connect": 10.0, "read": 30.0, "write": 30.0, "pool": 30.0}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 503])
async def test_error_protocol_is_preserved_without_logging_response_body(monkeypatch, caplog, status):
    sentinel = "test-upstream-private-response"
    _mock_transport(monkeypatch, lambda _: httpx.Response(status, text=sentinel))
    response = await HttpAppBackendAdapter("http://localhost:9999").request("GET", "/read")

    assert not response.success
    if status == 400:
        assert response.status_code == 400
        assert response.error == f"HTTP 400: {sentinel}"
    else:
        assert response.error == "backend_unavailable"
    assert sentinel not in caplog.text


@pytest.mark.asyncio
async def test_transport_errors_do_not_log_private_url_or_exception_text(monkeypatch, caplog):
    def fail(request):
        raise httpx.ConnectError("test-private-transport-message", request=request)

    _mock_transport(monkeypatch, fail)
    adapter = HttpAppBackendAdapter("http://localhost:9999/test-private-prefix")
    for _ in range(3):
        response = await adapter.request("GET", "/read?token=test-private-query")

    assert response.error == "backend_circuit_open"
    for secret in ("test-private-transport-message", "test-private-prefix", "test-private-query"):
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_non_json_protocol_preserved_without_logging_url_or_body(monkeypatch, caplog):
    _mock_transport(monkeypatch, lambda _: httpx.Response(200, text="test-private-body"))
    response = await HttpAppBackendAdapter("http://localhost:9999").request(
        "GET", "/read?token=test-private-query",
    )

    assert response.success and response.data == {"raw": "test-private-body"}
    assert "test-private-query" not in caplog.text
    assert "test-private-body" not in caplog.text


@pytest.mark.asyncio
async def test_deeply_nested_json_preserves_raw_response_fallback(monkeypatch):
    depth = sys.getrecursionlimit() + 100
    body = "[" * depth + "0" + "]" * depth
    # Establish that this exercises parser recursion, not ordinary invalid JSON.
    with pytest.raises(RecursionError):
        json.loads(body)
    _mock_transport(monkeypatch, lambda _: httpx.Response(200, text=body))

    response = await HttpAppBackendAdapter("http://localhost:9999").request("GET", "/read")

    assert response.success and response.status_code == 200
    assert response.data == {"raw": body}


@pytest.mark.asyncio
@pytest.mark.parametrize("strict_json", [False, True])
@pytest.mark.parametrize("status,data", [
    (200, {"ok": True}),
    (201, {"created": "item"}),
    (200, [{"id": "a"}, {"id": "b"}]),
    (201, []),
    (200, {"raw": "valid JSON field, not a parse failure"}),
    (200, None),
    (200, "text"),
    (200, 42),
    (200, 1.25),
    (200, False),
    (207, {"status": "degraded"}),
])
async def test_successful_json_response_values(http_server, status, data, strict_json):
    origin, requests = http_server(lambda _: _json_response(status, data))

    response = await HttpAppBackendAdapter(
        origin, trust_env=False, strict_json=strict_json,
    ).request("GET", "/read")

    assert response.success and response.status_code == status
    assert response.data == data
    assert type(response.data) is type(data)
    assert response.error is None
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"", b"not-json", b'{"broken":', b"\xff", b"NaN", b"Infinity", b"-Infinity", b'{"value":NaN}'])
async def test_strict_json_rejects_invalid_success_safely(http_server, caplog, body):
    origin, requests = http_server(lambda _: (200, {}, body))

    response = await HttpAppBackendAdapter(
        origin, trust_env=False, strict_json=True,
    ).request("GET", "/read?token=test-private-query")

    assert not response.success and response.status_code == 200
    assert response.error == "backend_invalid_response"
    assert response.data is None
    assert "test-private-query" not in caplog.text
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_strict_json_rejects_excessive_nesting(monkeypatch):
    depth = sys.getrecursionlimit() + 100
    body = "[" * depth + "0" + "]" * depth
    with pytest.raises(RecursionError):
        json.loads(body)
    _mock_transport(monkeypatch, lambda _: httpx.Response(201, text=body))

    response = await HttpAppBackendAdapter(
        "http://localhost:9999", strict_json=True,
    ).request("GET", "/read")

    assert not response.success and response.status_code == 201
    assert response.error == "backend_invalid_response" and response.data is None


@pytest.mark.asyncio
@pytest.mark.parametrize("strict_json,expected_data", [(False, {"raw": ""}), (True, None)])
async def test_empty_204_success_preserves_default_and_supports_strict_mode(http_server, strict_json, expected_data):
    origin, requests = http_server(lambda _: (204, {}, b""))

    response = await HttpAppBackendAdapter(
        origin, trust_env=False, strict_json=strict_json,
    ).request("DELETE", "/items/a")

    assert response.success and response.status_code == 204
    assert response.data == expected_data and response.error is None
    assert len(requests) == 1 and requests[0]["method"] == "DELETE"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 426, 429, 500, 502, 503, 504])
async def test_http_failures_preserve_actual_status(http_server, status, caplog):
    origin, requests = http_server(lambda _: (status, {}, b"test-private-error-body"))

    response = await HttpAppBackendAdapter(
        origin, trust_env=False, strict_json=True,
    ).request("GET", "/read")

    assert not response.success and response.status_code == status
    assert response.data is None
    assert response.error == (
        "backend_unavailable" if status >= 500 else f"HTTP {status}: test-private-error-body"
    )
    assert "test-private-error-body" not in caplog.text
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [307, 308])
async def test_post_redirect_never_replays_body_or_private_headers(http_server, monkeypatch, status):
    destination, destination_requests = http_server(lambda _: _json_response(201, {}))
    origin, origin_requests = http_server(lambda _: (status, {"Location": destination}, b""))
    proxy, proxy_requests = http_server(lambda _: _json_response(200, {}))
    _proxy_env(monkeypatch, proxy)

    response = await HttpAppBackendAdapter(
        origin, "test-private-key", trust_env=False, strict_json=True,
    ).request("POST", "/items", json_body={"private": "body"}, headers={"X-Actor-Id": "actor"})

    assert not response.success and response.status_code == status
    assert len(origin_requests) == 1
    assert json.loads(origin_requests[0]["body"]) == {"private": "body"}
    assert destination_requests == [] and proxy_requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "DELETE"])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("failure", ["http_503", "connect", "read_timeout"])
async def test_mutation_failure_is_one_attempt_with_or_without_breaker(monkeypatch, method, enabled, failure):
    requests = []

    def fail(request):
        requests.append(request)
        if failure == "http_503":
            return httpx.Response(503, text="test-private-error")
        if failure == "connect":
            raise httpx.ConnectError("test-private-error", request=request)
        raise httpx.ReadTimeout("test-private-error", request=request)

    _mock_transport(monkeypatch, fail)
    response = await HttpAppBackendAdapter(
        "http://localhost:9999", strict_json=True, circuit_breaker_enabled=enabled,
    ).request(method, "/items/a", json_body={"id": "a"})

    assert not response.success and response.error == "backend_unavailable"
    assert response.status_code == (503 if failure == "http_503" else 0)
    assert len(requests) == 1 and requests[0].method == method
    if not enabled:
        assert circuit_breaker._breakers == {}


@pytest.mark.asyncio
async def test_disabled_breaker_bypasses_registry_and_existing_open_circuit(http_server, monkeypatch):
    origin, requests = http_server(lambda _: _json_response(503, {"error": "unavailable"}))
    enabled = HttpAppBackendAdapter(origin, trust_env=False)
    for _ in range(2):
        assert (await enabled.request("GET", "/read")).status_code == 503
    assert (await enabled.request("GET", "/read")).error == "backend_circuit_open"
    assert len(requests) == 2

    def forbidden_registry_access(_name):
        pytest.fail("Disabled circuit protection must not consult the shared registry")

    monkeypatch.setattr(adapter_module, "get_circuit_breaker_sync", forbidden_registry_access)
    disabled = HttpAppBackendAdapter(origin, trust_env=False, circuit_breaker_enabled=False)
    for _ in range(3):
        response = await disabled.request("POST", "/items", json_body={"id": "a"})
        assert response.status_code == 503 and response.error == "backend_unavailable"
    assert len(requests) == 5
