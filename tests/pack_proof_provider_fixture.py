"""Test-owned loopback providers for OSS facade wiring, not hosted acceptance.

Only connector absence is an IO double. Real generated clients use their documented
environment setup and make HTTP requests to a loopback server with synthetic keys.
The normal module loader, action authorization and runtime observer remain intact.
The production smoke's environment allowlist is unchanged.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

PACKS = frozenset({"mozaiks_cloud", "mozaikspay"})
FIXTURE_KEY = "local-pack-fixture-only"
CLOUD_PREFIX = "/api/mozaiks-cloud/v1"
PAY_PREFIX = "/api/mozaikspay/v1"
CLOUD_ROUTES = {
    "operations": r"/operations/([^/]+)",
    "endpoints": r"/apps/([^/]+)/endpoints",
    "health": r"/deployments/([^/]+)/health",
    "verification": r"/domains/([^/]+)/verification",
    "instructions": r"/domains/([^/]+)/instructions",
    "domain": r"/domains/([^/]+)",
}


def _response(pack_id, path, query):
    if pack_id == "mozaikspay":
        if path == PAY_PREFIX + "/subscription/status" and query.get("user_id"):
            return "subscription", {"success": True, "found": True, "plan_id": "free",
                                    "plan_name": "Fixture free plan", "status": "active", "on_free_plan": True}
        if path == "/api/me/usage" and query.get("limit"):
            return "usage", {"success": True, "total_tokens": 3, "records": []}
        return None
    for route, pattern in CLOUD_ROUTES.items():
        matched = re.fullmatch(CLOUD_PREFIX + pattern, path)
        if matched:
            identifier = matched.group(1)
            bodies = {
                "operations": {"operation_id": identifier, "status": "succeeded"},
                "endpoints": {"app_id": identifier, "endpoints": []},
                "health": {"deployment_id": identifier, "status": "healthy"},
                "verification": {"binding_id": identifier, "verified": True},
                "instructions": {"binding_id": identifier, "records": []},
                "domain": {"binding_id": identifier, "status": "active", "dns_status": "verified", "tls_status": "active"},
            }
            return route, {"success": True, **bodies[route]}
    return None


def _server(pack_id, calls):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlsplit(self.path)
            response = _response(pack_id, url.path, parse_qs(url.query))
            bearer = self.headers.get("Authorization", "")
            runtime_usage = url.path == "/api/me/usage"
            credential_ok = (bearer.startswith("Bearer smoke-") if runtime_usage
                             else bearer == "Bearer " + FIXTURE_KEY)
            expected_header = "X-MozaiksCloud-Api-Version" if pack_id == "mozaiks_cloud" else "X-MozaiksPay-Contract-Version"
            expected_version = "mozaiks.cloud.v1" if pack_id == "mozaiks_cloud" else "mozaiks.provider_api_contract.v1"
            version_ok = self.headers.get(expected_header) == expected_version
            accepted = bool(response and credential_ok and version_ok)
            calls.append({"method": "GET", "path": url.path,
                          "route": response[0] if response else "unknown", "accepted": accepted,
                          "credential_kind": "forwarded_user" if runtime_usage else "synthetic_provider"})
            body = response[1] if accepted else {"success": False, "error": "unexpected fixture request"}
            encoded = json.dumps(body).encode("utf-8")
            self.send_response(200 if accepted else 400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


async def run_provider_probe(app_root: Path, *, mongo_uri: str, pack_id: str, mode: str) -> dict[str, Any]:
    """Run one trusted-fixture observer in a sanitized child, with explicit dependency fixtures."""
    from motor.motor_asyncio import AsyncIOMotorClient

    from factory_app.workflows.AppGenerator.tools import app_runtime_smoke as smoke

    assert pack_id in PACKS and mode in {"smoke", "sweep"}
    database = f"{smoke._SMOKE_DATABASE_PREFIX}provider_{uuid4().hex[:16]}"
    request = {"app_root": str(app_root), "mongo_uri": mongo_uri, "database_name": database,
               "pack_id": pack_id, "mode": mode}
    env = smoke.child_environment()
    env["PYTHONPATH"] = os.pathsep.join([str(Path(__file__).resolve().parents[1]), env["PYTHONPATH"]])
    env["USERPROFILE" if os.name == "nt" else "HOME"] = str(app_root.parent)
    client = AsyncIOMotorClient(mongo_uri, serverSelectionTimeoutMS=2000)
    try:
        result = await asyncio.to_thread(
            subprocess.run, [sys.executable, "-m", "tests.pack_proof_provider_fixture"],
            input=json.dumps(request), capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(app_root.parent), env=env, timeout=300,
        )
    finally:
        await client.drop_database(database)
        client.close()
    events = smoke._events(result.stdout)
    assert result.returncode == 0 and any(row.get("event") == "done" for row in events), result.stderr[-3000:]
    receipt = next(row for row in events if row.get("event") == "provider_fixture")
    expected = set(CLOUD_ROUTES) if pack_id == "mozaiks_cloud" else {"subscription", "usage"}
    assert {row["route"] for row in receipt["calls"]} == expected, receipt
    assert all(row["accepted"] for row in receipt["calls"]), receipt
    return {"results": [{key: value for key, value in row.items() if key != "event"}
                        for row in events if row.get("event") == "outcome"],
            "skipped_reason": None, "provider_fixture": receipt}


def _main():
    request = json.loads(sys.stdin.read())
    pack_id = request["pack_id"]
    assert pack_id in PACKS and request["mode"] in {"smoke", "sweep"}
    calls: list[dict[str, Any]] = []
    server, thread = _server(pack_id, calls)
    base = f"http://127.0.0.1:{server.server_port}"
    if pack_id == "mozaiks_cloud":
        os.environ.update(MOZAIKS_CLOUD_API_BASE=base, MOZAIKS_CLOUD_API_KEY=FIXTURE_KEY)
    else:
        os.environ.update(MOZAIKSPAY_API_BASE=base, MOZAIKSPAY_API_KEY=FIXTURE_KEY, MOZAIKS_APP_URL=base)
    initial_environment = set(os.environ)
    from factory_app.workflows.AppGenerator.tools import app_runtime_smoke as smoke
    from tests.pack_proof_action_sweep import _sweep

    app_id = json.loads((Path(request["app_root"]) / "app.json").read_text(encoding="utf-8"))["appId"]

    async def absent_connector(_store, *, scope, scope_id, service):
        assert scope == "app" and scope_id == app_id and service == pack_id
        return None

    channel = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
    sys.stdout = sys.stderr

    def emit(event, **data):
        channel.write(smoke._EVENT_PREFIX + json.dumps({"event": event, **data}, default=str) + "\n")
        channel.flush()

    try:
        with patch("mozaiksai.core.data.persistence.connector_store.ConnectorStore.get", absent_connector):
            runner = smoke._child_run if request["mode"] == "smoke" else _sweep
            asyncio.run(runner(request, emit, initial_environment))
        emit("provider_fixture", mode="loopback_http", connector_metadata="absent_io_double",
             real_provider_verified=False, calls=calls)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        channel.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
