from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from factory_app.workflows._shared.platform import build_lifecycle as hooks
from mozaiksai.core.workflow.pack.schema import parse_global_pack_graph

BINDING = {
    "target_app_id": "tracker", "build_id": "build_1",
    "build_registry_id": "appreg_1", "phase": "genesis",
}
CALL = {"app_id": "factory", "user_id": "alice", "chat_id": "chat_1", "execution_id": "exec_1"}


@pytest.fixture
def state(monkeypatch):
    session = {**{"run_build_binding": BINDING}, "journey_instance_id": "journey_1",
               "journey_key": "build", "journey_position": 0, "journey_total_steps": 2}
    record = {"app_id": "tracker", "chat_app_id": "factory", "build_registry_id": "appreg_1",
              "current_build_run": {"build_id": "build_1", "phase": "genesis"}}
    registry = AsyncMock()
    registry.get_app_record.return_value = {"app": record}
    registry.update_build_status.return_value = {"success": True}
    events = AsyncMock(return_value="outbox_1")
    telemetry = []
    monkeypatch.setattr(hooks, "_get_chat_session_context", AsyncMock(return_value=session))
    monkeypatch.setattr(hooks, "_app_registry_service", lambda: registry)
    monkeypatch.setattr(hooks, "upsert_outbox_event", events)
    monkeypatch.setattr(hooks, "_spawn_delivery", lambda **kw: None)
    monkeypatch.setattr(hooks, "_emit_telemetry", lambda **kw: telemetry.append(kw))
    pack = parse_global_pack_graph({
        "version": 3, "workflows": [{"id": "ValueEngine"}, {"id": "AppGenerator"}],
        "transitions": [], "workflow_sequences": [{"id": "build", "steps": [
            {"workflows": ["ValueEngine"]}, {"workflows": ["AppGenerator"]},
        ]}],
    })
    monkeypatch.setattr(hooks, "load_global_pack_graph", lambda: pack)
    return SimpleNamespace(session=session, record=record, registry=registry, events=events, telemetry=telemetry)


@pytest.mark.asyncio
async def test_started_uses_persisted_binding_and_preserves_host_identity(state):
    await hooks.emit_build_started(**CALL, workflow_name="ValueEngine", build_id="forged", build_registry_id="forged")
    payload = state.events.call_args.kwargs["payload"]
    assert payload["appId"] == "factory"
    assert payload["targetAppId"] == "tracker"
    assert payload["buildId"] == "build_1"
    assert payload["buildRegistryId"] == "appreg_1"
    assert payload["journeyInstanceId"] == "journey_1"
    assert payload["eventType"] == "build.started"
    assert payload["status"] == "started"
    assert payload["idempotencyKey"] == "build:factory:build_1:build.started"
    state.registry.get_app_record.assert_awaited_once_with(build_registry_id="appreg_1", owner_user_id="alice")
    state.registry.create_app_record.assert_not_called()


@pytest.mark.asyncio
async def test_each_workflow_advances_directory_pointer_without_duplicate_started_event(state):
    state.session["journey_position"] = 1
    assert await hooks.emit_build_started(**CALL, workflow_name="AppGenerator") is None
    state.events.assert_not_awaited()
    updated = state.registry.update_build_status.call_args.kwargs
    assert updated["active_chat_id"] == "chat_1"
    assert updated["active_workflow_id"] == "AppGenerator"
    assert updated["current_build_run"]["build_id"] == "build_1"


@pytest.mark.asyncio
async def test_completion_skips_nonterminal_workflow(state):
    assert await hooks.emit_build_completed(**CALL, workflow_name="ValueEngine") is None
    state.events.assert_not_awaited()
    state.registry.update_build_status.assert_not_awaited()


@pytest.mark.parametrize("wrong", [None, {"app_id": "other", "chat_app_id": "factory"},
                                   {"app_id": "tracker", "chat_app_id": "other"}])
@pytest.mark.asyncio
async def test_missing_or_mismatched_registry_never_recreated(state, wrong):
    state.registry.get_app_record.return_value = {"app": wrong}
    with pytest.raises(ValueError, match="not available"):
        await hooks.emit_build_started(**CALL, workflow_name="ValueEngine")
    state.registry.create_app_record.assert_not_called()
    state.registry.update_build_status.assert_not_awaited()
    state.events.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_completion_cannot_overwrite_new_build(state):
    state.record["current_build_run"]["build_id"] = "new_build"
    with pytest.raises(ValueError, match="superseded"):
        await hooks.emit_build_completed(**CALL, workflow_name="AppGenerator")
    state.registry.update_build_status.assert_not_awaited()
    state.events.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_binding_stops_before_registry_or_events(state):
    state.session.pop("run_build_binding")
    with pytest.raises(ValueError):
        await hooks.emit_build_started(**CALL, workflow_name="ValueEngine")
    state.registry.get_app_record.assert_not_awaited()
    state.events.assert_not_awaited()


@pytest.mark.asyncio
async def test_registry_update_failure_prevents_success_event(state):
    state.registry.update_build_status.return_value = {"success": False}
    with pytest.raises(ValueError, match="disappeared"):
        await hooks.emit_build_started(**CALL, workflow_name="ValueEngine")
    state.events.assert_not_awaited()


@pytest.mark.asyncio
async def test_completion_references_real_bundle_without_writing_summary(state, monkeypatch):
    from mozaiksai.core import artifacts
    state.session["journey_position"] = 1
    state.record["current_build_run"].update(artifact_version_id="av_1", bundle_path="generated/apps/tracker/build_1/app")
    store = AsyncMock()
    store.get_build_record.return_value = SimpleNamespace(
        id="av_1", commit_metadata=SimpleNamespace(metadata=BINDING),
    )
    monkeypatch.setattr(artifacts, "get_artifact_store", lambda: store)
    await hooks.emit_build_completed(**CALL, workflow_name="AppGenerator")
    store.get_build_record.assert_awaited_once_with(app_id="tracker", build_record_id="av_1")
    store.create_build_record.assert_not_called()
    payload = state.events.call_args.kwargs["payload"]
    assert payload["artifacts"]["artifactVersionId"] == "av_1"
    assert payload["eventType"] == "build.completed"
    assert payload["status"] == "completed"
    assert payload["artifacts"]["exportDownloadUrl"] == "/api/studio/build/artifacts/av_1/download?build_registry_id=appreg_1"
    assert state.registry.update_build_status.call_args.kwargs["status"] == "review"
    assert state.telemetry[0]["status"] == "completed"


@pytest.mark.asyncio
async def test_missing_bundle_does_not_get_a_fake_download_url(state):
    context = await hooks._resolve_build_event_context(**CALL, workflow_name="ValueEngine")
    assert await hooks.get_build_artifacts(context=context) == {}


@pytest.mark.asyncio
async def test_failure_updates_same_build_and_includes_live_gate_evidence(state):
    await hooks.emit_build_failed(
        **CALL, workflow_name="ValueEngine", error="generation failed",
        context_variables={"module_contract_quality_status": "blocked"},
    )
    event = state.events.call_args.kwargs
    assert event["event_type"] == "build.failed"
    assert event["payload"]["eventType"] == "build.failed"
    assert event["payload"]["status"] == "failed"
    assert event["payload"]["buildEvidence"]["gates"][0]["status"] == "blocked"
    assert state.registry.update_build_status.call_args.kwargs["status"] == "needs_revision"
    assert state.telemetry[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_session_lookup_includes_owner_host_and_workflow(monkeypatch):
    from mozaiksai.core.data.persistence import persistence_manager
    coll = AsyncMock()
    coll.find_one.return_value = None
    monkeypatch.setattr(persistence_manager.AG2PersistenceManager, "_coll", AsyncMock(return_value=coll))
    with pytest.raises(ValueError, match="not available"):
        await hooks._get_chat_session_context(app_id="factory", user_id="alice", chat_id="foreign", workflow_name="ValueEngine")
    query = coll.find_one.call_args.args[0]
    assert query["app_id"] == "factory"
    assert query["user_id"] == "alice"
    assert query["_id"] == "foreign"
    assert query["workflow_name"] == "ValueEngine"


@pytest.mark.asyncio
async def test_delivery_posts_original_host_scope_and_records_attempt(monkeypatch):
    monkeypatch.setattr(hooks, "get_outbox_event", AsyncMock(return_value={
        "app_id": "factory", "payload": {"targetAppId": "tracker"},
    }))
    client = AsyncMock()
    client.post_build_event.return_value = SimpleNamespace(ok=True, status_code=202, error=None)
    monkeypatch.setattr(hooks, "_build_events_client", lambda: client)
    attempt = AsyncMock()
    monkeypatch.setattr(hooks, "mark_attempt", attempt)
    await hooks._deliver_outbox_event_once(outbox_event_id="outbox_1")
    client.post_build_event.assert_awaited_once_with(app_id="factory", payload={"targetAppId": "tracker"})
    attempt.assert_awaited_once_with(outbox_id="outbox_1", ok=True, status_code=202, error=None)


@pytest.mark.asyncio
async def test_telemetry_remains_anonymized(monkeypatch):
    from mozaiksai.core import telemetry
    emitted = AsyncMock()
    monkeypatch.setattr(telemetry, "emit_build_telemetry", emitted)
    hooks._emit_telemetry(workflow_name="AppGenerator", status="completed", context=BINDING)
    await asyncio.sleep(0)
    assert emitted.await_count == 1
    payload = emitted.call_args.args[0]
    assert payload["build_id_hash"]
    assert "appreg_1" not in str(payload)

