"""Offline transport and security checks for the repository ACP model gateway."""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from infra.docker.acp_gateway import gateway

TOKEN = "T" * 40
UPSTREAM_KEY = "upstream-secret-key-123456"
MODEL = "approved-model"


class FakeUpstream(ThreadingHTTPServer):
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, str], bytes]] = []
        self.status = 200
        self.body = b'{"ok":true}'
        self.content_type = "application/json"
        self.advertise_length = True
        self.extra_headers: dict[str, str] = {}
        super().__init__(("127.0.0.1", 0), FakeUpstreamHandler)


class FakeUpstreamHandler(BaseHTTPRequestHandler):
    server: FakeUpstream

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.requests.append((self.path, dict(self.headers), body))
        self.send_response(self.server.status)
        self.send_header("Content-Type", self.server.content_type)
        if self.server.advertise_length:
            self.send_header("Content-Length", str(len(self.server.body)))
        for key, value in self.server.extra_headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(self.server.body)
        self.wfile.flush()

    def log_message(self, *_args: object) -> None:
        return


@pytest.fixture
def pair():
    upstream = FakeUpstream()
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    running = []

    def start(adapter: str) -> gateway.GatewayServer:
        server = gateway.GatewayServer(
            ("127.0.0.1", 0),
            gateway.StartupConfig(adapter, UPSTREAM_KEY, TOKEN, MODEL),
            connection_factory=lambda _adapter, timeout: http.client.HTTPConnection(
                "127.0.0.1", upstream.server_port, timeout=timeout
            ),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        running.append((server, thread))
        return server

    yield upstream, start

    for server, thread in running:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    upstream.shutdown()
    upstream.server_close()
    upstream_thread.join(timeout=2)


def post(server: gateway.GatewayServer, path: str, *, token: str = TOKEN, headers=None, body=None):
    if body is None:
        body = json.dumps({"model": MODEL}).encode()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    request_headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    request_headers.update(headers or {})
    connection.request("POST", path, body=body, headers=request_headers)
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    connection.close()
    return result


def test_codex_credential_is_replaced_and_route_is_exact(pair):
    upstream, start = pair
    server = start("codex")
    body = json.dumps({"model": MODEL, "input": "hello"}).encode()
    status, headers, response = post(server, "/v1/responses", body=body)
    assert (status, json.loads(response)) == (200, {"ok": True})
    assert headers["Cache-Control"] == "no-store"
    path, forwarded, forwarded_body = upstream.requests[0]
    assert path == "/v1/responses"
    assert json.loads(forwarded_body) == {
        "model": MODEL,
        "input": "hello",
        "max_output_tokens": gateway.MAX_OUTPUT_TOKENS,
    }
    assert forwarded["Authorization"] == f"Bearer {UPSTREAM_KEY}"
    assert TOKEN not in repr(forwarded)
    assert "x-api-key" not in {key.lower() for key in forwarded}

    assert post(server, "/v1/responses/abc")[0] == 404
    assert post(server, "http://example.test/v1/responses")[0] == 404
    assert post(server, "/v1/models")[0] == 404
    assert len(upstream.requests) == 1


def test_claude_headers_and_stream_bytes(pair):
    upstream, start = pair
    upstream.body = (
        b'event: message_start\ndata: {"type":"message_start"}\n\nevent: message_stop\ndata: {}\n\n'
    )
    upstream.content_type = "text/event-stream"
    server = start("claude_code")
    status, headers, body = post(
        server,
        "/v1/messages?beta=true",
        headers={"anthropic-version": "2023-06-01", "anthropic-beta": "prompt-caching-2024-07-31"},
        body=json.dumps({"model": MODEL, "stream": True}).encode(),
    )
    assert status == 200
    assert headers["Content-Type"] == "text/event-stream"
    assert body == upstream.body
    path, forwarded, forwarded_body = upstream.requests[0]
    assert path == "/v1/messages?beta=true"
    assert json.loads(forwarded_body)["max_tokens"] == gateway.MAX_OUTPUT_TOKENS
    assert forwarded["x-api-key"] == UPSTREAM_KEY
    assert forwarded["anthropic-version"] == "2023-06-01"
    assert forwarded["anthropic-beta"] == "prompt-caching-2024-07-31"
    assert "Authorization" not in forwarded
    assert post(server, "/v1/messages/count_tokens")[0] == 200
    assert "max_tokens" not in json.loads(upstream.requests[1][2])
    assert post(server, "/v1/messages?beta=false")[0] == 404
    assert post(server, "/v1/chat/completions")[0] == 404


@pytest.mark.parametrize(
    ("adapter", "path", "field"),
    (
        ("codex", "/v1/responses", "max_output_tokens"),
        ("codex", "/v1/chat/completions", "max_completion_tokens"),
        ("claude_code", "/v1/messages", "max_tokens"),
        ("claude_code", "/v1/messages?beta=true", "max_tokens"),
    ),
)
def test_generation_output_cap_is_enforced(pair, adapter, path, field):
    upstream, start = pair
    server = start(adapter)
    body = json.dumps({"model": MODEL, field: gateway.MAX_OUTPUT_TOKENS * 100}).encode()
    assert post(server, path, body=body)[0] == 200
    assert json.loads(upstream.requests[0][2])[field] == gateway.MAX_OUTPUT_TOKENS
    body = json.dumps({"model": MODEL, field: 100}).encode()
    assert post(server, path, body=body)[0] == 200
    assert json.loads(upstream.requests[1][2])[field] == 100


@pytest.mark.parametrize(
    ("adapter", "path"),
    (
        ("codex", "/v1/responses"),
        ("codex", "/v1/chat/completions"),
        ("claude_code", "/v1/messages"),
        ("claude_code", "/v1/messages?beta=true"),
        ("claude_code", "/v1/messages/count_tokens"),
    ),
)
def test_every_api_path_requires_trusted_model(pair, adapter, path):
    upstream, start = pair
    server = start(adapter)
    assert post(server, path, body=b'{"model":"other-model"}')[0] == 403
    assert post(server, path, body=b"{}")[0] == 403
    assert not upstream.requests


def test_bad_output_limits_and_multiple_chat_outputs_are_rejected(pair):
    upstream, start = pair
    server = start("codex")
    for body in (
        {"model": MODEL, "max_output_tokens": True},
        {"model": MODEL, "max_output_tokens": 0},
    ):
        assert post(server, "/v1/responses", body=json.dumps(body).encode())[0] == 400
    for body in (
        {"model": MODEL, "max_completion_tokens": 0},
        {"model": MODEL, "max_tokens": 100, "max_completion_tokens": 100},
        {"model": MODEL, "n": 2},
    ):
        assert post(server, "/v1/chat/completions", body=json.dumps(body).encode())[0] == 400
    assert not upstream.requests


def test_wrong_or_duplicate_job_token_cannot_reach_upstream(pair):
    upstream, start = pair
    server = start("codex")
    assert post(server, "/v1/responses", token="wrong")[0] == 401
    assert post(server, "/v1/responses", headers={"x-api-key": "wrong"})[0] == 401
    assert post(server, "/v1/responses", headers={"x-api-key": TOKEN})[0] == 200
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    connection.putrequest("POST", "/v1/responses")
    connection.putheader("Authorization", f"Bearer {TOKEN}")
    connection.putheader("Authorization", f"Bearer {TOKEN}")
    connection.putheader("Content-Type", "application/json")
    connection.putheader("Content-Length", "2")
    connection.endheaders(b"{}")
    response = connection.getresponse()
    assert response.status == 401
    response.read()
    connection.close()
    assert len(upstream.requests) == 1


def test_redirect_and_response_headers_are_not_forwarded(pair):
    upstream, start = pair
    upstream.status = 302
    upstream.extra_headers = {
        "Location": "https://attacker.example/",
        "Set-Cookie": "credential=bad",
    }
    server = start("codex")
    status, headers, body = post(server, "/v1/responses")
    assert status == 502
    assert json.loads(body) == {"error": "upstream_redirect_rejected"}
    assert "Location" not in headers
    assert "Set-Cookie" not in headers
    assert len(upstream.requests) == 1


def test_byte_and_request_budgets(pair, monkeypatch):
    upstream, start = pair
    assert gateway.MAX_REQUESTS == 16
    server = start("codex")
    monkeypatch.setattr(gateway, "MAX_REQUEST_BYTES", 10)
    assert post(server, "/v1/responses", body=b'{"input":"123456"}')[0] == 413
    assert not upstream.requests
    monkeypatch.setattr(gateway, "MAX_REQUESTS", 1)
    assert post(server, "/v1/responses")[0] == 429  # oversize request consumed the slot
    assert not upstream.requests


def test_advertised_oversize_response_fails_before_body_is_forwarded(pair, monkeypatch):
    upstream, start = pair
    upstream.body = b'{"large":true}'
    server = start("codex")
    monkeypatch.setattr(gateway, "MAX_RESPONSE_BYTES", 4)
    status, _, body = post(server, "/v1/responses")
    assert status == 502
    assert json.loads(body) == {"error": "response_too_large"}


def test_unadvertised_oversize_response_is_not_returned_as_valid_json(pair, monkeypatch):
    upstream, start = pair
    upstream.body = b'{"large":true}'
    upstream.advertise_length = False
    server = start("codex")
    monkeypatch.setattr(gateway, "MAX_RESPONSE_BYTES", 4)
    status, _, body = post(server, "/v1/responses")
    assert status == 502
    assert json.loads(body) == {"error": "response_too_large"}


def test_upstream_content_type_cannot_inject_response_headers(pair):
    upstream, start = pair
    upstream.content_type = "application/json\r\n X-Evil: yes"
    server = start("codex")
    status, headers, body = post(server, "/v1/responses")
    assert status == 502
    assert json.loads(body) == {"error": "invalid_upstream_content_type"}
    assert "X-Evil" not in headers


def test_idle_connections_cannot_spawn_unbounded_handlers(pair, monkeypatch):
    upstream, start = pair
    monkeypatch.setattr(gateway, "MAX_CONNECTIONS", 1)
    server = start("codex")
    idle = socket.create_connection(("127.0.0.1", server.server_port), timeout=2)
    try:
        deadline = time.monotonic() + 2
        while server.connection_slots._value != 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.connection_slots._value == 0
        with pytest.raises((http.client.RemoteDisconnected, OSError)):
            post(server, "/v1/responses")
        assert not upstream.requests
    finally:
        idle.close()
    deadline = time.monotonic() + 2
    while server.connection_slots._value != 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert post(server, "/v1/responses")[0] == 200


def test_startup_config_requires_exact_shape_and_secret_safe_errors():
    raw = (
        json.dumps(
            {
                "adapter": "codex",
                "upstream_api_key": UPSTREAM_KEY,
                "job_token": TOKEN,
                "model": MODEL,
            }
        ).encode()
        + b"\n"
    )
    assert gateway.StartupConfig.parse(raw).adapter == "codex"
    for invalid in (
        raw[:-1],
        raw.replace(b'"codex"', b'"unknown"'),
        raw.replace(b'"job_token"', b'"token"'),
        raw.replace(b'"model"', b'"other_model"'),
        raw.replace(MODEL.encode(), b"not a model"),
        raw.replace(b'"codex"', b'"codex", "upstream_url": "http://bad"'),
        raw.replace(UPSTREAM_KEY.encode(), b"too\nshort"),
    ):
        with pytest.raises(ValueError, match="invalid gateway startup configuration") as exc_info:
            gateway.StartupConfig.parse(invalid)
        assert UPSTREAM_KEY not in str(exc_info.value)
        assert TOKEN not in str(exc_info.value)


def test_production_upstream_ignores_proxy_environment(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://attacker.example")
    for adapter, host in (("codex", "api.openai.com"), ("claude_code", "api.anthropic.com")):
        connection = gateway._production_connection(adapter, 2)
        assert isinstance(connection, http.client.HTTPSConnection)
        assert connection.host == host
        connection.close()
