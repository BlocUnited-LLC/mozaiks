"""The live smoke runner drives its run through the canonical runtime host.

Only provider HTTP is replaced. The runner, the runtime host's lifespan and
websocket route, SimpleTransport, the AG2 network runner, the configured auth
adapter, and Mongo persistence are all real.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import uvicorn
import yaml
from fastapi import FastAPI

from scripts import run_live_workflow_smoke as runner

WORKFLOW = "HostLaunchSmoke"
PROMPT = "Check the launch boundary for order 4417."
REPLY = "Finish the check for order 4417."
SMOKE_USER = "smoke-user"
AUDIENCE = "smoke-runner-api"
_PROVIDER_HOST = "provider.invalid"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_workflow(directory: Path) -> None:
    """An AgentDriven workflow, so the host's websocket would start it on its own."""
    agents = ("GreeterAgent", "AskAgent", "FinishAgent")
    documents: dict[str, Any] = {
        "orchestrator": {
            "schema_version": "mozaiks.orchestrator.v1",
            "workflow_name": WORKFLOW,
            "max_turns": 8,
            "human_in_the_loop": True,
            "workflow_startup_mode": "AgentDriven",
            "orchestration_pattern": "ag2_network",
            "initial_agent": "GreeterAgent",
        },
        "agents": {"agents": [
            {
                "name": name,
                "prompt_sections": [{"id": "role", "heading": "[ROLE]", "content": f"You are {name}."}],
                "max_consecutive_auto_reply": 1,
                "structured_outputs_required": False,
            }
            for name in agents
        ]},
        "transition_graph": {"transition_rules": [
            {"source_agent": "GreeterAgent", "target_agent": "terminate", "transition_type": "after_turn"},
            {
                "source_agent": "AskAgent", "target_agent": "user", "transition_type": "after_turn",
                "transition_target": "RevertToUserTarget",
            },
            {
                "source_agent": "user", "target_agent": "FinishAgent", "transition_type": "after_turn",
                "transition_target": "AgentTarget",
            },
            {"source_agent": "FinishAgent", "target_agent": "terminate", "transition_type": "after_turn"},
        ]},
        "structured_outputs": {"schema_version": "mozaiks.structured_outputs.v1", "registry": {}, "models": {}},
        "context_variables": {"definitions": {}},
        "tools": {"tools": []},
        "middleware": {"prompt_middleware": []},
        "ui_config": {"visual_agents": ["AskAgent", "FinishAgent"]},
    }
    directory.mkdir(parents=True)
    for name, document in documents.items():
        (directory / f"{name}.yaml").write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


@dataclass
class _Provider:
    """OpenAI-compatible chat completions answered per agent, recording every request."""

    requests: list[dict[str, Any]] = field(default_factory=list)

    def respond(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == _PROVIDER_HOST
        payload = json.loads(request.content)
        self.requests.append(payload)
        agent = payload["model"]
        text = {
            "AskAgent": "Which order should I finish?",
            "FinishAgent": "Order check finished.",
        }.get(agent, f"{agent} spoke.")
        return httpx.Response(200, json={
            "id": f"completion-{len(self.requests)}",
            "object": "chat.completion",
            "created": 1,
            "model": agent,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    @property
    def agents(self) -> list[str]:
        return [body["model"] for body in self.requests]

    def messages(self, index: int) -> str:
        return json.dumps(self.requests[index]["messages"])


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> _Provider:
    from mozaiksai.core.workflow import llm_config
    from mozaiksai.core.workflow.agents import factory
    from mozaiksai.core.workflow.outputs import structured

    scenario = _Provider()

    async def config(*_args: Any, agent_name: str = "unnamed", **_kwargs: Any) -> tuple[None, dict[str, Any]]:
        return None, {
            "config_list": [{
                "model": agent_name, "api_type": "openai", "api_key": "test-only",
                "base_url": f"https://{_PROVIDER_HOST}/v1",
            }],
            "streaming": False,
            "max_completion_tokens": 128,
            "max_retries": 0,
        }

    original_converter = factory.llm_config_to_ag2_config

    def convert(value: dict[str, Any]) -> Any:
        client = httpx.AsyncClient(transport=httpx.MockTransport(scenario.respond))
        return original_converter(value).copy(http_client=client)

    monkeypatch.setattr(factory, "llm_config_to_ag2_config", convert)
    monkeypatch.setattr(llm_config, "get_llm_config", config)
    monkeypatch.setattr(structured, "get_llm_for_workflow", config)
    return scenario


@pytest.fixture
def host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[SimpleNamespace]:
    """Process-wide host state the runner touches, recorded and restored."""
    from mozaiksai.core.auth import clear_auth_config_cache
    from mozaiksai.core.core_config import close_mongo_client
    from mozaiksai.core.transport.simple_transport import SimpleTransport
    from mozaiksai.core.workflow import workflow_manager as workflow_manager_module
    from mozaiksai.hosts import runtime

    root = tmp_path / "workflows"
    _write_workflow(root / WORKFLOW)

    # A developer's .env must not reconfigure the host under test.
    monkeypatch.setattr(runner, "load_dotenv", lambda *_args, **_kwargs: False)
    for name in list(os.environ):
        if name.startswith(("AUTH_", "KEYCLOAK_", "SUPABASE_")):
            monkeypatch.delenv(name)
    monkeypatch.delenv(runner.SMOKE_ACCESS_TOKEN_ENV, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    # Any model call that escaped the fake provider would fail to resolve.
    monkeypatch.setenv("OPENAI_BASE_URL", f"https://{_PROVIDER_HOST}/v1")
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", "unset-by-test")
    monkeypatch.setattr(SimpleTransport, "_instance", None)
    monkeypatch.setattr(runtime.app.state, "simple_transport", None, raising=False)

    state = SimpleNamespace(root=root, lifecycle=Counter(), served_roots=[], launches=[])
    original_startup, original_shutdown = runtime._runtime_startup, runtime._runtime_shutdown

    async def startup() -> None:
        state.lifecycle["startup"] += 1
        state.served_roots.append(Path(str(workflow_manager_module.workflow_manager.workflows_base_path)))
        await original_startup()

    async def shutdown() -> None:
        state.lifecycle["shutdown"] += 1
        await original_shutdown()

    monkeypatch.setattr(runtime, "_runtime_startup", startup)
    monkeypatch.setattr(runtime, "_runtime_shutdown", shutdown)

    original_launch = SimpleTransport._launch_workflow_run_locked

    async def launch(self: Any, **kwargs: Any) -> Any:
        state.launches.append(kwargs)
        return await original_launch(self, **kwargs)

    monkeypatch.setattr(SimpleTransport, "_launch_workflow_run_locked", launch)

    manager = workflow_manager_module.workflow_manager
    catalog = dict(manager.__dict__)
    clear_auth_config_cache()
    # The process client belongs to the event loop that opened it; each run
    # gets its own loop, so none may inherit an earlier test's client.
    close_mongo_client()
    try:
        yield state
    finally:
        close_mongo_client()
        manager.__dict__.clear()
        manager.__dict__.update(catalog)
        clear_auth_config_cache()


@pytest.fixture
def mongo_uri(monkeypatch: pytest.MonkeyPatch) -> str:
    uri = os.environ.get("MONGO_URI", "").strip()
    if not uri:
        if os.getenv("MOZAIKS_REQUIRE_REAL_MONGO"):
            pytest.fail("Real MongoDB is required")
        pytest.skip("MONGO_URI is not set")
    from pymongo import MongoClient

    client: MongoClient = MongoClient(uri, serverSelectionTimeoutMS=2000)
    try:
        client.admin.command("ping")
    except Exception:
        if os.getenv("MOZAIKS_REQUIRE_REAL_MONGO"):
            pytest.fail("Real MongoDB is required")
        pytest.skip("MongoDB is unavailable")
    finally:
        client.close()
    return uri


@pytest.fixture
def identity_provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    """A local JWKS issuer configured as the host's auth, and a token minter."""
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = {
        **json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key())),
        "kid": "smoke-runner-key", "alg": "RS256", "use": "sig",
    }
    payload = json.dumps({"keys": [jwk]}).encode("utf-8")

    class _JWKS(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", _free_port()), _JWKS)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    issuer = f"http://127.0.0.1:{server.server_address[1]}"

    def enable() -> None:
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "jwt")
        monkeypatch.setenv("AUTH_ISSUER", issuer)
        monkeypatch.setenv("AUTH_JWKS_URL", f"{issuer}/jwks")
        monkeypatch.setenv("AUTH_AUDIENCE", AUDIENCE)

    def token(subject: str) -> str:
        now = int(time.time())
        claims = {"iss": issuer, "sub": subject, "aud": AUDIENCE, "iat": now, "nbf": now - 1, "exp": now + 600}
        return jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "smoke-runner-key", "typ": "at+jwt"})

    try:
        yield SimpleNamespace(enable=enable, token=token)
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _run(**kwargs: Any) -> runner.SmokeResult:
    return asyncio.run(runner.run_live_workflow_smoke(
        PROMPT,
        workflow_name=WORKFLOW,
        initial_agent="AskAgent",
        user_id=SMOKE_USER,
        user_replies=[REPLY],
        timeout_seconds=90.0,
        **kwargs,
    ))


@pytest.mark.parametrize("auth", ["disabled", "enabled"])
def test_runner_owns_the_only_launch_on_the_runtime_host(
    auth: str,
    host: SimpleNamespace,
    provider: _Provider,
    mongo_uri: str,
    identity_provider: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = None
    if auth == "enabled":
        identity_provider.enable()
        token = identity_provider.token(SMOKE_USER)
    else:
        monkeypatch.setenv("AUTH_ENABLED", "false")

    result = _run(workflows_root=host.root, access_token=token)

    # The host started on the custom workflow root and shut down normally.
    assert host.served_roots == [host.root.resolve()]
    assert host.lifecycle == Counter(startup=1, shutdown=1)
    # One launch, owned by the runner and carrying the requested agent; the
    # websocket's own empty-session start never ran.
    assert len(host.launches) == 1
    assert host.launches[0]["initial_agent_name_override"] == "AskAgent"
    # The model saw the requested agent first, with the prompt, then paused for
    # the user; the scripted reply resumed the run into FinishAgent.
    assert provider.agents == ["AskAgent", "FinishAgent"]
    assert PROMPT in provider.messages(0)
    assert REPLY in provider.messages(1)
    assert REPLY not in provider.messages(0)
    assert result.success is True
    assert result.workflow_name == WORKFLOW
    assert result.assistant_message == "Order check finished."


def test_runner_refuses_an_authenticated_host_without_a_token(
    host: SimpleNamespace,
    provider: _Provider,
    identity_provider: SimpleNamespace,
) -> None:
    identity_provider.enable()

    with pytest.raises(RuntimeError, match=runner.SMOKE_ACCESS_TOKEN_ENV):
        _run(workflows_root=host.root)

    assert host.lifecycle == Counter()
    assert provider.requests == []


def test_runner_surfaces_a_token_issued_to_another_user(
    host: SimpleNamespace,
    provider: _Provider,
    mongo_uri: str,
    identity_provider: SimpleNamespace,
) -> None:
    identity_provider.enable()

    with pytest.raises(RuntimeError, match="refused the smoke websocket"):
        _run(workflows_root=host.root, access_token=identity_provider.token("someone-else"))

    assert host.launches == []
    assert provider.requests == []
    assert host.lifecycle == Counter(startup=1, shutdown=1)


def test_websocket_offer_carries_the_token_the_host_decodes(
    host: SimpleNamespace,
    identity_provider: SimpleNamespace,
) -> None:
    from mozaiksai.core.auth.websocket_auth import WS_BEARER_SUBPROTOCOL, _decode_bearer_subprotocol

    identity_provider.enable()
    offer = runner._websocket_auth_subprotocols("header.payload.signature")

    assert offer is not None and offer[0] == WS_BEARER_SUBPROTOCOL
    assert _decode_bearer_subprotocol(offer[1]) == "header.payload.signature"


def _lifespan_app(events: list[str], *, fail_startup: bool = False, hang_shutdown: bool = False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if fail_startup:
            raise RuntimeError("startup failed")
        events.append("startup")
        yield
        if hang_shutdown:
            await asyncio.Event().wait()
        events.append("shutdown")

    return FastAPI(lifespan=lifespan)


async def _serve(app: FastAPI) -> tuple[uvicorn.Server, asyncio.Task[Any]]:
    server = uvicorn.Server(runner._build_uvicorn_config(app, _free_port()))
    return server, asyncio.create_task(runner._serve_host(server))


def test_stop_server_runs_lifespan_shutdown_without_forcing() -> None:
    events: list[str] = []

    async def scenario() -> uvicorn.Server:
        server, task = await _serve(_lifespan_app(events))
        await runner._wait_for_server(server, task)
        await runner._stop_server(server, task)
        assert task.done()
        return server

    server = asyncio.run(scenario())

    assert events == ["startup", "shutdown"]
    assert server.force_exit is False


def test_stop_server_forces_exit_only_after_shutdown_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "_FORCED_SHUTDOWN_GRACE_SECONDS", 0.2)
    events: list[str] = []

    async def scenario() -> uvicorn.Server:
        server, task = await _serve(_lifespan_app(events, hang_shutdown=True))
        await runner._wait_for_server(server, task)
        await runner._stop_server(server, task, timeout_seconds=0.5)
        assert task.done()
        return server

    server = asyncio.run(scenario())

    assert events == ["startup"]
    assert server.force_exit is True


def test_wait_for_server_reports_a_failed_host_startup() -> None:
    async def scenario() -> None:
        server, task = await _serve(_lifespan_app([], fail_startup=True))
        try:
            await runner._wait_for_server(server, task, timeout_seconds=10.0)
        finally:
            await runner._stop_server(server, task, timeout_seconds=1.0)

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="failed to start"):
        asyncio.run(scenario())
    assert time.monotonic() - started < 10.0


class _ExitingServer:
    """Stands in for uvicorn releases that exit the process on failed startup."""

    started = False
    should_exit = False
    force_exit = False

    async def serve(self) -> None:
        raise SystemExit(3)


def test_a_host_that_exits_on_failed_startup_is_reported_not_fatal() -> None:
    async def scenario() -> None:
        server: Any = _ExitingServer()
        task = asyncio.create_task(runner._serve_host(server))
        try:
            await runner._wait_for_server(server, task, timeout_seconds=5.0)
        finally:
            await runner._stop_server(server, task, timeout_seconds=1.0)

    with pytest.raises(RuntimeError, match="failed to start"):
        asyncio.run(scenario())
