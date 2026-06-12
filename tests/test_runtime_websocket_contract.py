from __future__ import annotations

from types import SimpleNamespace

import pytest


class _FakeWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.sent: list[dict] = []
        self.closed: list[tuple[int | None, str | None]] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def close(self, code: int | None = None, reason: str | None = None) -> None:
        self.closed.append((code, reason))


class _MemoryCollection:
    def __init__(self, docs: list[dict] | None = None) -> None:
        self._docs = {doc["_id"]: dict(doc) for doc in docs or []}

    async def find_one(self, query, projection=None, sort=None):  # noqa: ANN001
        _ = projection
        _ = sort
        for doc in self._docs.values():
            if all(doc.get(key) == value for key, value in query.items()):
                return dict(doc)
        return None

    async def update_one(self, filter_query, update, upsert=False):  # noqa: ANN001
        _ = upsert
        for doc_id, doc in self._docs.items():
            if all(doc.get(key) == value for key, value in filter_query.items()):
                updated = dict(doc)
                updated.update((update or {}).get("$set") or {})
                self._docs[doc_id] = updated
                return


class _FakeTransport:
    def __init__(self, *, connections: dict[str, dict] | None = None) -> None:
        self.connections = dict(connections or {})
        self.handle_websocket_calls: list[dict] = []
        self.api_calls: list[dict] = []
        self.ui_events: list[tuple[dict, str]] = []

    async def handle_websocket(self, **kwargs) -> None:  # noqa: ANN003
        self.handle_websocket_calls.append(kwargs)

    async def handle_user_input_from_api(self, **kwargs) -> None:  # noqa: ANN003
        self.api_calls.append(kwargs)

    async def send_event_to_ui(self, payload: dict, chat_id: str) -> None:
        self.ui_events.append((payload, chat_id))


class _FakeSessionRouter:
    def __init__(self, *, resume_resolution: dict, snapshot: dict | None = None) -> None:
        self.resume_resolution = dict(resume_resolution)
        self.snapshot = dict(snapshot or {})
        self.resolve_calls: list[dict] = []
        self.bind_calls: list[dict] = []
        self.snapshot_calls: list[dict] = []

    async def resolve_resume(self, **kwargs):  # noqa: ANN003
        self.resolve_calls.append(kwargs)
        return dict(self.resume_resolution)

    async def bind_workflow_session(self, **kwargs) -> None:  # noqa: ANN003
        self.bind_calls.append(kwargs)

    async def get_session_snapshot(self, **kwargs):  # noqa: ANN003
        self.snapshot_calls.append(kwargs)
        return dict(self.snapshot)


def _patch_runtime_websocket_harness(
    monkeypatch: pytest.MonkeyPatch,
    *,
    chat_docs: list[dict],
    resume_resolution: dict,
    workflow_startup_mode: str,
    workflow_names: list[str] | None = None,
):
    from mozaiksai.core.runtime.composition.platform_hooks import get_platform_hooks
    from mozaiksai.core.session import router as session_router_module
    from mozaiksai.core.transport import session_registry as session_registry_module
    from mozaiksai.core.workflow.pack import graph as workflow_graph_module
    from mozaiksai.core.workflow.workflow_manager import workflow_manager
    from mozaiksai.hosts import runtime as runtime_app

    collection = _MemoryCollection(chat_docs)
    loaded_workflow_names = list(workflow_names or ["AgentGenerator"])
    transport = _FakeTransport(
        connections={
            str(resume_resolution.get("chat_id") or "chat_agent_1"): {
                "websocket": object(),
            }
        }
    )
    session_router = _FakeSessionRouter(
        resume_resolution=resume_resolution,
        snapshot=resume_resolution.get("session_state") or {},
    )
    created_sessions: list[dict] = []
    added_workflows: list[dict] = []
    removed_sessions: list[int] = []
    scheduled_coroutines = []

    async def fake_chat_coll():
        return collection

    async def fake_auth(websocket, path_user_id: str, path_app_id: str, path_chat_id: str):  # noqa: ANN001
        _ = websocket
        _ = path_app_id
        _ = path_chat_id
        return SimpleNamespace(user_id=path_user_id)

    async def fake_create_chat_session(
        chat_id: str,
        app_id: str,
        workflow_name: str,
        user_id: str,
        extra_fields: dict | None = None,
    ) -> None:
        created_sessions.append(
            {
                "chat_id": chat_id,
                "app_id": app_id,
                "workflow_name": workflow_name,
                "user_id": user_id,
                "extra_fields": dict(extra_fields or {}),
            }
        )
        doc = {
            "_id": chat_id,
            "app_id": app_id,
            "workflow_name": workflow_name,
            "user_id": user_id,
            "status": 0,
            "messages": [],
        }
        doc.update(extra_fields or {})
        collection._docs[chat_id] = doc

    async def fake_get_or_assign_cache_seed(chat_id: str, app_id: str) -> str:
        return f"seed:{app_id}:{chat_id}"

    async def fake_load_run_history(*, chat_id: str, app_id: str):
        _ = app_id
        doc = collection._docs.get(chat_id) or {}
        return list(doc.get("messages") or [])

    async def fake_chat_prereqs(**kwargs):  # noqa: ANN003
        return True, None

    def fake_create_task(coro):  # noqa: ANN001
        scheduled_coroutines.append(coro)
        return SimpleNamespace(cancel=lambda: None)

    monkeypatch.setattr(runtime_app, "_chat_coll", fake_chat_coll)
    monkeypatch.setattr(runtime_app, "simple_transport", transport)
    monkeypatch.setattr(runtime_app, "authenticate_websocket_with_path_binding", fake_auth)
    monkeypatch.setattr(runtime_app.persistence_manager, "create_chat_session", fake_create_chat_session)
    monkeypatch.setattr(runtime_app.persistence_manager, "get_or_assign_cache_seed", fake_get_or_assign_cache_seed)
    monkeypatch.setattr(runtime_app.persistence_manager, "load_run_history", fake_load_run_history)
    monkeypatch.setattr(runtime_app.asyncio, "create_task", fake_create_task)

    monkeypatch.setattr(session_router_module, "get_session_router", lambda: session_router)
    monkeypatch.setattr(session_registry_module.session_registry, "add_workflow", lambda **kwargs: added_workflows.append(kwargs))
    monkeypatch.setattr(session_registry_module.session_registry, "remove_session", lambda ws_id: removed_sessions.append(ws_id))

    hooks = get_platform_hooks()
    monkeypatch.setattr(hooks, "call_chat_prereqs", fake_chat_prereqs)
    monkeypatch.setattr(hooks, "call_workflow_ordering", lambda names: list(names))
    monkeypatch.setattr(
        hooks,
        "call_workflow_name_resolver",
        lambda requested_workflow_name, workflow_names: next(
            (name for name in workflow_names if name.lower() == str(requested_workflow_name or "").lower()),
            None,
        ),
    )

    monkeypatch.setattr(workflow_manager, "get_all_workflow_names", lambda: list(loaded_workflow_names))
    monkeypatch.setattr(workflow_manager, "get_config", lambda workflow_name: {"workflow_startup_mode": workflow_startup_mode})
    monkeypatch.setattr(workflow_manager, "reload_workflow", lambda workflow_name: None)
    monkeypatch.setattr(workflow_graph_module, "workflow_declares_task_batches", lambda workflow_name: False)

    return SimpleNamespace(
        runtime_app=runtime_app,
        hooks=hooks,
        transport=transport,
        session_router=session_router,
        collection=collection,
        created_sessions=created_sessions,
        added_workflows=added_workflows,
        removed_sessions=removed_sessions,
        scheduled_coroutines=scheduled_coroutines,
    )


async def _drain_scheduled_coroutines(scheduled_coroutines: list) -> None:
    for coro in scheduled_coroutines:
        await coro


@pytest.mark.asyncio
async def test_runtime_websocket_endpoint_uses_resolved_resume_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _patch_runtime_websocket_harness(
        monkeypatch,
        chat_docs=[
            {
                "_id": "chat_requested",
                "app_id": "app_1",
                "user_id": "user_1",
                "workflow_name": "AgentGenerator",
                "status": 0,
                "messages": [{"role": "user", "content": "Already started"}],
            },
            {
                "_id": "chat_resumed",
                "app_id": "app_1",
                "user_id": "user_1",
                "workflow_name": "AgentGenerator",
                "status": 0,
                "messages": [{"role": "assistant", "content": "In progress"}],
            },
        ],
        resume_resolution={
            "chat_id": "chat_resumed",
            "session_state": {
                "current_chat_id": "chat_resumed",
                "journey_position": 2,
                "lifecycle_state": "active",
            },
        },
        workflow_startup_mode="Manual",
    )
    websocket = _FakeWebSocket()

    await harness.runtime_app.websocket_endpoint(
        websocket=websocket,
        workflow_name="AgentGenerator",
        app_id="app_1",
        chat_id="chat_requested",
        user_id="user_1",
    )
    await _drain_scheduled_coroutines(harness.scheduled_coroutines)

    assert websocket.closed == []
    assert harness.session_router.resolve_calls == [
        {
            "app_id": "app_1",
            "user_id": "user_1",
            "requested_workflow_id": "AgentGenerator",
            "requested_chat_id": "chat_requested",
        }
    ]
    assert harness.transport.handle_websocket_calls == [
        {
            "websocket": websocket,
            "chat_id": "chat_resumed",
            "user_id": "user_1",
            "workflow_name": "AgentGenerator",
            "app_id": "app_1",
            "ws_id": harness.transport.handle_websocket_calls[0]["ws_id"],
        }
    ]
    assert harness.added_workflows == [
        {
            "ws_id": harness.added_workflows[0]["ws_id"],
            "chat_id": "chat_resumed",
            "workflow_name": "AgentGenerator",
            "app_id": "app_1",
            "user_id": "user_1",
            "auto_activate": True,
        }
    ]
    assert harness.removed_sessions == [harness.added_workflows[0]["ws_id"]]
    assert harness.transport.ui_events == [
        (
            {
                "kind": "chat_meta",
                "chat_id": "chat_resumed",
                "workflow_name": "AgentGenerator",
                "app_id": "app_1",
                "user_id": "user_1",
                "has_children": False,
                "cache_seed": "seed:app_1:chat_resumed",
                "chat_exists": True,
                "last_artifact": None,
                "status": 0,
                "run_history_count": 1,
                "created_at": None,
                "session_state": {
                    "current_chat_id": "chat_resumed",
                    "journey_position": 2,
                    "lifecycle_state": "active",
                },
            },
            "chat_resumed",
        )
    ]
    assert harness.transport.api_calls == []


@pytest.mark.asyncio
async def test_runtime_websocket_endpoint_emits_chat_error_when_prereqs_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _patch_runtime_websocket_harness(
        monkeypatch,
        chat_docs=[
            {
                "_id": "chat_agent_1",
                "app_id": "app_1",
                "user_id": "user_1",
                "workflow_name": "AgentGenerator",
                "status": 0,
                "messages": [],
            }
        ],
        resume_resolution={
            "chat_id": "chat_agent_1",
            "session_state": None,
        },
        workflow_startup_mode="AgentDriven",
    )
    websocket = _FakeWebSocket()

    async def fail_chat_prereqs(**kwargs):  # noqa: ANN003
        return False, "Studio setup is incomplete."

    monkeypatch.setattr(harness.hooks, "call_chat_prereqs", fail_chat_prereqs)

    await harness.runtime_app.websocket_endpoint(
        websocket=websocket,
        workflow_name="AgentGenerator",
        app_id="app_1",
        chat_id="chat_agent_1",
        user_id="user_1",
    )

    assert websocket.accepted is True
    assert websocket.sent == [
        {
            "type": "chat.error",
            "data": {
                "message": "Studio setup is incomplete.",
                "error_code": "WORKFLOW_PREREQS_NOT_MET",
                "workflow_name": "AgentGenerator",
                "chat_id": "chat_agent_1",
            },
            "timestamp": websocket.sent[0]["timestamp"],
        }
    ]
    assert websocket.closed == [(1008, "Prerequisites not met")]
    assert harness.session_router.resolve_calls == []
    assert harness.transport.handle_websocket_calls == []
    assert harness.transport.ui_events == []
    assert harness.transport.api_calls == []
    assert harness.added_workflows == []
    assert harness.removed_sessions == []
    assert harness.scheduled_coroutines == []


@pytest.mark.asyncio
async def test_runtime_websocket_endpoint_emits_validation_error_when_prereq_check_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _patch_runtime_websocket_harness(
        monkeypatch,
        chat_docs=[
            {
                "_id": "chat_agent_1",
                "app_id": "app_1",
                "user_id": "user_1",
                "workflow_name": "AgentGenerator",
                "status": 0,
                "messages": [],
            }
        ],
        resume_resolution={
            "chat_id": "chat_agent_1",
            "session_state": None,
        },
        workflow_startup_mode="AgentDriven",
    )
    websocket = _FakeWebSocket()

    async def raise_chat_prereqs(**kwargs):  # noqa: ANN003
        raise RuntimeError("persistence unavailable")

    monkeypatch.setattr(harness.hooks, "call_chat_prereqs", raise_chat_prereqs)

    await harness.runtime_app.websocket_endpoint(
        websocket=websocket,
        workflow_name="AgentGenerator",
        app_id="app_1",
        chat_id="chat_agent_1",
        user_id="user_1",
    )

    assert websocket.accepted is True
    assert websocket.sent == [
        {
            "type": "chat.error",
            "data": {
                "message": "Failed to validate workflow prerequisites. Please try again.",
                "error_code": "PREREQ_VALIDATION_ERROR",
                "workflow_name": "AgentGenerator",
                "chat_id": "chat_agent_1",
            },
            "timestamp": websocket.sent[0]["timestamp"],
        }
    ]
    assert websocket.closed == [(1011, "Prerequisite validation failed")]
    assert harness.session_router.resolve_calls == []
    assert harness.transport.handle_websocket_calls == []
    assert harness.transport.ui_events == []
    assert harness.transport.api_calls == []
    assert harness.added_workflows == []
    assert harness.removed_sessions == []
    assert harness.scheduled_coroutines == []


@pytest.mark.asyncio
async def test_runtime_websocket_endpoint_auto_starts_empty_agent_driven_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _patch_runtime_websocket_harness(
        monkeypatch,
        chat_docs=[
            {
                "_id": "chat_agent_1",
                "app_id": "app_1",
                "user_id": "user_1",
                "workflow_name": "AgentGenerator",
                "status": 0,
                "messages": [],
            }
        ],
        resume_resolution={
            "chat_id": "chat_agent_1",
            "session_state": None,
        },
        workflow_startup_mode="AgentDriven",
    )
    websocket = _FakeWebSocket()

    await harness.runtime_app.websocket_endpoint(
        websocket=websocket,
        workflow_name="AgentGenerator",
        app_id="app_1",
        chat_id="chat_agent_1",
        user_id="user_1",
    )
    await _drain_scheduled_coroutines(harness.scheduled_coroutines)

    assert websocket.closed == []
    assert harness.transport.api_calls == [
        {
            "chat_id": "chat_agent_1",
            "user_id": "user_1",
            "workflow_name": "AgentGenerator",
            "message": None,
            "app_id": "app_1",
        }
    ]
    assert harness.transport.connections["chat_agent_1"]["autostarted"] is True
    assert harness.transport.handle_websocket_calls[0]["chat_id"] == "chat_agent_1"
    assert harness.transport.ui_events[0][0]["chat_id"] == "chat_agent_1"
    assert harness.transport.ui_events[0][0]["run_history_count"] == 0


@pytest.mark.asyncio
async def test_runtime_websocket_endpoint_rejects_unowned_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _patch_runtime_websocket_harness(
        monkeypatch,
        chat_docs=[
            {
                "_id": "chat_owned_elsewhere",
                "app_id": "app_1",
                "user_id": "other_user",
                "workflow_name": "AgentGenerator",
                "status": 0,
                "messages": [],
            }
        ],
        resume_resolution={
            "chat_id": "chat_owned_elsewhere",
            "session_state": None,
        },
        workflow_startup_mode="Manual",
    )
    websocket = _FakeWebSocket()

    await harness.runtime_app.websocket_endpoint(
        websocket=websocket,
        workflow_name="AgentGenerator",
        app_id="app_1",
        chat_id="chat_owned_elsewhere",
        user_id="user_1",
    )

    assert websocket.accepted is False
    assert websocket.sent == []
    assert websocket.closed == [(1008, "Chat not found")]
    assert harness.session_router.resolve_calls == []
    assert harness.transport.handle_websocket_calls == []
    assert harness.transport.ui_events == []
    assert harness.transport.api_calls == []
    assert harness.added_workflows == []
    assert harness.removed_sessions == []
    assert harness.scheduled_coroutines == []


@pytest.mark.asyncio
async def test_runtime_websocket_endpoint_honors_persisted_workflow_for_stale_client_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _patch_runtime_websocket_harness(
        monkeypatch,
        chat_docs=[
            {
                "_id": "chat_agent_1",
                "app_id": "app_1",
                "user_id": "user_1",
                "workflow_name": "AgentGenerator",
                "status": 0,
                "messages": [{"role": "assistant", "content": "Resume me"}],
            }
        ],
        resume_resolution={
            "chat_id": "chat_agent_1",
            "session_state": None,
        },
        workflow_startup_mode="Manual",
        workflow_names=["DesignDocs", "AgentGenerator"],
    )
    websocket = _FakeWebSocket()

    await harness.runtime_app.websocket_endpoint(
        websocket=websocket,
        workflow_name="DesignDocs",
        app_id="app_1",
        chat_id="chat_agent_1",
        user_id="user_1",
    )
    await _drain_scheduled_coroutines(harness.scheduled_coroutines)

    assert websocket.closed == []
    assert harness.session_router.resolve_calls == [
        {
            "app_id": "app_1",
            "user_id": "user_1",
            "requested_workflow_id": "AgentGenerator",
            "requested_chat_id": "chat_agent_1",
        }
    ]
    assert harness.transport.handle_websocket_calls == [
        {
            "websocket": websocket,
            "chat_id": "chat_agent_1",
            "user_id": "user_1",
            "workflow_name": "AgentGenerator",
            "app_id": "app_1",
            "ws_id": harness.transport.handle_websocket_calls[0]["ws_id"],
        }
    ]
    assert harness.transport.ui_events == [
        (
            {
                "kind": "chat_meta",
                "chat_id": "chat_agent_1",
                "workflow_name": "AgentGenerator",
                "app_id": "app_1",
                "user_id": "user_1",
                "has_children": False,
                "cache_seed": "seed:app_1:chat_agent_1",
                "chat_exists": True,
                "last_artifact": None,
                "status": 0,
                "run_history_count": 1,
                "created_at": None,
                "session_state": {},
            },
            "chat_agent_1",
        )
    ]
    assert harness.transport.api_calls == []


@pytest.mark.asyncio
async def test_runtime_websocket_endpoint_repairs_non_runnable_persisted_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _patch_runtime_websocket_harness(
        monkeypatch,
        chat_docs=[
            {
                "_id": "chat_agent_1",
                "app_id": "app_1",
                "user_id": "user_1",
                "workflow_name": "LegacyWorkflow",
                "status": 0,
                "messages": [{"role": "assistant", "content": "Recover me"}],
            }
        ],
        resume_resolution={
            "chat_id": "chat_agent_1",
            "session_state": None,
        },
        workflow_startup_mode="Manual",
        workflow_names=["AgentGenerator"],
    )
    websocket = _FakeWebSocket()

    await harness.runtime_app.websocket_endpoint(
        websocket=websocket,
        workflow_name="AgentGenerator",
        app_id="app_1",
        chat_id="chat_agent_1",
        user_id="user_1",
    )
    await _drain_scheduled_coroutines(harness.scheduled_coroutines)

    assert websocket.closed == []
    assert harness.created_sessions == []
    assert harness.collection._docs["chat_agent_1"]["workflow_name"] == "AgentGenerator"
    assert "last_updated_at" in harness.collection._docs["chat_agent_1"]
    assert harness.session_router.resolve_calls == [
        {
            "app_id": "app_1",
            "user_id": "user_1",
            "requested_workflow_id": "AgentGenerator",
            "requested_chat_id": "chat_agent_1",
        }
    ]
    assert harness.transport.handle_websocket_calls == [
        {
            "websocket": websocket,
            "chat_id": "chat_agent_1",
            "user_id": "user_1",
            "workflow_name": "AgentGenerator",
            "app_id": "app_1",
            "ws_id": harness.transport.handle_websocket_calls[0]["ws_id"],
        }
    ]
    assert harness.transport.ui_events == [
        (
            {
                "kind": "chat_meta",
                "chat_id": "chat_agent_1",
                "workflow_name": "AgentGenerator",
                "app_id": "app_1",
                "user_id": "user_1",
                "has_children": False,
                "cache_seed": "seed:app_1:chat_agent_1",
                "chat_exists": True,
                "last_artifact": None,
                "status": 0,
                "run_history_count": 1,
                "created_at": None,
                "session_state": {},
            },
            "chat_agent_1",
        )
    ]
    assert harness.transport.api_calls == []


@pytest.mark.asyncio
async def test_runtime_websocket_endpoint_backfills_missing_resolved_chat_before_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _patch_runtime_websocket_harness(
        monkeypatch,
        chat_docs=[
            {
                "_id": "chat_requested",
                "app_id": "app_1",
                "user_id": "user_1",
                "workflow_name": "AgentGenerator",
                "status": 0,
                "messages": [{"role": "assistant", "content": "Original link"}],
            }
        ],
        resume_resolution={
            "chat_id": "chat_recovered",
            "session_state": None,
        },
        workflow_startup_mode="Manual",
        workflow_names=["AgentGenerator"],
    )
    harness.session_router.snapshot = {
        "current_chat_id": "chat_recovered",
        "journey_position": 1,
        "lifecycle_state": "active",
    }
    websocket = _FakeWebSocket()

    await harness.runtime_app.websocket_endpoint(
        websocket=websocket,
        workflow_name="AgentGenerator",
        app_id="app_1",
        chat_id="chat_requested",
        user_id="user_1",
    )
    await _drain_scheduled_coroutines(harness.scheduled_coroutines)

    assert websocket.closed == []
    assert harness.created_sessions == [
        {
            "chat_id": "chat_recovered",
            "app_id": "app_1",
            "workflow_name": "AgentGenerator",
            "user_id": "user_1",
            "extra_fields": {},
        }
    ]
    assert harness.session_router.resolve_calls == [
        {
            "app_id": "app_1",
            "user_id": "user_1",
            "requested_workflow_id": "AgentGenerator",
            "requested_chat_id": "chat_requested",
        }
    ]
    assert harness.session_router.bind_calls == [
        {
            "app_id": "app_1",
            "user_id": "user_1",
            "workflow_id": "AgentGenerator",
            "chat_id": "chat_recovered",
        }
    ]
    assert harness.session_router.snapshot_calls == [
        {
            "app_id": "app_1",
            "user_id": "user_1",
        }
    ]
    assert harness.collection._docs["chat_recovered"]["workflow_name"] == "AgentGenerator"
    assert harness.transport.handle_websocket_calls == [
        {
            "websocket": websocket,
            "chat_id": "chat_recovered",
            "user_id": "user_1",
            "workflow_name": "AgentGenerator",
            "app_id": "app_1",
            "ws_id": harness.transport.handle_websocket_calls[0]["ws_id"],
        }
    ]
    assert harness.transport.ui_events == [
        (
            {
                "kind": "chat_meta",
                "chat_id": "chat_recovered",
                "workflow_name": "AgentGenerator",
                "app_id": "app_1",
                "user_id": "user_1",
                "has_children": False,
                "cache_seed": "seed:app_1:chat_recovered",
                "chat_exists": True,
                "last_artifact": None,
                "status": 0,
                "run_history_count": 0,
                "created_at": None,
                "session_state": {
                    "current_chat_id": "chat_recovered",
                    "journey_position": 1,
                    "lifecycle_state": "active",
                },
            },
            "chat_recovered",
        )
    ]
    assert harness.transport.api_calls == []

