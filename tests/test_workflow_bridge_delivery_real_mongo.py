"""A first reply after restart reaches real AG2 through the real runtime host.

The two hosts are separate OS processes sharing only Mongo. Only provider HTTP
is faked, using the existing runtime smoke fixture. Supply MONGO_URI to run;
MOZAIKS_REQUIRE_REAL_MONGO=1 makes an unavailable database a failure.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest
import yaml
from pymongo import MongoClient
from websockets.asyncio.client import connect

from scripts import run_live_workflow_smoke as smoke
from tests import test_run_live_workflow_smoke_runtime_host as smoke_fixtures
from tests.test_run_live_workflow_smoke_runtime_host import (
    SMOKE_USER,
    WORKFLOW,
    _free_port,
    _write_workflow,
    provider,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def identity_provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    yield from smoke_fixtures.identity_provider.__wrapped__(monkeypatch)


@pytest.fixture
def mongo_uri(monkeypatch: pytest.MonkeyPatch) -> str:
    return smoke_fixtures.mongo_uri.__wrapped__(monkeypatch)


def _provider_requests(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _child_environment(workflows: Path, uri: str) -> dict[str, str]:
    # No developer credentials, dotenv files, or inherited host policy enter a
    # child. The only authentication configuration is the fixture's JWT issuer.
    allowed = {
        "SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "COMSPEC", "TEMP", "TMP",
        "USERPROFILE", "HOME", "HOMEDRIVE", "HOMEPATH",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update({name: os.environ[name] for name in (
        "AUTH_ENABLED", "AUTH_PROVIDER", "AUTH_ISSUER", "AUTH_JWKS_URL", "AUTH_AUDIENCE",
    )})
    env.update({
        "PYTHONPATH": str(ROOT),
        "PYTHON_DOTENV_DISABLED": "1",
        "PYTHONUNBUFFERED": "1",
        "ENV": "test",
        "MONGO_URI": uri,
        "MOZAIKS_WORKFLOWS_PATH": str(workflows),
        "OPENAI_API_KEY": "test-only",
        "OPENAI_BASE_URL": "https://provider.invalid/v1",
    })
    return env


@contextmanager
def _host_process(directory: Path, workflows: Path, uri: str) -> Iterator[SimpleNamespace]:
    directory.mkdir()
    port = _free_port()
    requests = directory / "provider-requests.jsonl"
    ready = directory / "ready"
    with (directory / "host.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--host-child", str(port), str(requests), str(ready)],
            cwd=ROOT,
            env=_child_environment(workflows, uri),
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            deadline = time.monotonic() + 45
            while not ready.exists():
                if process.poll() is not None or time.monotonic() >= deadline:
                    log.flush()
                    pytest.fail(f"Runtime host did not start: {(directory / 'host.log').read_text(encoding='utf-8')}")
                time.sleep(0.05)
            yield SimpleNamespace(port=port, url=f"http://127.0.0.1:{port}", requests=requests, pid=process.pid)
        finally:
            if process.poll() is None:
                assert process.stdin is not None
                process.stdin.write("stop\n")
                process.stdin.flush()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                    pytest.fail(f"Runtime host {process.pid} did not shut down normally; see {directory / 'host.log'}")
            if process.stdin is not None:
                process.stdin.close()
            assert process.returncode == 0, f"Runtime host exited {process.returncode}; see {directory / 'host.log'}"


async def _websocket_reply(
    port: int, app_id: str, chat_id: str, token: str, reply: str, evidence: Path,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    url = f"ws://127.0.0.1:{port}/ws/{WORKFLOW}/{app_id}/{chat_id}/{SMOKE_USER}"
    async with connect(url, subprotocols=smoke._websocket_auth_subprotocols(token)) as websocket:
        await websocket.send(json.dumps(smoke._build_workflow_user_reply_message(chat_id, reply)))
        async with asyncio.timeout(30):
            async for raw in websocket:
                event = json.loads(raw)
                events.append(event)
                evidence.write_text(json.dumps(events, indent=2), encoding="utf-8")
                assert event.get("type") not in {"chat.error", "error"}, event
                assert (event.get("data") or {}).get("status") != "failed", event
                if smoke._is_terminal_completion_event(event):
                    return events
    pytest.fail(f"No completed outcome for the only reply: {events}")


def _channels(database: Any, app_id: str, chat_id: str) -> set[str]:
    docs = database.AG2NetworkKnowledge.find({"app_id": app_id, "chat_id": chat_id})
    return {
        str(doc["path"]).split("/")[2]
        for doc in docs
        if str(doc["path"]).startswith("/channels/") and str(doc["path"]).endswith("/metadata.json")
    }


@pytest.mark.parametrize("transport", ["http", "websocket"])
def test_first_reply_after_process_restart_reaches_agent(
    transport: str,
    tmp_path: Path,
    mongo_uri: str,
    identity_provider: SimpleNamespace,
) -> None:
    identity_provider.enable()
    token = identity_provider.token(SMOKE_USER)
    app_id = f"delivery-{uuid4().hex}"
    prompt = f"Check the launch boundary for {app_id}."
    reply = f"Finish the check for {app_id}."
    workflows = tmp_path / "workflows"
    _write_workflow(workflows / WORKFLOW)
    orchestrator_path = workflows / WORKFLOW / "orchestrator.yaml"
    orchestrator = yaml.safe_load(orchestrator_path.read_text(encoding="utf-8"))
    orchestrator["initial_agent"] = "AskAgent"
    orchestrator_path.write_text(yaml.safe_dump(orchestrator), encoding="utf-8")

    with MongoClient(mongo_uri, serverSelectionTimeoutMS=2000) as mongo:
        database = mongo["mozaiksai"]
        try:
            with _host_process(tmp_path / "before", workflows, mongo_uri) as before:
                with httpx.Client(base_url=before.url, headers={"Authorization": f"Bearer {token}"}, timeout=60) as client:
                    denied = httpx.post(f"{before.url}/api/chats/{app_id}/{WORKFLOW}/start", json={})
                    assert denied.status_code in {401, 403}
                    started = client.post(f"/api/chats/{app_id}/{WORKFLOW}/start", json={"user_id": SMOKE_USER, "force_new": True})
                    assert started.status_code == 200, started.text
                    chat_id = started.json()["chat_id"]
                    route = f"/chat/{app_id}/{chat_id}/{SMOKE_USER}/input"
                    first = client.post(route, json={"message": prompt, "workflow_name": WORKFLOW})
                    assert first.status_code == 200, first.text
                    assert first.json()["result"]["run_status"] == "paused", first.text
                first_requests = _provider_requests(before.requests)
                assert [item["model"] for item in first_requests] == ["AskAgent"]
                assert prompt in json.dumps(first_requests[0]["messages"])
                original_channels = _channels(database, app_id, chat_id)
                assert len(original_channels) == 1
                (tmp_path / "knowledge-before.json").write_text(json.dumps(list(
                    database.AG2NetworkKnowledge.find({"app_id": app_id}),
                ), default=str, indent=2), encoding="utf-8")

            # A new interpreter has no SimpleTransport singleton, live AG2
            # handle, workflow objects, or in-memory conversation history.
            with _host_process(tmp_path / "after", workflows, mongo_uri) as after:
                assert before.pid != after.pid
                if transport == "http":
                    response = httpx.post(
                        f"{after.url}{route}",
                        headers={"Authorization": f"Bearer {token}"},
                        json={"message": reply, "workflow_name": WORKFLOW},
                        timeout=60,
                    )
                    assert response.status_code == 200, response.text
                    assert response.json()["result"]["status"] == "success", response.text
                    assert response.json()["result"]["run_status"] == "completed", response.text
                else:
                    events = asyncio.run(_websocket_reply(
                        after.port, app_id, chat_id, token, reply, tmp_path / "websocket-events.json",
                    ))
                    assert any(smoke._is_terminal_completion_event(event) for event in events)

                resumed_requests = _provider_requests(after.requests)
                assert [item["model"] for item in resumed_requests] == ["FinishAgent"], resumed_requests
                assert json.dumps(resumed_requests[0]["messages"]).count(reply) == 1
                assert _channels(database, app_id, chat_id) == original_channels
                wal = database.AG2NetworkKnowledge.find_one({
                    "app_id": app_id, "chat_id": chat_id,
                    "path": f"/channels/{next(iter(original_channels))}/wal.jsonl",
                })
                assert wal is not None
                human_turns = [
                    event["event_data"]["text"]
                    for line in wal["content"].splitlines()
                    if (event := json.loads(line))["event_type"] == "ag2.msg.text"
                ]
                assert human_turns == [prompt, reply]
                user_events = list(database.AG2StreamEvents.find({"app_id": app_id, "event_class": "TextInput"}))
                assert len(user_events) == 2, user_events
                assert sum(reply in json.dumps(event["event_payload"]) for event in user_events) == 1
                assert sum(prompt in json.dumps(event["event_payload"]) for event in user_events) == 1
        finally:
            (tmp_path / "knowledge-after.json").write_text(json.dumps(list(
                database.AG2NetworkKnowledge.find({"app_id": app_id}),
            ), default=str, indent=2), encoding="utf-8")
            # This UUID app belongs exclusively to this test. Never drop a
            # shared database or touch another test's records.
            for name in database.list_collection_names():
                database[name].delete_many({"app_id": app_id})


async def _run_host_child(port: int, requests_path: Path, ready: Path) -> None:
    import uvicorn

    # Reuse the existing smoke provider configuration and packet boundary.
    # Runtime composition, authentication, transport, AG2 and storage are real.
    with pytest.MonkeyPatch.context() as patch:
        scenario = provider.__wrapped__(patch)
        original_response = scenario.respond

        def record(request: httpx.Request) -> httpx.Response:
            response = original_response(request)
            with requests_path.open("a", encoding="utf-8") as output:
                output.write(json.dumps(scenario.requests[-1]) + "\n")
            return response

        patch.setattr(scenario, "respond", record)
        from mozaiksai.hosts import runtime

        server = uvicorn.Server(smoke._build_uvicorn_config(runtime.app, port))
        task = asyncio.create_task(smoke._serve_host(server))
        try:
            await smoke._wait_for_server(server, task)
            ready.write_text(str(os.getpid()), encoding="utf-8")
            await asyncio.to_thread(sys.stdin.readline)
        finally:
            await smoke._stop_server(server, task)


if __name__ == "__main__":
    assert sys.argv[1] == "--host-child"
    asyncio.run(_run_host_child(int(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])))
