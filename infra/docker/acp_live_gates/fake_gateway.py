"""Model gateway stand-in for the opt-in live repository Docker gates.

It runs the real ``gateway.py`` request handling (token check, approved model,
route allowlist, output limits) but replaces the upstream connection with an
in-container fake. Nothing leaves the container: the fake upstream binds to
loopback and answers every allowed request with a fixed message.

``GET /gate/targets`` (job token required) tells the gate worker which
addresses on the gateway's egress side it must fail to reach, and whether the
fake upstream saw the delivered credential. It reports booleans and addresses
only, never the credential.
"""

from __future__ import annotations

import http.client
import json
import socket
import struct
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, "/app")

import gateway  # noqa: E402

_STATE = {"upstream_requests": 0, "credential_at_upstream": False}
_STATE_LOCK = threading.Lock()
_MESSAGE = json.dumps({
    "id": "msg_gate", "type": "message", "role": "assistant", "model": "gate",
    "content": [{"type": "text", "text": "gate"}], "stop_reason": "end_turn",
    "usage": {"input_tokens": 1, "output_tokens": 1},
}, separators=(",", ":")).encode("ascii")


class _FakeUpstream(ThreadingHTTPServer):
    daemon_threads = True
    expected_credential = ""


class _FakeUpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: _FakeUpstream

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        bearer = self.headers.get("Authorization", "")
        presented = self.headers.get("x-api-key") or (bearer[7:] if bearer.startswith("Bearer ") else "")
        with _STATE_LOCK:
            _STATE["upstream_requests"] += 1
            if presented == self.server.expected_credential:
                _STATE["credential_at_upstream"] = True
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(_MESSAGE)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(_MESSAGE)


def _default_route_gateway() -> str | None:
    with open("/proc/net/route", encoding="ascii") as routes:
        for line in routes.readlines()[1:]:
            fields = line.split()
            if len(fields) > 3 and fields[1] == "00000000" and int(fields[3], 16) & 0x2:
                return socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
    return None


def _source_address(destination: str) -> str | None:
    # A UDP connect selects the route and source address without sending.
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((destination, 9))
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()


class _GateHandler(gateway.GatewayHandler):
    def do_GET(self) -> None:
        if self.path != "/gate/targets":
            super().do_GET()
            return
        if not self._authenticated():
            self._send_json(401, "unauthorized")
            return
        egress_gateway = _default_route_gateway()
        with _STATE_LOCK:
            state = dict(_STATE)
        payload = json.dumps({
            "egress_gateway_ip": egress_gateway,
            "gateway_egress_ip": _source_address(egress_gateway) if egress_gateway else None,
            **state,
        }, separators=(",", ":")).encode("ascii")
        self.send_response_only(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)
        self.close_connection = True


def main() -> int:
    raw = sys.stdin.buffer.readline(gateway.MAX_STARTUP_BYTES + 1)
    try:
        config = gateway.StartupConfig.parse(raw)
        upstream = _FakeUpstream(("127.0.0.1", 0), _FakeUpstreamHandler)
        upstream.expected_credential = config.upstream_api_key
        port = upstream.server_address[1]
        server = gateway.GatewayServer(
            ("0.0.0.0", gateway.PORT), config,
            connection_factory=lambda _adapter, timeout: http.client.HTTPConnection(
                "127.0.0.1", port, timeout=timeout,
            ),
        )
    except (ValueError, OSError):
        print("gateway startup failed", file=sys.stderr, flush=True)
        return 2
    server.RequestHandlerClass = _GateHandler
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    print("READY", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        upstream.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
