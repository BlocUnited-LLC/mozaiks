"""Terminal session projections and AG2 restart refusal, without model calls."""
import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mozaiksai.core.data.models import WorkflowStatus
from mozaiksai.core.data.persistence.persistence_manager import (
    AG2PersistenceManager,
    ChatSessionTerminalError,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [WorkflowStatus.COMPLETED, WorkflowStatus.FAILED])
async def test_terminal_session_refuses_execution(status, monkeypatch):
    pm = AG2PersistenceManager()
    collection = SimpleNamespace(find_one=AsyncMock(return_value={"status": int(status)}))
    monkeypatch.setattr(pm, "_coll", AsyncMock(return_value=collection))
    with pytest.raises(ChatSessionTerminalError) as exc:
        await pm.assert_chat_resumable("chat", "app")
    assert exc.value.status is status
    assert collection.find_one.await_args.args[0] == {"_id": "chat", "app_id": "app"}


@pytest.mark.asyncio
@pytest.mark.parametrize("doc", [None, {"status": 0, "paused": True}])
async def test_new_and_paused_session_remain_resumable(doc, monkeypatch):
    pm = AG2PersistenceManager()
    monkeypatch.setattr(pm, "_coll", AsyncMock(return_value=SimpleNamespace(
        find_one=AsyncMock(return_value=doc),
    )))
    await pm.assert_chat_resumable("chat", "app")


@pytest.mark.asyncio
async def test_failure_write_is_scoped_fenced_and_clears_only_pending_input(monkeypatch):
    pm = AG2PersistenceManager()
    coll = SimpleNamespace(
        find_one=AsyncMock(return_value={"status": 0, "created_at": datetime.now(UTC)}),
        update_one=AsyncMock(return_value=SimpleNamespace(acknowledged=True, modified_count=1)),
    )
    monkeypatch.setattr(pm, "_coll", AsyncMock(return_value=coll))
    assert await pm.mark_chat_failed("chat", "app")
    query, update = coll.update_one.await_args.args
    assert query == {"_id": "chat", "app_id": "app", "status": 0}
    assert update["$set"]["status"] == 2
    assert "failed_at" in update["$set"] and "completed_at" not in update["$set"]
    assert update["$set"]["workflow_ui_state.pending_input_request"] is None
    assert "workflow_ui_state.last_artifact" not in update["$set"]
    assert update["$inc"] == {"session_version": 1}
    coll.find_one.return_value = {"status": 2}
    assert await pm.mark_chat_failed("chat", "app")
    with pytest.raises(ChatSessionTerminalError):
        await pm.mark_chat_completed("chat", "app")
    assert coll.update_one.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [WorkflowStatus.COMPLETED, WorkflowStatus.FAILED])
async def test_concurrent_same_outcome_settlement_is_idempotent(monkeypatch, status):
    pm = AG2PersistenceManager()
    both_read = asyncio.Event()
    doc = {"status": 0, "session_version": 7, "last_artifact": {"id": "preserved"}}
    read_count = 0
    writes = []

    async def find_one(query, projection):
        nonlocal read_count
        assert query == {"_id": "chat", "app_id": "app"}
        snapshot = {"status": doc["status"]}
        read_count += 1
        if read_count <= 2:
            if read_count == 2:
                both_read.set()
            await both_read.wait()
        return snapshot

    async def update_one(query, update):
        assert query == {"_id": "chat", "app_id": "app", "status": 0}
        if doc["status"] != query["status"]:
            return SimpleNamespace(acknowledged=True, modified_count=0)
        doc.update(update["$set"])
        doc["session_version"] += update["$inc"]["session_version"]
        writes.append(update)
        return SimpleNamespace(acknowledged=True, modified_count=1)

    coll = SimpleNamespace(find_one=AsyncMock(side_effect=find_one), update_one=AsyncMock(side_effect=update_one))
    monkeypatch.setattr(pm, "_coll", AsyncMock(return_value=coll))
    settle = pm.mark_chat_failed if status is WorkflowStatus.FAILED else pm.mark_chat_completed
    results = await asyncio.wait_for(asyncio.gather(settle("chat", "app"), settle("chat", "app")), timeout=2)
    assert results == [True, True]
    assert read_count == 3
    assert coll.update_one.await_count == 2 and len(writes) == 1
    assert doc["status"] == int(status) and doc["session_version"] == 8
    assert doc["last_artifact"] == {"id": "preserved"}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [WorkflowStatus.COMPLETED, WorkflowStatus.FAILED])
async def test_terminal_cas_miss_rejects_opposite_outcome_without_retry(monkeypatch, status):
    pm = AG2PersistenceManager()
    opposite = WorkflowStatus.COMPLETED if status is WorkflowStatus.FAILED else WorkflowStatus.FAILED
    coll = SimpleNamespace(
        find_one=AsyncMock(side_effect=[{"status": 0}, {"status": int(opposite)}]),
        update_one=AsyncMock(return_value=SimpleNamespace(acknowledged=True, modified_count=0)),
    )
    monkeypatch.setattr(pm, "_coll", AsyncMock(return_value=coll))
    with pytest.raises(ChatSessionTerminalError) as exc:
        await pm._mark_chat_terminal("chat", "app", status)
    assert exc.value.status is opposite
    assert coll.find_one.await_args.args == ({"_id": "chat", "app_id": "app"}, {"status": 1})
    assert coll.update_one.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("settled_doc", [None, {"status": 0}])
async def test_terminal_cas_miss_does_not_acknowledge_missing_or_unsettled_row(monkeypatch, settled_doc):
    pm = AG2PersistenceManager()
    coll = SimpleNamespace(
        find_one=AsyncMock(side_effect=[{"status": 0}, settled_doc]),
        update_one=AsyncMock(return_value=SimpleNamespace(acknowledged=True, modified_count=0)),
    )
    monkeypatch.setattr(pm, "_coll", AsyncMock(return_value=coll))
    with pytest.raises(RuntimeError, match="Terminal workflow session write was not applied"):
        await pm.mark_chat_failed("chat", "app")
    assert coll.find_one.await_count == 2
    assert coll.update_one.await_count == 1


@pytest.mark.asyncio
async def test_unacknowledged_terminal_write_fails_without_reading_result(monkeypatch):
    pm = AG2PersistenceManager()
    coll = SimpleNamespace(
        find_one=AsyncMock(return_value={"status": 0}),
        update_one=AsyncMock(return_value=SimpleNamespace(acknowledged=False)),
    )
    monkeypatch.setattr(pm, "_coll", AsyncMock(return_value=coll))
    with pytest.raises(RuntimeError, match="Terminal workflow session write was not applied"):
        await pm.mark_chat_failed("chat", "app")
    assert coll.find_one.await_count == coll.update_one.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [False, True])
async def test_closed_failed_wal_cannot_open_another_channel_after_restart(resume):
    from ag2.knowledge import MemoryKnowledgeStore

    from mozaiksai.core.adapters.ag2_network_runner import AG2NetworkRunner, AG2NetworkRunnerRequest
    from mozaiksai.core.ports.orchestration import RunStatus
    from tests.test_ag2_network_execution_alignment import _DeterministicAgent

    store = MemoryKnowledgeStore()
    agent = _DeterministicAgent("Worker", "Rejected")
    request = AG2NetworkRunnerRequest(
        workflow_name="DurabilitySmoke", chat_id="chat", app_id="app",
        agents={"Worker": agent}, initial_agent_name="Worker", initial_message="Start",
        transition_rules=[{
            "source_agent": "Worker", "target_agent": "terminate", "transition_type": "after_turn",
            "termination_reason": "workflow_failed",
        }], knowledge_store=store, close_timeout_seconds=3.0,
    )
    first = await AG2NetworkRunner().run(request)
    assert first.status is RunStatus.FAILED
    calls = len(agent.ask_calls)
    request.resume_existing_only = resume
    second = await AG2NetworkRunner().run(request)
    assert second.status is RunStatus.FAILED
    assert second.error == "ag2_network_channel_terminal"
    assert second.channel_id == first.channel_id
    assert second.wal == []
    assert len(agent.ask_calls) == calls


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [1, 2])
@pytest.mark.parametrize("route", ["start", "resume", "live", "callback"])
async def test_terminal_input_refused_before_any_mutation(monkeypatch, status, route):
    from mozaiksai.core.ports.orchestration import RunStatus
    from tests.test_workflow_bridge import (
        _ag2_mod,
        _DummyTransport,
        _FakeAdapter,
        _FakeLiveRun,
        _FakePersistenceManager,
        _LiveRunResult,
    )

    pm = _FakePersistenceManager()
    pm.status = status
    transport = _DummyTransport(pm)
    adapter = _FakeAdapter()
    monkeypatch.setattr(_ag2_mod, "get_ag2_adapter", lambda: adapter)
    mutate = AsyncMock()
    monkeypatch.setattr(transport, "_apply_user_text_context_updates", mutate)
    live = _FakeLiveRun(result=_LiveRunResult(status=RunStatus.PAUSED))
    if route == "live":
        transport.register_live_ag2_workflow_run("chat", live)
    elif route == "callback":
        transport._input_request_registries["chat"] = {"request": object()}
    result = await transport.handle_user_input_from_api(
        chat_id="chat", user_id="user", app_id="app", workflow_name="DurabilitySmoke",
        message=None if route == "resume" else "Continue",
        initial_agent_name_override="Worker" if route == "resume" else None,
    )
    assert result["error_code"] == "WORKFLOW_SESSION_TERMINAL"
    assert result["run_status"] == str(WorkflowStatus(status))
    assert adapter.run_requests == adapter.resume_requests == live.continued == []
    assert pm.run_user_messages == pm.pending_clears == pm.persisted_context == []
    assert transport.submitted_inputs == transport.persisted_messages == []
    assert pm.failed == pm.completed == []
    mutate.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_orchestration_terminal_refusal_does_not_invoke_hooks_or_agents(monkeypatch):
    from mozaiksai.core.workflow import orchestration_patterns as orchestration

    pm = SimpleNamespace(assert_chat_resumable=AsyncMock(side_effect=ChatSessionTerminalError(WorkflowStatus.FAILED)))
    monkeypatch.setattr(orchestration, "AG2PersistenceManager", lambda: pm)
    config = AsyncMock()
    monkeypatch.setattr(orchestration, "_load_workflow_config", config)
    with pytest.raises(ChatSessionTerminalError):
        await orchestration.run_workflow_orchestration(
            workflow_name="DurabilitySmoke", chat_id="chat", app_id="app", user_id="user",
        )
    config.assert_not_called()


@pytest.mark.asyncio
async def test_storage_failure_refuses_execution_and_failure_acknowledgement(monkeypatch):
    pm = AG2PersistenceManager()
    monkeypatch.setattr(pm, "_coll", AsyncMock(side_effect=RuntimeError("storage unavailable")))
    with pytest.raises(RuntimeError, match="storage unavailable"):
        await pm.assert_chat_resumable("chat", "app")
    with pytest.raises(RuntimeError, match="storage unavailable"):
        await pm.mark_chat_failed("chat", "app")


@pytest.mark.asyncio
async def test_failure_write_rejects_lost_lease_before_collection_access(monkeypatch):
    from mozaiksai.core.data.persistence import persistence_manager as module

    pm = AG2PersistenceManager()
    coll = AsyncMock()
    monkeypatch.setattr(pm, "_coll", coll)
    def lost(**kwargs):
        raise RuntimeError("lease lost")
    monkeypatch.setattr(module, "assert_chat_mutable", lost)
    with pytest.raises(RuntimeError, match="lease lost"):
        await pm.mark_chat_failed("chat", "app")
    coll.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [0, 1, 2])
async def test_status_projection_includes_in_progress_and_failed(monkeypatch, status):
    pm = AG2PersistenceManager()
    cursor = SimpleNamespace(to_list=AsyncMock(return_value=[{
        "_id": "chat", "workflow_name": "DurabilitySmoke", "status": status,
    }]))
    cursor.sort = lambda *args: cursor
    cursor.limit = lambda *args: cursor
    coll = SimpleNamespace(find=lambda *args: cursor)
    monkeypatch.setattr(pm, "_coll", AsyncMock(return_value=coll))
    result = await pm.get_user_workflow_statuses(app_id="app", user_id="user")
    assert result == {"DurabilitySmoke": {"chat_id": "chat", "status": str(WorkflowStatus(status))}}


@pytest.mark.asyncio
async def test_failed_runner_result_is_durable_before_lifecycle_and_ui(monkeypatch):
    from mozaiksai.core.ports.orchestration import RunStatus
    from mozaiksai.core.transport.simple_transport import SimpleTransport
    from mozaiksai.core.workflow import orchestration_patterns as orchestration
    from mozaiksai.core.workflow import task_batches
    from mozaiksai.core.workflow.execution import lifecycle
    from mozaiksai.core.workflow.outputs import structured
    from tests.test_ag2_network_execution_alignment import _DeterministicAgent

    order = []
    async def mark_failed(*args, **kwargs):
        order.append("durable")
        return True
    async def trigger(name, **kwargs):
        if name == "on_fail":
            assert order[0] == "durable"
            order.append("on_fail")
    async def send(event, chat_id):
        if event.get("kind") == "run_complete":
            assert order[0] == "durable"
            assert event["status"] == "failed" and not event["run_completed"]
            order.append("ui")
    pm = SimpleNamespace(
        assert_chat_resumable=AsyncMock(), load_run_events=AsyncMock(return_value=[]),
        create_chat_session=AsyncMock(), get_or_assign_cache_seed=AsyncMock(return_value=1),
        fetch_chat_session_extra_context=AsyncMock(return_value={}), persist_context_variables=AsyncMock(),
        mark_chat_failed=AsyncMock(side_effect=mark_failed), mark_chat_completed=AsyncMock(),
    )
    monkeypatch.setattr(orchestration, "AG2PersistenceManager", lambda: pm)
    monkeypatch.setattr(SimpleTransport, "get_instance", AsyncMock(return_value=SimpleNamespace(
        connections={}, send_event_to_ui=send,
    )))
    monkeypatch.setattr(orchestration, "_load_workflow_config", lambda name: {
        "config": {"workflow_startup_mode": "AgentDriven"}, "max_turns": 2,
        "workflow_startup_mode": "AgentDriven", "initial_agent_name": "Worker",
    })
    monkeypatch.setattr(task_batches, "load_task_batches_config", lambda name: None)
    monkeypatch.setattr(structured, "load_workflow_structured_outputs", lambda name: ({}, {}))
    monkeypatch.setattr(lifecycle, "get_lifecycle_manager", lambda name: SimpleNamespace(
        trigger_before_chat=AsyncMock(), execute_trigger=trigger,
    ))
    result = SimpleNamespace(
        status=RunStatus.FAILED, error="workflow_failed", context_variables={}, wal=[],
        agent_name_by_id={}, channel_id="channel", close_reason="workflow_failed", structured_outputs=[],
    )
    network = AsyncMock(return_value=result)
    monkeypatch.setattr(orchestration, "_run_ag2_network_phase", network)
    output = await orchestration.run_workflow_orchestration(
        workflow_name="DurabilitySmoke", chat_id="chat", app_id="app", user_id="user",
        agents_factory=AsyncMock(return_value={"Worker": _DeterministicAgent("Worker", "unused")}),
        context_factory=lambda: {},
    )
    assert output["failed"] is True
    assert order.count("durable") == order.count("on_fail") == order.count("ui") == 1
    pm.mark_chat_completed.assert_not_awaited()
    network.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["cancelled", "lease_lost", "admission"])
async def test_nonterminal_interruption_never_marks_failed(monkeypatch, interruption):
    import asyncio

    from mozaiksai.core.runtime.persistence.distributed_lock import ChatLeaseLostError
    from mozaiksai.core.tokens.guard import TokenUsageDecision, TokenUsageDenied
    from mozaiksai.core.transport.simple_transport import SimpleTransport
    from mozaiksai.core.workflow import orchestration_patterns as orchestration

    error = {
        "cancelled": asyncio.CancelledError(),
        "lease_lost": ChatLeaseLostError("chat:app:chat"),
        "admission": TokenUsageDenied(TokenUsageDecision(allowed=False, reason="low_balance")),
    }[interruption]
    pm = SimpleNamespace(
        assert_chat_resumable=AsyncMock(), mark_chat_failed=AsyncMock(), mark_chat_completed=AsyncMock(),
    )
    monkeypatch.setattr(orchestration, "AG2PersistenceManager", lambda: pm)
    monkeypatch.setattr(SimpleTransport, "get_instance", AsyncMock(return_value=SimpleNamespace()))
    def interrupt(name):
        raise error
    monkeypatch.setattr(orchestration, "_load_workflow_config", interrupt)
    with pytest.raises(type(error)):
        await orchestration.run_workflow_orchestration(
            workflow_name="DurabilitySmoke", chat_id="chat", app_id="app", user_id="user",
        )
    pm.mark_chat_failed.assert_not_awaited()
    pm.mark_chat_completed.assert_not_awaited()
