"""UI quality results must reach AG2 through the declared tool writer lane."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from ag2 import Agent
from ag2.network import EV_PACKET, Envelope, WorkflowAdapter, WorkflowState

from factory_app.workflows.AppGenerator.tools.ui_quality import review_ui_quality
from mozaiksai.core.adapters import ag2_network_runner as runner
from mozaiksai.core.events import auto_tool_handler, unified_event_dispatcher
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.authority import (
    ContextAuthorityError,
    build_context_authority_policy,
)
from mozaiksai.core.workflow.execution.network_graph import compile_transition_rules_to_graph
from mozaiksai.core.workflow.orchestration_patterns import _dispatch_agent_packet_output
from mozaiksai.core.workflow.outputs import structured
from mozaiksai.core.workflow.task_batches import parse_task_batches_config
from mozaiksai.core.workflow.workflow_manager import workflow_manager

WORKFLOW = "AppGenerator"
AGENT = "AppUIQualityAgent"
RUN = (WORKFLOW, "app-quality-test", "chat-quality-test")
ROOT = Path(__file__).resolve().parents[1] / "factory_app" / "workflows" / WORKFLOW


def _load(filename):
    return yaml.safe_load((ROOT / filename).read_text(encoding="utf-8"))


@pytest.fixture
def contract():
    rules = _load("transition_graph.yaml")["transition_rules"]
    definitions = _load("context_variables.yaml")["definitions"]
    batches = parse_task_batches_config(_load("extended_orchestration/task_batches.yaml")).batches
    policy = build_context_authority_policy(
        workflow_name=WORKFLOW, definitions=definitions, transition_rules=rules,
        task_batch_context_keys={key for batch in batches for key in (
            batch.result.context_key, batch.result.status_key,
        )},
    )
    names = [agent["name"] for agent in _load("agents.yaml")["agents"]]
    graph = compile_transition_rules_to_graph(
        rules, initial_agent_name=AGENT, agent_id_by_name={name: name for name in names},
        context_authority_policy=policy,
    )
    return SimpleNamespace(rules=rules, definitions=definitions, policy=policy, names=names, graph=graph)


def _bridge(contract, *, warnings=(), attempts=0):
    bridge = ContextVariablesBridge({
        "task_run_mode": False,
        "user_id": "quality-user",
        "app_ui_quality_status": None,
        "app_ui_quality_warnings": list(warnings),
        "app_ui_quality_revision_count": attempts,
        "app_schema_ready": True,
        "app_manifest": {"app_name": "Tickets"},
        "app_pages": [{
            "schema_version": "mozaiks.app_page.v1", "name": "Tickets", "route": "/tickets",
            "title": "Tickets", "page_type": "record_list", "layout": "full-width",
            "sections": [{"id": "header", "primitive": "PageHeader", "config": {"title": "Tickets"}}],
        }],
    }, authority_policy=contract.policy)
    bridge._bind_run(RUN, contract.policy)
    return bridge


def test_prompt_context_cannot_write_quality_state(contract):
    bridge = _bridge(contract)
    with pytest.raises(ContextAuthorityError, match="writer=context_bridge"):
        bridge.set("app_ui_quality_status", "passed")
    assert bridge.get("app_ui_quality_status") is None


def test_quality_gate_propagates_rejected_write(contract):
    bridge = _bridge(contract)
    with pytest.raises(ContextAuthorityError, match="writer=context_bridge"):
        review_ui_quality(context_variables=bridge)
    assert bridge.get("app_ui_quality_status") is None


@pytest.mark.asyncio
async def test_unset_status_fails_on_complete_compiled_appgenerator_graph(contract):
    bridge = _bridge(contract)

    class ReplyAgent(Agent):
        def __init__(self, name):
            super().__init__(name, prompt="Deterministic graph diagnosis")
            self._mozaiks_context_bridge = bridge

        async def ask(self, *messages, **kwargs):
            return SimpleNamespace(body='{"agent_message":"Review UI", "max_revision_attempts":2}')

    result = await runner.AG2NetworkRunner().run(runner.AG2NetworkRunnerRequest(
        workflow_name=WORKFLOW, app_id=RUN[1], chat_id=RUN[2],
        agents={name: ReplyAgent(name) for name in contract.names},
        transition_rules=contract.rules, initial_agent_name=AGENT, initial_message="Review UI",
        context_variables=bridge.snapshot(), context_authority_policy=contract.policy,
        close_timeout_seconds=5,
    ))
    assert result.status is RunStatus.FAILED
    assert result.close_reason == result.error == "no_transition_matched"
    assert result.context_variables["app_ui_quality_status"] is None


@pytest.fixture
def live_dispatch(monkeypatch):
    # Load the real workflow declarations and models without sharing global cache
    # state with unrelated tests. Only persistence and outbound UI are stubbed.
    namespaces = ("workflows", "factory_app.workflows")
    saved_modules = {
        name: module for name, module in sys.modules.items()
        if any(name == prefix or name.startswith(prefix + ".") for prefix in namespaces)
    }
    monkeypatch.setattr(sys, "path", list(sys.path))
    config = {
        "structured_outputs": _load("structured_outputs.yaml"),
        "tools": _load("tools.yaml")["tools"],
        "context_variables": _load("context_variables.yaml"),
        "transition_graph": _load("transition_graph.yaml"),
    }
    monkeypatch.setattr(workflow_manager, "resolve_workflow_path", lambda name: ROOT)
    monkeypatch.setattr(workflow_manager, "get_config", lambda name: config)
    for cache in ("_workflow_models", "_workflow_registries", "_workflow_structured_agents", "_provider_response_model_cache"):
        monkeypatch.setattr(structured, cache, {})

    class Persistence:
        async def persist_context_variables(self, **kwargs):
            pass

    async def no_transport():
        return None

    monkeypatch.setattr(auto_tool_handler, "AG2PersistenceManager", Persistence)
    monkeypatch.setattr(auto_tool_handler, "_get_simple_transport", no_transport)
    dispatcher = unified_event_dispatcher.UnifiedEventDispatcher()
    monkeypatch.setattr(unified_event_dispatcher, "get_event_dispatcher", lambda: dispatcher)
    registry = structured.get_structured_outputs_for_workflow(WORKFLOW)
    try:
        yield registry, workflow_manager.get_auto_tool_agents(WORKFLOW)
    finally:
        # Runtime loading refreshes workflow-owned imports. Restore module and
        # package identities held by tests collected before this dispatch.
        for name in list(sys.modules):
            if any(name == prefix or name.startswith(prefix + ".") for prefix in namespaces):
                sys.modules.pop(name, None)
        sys.modules.update(saved_modules)
        for name, module in saved_modules.items():
            parent, _, child = name.rpartition(".")
            if parent in sys.modules:
                setattr(sys.modules[parent], child, module)


@pytest.mark.asyncio
@pytest.mark.parametrize("warnings,attempts,budget,status,next_agent,count", [
    ([], 0, 2, "passed", "AdminRegistryAgent", 0),
    (["Too many sections"], 2, 3, "needs_revision", "AppSchemaAgent", 3),
    (["Too many sections"], 3, 3, "blocked", "user", 3),
    (["Too many sections"], 0, 0, "blocked", "user", 0),
])
async def test_registered_gate_commits_before_compiled_graph_routes(
    contract, live_dispatch, monkeypatch, warnings, attempts, budget, status, next_agent, count,
):
    bridge = _bridge(contract, warnings=warnings, attempts=attempts)
    registry, auto_agents = live_dispatch
    before = bridge.snapshot()
    sent, handlers = [], []
    packet = Envelope(
        channel_id="quality-channel", sender_id=AGENT, audience=None, event_type=EV_PACKET,
        causation_id="quality-input", event_data={"body": {
            "agent_message": "Review saved UI", "max_revision_attempts": budget,
        }},
    )

    async def send(envelope):
        sent.append(envelope)
        return "sent"

    async def reply(envelope, client):
        await client.send_envelope(packet)

    async def dispatch(agent_name, envelope):
        await _dispatch_agent_packet_output(
            agent_name=agent_name, packet=envelope, workflow_name=WORKFLOW,
            app_id=RUN[1], chat_id=RUN[2], user_id="quality-user", context_bridge=bridge,
            structured_registry=registry, auto_tool_agents=auto_agents,
            wf_logger=logging.getLogger(__name__),
        )

    client = SimpleNamespace(agent_id=AGENT, send_envelope=send, on_envelope=handlers.append)
    monkeypatch.setattr(runner, "default_handler", reply)
    runner._install_context_update_handler(
        agent=SimpleNamespace(_mozaiks_context_bridge=bridge), client=client, agent_name=AGENT,
        run_identity=RUN, context_authority_policy=contract.policy, agent_output_handler=dispatch,
    )
    await handlers[0](SimpleNamespace(channel_id="quality-channel"))

    assert bridge.get("app_ui_quality_status") == status
    assert bridge.get("app_ui_quality_revision_count") == count
    assert bridge.get("app_ui_quality_result")["max_revision_attempts"] == budget
    assert len(sent) == 1
    assert sent[0].event_data["context_updates"]["set"]["app_ui_quality_status"] == status
    state = WorkflowState(
        participant_order=contract.names + ["user"], expected_next_speaker=AGENT,
        last_speaker_id=AGENT, turn_count=1, creator_id="user",
        graph_data=contract.graph.to_dict(), context_vars=before,
    )
    routed = WorkflowAdapter().fold(sent[0], state)
    assert routed.context_vars["app_ui_quality_status"] == status
    assert routed.expected_next_speaker == next_agent
