from __future__ import annotations

import json
from time import perf_counter
from typing import Any

import pytest
from autogen.beta import Agent
from autogen.beta.knowledge import MemoryKnowledgeStore
from autogen.beta.network import (
    EV_CHANNEL_CLOSED,
    EV_PACKET,
    EV_TEXT,
    AgentTarget,
    FromSpeaker,
    Hub,
    HubClient,
    LocalLink,
    Passport,
    Resume,
    TerminateTarget,
    Transition,
    TransitionGraph,
)
from autogen.beta.network.policies import CHANNEL_STATE_DEP
from pydantic import BaseModel

import mozaiksai.core.transport.simple_transport as simple_transport_module
import mozaiksai.core.workflow.orchestration_patterns as orchestration_patterns_module
import mozaiksai.core.workflow.outputs.structured as structured_outputs_module
import mozaiksai.core.workflow.task_batches as task_batches_module
from mozaiksai.core.adapters.ag2_network_runner import AG2NetworkRunner, AG2NetworkRunnerRequest
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.orchestration_patterns import run_workflow_orchestration
from mozaiksai.core.workflow.task_batches import parse_task_batches_config


class _Reply:
    def __init__(self, body: str) -> None:
        self.body = body


class _DeterministicAgent(Agent):
    def __init__(self, name: str, body: str) -> None:
        super().__init__(name, prompt=f"{name} deterministic test agent")
        self._body = body
        self.ask_calls: list[dict[str, Any]] = []

    async def ask(self, *msg: Any, **kwargs: Any) -> _Reply:  # type: ignore[override]
        self.ask_calls.append({"msg": msg, "kwargs": kwargs})
        return _Reply(self._body)


class _PlannerOutput(BaseModel):
    plan_ready: bool


class _WorkerOutput(BaseModel):
    worker_done: bool


class _ContextMutatingAgent(_DeterministicAgent):
    def __init__(self, name: str, body: str, updates: dict[str, Any]) -> None:
        super().__init__(name, body)
        self._updates = updates

    async def ask(self, *msg: Any, **kwargs: Any) -> _Reply:  # type: ignore[override]
        bridge = self._mozaiks_context_bridge
        for key, value in self._updates.items():
            bridge.set(key, value)
        return await super().ask(*msg, **kwargs)


class _ContextOperationAgent(_DeterministicAgent):
    def __init__(
        self,
        name: str,
        body: str,
        operations: list[tuple[str, str, Any]],
    ) -> None:
        super().__init__(name, body)
        self._operations = operations

    async def ask(self, *msg: Any, **kwargs: Any) -> _Reply:  # type: ignore[override]
        bridge = self._mozaiks_context_bridge
        for operation, key, value in self._operations:
            if operation == "set":
                bridge.set(key, value)
            elif operation == "delete":
                bridge.delete(key)
            else:
                raise AssertionError(f"unknown context operation {operation!r}")
        return await super().ask(*msg, **kwargs)


class _FailingContextMutatingAgent(Agent):
    def __init__(self, name: str, updates: dict[str, Any]) -> None:
        super().__init__(name, prompt=f"{name} failing test agent")
        self._updates = updates
        self.ask_calls: list[dict[str, Any]] = []

    async def ask(self, *msg: Any, **kwargs: Any) -> _Reply:  # type: ignore[override]
        self.ask_calls.append({"msg": msg, "kwargs": kwargs})
        bridge = self._mozaiks_context_bridge
        for key, value in self._updates.items():
            bridge.set(key, value)
        raise RuntimeError("planned agent failure")


@pytest.mark.anyio
async def test_ag2_workflow_channel_owns_turn_order_wal_and_termination() -> None:
    hub = await Hub.open(MemoryKnowledgeStore(), ttl_sweep_interval=0, expectation_sweep_interval=0)
    link = LocalLink(hub)
    human_hc = HubClient(link, hub=hub)
    planner_hc = HubClient(link, hub=hub)
    worker_hc = HubClient(link, hub=hub)

    try:
        human = await human_hc.register_human(Passport(name="human"), resume=Resume())
        planner_agent = _DeterministicAgent("PlannerAgent", '{"plan_ready": true}')
        worker_agent = _DeterministicAgent("WorkerAgent", '{"worker_done": true}')
        planner = await planner_hc.register(
            planner_agent,
            Passport(name="PlannerAgent"),
            Resume(claimed_capabilities=["planning"]),
        )
        worker = await worker_hc.register(
            worker_agent,
            Passport(name="WorkerAgent"),
            Resume(claimed_capabilities=["execution"]),
        )

        graph = TransitionGraph(
            initial_speaker=human.agent_id,
            transitions=[
                Transition(
                    when=FromSpeaker(human.agent_id),
                    then=AgentTarget(planner.agent_id),
                ),
                Transition(
                    when=FromSpeaker(planner.agent_id),
                    then=AgentTarget(worker.agent_id),
                ),
                Transition(
                    when=FromSpeaker(worker.agent_id),
                    then=TerminateTarget(reason="alignment_smoke_complete"),
                ),
            ],
            max_turns=4,
        )

        channel = await human.open(
            type="workflow",
            target=[planner.agent_id, worker.agent_id],
            knobs={"graph": graph.to_dict(), "context_vars": {"build_id": "build-123"}},
        )
        await channel.send("Plan the deterministic smoke.", audience=[planner.agent_id])

        close_env = await human.wait_for_channel_event(
            channel_id=channel.channel_id,
            predicate=lambda envelope: envelope.event_type == EV_CHANNEL_CLOSED,
            timeout=10.0,
        )

        assert close_env.event_data.get("reason") == "alignment_smoke_complete"
        assert planner_agent.ask_calls
        assert worker_agent.ask_calls

        state = hub.adapter_state(channel.channel_id)
        assert state.expected_next_speaker is None
        assert state.context_vars["build_id"] == "build-123"

        wal = await hub.read_wal(channel.channel_id)
        event_types = [envelope.event_type for envelope in wal]
        assert EV_TEXT in event_types
        assert event_types.count(EV_PACKET) == 2
        assert event_types[-1] == EV_CHANNEL_CLOSED
    finally:
        await human_hc.close()
        await planner_hc.close()
        await worker_hc.close()
        await hub.close()


@pytest.mark.anyio
async def test_ag2_network_runner_executes_mozaiks_transition_rules() -> None:
    planner_agent = _DeterministicAgent("PlannerAgent", '{"plan_ready": true}')
    worker_agent = _DeterministicAgent("WorkerAgent", '{"worker_done": true}')

    result = await AG2NetworkRunner().run(
        AG2NetworkRunnerRequest(
            workflow_name="AlignmentSmoke",
            chat_id="chat-1",
            app_id="app-1",
            agents={
                "PlannerAgent": planner_agent,
                "WorkerAgent": worker_agent,
            },
            transition_rules=[
                {
                    "source_agent": "PlannerAgent",
                    "target_agent": "WorkerAgent",
                    "transition_type": "after_turn",
                },
                {
                    "source_agent": "WorkerAgent",
                    "target_agent": "terminate",
                    "transition_type": "after_turn",
                },
            ],
            initial_agent_name="PlannerAgent",
            initial_message="Plan and execute.",
            context_variables={"build_id": "build-456"},
            structured_registry={
                "PlannerAgent": _PlannerOutput,
                "WorkerAgent": _WorkerOutput,
            },
            max_turns=4,
            close_timeout_seconds=10.0,
        )
    )

    assert result.status is RunStatus.COMPLETED
    assert result.workflow_name == "AlignmentSmoke"
    assert result.channel_id
    assert result.close_reason == "workflow_complete"
    assert result.context_variables["build_id"] == "build-456"
    assert planner_agent.ask_calls
    assert worker_agent.ask_calls
    assert result.structured_outputs == [
        {
            "agent": "PlannerAgent",
            "model_name": "_PlannerOutput",
            "structured_data": {"plan_ready": True},
        },
        {
            "agent": "WorkerAgent",
            "model_name": "_WorkerOutput",
            "structured_data": {"worker_done": True},
        },
    ]
    assert [entry["event_type"] for entry in result.wal].count(EV_PACKET) == 2
    assert result.wal[-1]["event_type"] == EV_CHANNEL_CLOSED


@pytest.mark.anyio
async def test_ag2_network_runner_commits_tool_context_updates_before_routing() -> None:
    context: dict[str, Any] = {}
    planner_agent = _ContextMutatingAgent(
        "PlannerAgent",
        '{"plan_ready": true}',
        {"route": "review"},
    )
    planner_agent._mozaiks_context_bridge = ContextVariablesBridge(context)
    review_agent = _DeterministicAgent("ReviewAgent", '{"worker_done": true}')

    result = await AG2NetworkRunner().run(
        AG2NetworkRunnerRequest(
            workflow_name="ContextMutationSmoke",
            chat_id="chat-1",
            app_id="app-1",
            agents={
                "PlannerAgent": planner_agent,
                "ReviewAgent": review_agent,
            },
            transition_rules=[
                {
                    "source_agent": "PlannerAgent",
                    "target_agent": "ReviewAgent",
                    "transition_type": "condition",
                    "condition_type": "context_equals",
                    "condition_key": "route",
                    "condition_value": "review",
                },
                {
                    "source_agent": "PlannerAgent",
                    "target_agent": "terminate",
                    "transition_type": "after_turn",
                },
                {
                    "source_agent": "ReviewAgent",
                    "target_agent": "terminate",
                    "transition_type": "after_turn",
                },
            ],
            initial_agent_name="PlannerAgent",
            initial_message="Set route then continue.",
            close_timeout_seconds=10.0,
        )
    )

    assert result.status is RunStatus.COMPLETED
    assert result.context_variables["route"] == "review"
    assert planner_agent.ask_calls
    assert review_agent.ask_calls
    planner_packet = next(
        entry
        for entry in result.wal
        if entry["event_type"] == EV_PACKET and entry["sender_id"] in result.agent_name_by_id
        and result.agent_name_by_id[entry["sender_id"]] == "PlannerAgent"
    )
    assert planner_packet["event_data"]["context_updates"]["set"]["route"] == "review"


@pytest.mark.anyio
async def test_ag2_network_runner_pauses_immediately_on_user_handoff() -> None:
    interviewer = _DeterministicAgent("ValueInterviewAgent", "Please describe your product.")

    started_at = perf_counter()
    result = await AG2NetworkRunner().run(
        AG2NetworkRunnerRequest(
            workflow_name="PauseSmoke",
            chat_id="chat-pause",
            app_id="app-pause",
            agents={
                "ValueInterviewAgent": interviewer,
            },
            transition_rules=[
                {
                    "source_agent": "ValueInterviewAgent",
                    "target_agent": "user",
                    "transition_type": "after_turn",
                },
            ],
            initial_agent_name="ValueInterviewAgent",
            initial_message="Start the interview.",
            close_timeout_seconds=10.0,
        )
    )
    elapsed = perf_counter() - started_at

    assert result.status is RunStatus.PAUSED
    assert result.close_reason == "awaiting_user_input"
    assert interviewer.ask_calls
    assert elapsed < 2.0
    assert any(entry["event_type"] == EV_PACKET for entry in result.wal)
    assert not any(entry["event_type"] == EV_CHANNEL_CLOSED for entry in result.wal)
    assert result.live_run is not None
    await result.live_run.close()


@pytest.mark.anyio
async def test_ag2_network_runner_continues_paused_channel_with_user_message() -> None:
    interviewer = _DeterministicAgent("ValueInterviewAgent", "I recommend validating launch demand.")
    synthesis = _DeterministicAgent("SynthesisAgent", "Build the founder validation angle.")

    result = await AG2NetworkRunner().run(
        AG2NetworkRunnerRequest(
            workflow_name="PauseContinueSmoke",
            chat_id="chat-pause-continue",
            app_id="app-pause-continue",
            agents={
                "ValueInterviewAgent": interviewer,
                "SynthesisAgent": synthesis,
            },
            transition_rules=[
                {
                    "source_agent": "ValueInterviewAgent",
                    "target_agent": "user",
                    "transition_type": "after_turn",
                },
                {
                    "source_agent": "user",
                    "target_agent": "SynthesisAgent",
                    "transition_type": "after_turn",
                },
                {
                    "source_agent": "SynthesisAgent",
                    "target_agent": "terminate",
                    "transition_type": "after_turn",
                },
            ],
            initial_agent_name="ValueInterviewAgent",
            initial_message="Start the interview.",
            close_timeout_seconds=10.0,
        )
    )

    assert result.status is RunStatus.PAUSED
    assert result.live_run is not None

    continued = await result.live_run.continue_with_user_message(
        "founders validating launch demand",
        context_updates={"target_user": "founders"},
    )

    assert continued.status is RunStatus.COMPLETED
    assert continued.close_reason == "workflow_complete"
    assert synthesis.ask_calls
    assert continued.context_variables["target_user"] == "founders"
    assert [entry["event_type"] for entry in continued.wal].count(EV_PACKET) == 1
    assert continued.agent_name_by_id[
        next(entry["sender_id"] for entry in continued.wal if entry["event_type"] == EV_PACKET)
    ] == "SynthesisAgent"


@pytest.mark.anyio
async def test_ag2_network_runner_commits_multiple_context_updates_and_deletes() -> None:
    context: dict[str, Any] = {"obsolete": "old", "route": "draft"}
    planner_agent = _ContextOperationAgent(
        "PlannerAgent",
        '{"plan_ready": true}',
        [
            ("set", "route", "review"),
            ("set", "phase", "final"),
            ("delete", "obsolete", None),
        ],
    )
    planner_agent._mozaiks_context_bridge = ContextVariablesBridge(context)
    review_agent = _DeterministicAgent("ReviewAgent", '{"worker_done": true}')

    result = await AG2NetworkRunner().run(
        AG2NetworkRunnerRequest(
            workflow_name="ContextMutationSmoke",
            chat_id="chat-1",
            app_id="app-1",
            agents={
                "PlannerAgent": planner_agent,
                "ReviewAgent": review_agent,
            },
            transition_rules=[
                {
                    "source_agent": "PlannerAgent",
                    "target_agent": "ReviewAgent",
                    "transition_type": "condition",
                    "condition_type": "context_equals",
                    "condition_key": "obsolete",
                    "condition_value": None,
                },
                {
                    "source_agent": "PlannerAgent",
                    "target_agent": "terminate",
                    "transition_type": "after_turn",
                },
                {
                    "source_agent": "ReviewAgent",
                    "target_agent": "terminate",
                    "transition_type": "after_turn",
                },
            ],
            initial_agent_name="PlannerAgent",
            initial_message="Set route, phase, and delete obsolete.",
            close_timeout_seconds=10.0,
        )
    )

    assert result.status is RunStatus.COMPLETED
    assert result.context_variables["route"] == "review"
    assert result.context_variables["phase"] == "final"
    assert "obsolete" not in result.context_variables
    assert review_agent.ask_calls
    planner_packet = next(
        entry
        for entry in result.wal
        if entry["event_type"] == EV_PACKET
        and result.agent_name_by_id.get(entry["sender_id"]) == "PlannerAgent"
    )
    assert planner_packet["event_data"]["context_updates"] == {
        "set": {"route": "review", "phase": "final"},
        "delete": ["obsolete"],
    }
    assert planner_agent._mozaiks_context_bridge.consume_context_updates() == {
        "set": {},
        "delete": [],
    }


@pytest.mark.anyio
async def test_ag2_network_runner_leaves_noop_context_packet_unchanged() -> None:
    context: dict[str, Any] = {"route": "existing"}
    planner_agent = _DeterministicAgent("PlannerAgent", '{"plan_ready": true}')
    planner_agent._mozaiks_context_bridge = ContextVariablesBridge(context)

    result = await AG2NetworkRunner().run(
        AG2NetworkRunnerRequest(
            workflow_name="ContextNoopSmoke",
            chat_id="chat-1",
            app_id="app-1",
            agents={"PlannerAgent": planner_agent},
            transition_rules=[
                {
                    "source_agent": "PlannerAgent",
                    "target_agent": "terminate",
                    "transition_type": "after_turn",
                },
            ],
            initial_agent_name="PlannerAgent",
            initial_message="Do not mutate context.",
            context_variables=context,
            close_timeout_seconds=10.0,
        )
    )

    assert result.status is RunStatus.COMPLETED
    assert result.context_variables["route"] == "existing"
    planner_packet = next(
        entry
        for entry in result.wal
        if entry["event_type"] == EV_PACKET
        and result.agent_name_by_id.get(entry["sender_id"]) == "PlannerAgent"
    )
    assert planner_packet["event_data"]["context_updates"] == {
        "set": {},
        "delete": [],
    }
    assert planner_agent._mozaiks_context_bridge.consume_context_updates() == {
        "set": {},
        "delete": [],
    }


@pytest.mark.anyio
async def test_ag2_network_runner_fails_promptly_and_clears_pending_context_updates() -> None:
    context: dict[str, Any] = {}
    planner_agent = _FailingContextMutatingAgent(
        "PlannerAgent",
        {"route": "review"},
    )
    planner_agent._mozaiks_context_bridge = ContextVariablesBridge(context)

    result = await AG2NetworkRunner().run(
        AG2NetworkRunnerRequest(
            workflow_name="FailureSmoke",
            chat_id="chat-1",
            app_id="app-1",
            agents={"PlannerAgent": planner_agent},
            transition_rules=[
                {
                    "source_agent": "PlannerAgent",
                    "target_agent": "terminate",
                    "transition_type": "after_turn",
                },
            ],
            initial_agent_name="PlannerAgent",
            initial_message="Fail after mutating local context.",
            close_timeout_seconds=10.0,
        )
    )

    assert result.status is RunStatus.FAILED
    assert "AG2 turn failed for PlannerAgent: planned agent failure" == result.error
    assert planner_agent.ask_calls
    assert result.context_variables == {}
    assert result.agent_name_by_id
    assert not any(entry["event_type"] == EV_PACKET for entry in result.wal)
    assert planner_agent._mozaiks_context_bridge.to_dict() == {"route": "review"}
    assert planner_agent._mozaiks_context_bridge.consume_context_updates() == {
        "set": {},
        "delete": [],
    }


@pytest.mark.anyio
async def test_ag2_network_runner_reports_invalid_initial_agent() -> None:
    result = await AG2NetworkRunner().run(
        AG2NetworkRunnerRequest(
            workflow_name="AlignmentSmoke",
            chat_id="chat-1",
            app_id="app-1",
            agents={"PlannerAgent": _DeterministicAgent("PlannerAgent", "{}")},
            transition_rules=[],
            initial_agent_name="MissingAgent",
            initial_message="Start.",
        )
    )

    assert result.status is RunStatus.FAILED
    assert "initial agent 'MissingAgent' is not registered" in str(result.error)


@pytest.mark.anyio
async def test_ag2_network_runner_fails_invalid_structured_output_contract() -> None:
    result = await AG2NetworkRunner().run(
        AG2NetworkRunnerRequest(
            workflow_name="AlignmentSmoke",
            chat_id="chat-1",
            app_id="app-1",
            agents={
                "PlannerAgent": _DeterministicAgent("PlannerAgent", '{"unexpected": true}'),
            },
            transition_rules=[
                {
                    "source_agent": "PlannerAgent",
                    "target_agent": "terminate",
                    "transition_type": "after_turn",
                },
            ],
            initial_agent_name="PlannerAgent",
            initial_message="Plan.",
            structured_registry={"PlannerAgent": _PlannerOutput},
            close_timeout_seconds=10.0,
        )
    )

    assert result.status is RunStatus.FAILED
    assert result.channel_id
    assert result.close_reason == "workflow_complete"
    assert "structured output validation failed for PlannerAgent" in str(result.error)


@pytest.mark.anyio
async def test_run_workflow_orchestration_uses_ag2_network_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Persistence:
        def __init__(self) -> None:
            self.persisted_context: dict[str, Any] | None = None
            self.assistant_messages: list[dict[str, Any]] = []
            self.completed: list[tuple[str, str]] = []
            self.created_sessions: list[dict[str, Any]] = []

        async def load_run_events(self, *, chat_id: str, app_id: str) -> list[Any]:
            return []

        def project_run_events_to_messages(self, events: list[Any]) -> list[dict[str, Any]]:
            return []

        async def create_chat_session(self, **kwargs: Any) -> None:
            self.created_sessions.append(dict(kwargs))

        async def get_or_assign_cache_seed(self, chat_id: str, app_id: str) -> int:
            return 7

        async def fetch_chat_session_extra_context(self, *, chat_id: str, app_id: str) -> dict[str, Any]:
            return {}

        async def persist_context_variables(
            self,
            *,
            chat_id: str,
            app_id: str,
            variables: dict[str, Any],
        ) -> None:
            self.persisted_context = dict(variables)

        async def append_run_assistant_message(self, **kwargs: Any) -> None:
            self.assistant_messages.append(dict(kwargs))

        async def mark_chat_completed(self, chat_id: str, app_id: str) -> bool:
            self.completed.append((chat_id, app_id))
            return True

    class _Transport:
        def __init__(self) -> None:
            self.connections: dict[str, dict[str, Any]] = {}
            self.events: list[tuple[str, dict[str, Any]]] = []
            self.unregistered: list[str] = []

        async def send_event_to_ui(self, event: dict[str, Any], chat_id: str) -> None:
            self.events.append((chat_id, dict(event)))

        def unregister_derived_context_manager(self, chat_id: str) -> None:
            self.unregistered.append(chat_id)

    persistence = _Persistence()
    transport = _Transport()
    planner_agent = _DeterministicAgent("PlannerAgent", '{"plan_ready": true}')
    worker_agent = _DeterministicAgent("WorkerAgent", '{"worker_done": true}')

    async def _get_transport() -> _Transport:
        return transport

    async def _agents_factory(workflow_name: str, context: Any, cache_seed: int) -> dict[str, Agent]:
        assert workflow_name == "AlignmentSmoke"
        assert cache_seed == 7
        return {"PlannerAgent": planner_agent, "WorkerAgent": worker_agent}

    monkeypatch.setattr(orchestration_patterns_module, "AG2PersistenceManager", lambda: persistence)
    monkeypatch.setattr(simple_transport_module.SimpleTransport, "get_instance", staticmethod(_get_transport))
    monkeypatch.setattr(
        orchestration_patterns_module,
        "_load_workflow_config",
        lambda workflow_name: {
            "config": {
                "max_turns": 4,
                "workflow_startup_mode": "AgentDriven",
                "initial_agent": "PlannerAgent",
                "transition_graph": {
                    "transition_rules": [
                        {
                            "source_agent": "PlannerAgent",
                            "target_agent": "WorkerAgent",
                            "transition_type": "after_turn",
                        },
                        {
                            "source_agent": "WorkerAgent",
                            "target_agent": "terminate",
                            "transition_type": "after_turn",
                        },
                    ]
                },
            },
            "max_turns": 4,
            "workflow_startup_mode": "AgentDriven",
            "initial_agent_name": "PlannerAgent",
        },
    )
    monkeypatch.setattr(task_batches_module, "load_task_batches_config", lambda workflow_name: None)
    monkeypatch.setattr(structured_outputs_module, "load_workflow_structured_outputs", lambda workflow_name: ({}, {}))

    result = await run_workflow_orchestration(
        workflow_name="AlignmentSmoke",
        app_id="app-1",
        chat_id="chat-1",
        user_id="user-1",
        initial_message="Build the alignment smoke.",
        agents_factory=_agents_factory,
        context_factory=lambda: {"seed": "context"},
    )

    assert result is not None
    assert result["run_completed"] is True
    assert result["ag2_channel_id"]
    assert result["ag2_close_reason"] == "workflow_complete"
    assert planner_agent.ask_calls
    assert worker_agent.ask_calls
    assert [event["kind"] for _, event in transport.events].count("chat.text") == 2
    assert transport.events[-1][1]["kind"] == "run_complete"
    assert persistence.completed == [("chat-1", "app-1")]
    assert [message["agent_name"] for message in persistence.assistant_messages] == [
        "PlannerAgent",
        "WorkerAgent",
    ]


@pytest.mark.anyio
async def test_run_workflow_orchestration_executes_task_batches_between_ag2_phases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Persistence:
        def __init__(self) -> None:
            self.assistant_messages: list[dict[str, Any]] = []
            self.completed: list[tuple[str, str]] = []

        async def load_run_events(self, *, chat_id: str, app_id: str) -> list[Any]:
            return []

        def project_run_events_to_messages(self, events: list[Any]) -> list[dict[str, Any]]:
            return []

        async def create_chat_session(self, **kwargs: Any) -> None:
            return None

        async def get_or_assign_cache_seed(self, chat_id: str, app_id: str) -> int:
            return 7

        async def fetch_chat_session_extra_context(self, *, chat_id: str, app_id: str) -> dict[str, Any]:
            return {}

        async def persist_context_variables(
            self,
            *,
            chat_id: str,
            app_id: str,
            variables: dict[str, Any],
        ) -> None:
            self.persisted_context = dict(variables)

        async def append_run_assistant_message(self, **kwargs: Any) -> None:
            self.assistant_messages.append(dict(kwargs))

        async def mark_chat_completed(self, chat_id: str, app_id: str) -> bool:
            self.completed.append((chat_id, app_id))
            return True

    class _Transport:
        def __init__(self) -> None:
            self.connections: dict[str, dict[str, Any]] = {}
            self.events: list[tuple[str, dict[str, Any]]] = []

        async def send_event_to_ui(self, event: dict[str, Any], chat_id: str) -> None:
            self.events.append((chat_id, dict(event)))

        def unregister_derived_context_manager(self, chat_id: str) -> None:
            return None

    class _ContextAwareAgent(Agent):
        def __init__(self, name: str, body_factory) -> None:
            super().__init__(name, prompt=f"{name} deterministic test agent")
            self._body_factory = body_factory
            self.context_seen: list[dict[str, Any]] = []

        async def ask(self, *msg: Any, **kwargs: Any) -> _Reply:  # type: ignore[override]
            dependencies = kwargs.get("dependencies") or {}
            state = dependencies.get(CHANNEL_STATE_DEP)
            context = dict(getattr(state, "context_vars", {}) or {})
            self.context_seen.append(context)
            return _Reply(json.dumps(self._body_factory(context)))

    persistence = _Persistence()
    transport = _Transport()
    planner_agent = _ContextAwareAgent(
        "PlannerAgent",
        lambda _context: {
            "DecompositionPlan": {
                "tasks": [
                    {
                        "task_id": "module_contract",
                        "execution_agent": "WorkerAgent",
                        "task_prompt": "Build module contract.",
                        "owned_paths": ["modules/campaigns/module.yaml"],
                    }
                ]
            }
        },
    )
    worker_agent = _ContextAwareAgent(
        "WorkerAgent",
        lambda context: {
            "task_id": context["current_task_id"],
            "summary": "Worker used AG2 task channel context.",
            "owned_paths": context["current_task"]["owned_paths"],
            "agent_message": "Worker done.",
        },
    )
    synthesis_agent = _ContextAwareAgent(
        "SynthesisAgent",
        lambda context: {
            "agent_message": "Synthesis done.",
            "result": context["runtime_tasks_results"]["module_contract"]["summary"],
        },
    )

    async def _get_transport() -> _Transport:
        return transport

    async def _agents_factory(workflow_name: str, context: Any, cache_seed: int) -> dict[str, Agent]:
        return {
            "PlannerAgent": planner_agent,
            "WorkerAgent": worker_agent,
            "SynthesisAgent": synthesis_agent,
        }

    monkeypatch.setattr(orchestration_patterns_module, "AG2PersistenceManager", lambda: persistence)
    monkeypatch.setattr(simple_transport_module.SimpleTransport, "get_instance", staticmethod(_get_transport))
    monkeypatch.setattr(
        orchestration_patterns_module,
        "_load_workflow_config",
        lambda workflow_name: {
            "config": {
                "max_turns": 4,
                "workflow_startup_mode": "AgentDriven",
                "initial_agent": "PlannerAgent",
                "transition_graph": {
                    "transition_rules": [
                        {
                            "source_agent": "PlannerAgent",
                            "target_agent": "SynthesisAgent",
                            "transition_type": "after_turn",
                        },
                        {
                            "source_agent": "SynthesisAgent",
                            "target_agent": "terminate",
                            "transition_type": "after_turn",
                        },
                    ]
                },
            },
            "max_turns": 4,
            "workflow_startup_mode": "AgentDriven",
            "initial_agent_name": "PlannerAgent",
        },
    )
    monkeypatch.setattr(
        task_batches_module,
        "load_task_batches_config",
        lambda workflow_name: parse_task_batches_config(
            {
                "version": 1,
                "conveyors": [
                    {
                        "id": "runtime_tasks",
                        "decomposition_agent": "PlannerAgent",
                        "execution_agents": ["WorkerAgent"],
                    }
                ],
            }
        ),
    )
    monkeypatch.setattr(structured_outputs_module, "load_workflow_structured_outputs", lambda workflow_name: ({}, {}))

    result = await run_workflow_orchestration(
        workflow_name="TaskBatchAlignmentSmoke",
        app_id="app-1",
        chat_id="chat-1",
        user_id="user-1",
        initial_message="Build a campaign module.",
        agents_factory=_agents_factory,
        context_factory=lambda: {},
    )

    assert result is not None
    assert result["run_completed"] is True
    assert worker_agent.context_seen[0]["current_task_id"] == "module_contract"
    assert synthesis_agent.context_seen[0]["runtime_tasks_status"] == "completed"
    assert synthesis_agent.context_seen[0]["runtime_tasks_results"]["module_contract"]["summary"] == (
        "Worker used AG2 task channel context."
    )
    assert [message["agent_name"] for message in persistence.assistant_messages] == [
        "PlannerAgent",
        "SynthesisAgent",
    ]
    assert persistence.completed == [("chat-1", "app-1")]
