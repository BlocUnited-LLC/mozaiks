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
from ag2.knowledge import MemoryKnowledgeStore

from mozaiksai.core.adapters.ag2_network_runner import AG2NetworkRunner, AG2NetworkRunnerRequest
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge, _workflow_tool_invocation
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


def _request(
    agents: dict[str, _ScriptedAgent],
    adoption_level: str,
    *,
    store: MemoryKnowledgeStore | None = None,
    initial_message: str = "Inspect the synthetic community app.",
) -> AG2NetworkRunnerRequest:
    orchestrator = _config("orchestrator.yaml")
    initial = _context()
    initial.pop("structured_output")
    initial["adoption_level"] = adoption_level
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


def _agents() -> dict[str, _ScriptedAgent]:
    replies = {
        "DiscoveryHostAgent": ["Does this match the app?", "Any other corrections?", "NEXT"],
        "CapabilityMapperAgent": ["Confirm the capability map?", "NEXT"],
        "IntegrationPlannerAgent": ["Confirm the adoption plan?", "NEXT"],
        "ModuleDecomposerAgent": ["Confirm the decomposition?", "NEXT"],
        "DiscoveryArtifactAssemblerAgent": [json.dumps(_discovery_output())],
    }
    assert set(replies) == {agent["name"] for agent in _config("agents.yaml")["agents"]}
    return {name: _ScriptedAgent(name, script) for name, script in replies.items()}


@pytest.mark.asyncio
@pytest.mark.parametrize("adoption_level", ["embed", "bridge", "ecosystem", "gradual_modernization"])
async def test_discovery_human_replies_follow_confirmed_stages(adoption_level: str, monkeypatch) -> None:
    monkeypatch.setattr(save_module, "get_artifact_store", _FakeArtifactStore)
    monkeypatch.setattr(save_module, "emit_app_intelligence_enriched_overview_card", AsyncMock())
    agents = _agents()
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
        # A selected adoption level is not confirmation of the plan.
        assert not agents["ModuleDecomposerAgent"].history
        assert not agents["DiscoveryArtifactAssemblerAgent"].history

        result = await live.continue_with_user_message("Confirm the adoption plan.")
        assert result.context_variables["plan_complete"] is True
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
