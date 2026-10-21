"""Exercise the shipped discovery graph with AG2, without model or provider calls."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import yaml
from ag2 import Agent
from ag2.events import ModelMessage, ModelResponse, ToolCallEvent, ToolCallsEvent
from ag2.knowledge import MemoryKnowledgeStore

from factory_app.workflows.ExistingAppDiscovery.tools.record_discovery_plans import (
    record_adoption_plan,
    record_module_decomposition,
)
from mozaiksai.core.adapters.ag2_network_runner import AG2NetworkRunner, AG2NetworkRunnerRequest
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow.agents.factory import (
    ContextVariablesBridge,
    _workflow_tool_invocation,
    _wrap_tool_with_context,
)
from mozaiksai.core.workflow.context.adapter import create_context_container
from mozaiksai.core.workflow.context.authority import (
    ContextAuthorityError,
    build_context_authority_policy,
)
from mozaiksai.core.workflow.context.derived import DerivedContextManager
from mozaiksai.core.workflow.context.schema import load_context_variables_config
from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay
from mozaiksai.core.workflow.contract_validation import validate_workflow_tool_outcomes
from mozaiksai.core.workflow.declarative.contracts import ToolOutcomeSpec
from mozaiksai.core.workflow.validation.tool_outcomes import wrap_tool_outcome
from tests.test_existing_app_discovery_app_context_persistence import (
    _context,
    _decomposition_evidence,
    _discovery_output,
    _FakeArtifactStore,
    save_module,
)

WORKFLOW = Path(__file__).resolve().parents[1] / "factory_app/workflows/ExistingAppDiscovery"


def _config(name: str) -> dict[str, Any]:
    return yaml.safe_load((WORKFLOW / name).read_text(encoding="utf-8"))


class _ScriptedAgent(Agent):
    def __init__(self, name: str, replies: list[str]) -> None:
        super().__init__(name, prompt="Synthetic discovery routing fixture.")
        self.replies = replies
        self.history: list[Any] = []

    async def ask(self, *msg: Any, **kwargs: Any) -> Any:
        self.history.append([await kwargs["stream"].history.get_events(), msg])
        return SimpleNamespace(body=self.replies[len(self.history) - 1])


class _PlanConfig:
    model = "offline-discovery-plan"
    provider = "openai"

    def __init__(self, function, plan, *, record=True):
        self.function = function
        self.plan = plan
        self.record = record
        self.calls = 0

    def copy(self):
        return self

    def create(self):
        async def client(messages, context, *, tools, response_schema, serializer):
            self.calls += 1
            if self.calls == 1:
                return ModelResponse(ModelMessage("Confirm the proposed plan?"))
            if self.calls == 2 and self.record:
                return ModelResponse(tool_calls=ToolCallsEvent([
                    ToolCallEvent(self.function.__name__, arguments=json.dumps({"plan": self.plan})),
                ]))
            return ModelResponse(ModelMessage("Your plan is recorded." if self.record else "NEXT"))
        return client


class _PlanAgent(Agent):
    def __init__(self, name, function, plan, *, record=True):
        super().__init__(name, prompt="Record only after confirmation.", config=_PlanConfig(function, plan, record=record))
        self.history = []

    async def ask(self, *msg, **kwargs):
        self.history.append([await kwargs["stream"].history.get_events(), msg])
        return await super().ask(*msg, **kwargs)


def _request(
    agents: dict[str, Agent],
    adoption_level: str,
    *,
    store: MemoryKnowledgeStore | None = None,
    initial_message: str = "Inspect the synthetic community app.",
) -> AG2NetworkRunnerRequest:
    orchestrator = _config("orchestrator.yaml")
    initial = _context()
    initial.pop("structured_output")
    for key in ("identity_complete", "capabilities_complete", "adoption_level", "agent_augmentation_plan",
                "plan_complete", "decomposition_complete", "module_decomposition_plan"):
        initial.pop(key, None)
    context = create_context_container(initial=initial)
    context._mozaiks_context_definitions = load_context_variables_config(
        _config("context_variables.yaml")
    ).definitions
    derived = DerivedContextManager("ExistingAppDiscovery", {}, context)
    rules = _config("transition_graph.yaml")["transition_rules"]
    policy = build_context_authority_policy(
        workflow_name="ExistingAppDiscovery",
        definitions=_config("context_variables.yaml")["definitions"],
        transition_rules=rules,
    )
    bridge = ContextVariablesBridge(context.snapshot(), authority_policy=policy)
    tool = next(t for t in _config("tools.yaml")["tools"] if t["function"] == "save_existing_app_artifacts")
    outcome = ToolOutcomeSpec.model_validate(tool["outcome"])
    save = wrap_tool_outcome(save_module.save_existing_app_artifacts, outcome)
    for name, agent in agents.items():
        agent._mozaiks_context_bridge = bridge
        if isinstance(agent, _PlanAgent):
            agent.add_tool(_wrap_tool_with_context(agent.config.function, bridge))
        agent._mozaiks_tool_outcome = outcome if name == "DiscoveryArtifactAssemblerAgent" else None

    async def save_output(agent_name: str, packet: Any) -> None:
        if agent_name != "DiscoveryArtifactAssemblerAgent":
            return
        # Match the runtime's transient projection and trusted tool invocation.
        data = json.loads(packet.event_data["body"])
        with _workflow_tool_invocation(bridge):
            await save(context_variables=StructuredOutputOverlay(bridge, data))

    return AG2NetworkRunnerRequest(
        workflow_name="ExistingAppDiscovery",
        chat_id=f"discovery-continuation-{adoption_level}",
        app_id="synthetic-discovery-app",
        agents=agents,
        transition_rules=rules,
        initial_agent_name=orchestrator["initial_agent"],
        initial_message=initial_message,
        max_turns=orchestrator["max_turns"],
        context_variables=context.snapshot(),
        agent_text_context_deriver=derived.preview_agent_text_updates,
        context_authority_policy=policy,
        agent_output_handler=save_output,
        knowledge_store=store,
        idle_timeout_seconds=3.0,
    )


def _agents(adoption_level="bridge", *, record=True) -> dict[str, Agent]:
    output = _discovery_output()
    output["agent_augmentation_plan"]["adoption_level"] = adoption_level
    replies = {
        "DiscoveryHostAgent": ["Does this match the app?", "Any other corrections?", "NEXT"],
        "CapabilityMapperAgent": ["Confirm the capability map?", "NEXT"],
        "IntegrationPlannerAgent": ["Confirm the adoption plan?", "NEXT"],
        "ModuleDecomposerAgent": ["Confirm the decomposition?", "NEXT"],
        "DiscoveryArtifactAssemblerAgent": [json.dumps(output)],
    }
    assert set(replies) == {agent["name"] for agent in _config("agents.yaml")["agents"]}
    agents = {name: _ScriptedAgent(name, script) for name, script in replies.items()}
    agents["IntegrationPlannerAgent"] = _PlanAgent(
        "IntegrationPlannerAgent", record_adoption_plan, output["agent_augmentation_plan"], record=record,
    )
    agents["ModuleDecomposerAgent"] = _PlanAgent(
        "ModuleDecomposerAgent", record_module_decomposition, _decomposition_evidence(adoption_level), record=record,
    )
    return agents


@pytest.mark.asyncio
@pytest.mark.parametrize("adoption_level", ["embed", "bridge", "ecosystem", "gradual_modernization"])
async def test_discovery_human_replies_follow_confirmed_stages(adoption_level: str, monkeypatch) -> None:
    monkeypatch.setattr(save_module, "get_artifact_store", _FakeArtifactStore)
    monkeypatch.setattr(save_module, "emit_app_intelligence_enriched_overview_card", AsyncMock())
    agents = _agents(adoption_level)
    result = await AG2NetworkRunner().run(_request(agents, adoption_level))
    assert result.status is RunStatus.PAUSED, result.error
    assert result.live_run is not None
    live = result.live_run
    try:
        result = await live.continue_with_user_message("Posts are private by default.")
        assert result.status is RunStatus.PAUSED, result.error
        assert len(agents["DiscoveryHostAgent"].history) == 2
        assert not agents["CapabilityMapperAgent"].history

        result = await live.continue_with_user_message("The corrected identity is confirmed.")
        assert result.status is RunStatus.PAUSED, result.error
        assert result.context_variables["identity_complete"] is True
        assert len(agents["CapabilityMapperAgent"].history) == 1
        assert "Posts are private by default." in repr(agents["CapabilityMapperAgent"].history)

        result = await live.continue_with_user_message("Confirm the capability map.")
        assert result.status is RunStatus.PAUSED, result.error
        assert result.context_variables["capabilities_complete"] is True
        assert result.context_variables["plan_complete"] is False
        assert len(agents["IntegrationPlannerAgent"].history) == 1
        assert result.context_variables["adoption_level"] is None
        assert result.context_variables["agent_augmentation_plan"] is None
        # There is no preseeded selection or confirmation.
        assert not agents["ModuleDecomposerAgent"].history
        assert not agents["DiscoveryArtifactAssemblerAgent"].history

        result = await live.continue_with_user_message("Confirm the adoption plan.")
        assert result.context_variables["plan_complete"] is True
        assert result.context_variables["adoption_level"] == adoption_level
        assert result.context_variables["agent_augmentation_plan"]["adoption_level"] == adoption_level
        if adoption_level in {"ecosystem", "gradual_modernization"}:
            assert result.status is RunStatus.PAUSED, result.error
            assert len(agents["ModuleDecomposerAgent"].history) == 1
            assert not agents["DiscoveryArtifactAssemblerAgent"].history
            result = await live.continue_with_user_message("Confirm the decomposition.")
            assert result.context_variables["decomposition_complete"] is True
        else:
            assert not agents["ModuleDecomposerAgent"].history

        assert len(agents["DiscoveryArtifactAssemblerAgent"].history) == 1
        assert result.status is RunStatus.COMPLETED, result.error
        assert result.context_variables["discovery_save_outcome"] == "saved"
        assert result.context_variables["discovery_save_attempts"] == 1
        assert result.context_variables["app_context_version_artifact_version_id"]
    finally:
        await live.close()


@pytest.mark.asyncio
async def test_discovery_reconnect_resumes_saved_capability_stage() -> None:
    store = MemoryKnowledgeStore()
    agents = _agents()
    agents["DiscoveryHostAgent"] = _ScriptedAgent("DiscoveryHostAgent", ["NEXT"])
    paused = await AG2NetworkRunner().run(_request(agents, "bridge", store=store))
    assert paused.status is RunStatus.PAUSED, paused.error
    assert paused.live_run is not None
    assert paused.context_variables["identity_complete"] is True
    await paused.live_run.close()

    fresh_agents = _agents()
    fresh_agents["CapabilityMapperAgent"] = _ScriptedAgent("CapabilityMapperAgent", ["NEXT"])
    resumed = await AG2NetworkRunner().run(_request(
        fresh_agents, "bridge", store=store, initial_message="Confirm the saved capability map.",
    ))
    try:
        assert resumed.status is RunStatus.PAUSED, resumed.error
        assert resumed.channel_id == paused.channel_id
        assert not fresh_agents["DiscoveryHostAgent"].history
        assert len(fresh_agents["CapabilityMapperAgent"].history) == 1
        assert len(fresh_agents["IntegrationPlannerAgent"].history) == 1
        assert not fresh_agents["DiscoveryArtifactAssemblerAgent"].history
    finally:
        if resumed.live_run is not None:
            await resumed.live_run.close()


def test_discovery_declares_valid_protected_tool_outcome() -> None:
    config = {
        **_config("orchestrator.yaml"),
        **_config("tools.yaml"),
        "context_variables": _config("context_variables.yaml"),
        "transition_graph": _config("transition_graph.yaml"),
        "structured_outputs": _config("structured_outputs.yaml"),
    }
    validate_workflow_tool_outcomes(config)
    request = _request(_agents(), "bridge")
    bridge = request.agents["DiscoveryArtifactAssemblerAgent"]._mozaiks_context_bridge
    with pytest.raises(ContextAuthorityError):
        bridge.set("discovery_save_outcome", "saved")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["no_recording", "preselected_only", "unknown_choice"])
async def test_unrecorded_plan_cannot_advance_even_after_next(failure) -> None:
    agents = _agents(record=failure == "unknown_choice")
    if failure == "unknown_choice":
        agents["IntegrationPlannerAgent"].config.plan["adoption_level"] = "take_over_everything"
    request = _request(agents, "unseeded")
    request.initial_agent_name = "IntegrationPlannerAgent"
    request.context_variables.update(identity_complete=True, capabilities_complete=True)
    selected = "bridge" if failure == "preselected_only" else None
    request.context_variables["adoption_level"] = selected
    paused = await AG2NetworkRunner().run(request)
    assert paused.status is RunStatus.PAUSED, paused.error
    live = paused.live_run
    try:
        for _ in range(2):
            result = await live.continue_with_user_message("Confirm the proposed plan.")
            assert result.status is RunStatus.PAUSED, result.error
            assert result.context_variables["plan_complete"] is False
            assert result.context_variables["adoption_level"] == selected
            assert not agents["ModuleDecomposerAgent"].history
            assert not agents["DiscoveryArtifactAssemblerAgent"].history
    finally:
        await live.close()


@pytest.mark.asyncio
async def test_decomposition_text_cannot_replace_recording() -> None:
    agents = _agents("ecosystem", record=False)
    request = _request(agents, "ecosystem")
    request.initial_agent_name = "ModuleDecomposerAgent"
    request.context_variables.update(
        identity_complete=True, capabilities_complete=True, plan_complete=True,
        adoption_level="ecosystem",
        agent_augmentation_plan={**_discovery_output()["agent_augmentation_plan"], "adoption_level": "ecosystem"},
    )
    paused = await AG2NetworkRunner().run(request)
    assert paused.status is RunStatus.PAUSED, paused.error
    live = paused.live_run
    try:
        result = await live.continue_with_user_message("Confirm the decomposition.")
        assert result.status is RunStatus.PAUSED, result.error
        assert result.context_variables["decomposition_complete"] is False
        assert result.context_variables["module_decomposition_plan"] is None
        assert not agents["DiscoveryArtifactAssemblerAgent"].history
    finally:
        await live.close()


def test_plan_writers_preserve_canonical_schema_and_reject_stale_completion() -> None:
    from pydantic import ValidationError

    from mozaiksai.core.workflow.agents.tools import load_agent_tool_functions

    tools = load_agent_tool_functions("ExistingAppDiscovery")
    assert any(t.__name__ == "record_adoption_plan" for t in tools["IntegrationPlannerAgent"])
    assert any(t.__name__ == "record_module_decomposition" for t in tools["ModuleDecomposerAgent"])
    context = _context()
    with pytest.raises(ValidationError):
        record_adoption_plan({**context["agent_augmentation_plan"], "adoption_level": "unknown"}, context)
    assert context["plan_complete"] is False
    assert context["decomposition_complete"] is False
    assert context["module_decomposition_plan"] is None
    record_adoption_plan(_discovery_output()["agent_augmentation_plan"], context)
    with pytest.raises(ValueError, match="must match"):
        record_module_decomposition(_decomposition_evidence("ecosystem"), context)
    assert context["decomposition_complete"] is False
    assert context["module_decomposition_plan"] is None
    record_module_decomposition(_decomposition_evidence(), context)
    assert context["decomposition_complete"] is True
    assert json.loads(context["module_decomposition_plan"])["proposed_modules"][0]["module_id"] == "work_order_manager"


@pytest.mark.asyncio
async def test_assembler_cannot_replace_confirmed_plan_or_save_conflicting_choice(monkeypatch) -> None:
    store = _FakeArtifactStore()
    monkeypatch.setattr(save_module, "get_artifact_store", lambda: store)
    emit = AsyncMock()
    monkeypatch.setattr(save_module, "emit_app_intelligence_enriched_overview_card", emit)
    request = _request(_agents("ecosystem"), "bridge")
    approved = {**_discovery_output()["agent_augmentation_plan"], "adoption_level": "bridge"}
    bridge = ContextVariablesBridge(
        {**_context(), "adoption_level": "bridge", "agent_augmentation_plan": approved},
        authority_policy=request.context_authority_policy,
    )
    for key, value in {
        "agent_augmentation_plan": {**approved, "adoption_level": "ecosystem"},
        "adoption_level": "ecosystem", "plan_complete": True,
        "module_decomposition_plan": "{}", "decomposition_complete": True,
    }.items():
        with pytest.raises(ContextAuthorityError), _workflow_tool_invocation(bridge, writer_id="structured_output"):
            bridge[key] = value
    assert bridge.snapshot()["agent_augmentation_plan"] == approved
    request.initial_agent_name = "DiscoveryArtifactAssemblerAgent"
    request.context_variables.update(
        identity_complete=True, capabilities_complete=True, plan_complete=True,
        adoption_level="bridge", agent_augmentation_plan=approved,
    )
    result = await AG2NetworkRunner().run(request)
    try:
        assert result.status is RunStatus.FAILED, result.error
        assert result.context_variables["discovery_save_outcome"] == "failed"
        assert result.context_variables["agent_augmentation_plan"] == approved
        assert result.context_variables["adoption_level"] == "bridge"
        assert store.calls == []
        emit.assert_not_awaited()
    finally:
        if result.live_run is not None:
            await result.live_run.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["mapping", "persistence", "registration", "missing_tool"])
async def test_discovery_failed_save_cannot_complete_from_preloaded_refs(monkeypatch, failure) -> None:
    store = _FakeArtifactStore()
    monkeypatch.setattr(save_module, "get_artifact_store", lambda: store)
    emit = AsyncMock()
    monkeypatch.setattr(save_module, "emit_app_intelligence_enriched_overview_card", emit)
    if failure == "mapping":
        def broken_mapping(*args, **kwargs):
            raise ValueError("Synthetic invalid discovery mapping")
        monkeypatch.setattr(save_module, "build_existing_app_context_artifacts", broken_mapping)
    elif failure == "persistence":
        monkeypatch.setattr(store, "create_build_record", AsyncMock(side_effect=OSError("Synthetic store unavailable")))
    elif failure == "registration":
        monkeypatch.setattr(save_module, "register_app_context_version", AsyncMock(side_effect=OSError("Synthetic registration failed")))
    request = _request(_agents(), "bridge")
    request.initial_agent_name = "DiscoveryArtifactAssemblerAgent"
    request.context_variables.update({
        "identity_complete": True,
        "capabilities_complete": True,
        "plan_complete": True,
        "adoption_level": "bridge",
        "agent_augmentation_plan": {**_discovery_output()["agent_augmentation_plan"], "adoption_level": "bridge"},
        "discovery_save_outcome": "saved",
        "current_app_context_version_id": "preloaded-context",
        "app_context_version_artifact_version_id": "preloaded-artifact",
    })
    if failure == "missing_tool":
        request.agent_output_handler = None
    result = await AG2NetworkRunner().run(request)
    try:
        assert result.status is RunStatus.FAILED, result.error
        assert result.context_variables["current_app_context_version_id"] == "preloaded-context"
        assert result.context_variables["app_context_version_artifact_version_id"] == "preloaded-artifact"
        if failure != "missing_tool":
            assert result.context_variables["discovery_save_outcome"] == "failed"
            assert result.context_variables["brownfield_app_context_artifact_version_refs"] == {}
        emit.assert_not_awaited()
    finally:
        if result.live_run is not None:
            await result.live_run.close()
