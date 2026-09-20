"""Recovery reservations use AG2's persisted channel context and writer authority."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
from ag2.knowledge import MemoryKnowledgeStore
from ag2.network import EV_CONTEXT_SET, EV_PACKET, Hub

from mozaiksai.core.adapters.ag2_network_runner import (
    AG2NetworkRunner,
    AG2NetworkRunnerRequest,
    _resume_pending_agent_turns,
    checkpoint_agent_context,
)
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge, _workflow_tool_invocation
from mozaiksai.core.workflow.context.authority import (
    TASK_BATCH_WRITER,
    build_context_authority_policy,
)
from tests.test_ag2_network_execution_alignment import _DeterministicAgent
from tests.test_task_batch_recovery import _config, _context, _request, _runner


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_after_checkpoint", [False, True])
async def test_batch_checkpoint_survives_hub_reopen_without_advancing_failed_packet(fail_after_checkpoint):
    store = MemoryKnowledgeStore()
    planner = _DeterministicAgent("Planner", "planned")
    downstream = _DeterministicAgent("After", "done")
    policy = build_context_authority_policy(
        workflow_name="CheckpointTest", definitions={"batch_results": {"type": "object", "source": "computed"}},
        task_batch_context_keys={"batch_results"},
    )
    bridge = ContextVariablesBridge({}, authority_policy=policy)
    planner._mozaiks_context_bridge = bridge

    async def output_handler(agent_name, packet):
        if agent_name != "Planner":
            return
        with _workflow_tool_invocation(bridge, writer_id=TASK_BATCH_WRITER):
            bridge.set("batch_results", {"in_flight": {"services": {"attempt": 2}}})
        await checkpoint_agent_context()
        # The callback proceeds only after the existing Hub event is accepted.
        assert bridge.consume_context_updates() == {"set": {}, "delete": []}
        if fail_after_checkpoint:
            raise RuntimeError("interrupted batch correction")
        with _workflow_tool_invocation(bridge, writer_id=TASK_BATCH_WRITER):
            bridge.set("batch_results", {"in_flight": {}, "accepted": ["services"]})
        await checkpoint_agent_context()

    result = await AG2NetworkRunner().run(AG2NetworkRunnerRequest(
        workflow_name="CheckpointTest", chat_id="checkpoint-chat", app_id="checkpoint-app",
        agents={"Planner": planner, "After": downstream}, initial_agent_name="Planner", initial_message="Build",
        transition_rules=[
            {"source_agent": "Planner", "target_agent": "After", "transition_type": "after_turn"},
            {"source_agent": "After", "target_agent": "terminate", "transition_type": "after_turn"},
        ],
        agent_output_handler=output_handler, knowledge_store=store, context_authority_policy=policy,
        close_timeout_seconds=10,
    ))
    assert result.status is (RunStatus.FAILED if fail_after_checkpoint else RunStatus.COMPLETED)
    assert bool(downstream.ask_calls) is not fail_after_checkpoint
    hub = await Hub.open(store, ttl_sweep_interval=0, expectation_sweep_interval=0)
    try:
        wal = await hub.read_wal(result.channel_id)
        checkpoints = [event for event in wal if event.event_type == EV_CONTEXT_SET]
        assert checkpoints[0].event_data["set"]["batch_results"]["in_flight"]["services"]["attempt"] == 2
        assert all(event.audience == [] for event in checkpoints)
        if fail_after_checkpoint:
            assert len(checkpoints) == 1
            assert not any(event.event_type == EV_PACKET for event in wal)
        else:
            assert len(checkpoints) == 2
            assert checkpoints[-1].event_data["set"]["batch_results"]["accepted"] == ["services"]
    finally:
        await hub.close()


@pytest.mark.asyncio
async def test_checkpoint_is_unavailable_outside_an_active_packet_hook():
    with pytest.raises(RuntimeError, match="no active AG2 packet"):
        await checkpoint_agent_context()


@pytest.mark.asyncio
async def test_same_channel_resume_hydrates_inflight_evidence_before_recovery(monkeypatch):
    from mozaiksai.core.workflow.task_batches import execute_task_batches_for_trigger

    store, checkpoints, config = MemoryKnowledgeStore(), [], _config()
    _, counts = _runner(monkeypatch, checkpoints)
    task_keys = {"results", "status", "recovery_request", "recovery_result", "recovery_status"}
    policy = build_context_authority_policy(
        workflow_name="RecoveryTest", task_batch_context_keys=task_keys,
        definitions={
            **{key: {"type": "object", "source": "computed"} for key in task_keys},
            "bundle_repair_attempt_count": {
                "type": "integer", "source": "computed",
                "authority_class": "closed_writer_quality_state", "writer_ids": ["deterministic_tool"],
            },
        },
    )
    stale = {}

    def request(*, resumed=False):
        bridge = ContextVariablesBridge(deepcopy(stale) if resumed else _context(), authority_policy=policy)
        planner = _DeterministicAgent("Plan", "plan")
        planner._mozaiks_context_bridge = bridge

        async def checkpoint(updates):
            checkpoints.append(deepcopy(updates))
            with _workflow_tool_invocation(bridge, writer_id=TASK_BATCH_WRITER):
                for key, value in updates.items():
                    bridge.set(key, value)
            await checkpoint_agent_context()

        async def output_handler(agent_name, packet):
            context = bridge.snapshot()
            if resumed:
                assert context["results"]["_meta"]["in_flight"]["services"]["attempt"] == 2
                assert context["bundle_repair_attempt_count"] == 2
            await execute_task_batches_for_trigger(
                workflow_name="RecoveryTest", trigger_agent="Validate" if resumed else "Plan",
                batches_config=config, agents={name: object() for name in (
                    "ModelAgent", "ServiceAgent", "PageAgent", "OtherAgent",
                )}, context_variables=context, fresh_agents_per_task=False,
                checkpoint=checkpoint, chat_id="chat", app_id="app", parent_channel_id=packet.channel_id,
            )
            if resumed:
                assert context["recovery_status"] == "blocked"
                assert "interrupted" in context["recovery_result"]["blocked_reasons"][0]
                return
            _request(context)
            stale.update(deepcopy(context))
            stale["bundle_repair_attempt_count"] = 0
            context["results"]["_meta"]["in_flight"]["services"] = {"attempt": 2}
            await checkpoint({"results": context["results"], "recovery_request": context["recovery_request"]})
            with _workflow_tool_invocation(bridge):
                bridge.set("bundle_repair_attempt_count", 2)
            await checkpoint_agent_context()
            raise RuntimeError("process lost after reserving correction")

        return AG2NetworkRunnerRequest(
            workflow_name="RecoveryTest", chat_id="chat", app_id="app", agents={"Plan": planner},
            initial_agent_name="Plan", initial_message=None if resumed else "Build",
            transition_rules=[{"source_agent": "Plan", "target_agent": "terminate", "transition_type": "after_turn"}],
            context_variables=deepcopy(stale) if resumed else _context(),
            knowledge_store=store, context_authority_policy=policy, agent_output_handler=output_handler,
            resume_existing_only=resumed, close_timeout_seconds=3,
        )

    first = await AG2NetworkRunner().run(request())
    assert first.status is RunStatus.FAILED
    before = counts.copy()
    resumed = await AG2NetworkRunner().run(request(resumed=True))
    assert resumed.status is RunStatus.COMPLETED, resumed.error
    assert resumed.channel_id == first.channel_id
    assert counts == before
    assert counts["services"] == 1


@pytest.mark.asyncio
async def test_empty_hub_context_removes_stale_seed_and_pending_mutations():
    bridge = ContextVariablesBridge({"results": {"stale": True}, "bundle_repair_attempt_count": 0})
    bridge.set("pending", "uncommitted")
    agent = _DeterministicAgent("Check", "done")
    agent._mozaiks_context_bridge = bridge

    async def output_handler(agent_name, packet):
        assert bridge.snapshot() == {}
        assert bridge.consume_context_updates() == {"set": {}, "delete": []}

    result = await AG2NetworkRunner().run(AG2NetworkRunnerRequest(
        workflow_name="EmptyHydration", chat_id="chat", app_id="app", agents={"Check": agent},
        initial_agent_name="Check", initial_message="Check", context_variables={},
        transition_rules=[{"source_agent": "Check", "target_agent": "terminate", "transition_type": "after_turn"}],
        agent_output_handler=output_handler, close_timeout_seconds=3,
    ))
    assert result.status is RunStatus.COMPLETED, result.error


@pytest.mark.asyncio
async def test_interrupted_artifact_worker_cannot_replay_before_validation():
    store = MemoryKnowledgeStore()

    class InterruptedWorker(_DeterministicAgent):
        async def ask(self, *msg, **kwargs):
            await super().ask(*msg, **kwargs)
            raise RuntimeError("process lost during worker execution")

    def request(worker, *, resumed=False):
        worker._mozaiks_pending_turn_replay = "block"
        return AG2NetworkRunnerRequest(
            workflow_name="ArtifactRepair", chat_id="chat", app_id="app", agents={"ServiceAgent": worker},
            initial_agent_name="ServiceAgent", initial_message=None if resumed else "Repair",
            context_variables={"accepted_output": {"schemas.py": "preserved"}, "repair_status": "selected"},
            transition_rules=[{"source_agent": "ServiceAgent", "target_agent": "terminate", "transition_type": "after_turn"}],
            knowledge_store=store, resume_existing_only=resumed, close_timeout_seconds=3,
        )

    first_worker = InterruptedWorker("ServiceAgent", "unused")
    failed = await AG2NetworkRunner().run(request(first_worker))
    assert failed.status is RunStatus.FAILED
    assert len(first_worker.ask_calls) == 1
    for _ in range(2):
        worker = _DeterministicAgent("ServiceAgent", "must not execute")
        blocked = await AG2NetworkRunner().run(request(worker, resumed=True))
        assert blocked.status is RunStatus.FAILED
        assert blocked.channel_id == failed.channel_id
        assert "ag2_pending_turn_replay_blocked" in blocked.error
        assert "ServiceAgent" in blocked.error and failed.channel_id in blocked.error
        assert blocked.context_variables["accepted_output"] == {"schemas.py": "preserved"}
        assert blocked.context_variables["repair_status"] == "selected"
        assert not worker.ask_calls


@pytest.mark.asyncio
async def test_pending_replay_preflights_blocked_worker_before_any_allowed_turn():
    calls = []

    class Client:
        def __init__(self, name, policy):
            self.agent_id = name
            self.agent = SimpleNamespace(_mozaiks_pending_turn_replay=policy)

        async def resume_pending_turns(self):
            calls.append(self.agent_id)
            return 1

    class PendingHub:
        async def pending_turns_for(self, agent_id):
            return [SimpleNamespace(channel_id="channel")]

    with pytest.raises(RuntimeError, match="ag2_pending_turn_replay_blocked"):
        await _resume_pending_agent_turns(
            hub=PendingHub(), channel_id="channel", workflow_name="Test", chat_id="chat",
            agent_clients={"Allowed": Client("Allowed", "allow"), "Blocked": Client("Blocked", "allow")},
            pending_turn_replay={"Allowed": "allow", "Blocked": "block"},
        )
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", [None, False, [], "unknown"])
async def test_invalid_pending_replay_policy_fails_before_worker_execution(policy):
    agent = _DeterministicAgent("Worker", "unused")
    agent._mozaiks_pending_turn_replay = policy
    result = await AG2NetworkRunner().run(AG2NetworkRunnerRequest(
        workflow_name="InvalidReplay", chat_id="chat", app_id="app", agents={"Worker": agent},
        initial_agent_name="Worker", initial_message="Start",
        transition_rules=[{"source_agent": "Worker", "target_agent": "terminate", "transition_type": "after_turn"}],
    ))
    assert result.status is RunStatus.FAILED
    assert "invalid pending_turn_replay" in result.error
    assert not agent.ask_calls


@pytest.mark.asyncio
async def test_factory_preserves_pending_replay_policy_for_remote_agents(monkeypatch):
    from mozaiksai.core.workflow.agents import factory
    from mozaiksai.core.workflow.agents import tools as agent_tools

    config = {"agents": [{"name": "RemoteWorker", "structured_outputs_required": False,
                          "system_message": "Repair", "pending_turn_replay": "block"}]}
    manager = SimpleNamespace(get_config=lambda name: config, get_auto_tool_agents=lambda name: set())
    remote = SimpleNamespace()
    monkeypatch.setattr(factory, "workflow_manager", manager)
    monkeypatch.setattr(factory, "load_a2a_agent_specs", lambda config: {"RemoteWorker": object()})
    monkeypatch.setattr(factory, "create_a2a_remote_agent", lambda *args, **kwargs: remote)
    monkeypatch.setattr(factory, "get_structured_outputs_for_workflow", lambda name: {})
    monkeypatch.setattr(agent_tools, "load_agent_tool_functions", lambda name: {})
    agents = await factory.create_agents("RemoteRepair", context_variables={})
    assert agents["RemoteWorker"] is remote
    assert remote._mozaiks_pending_turn_replay == "block"
