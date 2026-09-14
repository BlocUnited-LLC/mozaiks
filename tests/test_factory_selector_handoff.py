from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from mozaiksai.core.auth.dependencies import UserPrincipal, require_user_scope
from mozaiksai.core.data.models import WorkflowStatus
from mozaiksai.core.runtime.composition import platform_hooks
from mozaiksai.core.session import launcher
from mozaiksai.core.session import router as session_router
from mozaiksai.core.session.persistence import SessionStateStore
from mozaiksai.core.workflow.pack.config import load_global_pack_graph
from mozaiksai.hosts.routers import transitions
from tests.test_session_router import _FakePersistence

WORKSPACE = Path(__file__).resolve().parents[1]
HOST = "factory-host"
USER = "selector-owner"
TARGET = "selector-target"
SOURCE_CHAT = "theme-chat"


def _exercise_chat_page_handoff(requests: list[dict], responses: list[dict]) -> None:
    source = (WORKSPACE / "chat-ui/src/pages/ChatPage.js").read_text(encoding="utf-8")
    callback = source.split("const handlePendingTransitionNavigate = useCallback(\n", 1)[1]
    callback = callback.split("\n    },\n    [", 1)[0] + "\n    }"
    # Execute the production callback, as existing handoff tests do, without a browser/server.
    script = r"""
const assert = require('node:assert/strict');
const { callback, requests, responses } = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const appId = requests[0].app_id;
const user = { id: requests[0].user_id };
const auth = {};
const config = {};
let currentChatId = requests[0].source_chat_id;
let pendingTransitionId = requests[0].transition_id;
let pendingTransitionContext = requests[0].context_variables;
let closed = 0;
let calls = 0;
const navigations = [];
const remembered = [];
const wsRef = { current: { close: () => { closed++; } } };
const validatedChatIdRef = { current: currentChatId };
const queryResumeHandledRef = { current: currentChatId };
const currentWorkflowNameRef = { current: 'ThemeCapture' };
const workflowReplayPendingRef = { current: true };
const connectionInProgressRef = { current: true };
const setPendingTransitionId = value => { pendingTransitionId = value; };
const setPendingTransitionContext = value => { pendingTransitionContext = value; };
const setCurrentChatId = value => { currentChatId = value; };
const setWs = () => {};
const setActiveChatId = () => {};
const setCurrentWorkflowName = () => {};
const setActiveWorkflowName = () => {};
const setWorkflowCompleted = () => {};
const setCompletionData = () => {};
const setConnectionInitialized = () => {};
const setConversationMode = () => {};
const rememberWorkflowChatSession = (...args) => remembered.push(args);
const resolveKnownWorkflowName = value => value;
const navigate = url => navigations.push(url);
const authFetch = async (url, options, credentials) => {
  assert.equal(url, '/api/transitions/resolve');
  assert.equal(options.method, 'POST');
  assert.equal(credentials.auth, auth);
  assert.deepEqual(JSON.parse(options.body), requests[calls]);
  const data = responses[calls++];
  return { ok: true, json: async () => data };
};
const handle = eval('(' + callback + ')');
(async () => {
  // The coding choice now resolves straight into DesignDocs: there is no
  // intermediate database prompt to leave pending.
  assert.equal(await handle(requests[0].option_id), true);
  assert.equal(pendingTransitionId, null);
  assert.equal(currentChatId, 'design-chat');
  assert.equal(currentWorkflowNameRef.current, 'DesignDocs');
  assert.equal(closed, 1);
  assert.equal(calls, 1);
  assert.deepEqual(remembered, [['design-chat', 'DesignDocs']]);
  assert.deepEqual(navigations, ['/chat?mode=workflow&workflow=DesignDocs&chat_id=design-chat']);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        ["node", "--eval", script],
        input=json.dumps({"callback": callback, "requests": requests, "responses": responses}),
        cwd=WORKSPACE,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.asyncio
@pytest.mark.parametrize("coding_option", ["guided", "autonomous"])
async def test_normal_chat_coding_choice_goes_straight_to_design(
    monkeypatch, coding_option: str,
) -> None:
    """The build no longer stops to ask how MongoDB will be connected.

    Every option on the old database gate routed to DesignDocs and set the
    same provider; only a setup-mode string differed, and its own
    "skip for now" option stated that the schema can be designed now and the
    database connected later. So the answer was never needed to proceed, and
    asking a non-technical founder to choose a connection strategy bought
    nothing. The platform now defaults it and the choice moves to deployment.
    """
    pack = load_global_pack_graph(workflows_root=WORKSPACE / "factory_app/workflows")
    assert pack is not None
    monkeypatch.setattr(session_router, "load_global_pack_graph", lambda: pack)
    monkeypatch.setattr(launcher, "load_global_pack_graph", lambda: pack)
    persistence = _FakePersistence()
    store = SessionStateStore(persistence)
    router = session_router.SessionRouter(persistence=persistence, store=store)
    monkeypatch.setattr(session_router, "get_session_router", lambda: router)
    binding = {
        "build_registry_id": "registry-selector", "target_app_id": TARGET,
        "build_id": "build-selector", "phase": "genesis",
    }
    coll = await persistence._coll()
    for workflow, chat_id in [("ValueEngine", "value-chat"), ("ThemeCapture", SOURCE_CHAT)]:
        await coll.update_one({"_id": chat_id}, {"$set": {
            "app_id": HOST, "user_id": USER, "workflow_name": workflow,
            "status": int(WorkflowStatus.COMPLETED), "run_build_binding": binding,
        }}, upsert=True)

    async def verify_source(**kwargs):
        assert kwargs["app_id"] == HOST
        assert kwargs["user_id"] == USER
        assert kwargs["chat_id"] == SOURCE_CHAT
        assert kwargs["phase"] == "resume"
        assert kwargs["session_fields"]["run_build_binding"] == binding
        return kwargs["session_fields"]

    monkeypatch.setattr(platform_hooks, "get_platform_hooks", lambda: SimpleNamespace(
        call_chat_session_fields=verify_source,
    ))
    bound_router = router.for_target(TARGET)
    await bound_router.bind_workflow_session(
        app_id=HOST, user_id=USER, workflow_id="ThemeCapture", chat_id=SOURCE_CHAT,
        journey_id="build",
    )
    from mozaiksai.core.artifacts import store as artifact_store

    monkeypatch.setattr(artifact_store, "get_artifact_store", lambda: SimpleNamespace(
        get_current_build_record_refs=AsyncMock(return_value={}),
    ))
    advance = await bound_router.advance_journey_after_run_complete(
        app_id=HOST, user_id=USER, workflow_id="ThemeCapture", chat_id=SOURCE_CHAT,
    )
    assert advance is not None and advance.next_transition_id == "coding_journey_selector"

    # Exercise HTTP/routing with an in-memory store and stop before agent execution.
    launch_workflow = AsyncMock(return_value=SimpleNamespace(
        chat_id="design-chat", workflow_id="DesignDocs", requested_workflow_id="DesignDocs",
        journey_id="build", websocket_url="/unused", routing_explanation="transition",
        rerouted_by_dependency=False,
    ))
    monkeypatch.setattr(launcher, "launch_routed_workflow", launch_workflow)
    app = FastAPI()
    app.include_router(transitions.router)
    app.dependency_overrides[require_user_scope] = lambda: UserPrincipal(
        user_id=USER, app_id=HOST, provider="test", roles=[], scopes=["access_as_user"],
        raw_claims={}, email=None, name="Selector Owner",
    )
    requests = [{
        "transition_id": "coding_journey_selector", "source_chat_id": SOURCE_CHAT,
        "option_id": coding_option, "context_variables": {"app_type": "greenfield_app"},
        "app_id": HOST, "user_id": USER,
    }]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        coding = await client.post("/api/transitions/resolve", json=requests[0])
        assert coding.status_code == 200, coding.text
        first = coding.json()
    # One resolve, straight into design — no intermediate database prompt.
    assert first["resolution_type"] == "workflow", first
    assert first["workflow_id"] == "DesignDocs"
    expected_context = {
        "app_type": "greenfield_app", "coding_participation": coding_option,
        "design_docs_hitl": coding_option == "guided", "database_provider": "mongodb",
        "database_setup_mode": "local",
    }
    assert first["context_variables"] == expected_context
    launch_workflow.assert_awaited_once()
    kwargs = launch_workflow.await_args.kwargs
    assert kwargs["context_variables"] == expected_context
    assert kwargs["app_id"] == HOST
    assert kwargs["source_chat_id"] == SOURCE_CHAT
    assert kwargs["session_router"]._target_app_id == TARGET
    _exercise_chat_page_handoff(requests, [first])
