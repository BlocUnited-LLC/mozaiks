"""Live failure -> declared Factory hook -> real target registry and Apps projection."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from factory_app.app.modules.app_registry.backend.repo import AppRegistryRepo
from factory_app.app.modules.app_registry.backend.service import AppRegistryService
from factory_app.workflows._shared.platform import build_lifecycle
from factory_app.workflows.AppGenerator.tools.platform import build_events_outbox
from mozaiksai.core.data.models import WorkflowStatus
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.runtime.app.studio_summary import build_apps_summary
from mozaiksai.core.session import router as session_router
from mozaiksai.core.workflow.execution import lifecycle
from tests.test_workflow_bridge import _DummyTransport, _FakeLiveRun, _LiveRunResult

WORKSPACE = Path(__file__).resolve().parents[1]


@pytest_asyncio.fixture
async def failure_database(monkeypatch):
    database_name = f"workflow_failure_{uuid4().hex}"
    client = AsyncIOMotorClient(
        os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017"), serverSelectionTimeoutMS=1500,
    )
    try:
        await client.admin.command("ping")
    except Exception:
        client.close()
        if os.getenv("MOZAIKS_REQUIRE_REAL_MONGO"):
            pytest.fail("Real Mongo is required for workflow failure settlement acceptance")
        pytest.skip("Mongo is unavailable")
    database = client[database_name]

    async def chat_collection(self, name=None):
        return database[name or "ChatSessions"]

    async def registry_collection(self):
        return database["AppRegistryRecords"]

    async def outbox_collection():
        return database["BuildEventsOutbox"]

    monkeypatch.setattr(AG2PersistenceManager, "_coll", chat_collection)
    monkeypatch.setattr(AppRegistryRepo, "_collection", registry_collection)
    monkeypatch.setattr(build_events_outbox, "_coll", outbox_collection)
    monkeypatch.setattr(build_events_outbox, "_INDEX_READY", False)
    # Keep all persistence real, but never deliver to hosted HTTP or telemetry sinks.
    monkeypatch.setattr(build_lifecycle, "_spawn_delivery", lambda **kwargs: None)
    monkeypatch.setattr(build_lifecycle, "_emit_telemetry", lambda **kwargs: None)
    monkeypatch.setattr(lifecycle.LifecycleToolManager, "_emit_lifecycle_event", AsyncMock())
    try:
        yield database
    finally:
        assert database_name.startswith("workflow_failure_")
        await client.drop_database(database_name)
        client.close()


def _assert_apps_label(before: dict, after: dict) -> None:
    module_url = (WORKSPACE / "chat-ui/src/admin/appStudioModel.js").as_uri()
    script = """
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const { getAppRecordSnapshot } = await import(MODULE_URL);
const { before, after } = JSON.parse(readFileSync(0, 'utf8'));
assert.equal(getAppRecordSnapshot(before).lifecycleLabel, 'Building');
assert.equal(getAppRecordSnapshot(after).lifecycleLabel, 'Needs Revision');
""".replace("MODULE_URL", json.dumps(module_url))
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        input=json.dumps({"before": before, "after": after}), cwd=WORKSPACE,
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.asyncio
@pytest.mark.parametrize("binding_state", ["current", "stale_build", "foreign_owner", "foreign_target"])
async def test_actual_live_failure_hook_settles_only_current_owned_target(
    monkeypatch, failure_database, binding_state, caplog,
):
    database = failure_database
    service = AppRegistryService()
    record = (await service.create_app_record(
        owner_user_id="owner-1", app_id="target-app", chat_app_id="execution-app",
        name="Failure Test", status="building", active_chat_id="chat-1",
        active_workflow_id="AppGenerator",
        current_build_run={"build_id": "build-1", "phase": "genesis"},
    ))["app"]
    other = (await service.create_app_record(
        owner_user_id="owner-1", app_id="other-target", chat_app_id="execution-app",
        status="building", current_build_run={"build_id": "other-build", "phase": "genesis"},
    ))["app"]
    binding = {
        "target_app_id": "target-app", "build_registry_id": record["build_registry_id"],
        "build_id": "build-1", "phase": "genesis",
    }
    if binding_state == "stale_build":
        await service.update_build_status(
            owner_user_id="owner-1", build_registry_id=record["build_registry_id"],
            status="building", current_build_run={"build_id": "build-2", "phase": "refinement"},
            expected_build_id="build-1",
        )
    elif binding_state == "foreign_owner":
        await database.AppRegistryRecords.update_one(
            {"_id": record["build_registry_id"]}, {"$set": {"owner_user_id": "other-owner"}},
        )
    elif binding_state == "foreign_target":
        binding["target_app_id"] = "other-target"
    await database.ChatSessions.insert_one({
        "_id": "chat-1", "app_id": "execution-app", "user_id": "owner-1",
        "workflow_name": "AppGenerator", "status": int(WorkflowStatus.IN_PROGRESS),
        "run_build_binding": binding, "journey_key": "build",
        "journey_position": 8, "journey_total_steps": 11,
    })
    registry_before = await database.AppRegistryRecords.find_one({"_id": record["build_registry_id"]})
    other_before = await database.AppRegistryRecords.find_one({"_id": other["build_registry_id"]})
    chat_before = await database.ChatSessions.find_one({"_id": "chat-1"})
    apps_before = build_apps_summary(
        WORKSPACE / "factory_app/app", app_records=(await service.list_apps(owner_user_id="owner-1"))["apps"],
    )

    persistence = AG2PersistenceManager()
    # AG2 history/agent execution is outside this test; lifecycle reads and writes are not stubbed.
    monkeypatch.setattr(persistence, "append_run_user_message", AsyncMock())
    transport = _DummyTransport(persistence)
    monkeypatch.setattr(transport, "_apply_user_text_context_updates", AsyncMock(return_value={}))
    revision_failed = AsyncMock()
    monkeypatch.setattr(session_router, "get_session_router_for_chat", AsyncMock(
        return_value=SimpleNamespace(fail_active_revision=revision_failed),
    ))
    runner_result = _LiveRunResult(status=RunStatus.FAILED)
    runner_result.context_variables = {}
    runner_result.error = "AG2 turn failed: structured output validation failed: truncated plan"
    live_run = _FakeLiveRun(result=runner_result)
    transport.register_live_ag2_workflow_run("chat-1", live_run)

    response = await transport._continue_live_ag2_workflow_run(
        live_run=live_run, chat_id="chat-1", app_id="execution-app", user_id="owner-1",
        workflow_name="AppGenerator", message="Proceed",
    )
    assert response["status"] == "error" and response["run_status"] == "failed"
    assert transport.get_live_ag2_workflow_run("chat-1") is None
    # A failed run is terminal, but must never satisfy a completed dependency.
    chat_after = await database.ChatSessions.find_one({"_id": "chat-1"})
    assert chat_after["status"] == int(WorkflowStatus.FAILED)
    assert "failed_at" in chat_after and "completed_at" not in chat_after
    assert chat_after["run_build_binding"] == chat_before["run_build_binding"]
    assert chat_after["workflow_ui_state"]["pending_input_request"] is None
    assert chat_before["status"] == 0
    # Recreate the transport as after a process restart, without any live handle.
    restarted = _DummyTransport(AG2PersistenceManager())
    from mozaiksai.core.adapters import ag2_orchestration

    def forbidden_adapter():
        pytest.fail("A failed persisted session must not reach the execution adapter")

    monkeypatch.setattr(ag2_orchestration, "get_ag2_adapter", forbidden_adapter)
    blocked = await restarted.handle_user_input_from_api(
        chat_id="chat-1", app_id="execution-app", user_id="owner-1",
        workflow_name="AppGenerator", message="Continue after restart",
    )
    assert blocked["error_code"] == "WORKFLOW_SESSION_TERMINAL"
    assert blocked["run_status"] == "failed"
    assert restarted.persisted_messages == []
    assert await database.ChatSessions.find_one({"_id": "chat-1"}) == chat_after
    assert await database.AppRegistryRecords.find_one({"_id": other["build_registry_id"]}) == other_before
    assert await database.AppRegistryRecords.count_documents({}) == 2
    persisted = await database.AppRegistryRecords.find_one({"_id": record["build_registry_id"]})

    if binding_state != "current":
        assert persisted == registry_before
        assert await database.BuildEventsOutbox.count_documents({}) == 0
        assert "emit_build_failed" in caplog.text and "failed" in caplog.text
        return

    assert persisted["lifecycle_state"] == "needs_revision"
    assert persisted["current_build_run"]["status"] == "needs_revision"
    assert persisted["current_build_run"]["build_id"] == "build-1"
    assert persisted["active_chat_id"] == "chat-1"
    assert persisted["active_workflow_id"] == "AppGenerator"
    assert persisted["app_id"] == "target-app"
    assert persisted["chat_app_id"] == "execution-app"
    assert await database.BuildEventsOutbox.count_documents({}) == 1
    event = await database.BuildEventsOutbox.find_one({})
    assert event["event_type"] == "build.failed"
    assert event["status"] == "failed"
    assert event["app_id"] == "execution-app"
    assert event["payload"]["targetAppId"] == "target-app"
    assert event["payload"]["buildRegistryId"] == record["build_registry_id"]
    assert event["payload"]["buildId"] == "build-1"
    assert event["payload"]["error"] == runner_result.error

    apps_after = build_apps_summary(
        WORKSPACE / "factory_app/app", app_records=(await service.list_apps(owner_user_id="owner-1"))["apps"],
    )
    before = next(app for app in apps_before["apps"] if app["app_id"] == "target-app")
    after = next(app for app in apps_after["apps"] if app["app_id"] == "target-app")
    assert after["status"] == "needs_revision"
    assert after["lifecycle_label"] == "Needs Revision"
    assert apps_after["metrics"]["needs_revision"] == 1
    _assert_apps_label(before, after)
