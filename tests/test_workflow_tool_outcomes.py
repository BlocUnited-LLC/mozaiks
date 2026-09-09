from __future__ import annotations

import asyncio
import json
import logging
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from ag2 import Agent
from pydantic import BaseModel, ConfigDict

from factory_app.workflows.AgentGenerator.tools.outcome_materialization import (
    materialize_workflow_outcomes,
)
from factory_app.workflows.AppGenerator.tools.generate_and_download import _export_repair_outcome
from mozaiksai.core.adapters import ag2_network_runner as runner
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.authority import (
    ContextAuthorityError,
    build_context_authority_policy,
)
from mozaiksai.core.workflow.contract_validation import validate_workflow_tool_outcomes
from mozaiksai.core.workflow.declarative.contracts import ToolOutcomeSpec
from mozaiksai.core.workflow.execution.network_graph import (
    compile_transition_rules_to_graph,
    resolve_next_agent,
)
from mozaiksai.core.workflow.validation.tool_outcomes import wrap_tool_outcome


def _entry():
    plan = {
        "agent": "CheckAgent", "function": "check_document", "result_field": "status",
        "context_key": "document_outcome", "attempts_key": "document_attempts",
        "error_value": "blocked", "max_attempts": 2, "retry_on": ["needs_revision"],
        "routes": [
            {"value": "ready", "target_agent": "DoneAgent"},
            {"value": "needs_revision", "target_agent": "RepairAgent"},
            {"value": "blocked", "target_agent": "user"},
        ],
    }
    documents = {
        "orchestrator": {
            "schema_version": "mozaiks.orchestrator.v1", "workflow_name": "DocumentCheck",
            "workflow_startup_mode": "AgentDriven", "human_in_the_loop": True,
            "initial_agent": "CheckAgent", "max_turns": 12,
            "orchestration_pattern": "ag2_network",
        },
        "agents": {"agents": [
            {"name": name, "system_message": "Process the document.", "structured_outputs_required": name == "CheckAgent"}
            for name in ("CheckAgent", "RepairAgent", "DoneAgent")
        ]},
        "tools": {"tools": [{"agent": "CheckAgent", "file": "tools/check_document.py", "function": "check_document", "tool_type": "Agent_Tool"}]},
        "context_variables": {"definitions": {}},
        "transition_graph": {"transition_rules": [
            {"source_agent": "RepairAgent", "target_agent": "CheckAgent", "transition_type": "after_turn"},
            {"source_agent": "DoneAgent", "target_agent": "terminate", "transition_type": "after_turn"},
        ]},
        "structured_outputs": {
            "schema_version": "mozaiks.structured_outputs.v1",
            "models": {"CheckInput": {"type": "model", "fields": {"result": {"type": "str"}}}},
            "registry": {"CheckAgent": "CheckInput"},
        },
        "middleware": {"prompt_middleware": []},
        "ui_config": {"visual_agents": []},
    }
    return {
        "workflow_name": "DocumentCheck", "outcome_plans": [plan],
        "files": [
            {"filename": f"{name}.yaml", "content": yaml.safe_dump(document)}
            for name, document in documents.items()
        ] + [{"filename": "tools/check_document.py", "content": (
            "async def check_document(result: str, context_variables=None):\n"
            "    if result == 'crash':\n"
            "        raise TimeoutError('document service unavailable')\n"
            "    return {'status': result}\n"
        )}],
    }


def _config(entry=None):
    entry = materialize_workflow_outcomes(entry or _entry())
    config = {
        file["filename"].removesuffix(".yaml"): yaml.safe_load(file["content"])
        for file in entry["files"] if file["filename"].endswith(".yaml")
    }
    config["tools"] = config["tools"]["tools"]
    return config


def _contract():
    return ToolOutcomeSpec.model_validate(_config()["tools"][0]["outcome"])


def _context(**updates):
    return ContextVariablesBridge({"document_outcome": "blocked", "document_attempts": 0, **updates})


@pytest.mark.parametrize("change", [
    {"max_attempts": True}, {"max_attempts": 0}, {"max_attempts": 101},
    {"retry_on": ["blocked"]}, {"retry_on": ["unknown"]}, {"retry_on": []},
    {"error_value": "unknown"}, {"values": ["ready", "ready"]},
    {"attempts_key": "document_outcome"}, {"context_key": "structured_output"},
])
def test_invalid_outcome_contract_rejected(change):
    with pytest.raises(ValueError):
        ToolOutcomeSpec.model_validate({**_contract().model_dump(), **change})


@pytest.mark.parametrize("bad_result", [None, [], {}, {"status": "invented"}, {"status": True}])
def test_unknown_results_clear_stale_success(bad_result):
    context = _context(document_outcome="ready")
    result = wrap_tool_outcome(lambda context_variables: bad_result, _contract())(context)
    assert result == {"status": "blocked", "outcome_error": "invalid_tool_outcome"}
    assert context.get("document_outcome") == "blocked"
    assert context.get("document_attempts") == 1


@pytest.mark.parametrize("initial", [-1, True, "1", None])
def test_invalid_attempt_state_prevents_execution(initial):
    calls = []
    context = _context(document_attempts=initial)
    wrapped = wrap_tool_outcome(lambda context_variables: calls.append(1), _contract())
    assert wrapped(context)["outcome_error"] == "invalid_attempt_state"
    assert calls == []


def test_budget_survives_replay_and_stops_before_another_side_effect():
    calls = []

    def operation(context_variables):
        calls.append(1)
        context_variables.set("document_attempts", 0)
        return {"status": "needs_revision"}

    wrapped = wrap_tool_outcome(operation, _contract())
    first = _context()
    assert wrapped(first)["status"] == "needs_revision"
    replay = ContextVariablesBridge(first.snapshot())
    assert wrapped(replay)["status"] == "needs_revision"
    assert wrapped(replay)["outcome_error"] == "attempts_exhausted"
    assert replay.get("document_attempts") == 2
    assert calls == [1, 1]


def test_success_does_not_authorize_a_second_operation():
    context = _context()
    wrapped = wrap_tool_outcome(lambda context_variables: {"status": "ready"}, _contract())
    assert wrapped(context)["status"] == "ready"
    assert wrapped(context)["outcome_error"] == "retry_not_permitted"


@pytest.mark.asyncio
async def test_interruption_blocks_retry_and_preserves_attempt():
    entered = asyncio.Event()

    async def operation(context_variables):
        context_variables.set("document_outcome", "ready")
        entered.set()
        await asyncio.Event().wait()

    wrapped = wrap_tool_outcome(operation, _contract())
    context = _context()
    task = asyncio.create_task(wrapped(context))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert context.get("document_outcome") == "blocked"
    assert context.get("document_attempts") == 1
    assert (await wrapped(context))["outcome_error"] == "retry_not_permitted"


@pytest.mark.parametrize("mutation", ["missing_route", "ambiguous", "unsafe_default", "error_success", "unprotected", "multiple_auto", "no_model"])
def test_graph_and_state_contract_rejects_drift(mutation):
    config = _config()
    rules = config["transition_graph"]["transition_rules"]
    if mutation == "missing_route":
        rules[:] = [rule for rule in rules if rule.get("condition_value") != "ready"]
    elif mutation == "ambiguous":
        rules.append(deepcopy(next(rule for rule in rules if rule.get("condition_value") == "ready")))
    elif mutation == "unsafe_default":
        rules[-1]["target_agent"] = "DoneAgent"
    elif mutation == "error_success":
        next(rule for rule in rules if rule.get("condition_value") == "blocked")["target_agent"] = "DoneAgent"
    elif mutation == "unprotected":
        config["context_variables"]["definitions"]["document_attempts"]["writer_ids"] = ["structured_output"]
    elif mutation == "multiple_auto":
        config["tools"].append({**config["tools"][0], "function": "other"})
    else:
        config["structured_outputs"]["registry"] = {}
    with pytest.raises(ValueError):
        validate_workflow_tool_outcomes(config)


def test_materialization_is_repeatable_and_preserves_other_routes():
    original = _entry()
    emitted = materialize_workflow_outcomes(original)
    assert emitted == materialize_workflow_outcomes(emitted)
    assert original == _entry()
    config = _config(emitted)
    assert config["transition_graph"]["transition_rules"][0]["source_agent"] == "RepairAgent"
    policy = build_context_authority_policy(workflow_name="DocumentCheck", definitions=config["context_variables"]["definitions"])
    context = ContextVariablesBridge(_context().snapshot(), authority_policy=policy)
    with pytest.raises(ContextAuthorityError):
        context.set("document_outcome", "ready")
    with pytest.raises(ContextAuthorityError):
        context.set("document_attempts", 0)


@pytest.mark.parametrize("workflow", ["AgentGenerator", "AppGenerator"])
def test_factory_export_dogfoods_outcome_contract(workflow):
    root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows" / workflow
    config = {path.stem: yaml.safe_load(path.read_text(encoding="utf-8")) for path in root.glob("*.yaml")}
    config["tools"] = config["tools"]["tools"]
    validate_workflow_tool_outcomes(config)
    graph = compile_transition_rules_to_graph(
        config["transition_graph"]["transition_rules"], initial_agent_name="DownloadAgent",
        agent_id_by_name={agent["name"]: agent["name"] for agent in config["agents"]["agents"]},
    )
    tool = next(tool for tool in config["tools"] if tool.get("outcome"))
    key = tool["outcome"]["context_key"]
    assert resolve_next_agent(graph, current_agent_name="DownloadAgent", context_variables={key: "blocked"}) == "user"
    repair = "needs_revision" if workflow == "AgentGenerator" else "repair_service"
    target = "PackBuildCoordinator" if workflow == "AgentGenerator" else "ServiceAgent"
    assert resolve_next_agent(graph, current_agent_name="DownloadAgent", context_variables={key: repair}) == target


@pytest.mark.parametrize("target,expected", [("ServiceAgent", "repair_service"), ("AppSchemaAgent", "repair_schema"), ("InventedAgent", "blocked")])
def test_final_export_consumes_only_known_repair_destinations(target, expected):
    assert _export_repair_outcome({"bundle_repair": {"status": "needs_revision", "target_agent": target}}) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("invoke", [False, True])
async def test_packet_requires_current_tool_result(monkeypatch, invoke):
    bridge = _context(document_outcome="ready")
    sent, handlers = [], []

    async def send(envelope):
        sent.append(envelope)
        return "sent"

    client = SimpleNamespace(send_envelope=send, on_envelope=handlers.append)
    agent = SimpleNamespace(_mozaiks_context_bridge=bridge, _mozaiks_tool_outcome=_contract())

    async def handler(envelope, active_client):
        if invoke:
            wrap_tool_outcome(lambda context_variables: {"status": "ready"}, _contract())(bridge)
        await active_client.send_envelope(SimpleNamespace(event_type=runner.EV_PACKET, channel_id="c", event_data={"body": "done"}))

    monkeypatch.setattr(runner, "default_handler", handler)
    runner._install_context_update_handler(agent=agent, client=client, agent_name="CheckAgent", run_identity=("DocumentCheck", "app", "chat"))
    if invoke:
        await handlers[0](SimpleNamespace(channel_id="c"))
        assert sent[0].event_data["context_updates"]["set"]["document_outcome"] == "ready"
    else:
        with pytest.raises(ValueError, match="missing_current_result"):
            await handlers[0](SimpleNamespace(channel_id="c"))
        assert not sent


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["handoff", "finish"])
async def test_outcome_agents_cannot_bypass_graph_before_tool_execution(monkeypatch, kind):
    bridge = _context()
    handlers = []
    send, dispatch = AsyncMock(), AsyncMock()
    client = SimpleNamespace(send_envelope=send, on_envelope=handlers.append)
    agent = SimpleNamespace(_mozaiks_context_bridge=bridge, _mozaiks_tool_outcome=_contract())

    async def handler(envelope, active_client):
        await active_client.send_envelope(SimpleNamespace(
            event_type=runner.EV_PACKET, channel_id="c", event_data={"routing": {"kind": kind}},
        ))

    monkeypatch.setattr(runner, "default_handler", handler)
    runner._install_context_update_handler(
        agent=agent, client=client, agent_name="CheckAgent",
        run_identity=("DocumentCheck", "app", "chat"), agent_output_handler=dispatch,
    )
    with pytest.raises(ValueError, match="requires_declared_graph"):
        await handlers[0](SimpleNamespace(channel_id="c"))
    dispatch.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("results,expected,headless,resume", [
    (["needs_revision", "ready"], "ready", False, False),
    (["crash"], "blocked", False, False),
    (["invented"], "blocked", False, False),
    (["needs_revision"] * 3, "blocked", False, False),
    (["crash"], "blocked", True, False),
    (["needs_revision", "ready"], "ready", False, True),
])
async def test_generated_workflow_loads_and_executes_in_real_ag2(tmp_path, monkeypatch, results, expected, headless, resume):
    from mozaiksai.core.events import auto_tool_handler, unified_event_dispatcher
    from mozaiksai.core.workflow.agents import tools as tool_loader
    from mozaiksai.core.workflow.orchestration_patterns import _dispatch_agent_packet_output
    from mozaiksai.core.workflow.workflow_manager import UnifiedWorkflowManager

    entry = _entry()
    if headless:
        entry["outcome_plans"][0]["routes"][-1].update(target_agent="terminate", termination_reason="workflow_failed")
    if resume:
        entry["outcome_plans"][0]["routes"][1]["target_agent"] = "user"
        graph_file = next(file for file in entry["files"] if file["filename"] == "transition_graph.yaml")
        graph = yaml.safe_load(graph_file["content"])
        graph["transition_rules"].append({
            "source_agent": "user", "target_agent": "CheckAgent", "transition_type": "after_turn",
        })
        graph_file["content"] = yaml.safe_dump(graph)
    emitted = materialize_workflow_outcomes(entry)
    root = tmp_path / "DocumentCheck"
    root.mkdir()
    for file in emitted["files"]:
        path = root / file["filename"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(file["content"], encoding="utf-8")
    monkeypatch.setattr(UnifiedWorkflowManager, "_instance", None)
    manager = UnifiedWorkflowManager(workflows_base_path=str(tmp_path))
    info = manager.get_workflow_info("DocumentCheck")
    assert info is not None and info["status"] != "error", info
    monkeypatch.setattr(tool_loader, "workflow_manager", manager)
    monkeypatch.setattr(auto_tool_handler, "workflow_manager", manager)

    class CheckInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        result: str

    registry = {"CheckAgent": CheckInput}
    monkeypatch.setattr(auto_tool_handler, "get_structured_outputs_for_workflow", lambda name: registry)
    handler = auto_tool_handler.AutoToolEventHandler()
    for method in ("_emit_tool_call", "_emit_tool_result", "_persist_context_variables"):
        monkeypatch.setattr(handler, method, AsyncMock())

    async def emit(kind, payload):
        await handler.handle_tool_dispatch(payload)

    monkeypatch.setattr(unified_event_dispatcher, "get_event_dispatcher", lambda: SimpleNamespace(emit=emit))
    config = _config(emitted)
    policy = build_context_authority_policy(workflow_name="DocumentCheck", definitions=config["context_variables"]["definitions"])
    bridge = ContextVariablesBridge(_context().snapshot(), authority_policy=policy)
    observed = []
    pending = iter(results)

    class ScriptedAgent(Agent):
        async def ask(self, *args, **kwargs):
            observed.append(self.name)
            if self.name == "CheckAgent":
                return SimpleNamespace(body=json.dumps({"result": next(pending)}))
            return SimpleNamespace(body="Document processed.")

    agents = {name: ScriptedAgent(name, prompt="Process the document.") for name in ("CheckAgent", "RepairAgent", "DoneAgent")}
    for name, agent in agents.items():
        agent._mozaiks_context_bridge = bridge
        agent._mozaiks_tool_outcome = _contract() if name == "CheckAgent" else None

    async def before_packet(agent_name, packet):
        await _dispatch_agent_packet_output(
            agent_name=agent_name, packet=packet, workflow_name="DocumentCheck", chat_id="chat",
            app_id="app", user_id=None, context_bridge=bridge, structured_registry=registry,
            auto_tool_agents={"CheckAgent"}, wf_logger=logging.getLogger(__name__),
        )

    result = await runner.AG2NetworkRunner().run(runner.AG2NetworkRunnerRequest(
        workflow_name="DocumentCheck", app_id="app", chat_id="chat", agents=agents,
        transition_rules=config["transition_graph"]["transition_rules"], initial_agent_name="CheckAgent",
        initial_message="Check the document.", context_variables=bridge.snapshot(),
        context_authority_policy=policy, max_turns=12, close_timeout_seconds=float("inf") if resume else 5,
        agent_output_handler=before_packet,
    ))
    live_run = result.live_run
    try:
        if resume:
            assert result.status is RunStatus.PAUSED
            assert result.context_variables["document_attempts"] == 1
            result = await live_run.continue_with_user_message("The document has been corrected.")
        assert result.error == ("workflow_failed" if headless else None), result.error
        assert result.status is (RunStatus.FAILED if headless else RunStatus.COMPLETED if expected == "ready" else RunStatus.PAUSED)
        assert result.context_variables["document_outcome"] == expected
        assert ("DoneAgent" in observed) is (expected == "ready")
        assert result.context_variables["document_attempts"] <= 2
    finally:
        if live_run is not None:
            await live_run.close()


def test_materializer_rejects_conflicting_state_routes_and_missing_tool():
    for conflict in ("context", "routes", "tool"):
        entry = _entry()
        if conflict == "tool":
            entry["outcome_plans"][0]["function"] = "missing_function"
        elif conflict == "context":
            context_file = next(file for file in entry["files"] if file["filename"] == "context_variables.yaml")
            context_file["content"] = yaml.safe_dump({"definitions": {
                "document_outcome": {"source": {"type": "state", "default": "ready"}},
            }})
        else:
            graph_file = next(file for file in entry["files"] if file["filename"] == "transition_graph.yaml")
            graph_file["content"] = yaml.safe_dump({"transition_rules": [{
                "source_agent": "CheckAgent", "target_agent": "terminate", "transition_type": "after_turn",
            }]})
        with pytest.raises(ValueError):
            materialize_workflow_outcomes(entry)


@pytest.mark.parametrize("role", ["initial", "decomposition", "worker"])
def test_task_batch_agents_cannot_bypass_outcome_graph(role):
    from mozaiksai.core.workflow.task_batches import parse_task_batches_config

    config = _config()
    config["initial_agent"] = "CheckAgent" if role == "initial" else "PlannerAgent"
    batches = parse_task_batches_config({"conveyors": [{
        "id": "build",
        "decomposition_agent": "CheckAgent" if role == "decomposition" else "PlannerAgent",
        "execution_agents": ["CheckAgent" if role == "worker" else "WorkerAgent"],
    }]})
    with pytest.raises(ValueError, match="cannot be task-batch"):
        validate_workflow_tool_outcomes(config, task_batches=batches)


def test_factory_structured_output_materializes_a_runtime_valid_bundle():
    from factory_app.workflows.AgentGenerator.tools.workflow_quality_gate import (
        validate_workflow_bundle_structure,
    )
    from mozaiksai.core.workflow.outputs.structured import (
        build_models_from_config,
        supports_provider_response_format,
    )

    root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows" / "AgentGenerator"
    config = yaml.safe_load((root / "structured_outputs.yaml").read_text(encoding="utf-8"))["models"]
    models = build_models_from_config(config, exact_model_ids=frozenset(config))
    model = models["WorkflowBundleBuilderOutput"]
    assert supports_provider_response_format(model) == (True, None)
    entry = _entry()
    entry.update(agent_message="Document workflow generated.", pattern_id=1, pattern_name="Sequential")
    for file in entry["files"]:
        file["installRequirements"] = []
    for route in entry["outcome_plans"][0]["routes"]:
        route["termination_reason"] = None
    candidate = model.model_validate(entry).model_dump(mode="json")
    report = validate_workflow_bundle_structure(bundle_entries=[candidate])
    assert report["valid"], report["errors"]
    emitted = materialize_workflow_outcomes(candidate)
    assert emitted == materialize_workflow_outcomes(emitted)
