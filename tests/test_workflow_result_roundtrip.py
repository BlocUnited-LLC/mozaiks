"""Real JWT and standalone-Mongo integration from an app action to a structured result."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import yaml
from motor.motor_asyncio import AsyncIOMotorClient

from factory_app.workflows.AgentGenerator.tools.result_write_materialization import (
    materialize_workflow_result_write,
)
from mozaiksai.core.data.persistence.persistence_manager import (
    AG2PersistenceManager,
    _context_authority_policy_for_workflow,
)
from mozaiksai.core.events.unified_event_dispatcher import UnifiedEventDispatcher
from mozaiksai.core.runtime.app.loader import AppLoader
from mozaiksai.core.runtime.composition.module_event_router import ModuleEventRouter
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
from mozaiksai.core.runtime.composition.reaction_idempotency_store import ReactionIdempotencyStore
from mozaiksai.core.runtime.composition.workflow_trigger_guard import WorkflowTriggerGuard
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.outputs.runtime_events import emit_validated_agent_output
from mozaiksai.core.workflow.outputs.structured import (
    invalidate_workflow_structured_outputs,
    load_workflow_structured_outputs,
)
from mozaiksai.core.workflow.workflow_manager import workflow_manager
from tests.test_module_reaction_result_write import bundle  # noqa: F401 -- real on-disk module fixture
from tests.test_runtime_owner_scoping_http import http_runtime  # noqa: F401 -- authentic local signed JWTs
from tests.test_workflow_trigger_guard import _TenantRateLimiter

WORKFLOW = "ReviewWorkflow"
AGENT = "SummaryAgent"
APP = "ownership-test-app"
CREATED = "domain.tasks.task_created"
UPDATED = "domain.tasks.task_updated"


def write_result_workflow(root: Path, declaration: dict):
    """Create the workflow through Factory's result-tool materializer for unit and live runs."""
    trigger = {"type": "event", "event": CREATED, "capability_id": "tasks.review"}
    files = {
        "orchestrator.yaml": {"schema_version": "mozaiks.orchestrator.v1", "workflow_name": WORKFLOW,
                              "initial_agent": AGENT, "orchestration_pattern": "ag2_network", "workflow_startup_mode": "BackendOnly",
                              "human_in_the_loop": False, "max_turns": 2, "triggers": [trigger],
                              "initial_message": "Summarize the task in the incoming event."},
        "agents.yaml": {"agents": [{"name": AGENT, "description": "Summarize the new task.", "structured_outputs_required": True,
                                   "system_message": "Write a one-sentence summary of event_payload.title. Return only a JSON object with the summary string."}]},
        "transition_graph.yaml": {"transition_rules": [{"source_agent": AGENT, "target_agent": "terminate",
                                                        "transition_type": "after_turn", "transition_target": "TerminateTarget"}]},
        "context_variables.yaml": {"definitions": {"event_payload": {"type": "object", "source": {"type": "state", "default": {}}}},
                                   "agents": {AGENT: {"variables": ["event_payload"]}}}, "ui_config.yaml": {"visual_agents": []},
        "structured_outputs.yaml": {"schema_version": "mozaiks.structured_outputs.v1", "registry": {AGENT: "TaskSummary"},
                                    "models": {"TaskSummary": {"type": "model", "fields": {"summary": {"type": "str"}}}}},
        "tools.yaml": {"tools": []},
    }
    reviewed_spec = {
        "result_write_plans": [{"agent": AGENT, "module_id": "tasks", "event_type": CREATED, "result_write": declaration}],
    }
    result = materialize_workflow_result_write({
        "files": [{"filename": name, "content": yaml.safe_dump(content)} for name, content in files.items()],
    }, reviewed_spec)
    for entry in result["files"]:
        path = root / WORKFLOW / entry["filename"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(entry["content"], encoding="utf-8")


async def deliver_result(chat_id: str, actor: str, value: str, turn: int):
    policy = _context_authority_policy_for_workflow(WORKFLOW)
    bridge = ContextVariablesBridge({"user_id": actor, "app_id": APP, "chat_id": chat_id}, authority_policy=policy)
    bridge._bind_run((WORKFLOW, APP, chat_id), policy)
    _, registry = load_workflow_structured_outputs(WORKFLOW)
    return await emit_validated_agent_output(
        current_agent_name=AGENT, last_reply={"summary": value}, workflow_name=WORKFLOW, chat_id=chat_id,
        app_id=APP, user_id=actor, turn_sequence=turn, context_vars_dict=bridge.snapshot(), context_bridge=bridge,
        structured_registry=registry, auto_tool_agents={AGENT}, wf_logger=logging.getLogger(__name__),
    )


@pytest.fixture
async def result_runtime(request, tmp_path, monkeypatch):
    from mozaiksai.hosts import platform

    auth_http = request.getfixturevalue("http_runtime")
    module, reactions, events, data, write = request.getfixturevalue("bundle")
    module["actions"][0]["permissions"] = []
    module["actions"].append({"id": "update", "handler_method": "update_task", "description": "Edit a task", "emits": [UPDATED]})
    module["actions"].append({"id": "list_tasks", "handler_method": "list_tasks", "description": "List my tasks"})
    for capability in module["capabilities"]:
        capability["permissions"] = []
    events["events"].append({**events["events"][0], "type": UPDATED})
    write()
    (tmp_path / "modules/tasks/backend/handler.py").write_text(
        "class TasksModule:\n"
        "    async def create_task(self, ctx, *, title, task_id):\n"
        "        await ctx.persistence.collection('tasks', 'Task').insert_one({'task_id': task_id, 'title': title})\n"
        f"        await ctx.emit('{CREATED}', {{'task_id': task_id, 'title': title}})\n"
        "        return {'task_id': task_id}\n"
        "    async def update_task(self, ctx, *, title, task_id):\n"
        "        result = await ctx.persistence.collection('tasks', 'Task').update_one({'task_id': task_id}, {'$set': {'title': title}})\n"
        f"        if result.matched_count: await ctx.emit('{UPDATED}', {{'task_id': task_id, 'title': title}})\n"
        "        return {'matched': result.matched_count}\n"
        "    async def list_tasks(self, ctx):\n"
        "        return {'items': await ctx.persistence.collection('tasks', 'Task').find_many({}, projection={'_id': 0})}\n"
        "    async def save_summary(self, ctx, *, summary):\n"
        "        return await ctx.commit_workflow_result({'summary': summary})\n",
        encoding="utf-8",
    )
    folder = tmp_path / "workflows"
    write_result_workflow(folder, reactions["reactions"][0]["target"]["result_write"])
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(folder))
    monkeypatch.setenv("MOZAIKS_APP_PATH", str(tmp_path))
    for key in ("_workflows", "_workflow_paths", "_config_cache"):
        monkeypatch.setattr(workflow_manager, key, {})
    monkeypatch.setattr(workflow_manager, "workflows_base_path", folder)
    info = workflow_manager.reload_workflow(WORKFLOW)
    assert not info.get("error"), info
    invalidate_workflow_structured_outputs(WORKFLOW)
    client = AsyncIOMotorClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=3000)
    database = "mz_workflow_result_" + uuid4().hex
    sessions = client[database]["ChatSessions"]

    async def session_collection(self):
        return sessions

    monkeypatch.setattr(AG2PersistenceManager, "_coll", session_collection)
    dispatcher = UnifiedEventDispatcher()
    monkeypatch.setattr("mozaiksai.core.events.unified_event_dispatcher.get_event_dispatcher", lambda: dispatcher)
    executor = ModuleExecutor(data_contract=data, persistence_client=client, persistence_database=database, event_emitter=dispatcher.emit)
    app = await AppLoader.load(str(tmp_path))
    assert not app.failed_module_names, app.failed_module_names
    for loaded in app.modules:
        executor.register_loaded_module(loaded)
    routes = platform._load_workflow_capability_routes(tmp_path)
    assert "tasks.review" in routes
    guard = WorkflowTriggerGuard(claim_store=ReactionIdempotencyStore(database_name=database, client=client),
                                 rate_limiter=_TenantRateLimiter())
    async def invoke(capability, envelope, subscription):
        return await platform._invoke_workflow_capability(
            capability_id=capability, source_event=envelope, subscription=subscription,
            routes=routes, trigger_guard=guard, auto_start=getattr(request, "param", False),
        )

    router = ModuleEventRouter(app.modules, capability_invoker=invoke, data_contract=data)
    router.register(dispatcher)
    http = auth_http.client().app
    registry = SimpleNamespace(module_executor=executor)
    monkeypatch.setattr(platform.app.state, "executor_registry", registry, raising=False)
    http.state.executor_registry = registry
    http.state.module_action_surfaces = {"tasks": {"create": "public", "update": "public", "list_tasks": "public", "save_summary": "internal"}}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=http), base_url="http://test") as api:
            yield SimpleNamespace(api=api, token=auth_http.token, sessions=sessions, db=client[database],
                                  workflow_dir=folder / WORKFLOW)
    finally:
        invalidate_workflow_structured_outputs(WORKFLOW)
        await client.drop_database(database)
        client.close()


@pytest.mark.skipif(os.getenv("MOZAIKS_RUN_REAL_MONGO_TESTS") != "1", reason="requires local Mongo")
async def test_signed_create_delivers_one_result_to_its_own_row(result_runtime):
    from mozaiksai.core.session.launcher import create_routed_chat_session

    case = result_runtime
    owner = case.token("owner", "workspace")
    other = case.token("other", "workspace")
    for headers in (owner, other):
        response = await case.api.post("/api/modules/tasks/create", headers=headers, json={"task_id": "shared", "title": "source"})
        assert response.status_code == 200, response.text
    sessions = await case.sessions.find({"workflow_name": WORKFLOW}).to_list(None)
    assert len(sessions) == 2
    selected = next(s for s in sessions if s["user_id"] == "owner")
    grant = selected["workflow_result_grant"]
    assert grant["subject"]["user_id"] == "owner" and grant["subject"]["record_id"] == "shared"
    collection = case.db[grant["subject"]["physical_collection"]]
    sibling = await create_routed_chat_session(
        workflow_id=WORKFLOW, app_id=APP, user_id="owner", context_variables={},
        trigger_meta={**selected["trigger_meta"], "workflow_result_grant": grant}, session_router=None,
    )
    await deliver_result(sibling, "owner", "spoofed sibling", 1)
    await deliver_result(selected["_id"], "other", "unauthorized", 1)
    assert await collection.count_documents({"summary": {"$exists": True}}) == 0
    await deliver_result(selected["_id"], "owner", "accepted summary", 2)
    receipt = (await collection.find_one({"author": "owner"}))["_mz_workflow_result"]["receipt"]
    assert receipt["invocation_id"] == grant["invocation_id"] and receipt["action_id"] == "save_summary"
    await deliver_result(selected["_id"], "owner", "accepted summary", 3)
    await deliver_result(selected["_id"], "owner", "second write", 4)
    assert (await collection.find_one({"author": "owner"}))["summary"] == "accepted summary"
    assert "summary" not in await collection.find_one({"author": "other"})
    for headers, author in ((owner, "owner"), (other, "other")):
        listing = await case.api.get("/api/modules/tasks/list_tasks", headers=headers)
        assert listing.status_code == 200, listing.text
        [row] = listing.json()["items"]
        assert row["author"] == author and "_mz_workflow_result" not in row
        assert row.get("summary") == ("accepted summary" if author == "owner" else None)
    denied = await case.api.post("/api/modules/tasks/save_summary", headers=other, json={"summary": "bypass"})
    assert denied.status_code in {403, 404}
    updated = await case.api.post("/api/modules/tasks/update", headers=owner, json={"task_id": "shared", "title": "edited"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["matched"] == 1
    assert await case.sessions.count_documents({"workflow_name": WORKFLOW, "workflow_result_grant": {"$exists": True}}) == 2
    assert await case.sessions.count_documents({"workflow_name": WORKFLOW}) == 3
