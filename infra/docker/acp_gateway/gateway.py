"""Credential-isolating model API gateway for one repository ACP worker.

The trusted launcher writes one JSON line to stdin. The agent receives only a
random job token and can reach this gateway on its private Docker network.
Only the fixed model creation routes below are proxied. This process never
accepts an upstream URL or a credential from an HTTP request.
"""

from __future__ import annotations

import hmac
import http.client
import json
import re
import ssl
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8765
MAX_STARTUP_BYTES = 16 * 1024
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_REQUESTS = 16
MAX_OUTPUT_TOKENS = 8192
MAX_CONCURRENT_REQUESTS = 4
MAX_CONNECTIONS = 8
MAX_UPTIME_SECONDS = 15 * 60
MAX_REQUEST_SECONDS = 10 * 60
MAX_IDLE_SECONDS = 60
CHUNK_BYTES = 16 * 1024

_PATHS = {
    "codex": frozenset(("/v1/responses", "/v1/chat/completions")),
    "claude_code": frozenset(
        ("/v1/messages", "/v1/messages?beta=true", "/v1/messages/count_tokens")
    ),
}
_HOSTS = {"codex": "api.openai.com", "claude_code": "api.anthropic.com"}
_VERSION_RE = re.compile(r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_BETA_RE = re.compile(r"\A[A-Za-z0-9_., -]{1,512}\Z")
_CONTENT_TYPE_RE = re.compile(
    r"\A(?:application/json|text/event-stream)(?:;\s*charset=[A-Za-z0-9._-]+)?\Z",
    re.IGNORECASE,
)


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


class ResponseLimitExceeded(Exception):
    """An upstream response exceeded the gateway's fixed byte budget."""


@dataclass(frozen=True)
class StartupConfig:
    adapter: str
    upstream_api_key: str
    job_token: str
    model: str

    @classmethod
    def parse(cls, raw: bytes) -> StartupConfig:
        try:
            if not raw or len(raw) > MAX_STARTUP_BYTES or not raw.endswith(b"\n"):
                raise ValueError
            data = json.loads(raw, object_pairs_hook=_strict_object)
            if not isinstance(data, dict) or set(data) != {
                "adapter",
                "upstream_api_key",
                "job_token",
                "model",
            }:
                raise ValueError
            adapter = data["adapter"]
            key = data["upstream_api_key"]
            token = data["job_token"]
            model = data["model"]
            if adapter not in _PATHS:
                raise ValueError
            if (
                not isinstance(key, str)
                or not 8 <= len(key) <= 8192
                or not key.isascii()
                or not key.isprintable()
                or any(character.isspace() for character in key)
            ):
                raise ValueError
            if not isinstance(token, str) or not 32 <= len(token) <= 256 or not token.isascii():
                raise ValueError
            if not re.fullmatch(r"[A-Za-z0-9_-]+", token):
                raise ValueError
            if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", model):
                raise ValueError
            return cls(adapter=adapter, upstream_api_key=key, job_token=token, model=model)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, KeyError) as exc:
            raise ValueError("invalid gateway startup configuration") from exc
        except ValueError as exc:
            raise ValueError("invalid gateway startup configuration") from exc


def _production_connection(adapter: str, timeout: float) -> http.client.HTTPConnection:
    # http.client does not consult HTTP(S)_PROXY. TLS verifies this fixed host.
    return http.client.HTTPSConnection(
        _HOSTS[adapter], timeout=timeout, context=ssl.create_default_context()
    )


class GatewayServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(
        self,
        address: tuple[str, int],
        config: StartupConfig,
        *,
        connection_factory: Callable[
            [str, float], http.client.HTTPConnection
        ] = _production_connection,
    ) -> None:
        self.config = config
        self.connection_factory = connection_factory
        self.started_at = time.monotonic()
        self.requests_used = 0
        self.count_lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
        self.connection_slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(address, GatewayHandler)

    def reserve_request(self) -> bool:
        with self.count_lock:
            if self.requests_used >= MAX_REQUESTS:
                return False
            self.requests_used += 1
            return True

    def handle_error(self, request, client_address) -> None:  # type: ignore[override]
        # The default server prints tracebacks from malformed/disconnected
        # requests. The gateway never writes request data to logs.
        return

    def process_request(self, request, client_address) -> None:  # type: ignore[override]
        if not self.connection_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        request.settimeout(MAX_IDLE_SECONDS)
        try:
            super().process_request(request, client_address)
        except Exception:
            self.connection_slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:  # type: ignore[override]
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connection_slots.release()


class GatewayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: GatewayServer

    def log_message(self, _format: str, *_args: object) -> None:
        # BaseHTTPRequestHandler otherwise logs paths and header parser errors.
        return

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        self._send_json(code, "request_rejected")

    def _send_json(self, code: int, error: str) -> None:
        payload = json.dumps({"error": error}, separators=(",", ":")).encode("ascii")
        self.send_response_only(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)
        self.close_connection = True

    def do_GET(self) -> None:
        self._send_json(404, "unknown_route")

    def _authenticated(self) -> bool:
        bearer = self.headers.get_all("Authorization", [])
        api_key = self.headers.get_all("x-api-key", [])
        if len(bearer) > 1 or len(api_key) > 1 or not (bearer or api_key):
            return False
        if bearer and not bearer[0].startswith("Bearer "):
            return False
        expected = self.server.config.job_token
        return all(
            hmac.compare_digest(value, expected)
            for value in ([bearer[0][7:]] if bearer else []) + api_key
        )

    def _read_request_body(self, deadline: float) -> bytes | None:
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isdigit():
            self._send_json(411, "content_length_required")
            return None
        length = int(lengths[0])
        if length < 1 or length > MAX_REQUEST_BYTES:
            self._send_json(413, "request_too_large")
            return None
        if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Encoding"):
            self._send_json(400, "unsupported_encoding")
            return None
        if (
            self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            != "application/json"
        ):
            self._send_json(415, "json_required")
            return None
        chunks = []
        remaining = length
        while remaining:
            seconds_left = deadline - time.monotonic()
            if seconds_left <= 0:
                self._send_json(408, "request_timeout")
                return None
            self.connection.settimeout(min(MAX_IDLE_SECONDS, seconds_left))
            try:
                chunk = self.rfile.read1(min(CHUNK_BYTES, remaining))
            except (TimeoutError, OSError):
                self._send_json(408, "request_timeout")
                return None
            if not chunk:
                self._send_json(400, "incomplete_body")
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _prepare_body(self, body: bytes) -> bytes | None:
        try:
            data = json.loads(body, object_pairs_hook=_strict_object)
            if not isinstance(data, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError, RecursionError):
            self._send_json(400, "invalid_json")
            return None
        if data.get("model") != self.server.config.model:
            self._send_json(403, "model_not_approved")
            return None

        if self.path != "/v1/messages/count_tokens":
            if self.path == "/v1/responses":
                output_field = "max_output_tokens"
                minimum = 16  # OpenAI Responses requires at least 16.
            elif self.path in ("/v1/messages", "/v1/messages?beta=true"):
                output_field = "max_tokens"
                minimum = 0  # Anthropic permits zero for cache warmup.
            else:
                fields = [
                    name
                    for name in ("max_completion_tokens", "max_tokens")
                    if data.get(name) is not None
                ]
                if len(fields) > 1 or type(data.get("n", 1)) is not int or data.get("n", 1) != 1:
                    self._send_json(400, "invalid_output_limit")
                    return None
                output_field = fields[0] if fields else "max_completion_tokens"
                minimum = 1

            output_limit = data.get(output_field)
            if output_limit is not None and (
                type(output_limit) is not int or output_limit < minimum
            ):
                self._send_json(400, "invalid_output_limit")
                return None
            data[output_field] = (
                min(output_limit, MAX_OUTPUT_TOKENS)
                if output_limit is not None
                else MAX_OUTPUT_TOKENS
            )

        try:
            encoded = json.dumps(
                data, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
        except ValueError:
            self._send_json(400, "invalid_json")
            return None
        if len(encoded) > MAX_REQUEST_BYTES:
            self._send_json(413, "request_too_large")
            return None
        return encoded

    def _upstream_headers(self, body: bytes) -> dict[str, str] | None:
        headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
        config = self.server.config
        if config.adapter == "codex":
            headers["Authorization"] = f"Bearer {config.upstream_api_key}"
        else:
            headers["x-api-key"] = config.upstream_api_key
            versions = self.headers.get_all("anthropic-version", [])
            if len(versions) > 1 or (versions and not _VERSION_RE.fullmatch(versions[0])):
                self._send_json(400, "invalid_version")
                return None
            headers["anthropic-version"] = versions[0] if versions else "2023-06-01"
            betas = self.headers.get_all("anthropic-beta", [])
            if len(betas) > 1 or (betas and not _BETA_RE.fullmatch(betas[0])):
                self._send_json(400, "invalid_beta")
                return None
            if betas:
                headers["anthropic-beta"] = betas[0]
        return headers

    def do_POST(self) -> None:
        self.close_connection = True
        if not self._authenticated():
            self._send_json(401, "unauthorized")
            return
        if self.path not in _PATHS[self.server.config.adapter]:
            self._send_json(404, "unknown_route")
            return
        if time.monotonic() - self.server.started_at >= MAX_UPTIME_SECONDS:
            self._send_json(503, "gateway_expired")
            return
        if not self.server.slots.acquire(blocking=False):
            self._send_json(429, "gateway_busy")
            return
        try:
            if not self.server.reserve_request():
                self._send_json(429, "request_budget_exceeded")
                return
            deadline = min(
                time.monotonic() + MAX_REQUEST_SECONDS,
                self.server.started_at + MAX_UPTIME_SECONDS,
            )
            body = self._read_request_body(deadline)
            if body is None:
                return
            body = self._prepare_body(body)
            if body is None:
                return
            headers = self._upstream_headers(body)
            if headers is None:
                return
            self._forward(body, headers, deadline)
        finally:
            self.server.slots.release()

    def _forward(self, body: bytes, headers: dict[str, str], deadline: float) -> None:
        connection: http.client.HTTPConnection | None = None
        started_response = False
        try:
            connection = self.server.connection_factory(
                self.server.config.adapter,
                min(MAX_IDLE_SECONDS, max(0.1, deadline - time.monotonic())),
            )
            connection.request("POST", self.path, body=body, headers=headers)
            upstream = connection.getresponse()
            if not 200 <= upstream.status <= 599 or 300 <= upstream.status < 400:
                self._send_json(502, "upstream_redirect_rejected")
                return
            advertised_length = upstream.getheader("Content-Length")
            if (
                advertised_length
                and advertised_length.isdigit()
                and int(advertised_length) > MAX_RESPONSE_BYTES
            ):
                self._send_json(502, "response_too_large")
                return
            content_type = upstream.getheader("Content-Type", "application/json")
            if not _CONTENT_TYPE_RE.fullmatch(content_type):
                self._send_json(502, "invalid_upstream_content_type")
                return
            streaming = content_type.lower().startswith("text/event-stream")
            if streaming:
                self._begin_response(upstream.status, content_type)
                started_response = True
            else:
                payload = bytearray()
                for chunk in self._response_chunks(upstream, connection, deadline):
                    payload.extend(chunk)
                self._begin_response(upstream.status, content_type, content_length=len(payload))
                started_response = True
                self.wfile.write(payload)
                return
            for chunk in self._response_chunks(upstream, connection, deadline):
                self.wfile.write(chunk)
                self.wfile.flush()  # Forward SSE events as they arrive.
        except ResponseLimitExceeded:
            if not started_response:
                self._send_json(502, "response_too_large")
        except (TimeoutError, OSError, http.client.HTTPException, ValueError):
            if not started_response:
                self._send_json(502, "upstream_unavailable")
        finally:
            self.close_connection = True
            if connection is not None:
                connection.close()

    def _begin_response(
        self, status: int, content_type: str, *, content_length: int | None = None
    ) -> None:
        self.send_response_only(status)
        # Response headers are never forwarded wholesale (cookies, redirects,
        # auth challenges and tracing metadata must stay inside the gateway).
        self.send_header("Content-Type", content_type)
        if content_length is not None:
            self.send_header("Content-Length", str(content_length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()

    def _response_chunks(
        self,
        upstream: http.client.HTTPResponse,
        connection: http.client.HTTPConnection,
        deadline: float,
    ) -> Iterator[bytes]:
        received = 0
        while True:
            seconds_left = deadline - time.monotonic()
            if seconds_left <= 0:
                raise TimeoutError
            if connection.sock is not None:
                connection.sock.settimeout(min(MAX_IDLE_SECONDS, seconds_left))
            chunk = upstream.read1(CHUNK_BYTES)
            if not chunk:
                break
            received += len(chunk)
            if received > MAX_RESPONSE_BYTES:
                raise ResponseLimitExceeded
            yield chunk


def main() -> int:
    raw = sys.stdin.buffer.readline(MAX_STARTUP_BYTES + 1)
    try:
        config = StartupConfig.parse(raw)
        server = GatewayServer(("0.0.0.0", PORT), config)
    except (ValueError, OSError):
        print("gateway startup failed", file=sys.stderr, flush=True)
        return 2
    expiry = threading.Timer(MAX_UPTIME_SECONDS, server.shutdown)
    expiry.daemon = True
    expiry.start()
    print("READY", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        expiry.cancel()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
