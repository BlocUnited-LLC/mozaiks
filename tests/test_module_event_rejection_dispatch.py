"""An event that breaks its declared contract never fails the action that emitted it.

The app is loaded by AppLoader and composed the way the platform host composes
it: one UnifiedEventDispatcher, a ModuleEventRouter registered on it for
reactions and notifications, and a ModuleExecutor whose event emitter is that
dispatcher. Actions are called through the real module router and through a
live workflow tool's module dispatch, and persist to the real Mongo
(MONGO_URI) in a disposable database.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from starlette.websockets import WebSocketState

from mozaiksai.core.audit.audit_logger import AuditLogger, AuditRecord
from mozaiksai.core.auth import optional_user
from mozaiksai.core.auth.adapters.base import UserClaims
from mozaiksai.core.auth.dependencies import UserPrincipal
from mozaiksai.core.auth.websocket_auth import WebSocketUser
from mozaiksai.core.events.unified_event_dispatcher import UnifiedEventDispatcher
from mozaiksai.core.runtime.app.loader import AppLoader
from mozaiksai.core.runtime.composition import module_executor
from mozaiksai.core.runtime.composition.executor_registry import ExecutorRegistry
from mozaiksai.core.runtime.composition.module_event_router import ModuleEventRouter
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
from mozaiksai.core.runtime.persistence import PersistencePrincipal
from mozaiksai.core.transport.simple_transport import SimpleTransport
from mozaiksai.core.workflow import module_tools
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge, _wrap_tool_with_context
from mozaiksai.core.workflow.context.authority import build_context_authority_policy
from mozaiksai.hosts.routers import modules as module_router

APP_ID = "event-rejection-app"
CANARY = "payload-canary-7f3a"
CREATED = "domain.task_board.task_created"
DELETED = "domain.task_board.task_deleted"

MODULE_YAML = """
schema_version: mozaiks.module.v1
module:
  id: task_board
  display_name: Task Board
  version: 1.0.0
  description: Tasks whose events carry the owner
  handler: backend.handler:TaskBoardModule
actions:
  - id: create_task
    description: Store a task, then emit a created event without the owner the schema requires
    handler_method: create_task
    input_schema: {type: object, required: [title], properties: {title: {type: string}}}
    emits: [domain.task_board.task_created]
  - id: create_task_valid
    description: Store a task, then emit a valid created event
    handler_method: create_task_valid
    input_schema: {type: object, required: [title], properties: {title: {type: string}}}
    emits: [domain.task_board.task_created]
  - id: create_task_raises
    description: Store a task, then raise
    handler_method: create_task_raises
    input_schema: {type: object, required: [title], properties: {title: {type: string}}}
    emits: [domain.task_board.task_created]
  - id: delete_task
    description: Delete a task, then emit a deleted event without the owner the schema requires
    handler_method: delete_task
    input_schema: {type: object, required: [task_id], properties: {task_id: {type: string}}}
    emits: [domain.task_board.task_deleted]
"""

EVENTS_YAML = """
schema_version: mozaiks.events.v1
events:
  - type: domain.task_board.task_created
    version: 1
    producer: task_board
    payload_schema:
      type: object
      required: [task_id, title, owner]
      properties: {task_id: {type: string}, title: {type: string}, owner: {type: string}}
  - type: domain.task_board.task_deleted
    version: 1
    producer: task_board
    payload_schema:
      type: object
      required: [task_id, owner]
      properties: {task_id: {type: string}, owner: {type: string}}
"""

REACTIONS_YAML = """
schema_version: mozaiks.reactions.v1
reactions:
  - id: task_created_handler
    event_type: domain.task_board.task_created
    target: {kind: handler, handler_method: on_task_created}
  - id: task_created_notify
    event_type: domain.task_board.task_created
    target: {kind: notification, notification_id: task_created}
"""

NOTIFICATIONS_YAML = """
schema_version: mozaiks.notifications.v1
notifications:
  - id: task_created
    event_type: domain.task_board.task_created
    channels: [in_app]
"""

HANDLER_PY = '''
from uuid import uuid4


class TaskBoardModule:
    def __init__(self):
        self.reactions = []

    def _tasks(self, ctx):
        return ctx.persistence.collection("task_board", "tasks")

    async def _store(self, ctx, title):
        task_id = f"task_{uuid4().hex[:12]}"
        await self._tasks(ctx).insert_one({"task_id": task_id, "title": title})
        return task_id

    @staticmethod
    def _outcome(rejection):
        # What a caller that must know learns from emit: None, or the rejection.
        return None if rejection is None else rejection.event_id

    async def create_task(self, ctx, *, title):
        task_id = await self._store(ctx, title)
        rejection = await ctx.emit("domain.task_board.task_created", {"task_id": task_id, "title": title})
        return {"task_id": task_id, "rejected_event_id": self._outcome(rejection)}

    async def create_task_valid(self, ctx, *, title):
        task_id = await self._store(ctx, title)
        rejection = await ctx.emit(
            "domain.task_board.task_created", {"task_id": task_id, "title": title, "owner": ctx.user_id},
        )
        return {"task_id": task_id, "rejected_event_id": self._outcome(rejection)}

    async def create_task_raises(self, ctx, *, title):
        await self._store(ctx, title)
        raise RuntimeError("failed after its write")

    async def delete_task(self, ctx, *, task_id):
        result = await self._tasks(ctx).delete_one({"task_id": task_id})
        rejection = await ctx.emit("domain.task_board.task_deleted", {"task_id": task_id})
        return {"deleted": result.deleted_count == 1, "rejected_event_id": self._outcome(rejection)}

    async def on_task_created(self, ctx, **payload):
        self.reactions.append(payload["task_id"])
'''

CONTRACT = {
    "app_id": APP_ID,
    "version": "1",
    "surfaces": [{
        "surface_id": "task_board", "surface_kind": "module",
        "collections": [{
            "name": "tasks", "entity": "Task", "scope": "app", "tenancy": "per_user", "owner_field": "owner",
            "ownership": {"surface_id": "task_board", "surface_kind": "module"},
            "fields": [{"name": name, "type": "string", "required": True} for name in ("task_id", "title", "owner")],
            "search_by": "task_id",
        }],
    }],
}


class _RecordingAuditLogger(AuditLogger):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[AuditRecord] = []

    async def log(self, record: AuditRecord) -> None:
        self.records.append(record)


class _LogCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _write_app(root) -> None:
    module = root / "modules" / "task_board"
    (module / "backend").mkdir(parents=True)
    (module / "contracts").mkdir()
    (root / "data").mkdir()
    (root / "app.json").write_text(json.dumps({"appName": "Event rejection"}), encoding="utf-8")
    (root / "data" / "contract.json").write_text(json.dumps(CONTRACT), encoding="utf-8")
    (module / "module.yaml").write_text(MODULE_YAML.lstrip(), encoding="utf-8")
    (module / "contracts" / "events.yaml").write_text(EVENTS_YAML.lstrip(), encoding="utf-8")
    (module / "contracts" / "reactions.yaml").write_text(REACTIONS_YAML.lstrip(), encoding="utf-8")
    (module / "contracts" / "notifications.yaml").write_text(NOTIFICATIONS_YAML.lstrip(), encoding="utf-8")
    (module / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (module / "backend" / "handler.py").write_text(HANDLER_PY.lstrip(), encoding="utf-8")


@pytest.fixture
async def mongo():
    from motor.motor_asyncio import AsyncIOMotorClient

    uri = os.environ.get("MONGO_URI")
    if not uri:
        if os.getenv("MOZAIKS_REQUIRE_REAL_MONGO"):
            pytest.fail("Real MongoDB is required")
        pytest.skip("MONGO_URI is not set")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=2000)
    try:
        await client.admin.command("ping")
    except Exception:
        client.close()
        if os.getenv("MOZAIKS_REQUIRE_REAL_MONGO"):
            pytest.fail("Real MongoDB is required")
        pytest.skip("MongoDB is unavailable")
    database = f"event_rejection_{uuid4().hex[:12]}"
    yield SimpleNamespace(client=client, database=database)
    await client.drop_database(database)
    client.close()


@pytest.fixture
async def runtime(mongo, tmp_path):
    root = tmp_path / "app"
    _write_app(root)
    load = await AppLoader.load(str(root))
    assert not load.failed_module_names, load.module_load_errors

    dispatcher = UnifiedEventDispatcher()
    notifications: list[dict] = []
    ModuleEventRouter(
        load.modules, event_emitter=dispatcher.emit, notification_store=notifications.append,
    ).register(dispatcher)
    results: list = []

    class ObservedExecutor(ModuleExecutor):
        async def execute(self, request, context=None):
            result = await super().execute(request, context)
            results.append(result)
            return result

    audit = _RecordingAuditLogger()
    hooks = PlatformHookRegistry()
    executor = ObservedExecutor(
        event_emitter=dispatcher.emit, data_contract=load.data_contract, platform_hooks=hooks,
        audit_logger=audit, persistence_database=mongo.database, persistence_client=mongo.client,
    )
    for loaded in load.modules:
        executor.register_loaded_module(loaded)
    registry = ExecutorRegistry()
    registry.register(executor)
    app = FastAPI()
    app.state.executor_registry = registry
    app.state.module_action_surfaces = {module.name: module.action_api_surface_map for module in load.modules}
    app.include_router(module_router.router)

    async def principal(request: Request) -> UserPrincipal | None:
        user = request.headers.get("x-test-user")
        if not user:
            return None
        return UserPrincipal(
            user_id=user, email=None, name=user, roles=[], scopes=[], raw_claims={"sub": user, "app_id": APP_ID},
            provider="test", app_id=APP_ID, auth_provenance="token_validated",
        )

    invocations: list[dict] = []
    environment = module_router.ModuleDispatchEnvironment(
        authentication_enabled=True, platform_hooks=hooks, record_invocation=lambda **kw: invocations.append(kw),
        persistence_principal=lambda user: PersistencePrincipal(user_id=user.user_id) if user else None,
    )
    app.dependency_overrides[optional_user] = principal
    app.dependency_overrides[module_router.module_dispatch_environment] = lambda: environment

    capture = _LogCapture()
    runtime_logger = logging.getLogger("mozaiks.workflow")
    runtime_logger.addHandler(capture)

    async def stored() -> list[dict]:
        database = mongo.client[mongo.database]
        rows: list[dict] = []
        for name in await database.list_collection_names():
            rows.extend(await database[name].find({}, {"_id": 0}).to_list(None))
        return rows

    async def audits() -> list[AuditRecord]:
        loop = asyncio.get_running_loop()
        await asyncio.gather(*[task for task in module_executor._PENDING_AUDIT_TASKS if task.get_loop() is loop])
        return audit.records

    async def call(action: str, body: dict, user: str = "alice"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            return await http.post(f"/api/modules/task_board/{action}", json=body, headers={"x-test-user": user})

    try:
        yield SimpleNamespace(
            app=app, executor=executor, handler=load.modules[0].handler, results=results, notifications=notifications,
            invocations=invocations, stored=stored, audits=audits, call=call, logs=capture.records,
        )
    finally:
        runtime_logger.removeHandler(capture)


def _rejection_logs(runtime) -> list[logging.LogRecord]:
    return [record for record in runtime.logs if record.getMessage().startswith("MODULE_EVENT_REJECTED")]


async def test_write_then_rejected_event_succeeds_and_stores_the_record_once(runtime):
    response = await runtime.call("create_task", {"title": CANARY})

    assert response.status_code == 200, response.text
    task_id = response.json()["task_id"]
    [result] = runtime.results
    [rejection] = result.rejected_events
    # The handler got the same rejection back from ctx.emit that the result names.
    assert response.json() == {"task_id": task_id, "rejected_event_id": rejection.event_id}
    assert (result.success, result.data, result.error_code) == (True, response.json(), None)
    assert rejection.event_id.startswith("evt_")
    assert rejection.to_dict() == {
        "event_id": rejection.event_id, "event_type": CREATED, "category": "value_invalid",
        "reason": "Missing required properties: 'owner'.", "validator": "required", "schema_path": "$.required",
    }
    [row] = await runtime.stored()
    assert (row["task_id"], row["title"], row["owner"]) == (task_id, CANARY, "alice")
    # The event was not dispatched: no reaction or notification ran for it.
    assert runtime.handler.reactions == []
    assert runtime.notifications == []
    [record] = await runtime.audits()
    assert (record.outcome, record.error) == ("ok", None)
    assert record.extra["dispatch"]["outcome"] == "ok"
    assert record.extra["dispatch"]["rejected_events"] == [rejection.to_dict()]
    [log] = _rejection_logs(runtime)
    assert log.levelno == logging.ERROR
    assert rejection.event_id in log.getMessage()
    assert (await runtime.executor.health())["rejected_events"] == {
        "total": 1, "by_action": {"task_board.create_task": 1},
    }
    # A succeeded action is metered like any other.
    assert [item["action_id"] for item in runtime.invocations] == ["create_task"]
    # Never payload contents.
    for text in (json.dumps(rejection.to_dict()), json.dumps(record.extra, default=str), log.getMessage()):
        assert CANARY not in text


async def test_write_then_valid_event_is_unchanged(runtime):
    response = await runtime.call("create_task_valid", {"title": "valid"})

    assert response.status_code == 200, response.text
    task_id = response.json()["task_id"]
    assert response.json()["rejected_event_id"] is None
    [result] = runtime.results
    assert (result.success, result.rejected_events) == (True, ())
    assert runtime.handler.reactions == [task_id]
    assert [item["event_type"] for item in runtime.notifications] == [CREATED]
    assert [row["task_id"] for row in await runtime.stored()] == [task_id]
    [record] = await runtime.audits()
    assert (record.outcome, record.extra["dispatch"]["rejected_events"]) == ("ok", [])
    assert _rejection_logs(runtime) == []
    assert (await runtime.executor.health())["rejected_events"] == {"total": 0, "by_action": {}}


async def test_handler_that_raises_after_its_write_still_fails(runtime):
    response = await runtime.call("create_task_raises", {"title": "raises"})

    assert response.status_code == 500, response.text
    assert response.json()["detail"]["error_code"] == "EXECUTION_ERROR"
    [result] = runtime.results
    assert (result.success, result.error_code, result.rejected_events) == (False, "EXECUTION_ERROR", ())
    [record] = await runtime.audits()
    assert (record.outcome, record.error) == ("failed", "RuntimeError")
    assert runtime.invocations == []


async def test_delete_then_rejected_event_succeeds_and_the_record_is_gone(runtime):
    created = await runtime.call("create_task_valid", {"title": "to delete"})
    task_id = created.json()["task_id"]

    response = await runtime.call("delete_task", {"task_id": task_id})

    assert response.status_code == 200, response.text
    result = runtime.results[-1]
    assert response.json() == {"deleted": True, "rejected_event_id": result.rejected_events[0].event_id}
    assert result.success is True
    assert [(item.event_type, item.reason) for item in result.rejected_events] == [
        (DELETED, "Missing required properties: 'owner'."),
    ]
    assert await runtime.stored() == []
    record = (await runtime.audits())[-1]
    assert record.extra["dispatch"]["rejected_events"] == [result.rejected_events[0].to_dict()]
    assert (await runtime.executor.health())["rejected_events"]["by_action"] == {"task_board.delete_task": 1}


async def test_workflow_tool_dispatch_reports_success_with_the_rejected_event(runtime, monkeypatch):
    run = ("EventRejection", APP_ID, "chat_1")
    principal = WebSocketUser.from_claims(UserClaims(
        user_id="alice", app_id=APP_ID, scopes=[], provider="keycloak", raw_claims={"exp": 9_999_999_999},
    ))
    socket = SimpleNamespace(
        state=SimpleNamespace(user=principal), app=runtime.app,
        client_state=WebSocketState.CONNECTED, application_state=WebSocketState.CONNECTED,
    )
    transport = SimpleNamespace(connections={
        run[2]: {"websocket": socket, "active": True, "app_id": APP_ID, "user_id": "alice"},
    })
    monkeypatch.setattr(SimpleTransport, "get_instance", AsyncMock(return_value=transport))
    monkeypatch.setattr(module_tools, "is_auth_enabled", lambda: True)
    monkeypatch.setattr(module_tools, "get_platform_hooks", PlatformHookRegistry)
    policy = build_context_authority_policy(workflow_name=run[0], definitions={})
    bridge = ContextVariablesBridge({"app_id": APP_ID, "user_id": "alice"}, authority_policy=policy)
    bridge._bind_run(run, policy)

    async def tool(context_variables):
        return await module_tools.dispatch_workflow_module_action("task_board", "create_task", {"title": "workflow"})

    result = await _wrap_tool_with_context(tool, bridge)()

    assert (result.success, result.error_code) == (True, None)
    [rejection] = result.rejected_events
    assert (rejection.event_type, rejection.category) == (CREATED, "value_invalid")
    assert result.data["rejected_event_id"] == rejection.event_id
    assert [(row["task_id"], row["owner"]) for row in await runtime.stored()] == [(result.data["task_id"], "alice")]
    assert runtime.handler.reactions == []
    [record] = await runtime.audits()
    assert record.extra["dispatch"]["authority_kind"] == "workflow"
    assert record.extra["dispatch"]["audit_tags"] == {"surface": "workflow_tool"}
    assert (record.outcome, record.extra["dispatch"]["rejected_events"]) == ("ok", [rejection.to_dict()])
