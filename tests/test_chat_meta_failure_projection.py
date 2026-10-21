"""The mounted metadata route projects only a workflow's declared failure text."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from mozaiksai.core.auth import require_user_scope
from mozaiksai.core.workflow import session_manager
from mozaiksai.core.workflow.workflow_manager import get_workflow_manager
from mozaiksai.hosts.routers import chat


@pytest.fixture
def metadata_route(monkeypatch):
    state = SimpleNamespace(
        config={"failure_message_key": "build_feedback"},
        document={
            "_id": "chat-1", "app_id": "app-1", "user_id": "owner",
            "workflow_name": "ExampleWorkflow", "status": 2,
            "build_feedback": "The page could not be validated.\nFix its action binding.",
            "private_context": "must not be exposed", "unrelated_feedback": "not the declared key",
        },
        reads=[],
        principal=SimpleNamespace(user_id="owner"),
    )

    async def find_one(query, projection):
        state.reads.append((query, projection))
        if any(state.document.get(key) != value for key, value in query.items()):
            return None
        return {key: value for key, value in state.document.items() if key in projection}

    monkeypatch.setattr(chat.runtime_app, "_chat_coll", AsyncMock(return_value=SimpleNamespace(find_one=find_one)))
    state.history = AsyncMock(return_value=[])
    monkeypatch.setattr(chat.runtime_app.persistence_manager, "load_run_history", state.history)
    monkeypatch.setattr(session_manager, "get_workflow_session", AsyncMock(return_value=None))
    monkeypatch.setattr(get_workflow_manager(), "get_config", lambda name: state.config)
    app = FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[require_user_scope] = lambda: state.principal
    state.app = app
    return state


async def _get(state, *, app_id="app-1", workflow="ExampleWorkflow", chat_id="chat-1"):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=state.app), base_url="http://test") as client:
        response = await client.get(f"/api/chats/meta/{app_id}/{workflow}/{chat_id}")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_mounted_route_projects_declared_failure_from_one_owned_snapshot(metadata_route):
    state = metadata_route
    payload = await _get(state)
    assert payload["failure_message"] == state.document["build_feedback"]
    assert payload["status"] == 2
    assert len(state.reads) == 1
    query, projection = state.reads[0]
    assert query == {"_id": "chat-1", "app_id": "app-1", "user_id": "owner", "workflow_name": "ExampleWorkflow"}
    assert projection["build_feedback"] == 1
    assert "private_context" not in projection
    assert "unrelated_feedback" not in projection
    assert "build_feedback" not in payload
    assert "private_context" not in payload
    assert "unrelated_feedback" not in payload
    state.history.assert_awaited_once_with(chat_id="chat-1", app_id="app-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [0, 1, "2", "failed", None])
async def test_nonfailed_record_cannot_project_stale_failure(metadata_route, status):
    metadata_route.document["status"] = status
    assert (await _get(metadata_route))["failure_message"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "", "  \n", 2, {"text": "nested context"}, ["nested context"]])
async def test_failure_projection_requires_nonblank_string(metadata_route, value):
    metadata_route.document["build_feedback"] = value
    assert (await _get(metadata_route))["failure_message"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("config", [{}, {"failure_message_key": None}, {"failure_message_key": "other_feedback"}])
async def test_missing_config_or_declared_value_does_not_guess_failure_key(metadata_route, config):
    metadata_route.config = config
    assert (await _get(metadata_route))["failure_message"] is None
    if not config.get("failure_message_key"):
        assert "build_feedback" not in metadata_route.reads[0][1]


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["user_id", "app_id", "workflow_name", "_id"])
async def test_foreign_session_cannot_expose_failure_or_load_history(metadata_route, scope):
    metadata_route.document[scope] = "foreign"
    assert await _get(metadata_route) == {"exists": False}
    assert len(metadata_route.reads) == 1
    metadata_route.history.assert_not_awaited()


def test_production_host_mounts_the_tested_chat_metadata_handler():
    from mozaiksai.hosts import platform

    assert platform._chat_router is chat.router
    assert any(
        getattr(route, "original_router", None) is chat.router
        or getattr(route, "endpoint", None) is chat.chat_meta
        for route in platform.app.routes
    )
