"""Opt-in statusbar projection through real AG2 model requests and tool calls."""

from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from ag2 import Agent
from ag2.config.openai.mappers import convert_messages
from ag2.events import ModelMessage, ModelResponse, StatusObservation, ToolCallEvent, ToolCallsEvent
from ag2.middleware import RetryMiddleware

from mozaiksai.core.workflow.agents import factory
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.contract_validation import validate_workflow_context_contract
from mozaiksai.core.workflow.declarative.contracts import parse_middleware_config
from mozaiksai.core.workflow.execution.middleware import build_statusbar_middleware


def _config():
    return {
        "agents": {"agents": {
            name: {"name": name, "system_message": "Keep the approved instructions.", "structured_outputs_required": False}
            for name in ("Planner", "Reviewer")
        }},
        "context_variables": {
            "definitions": {
                key: {"type": "string", "source": {"type": "state"}, **metadata}
                for key, metadata in {
                    "phase": {}, "review_phase": {}, "stable": {}, "unexposed": {},
                    "private_token": {}, "tool_data": {"authority_class": "tool_only_information"},
                    "hidden": {"model_visible": False},
                }.items()
            },
            "agents": {"Planner": {"variables": ["phase", "stable"]}, "Reviewer": {"variables": ["review_phase"]}},
        },
        "middleware": {"statusbar_middleware": [
            {"agent": "Planner", "variables": ["phase"]},
            {"agent": "Reviewer", "variables": ["review_phase"]},
        ]},
    }


@pytest.mark.parametrize("entries", [
    [{"agent": "Planner", "variables": []}],
    [{"agent": "Planner", "variables": ["phase", "phase"]}],
    [{"agent": "Planner", "variables": [" "]}],
    [{"agent": "Planner", "variables": ["phase"], "persist": True}],
    [{"agent": "Planner", "variables": ["phase"], "function": "custom_callback"}],
    [{"agent": "Planner", "variables": ["phase"]}] * 2,
])
def test_statusbar_schema_rejects_ambiguous_or_executable_options(entries):
    with pytest.raises(ValueError):
        parse_middleware_config({"statusbar_middleware": entries})


@pytest.mark.parametrize("variable,expose", [
    ("missing", True), ("unexposed", False), ("private_token", True),
    ("tool_data", True), ("hidden", True), ("review_phase", False),
])
def test_statusbar_cannot_expand_declared_model_visibility(variable, expose):
    config = _config()
    config["middleware"]["statusbar_middleware"][0]["variables"] = [variable]
    if expose:
        config["context_variables"]["agents"]["Planner"]["variables"].append(variable)
    with pytest.raises(ValueError):
        validate_workflow_context_contract(workflow_name="StatusFlow", workflow_config=config)


@pytest.mark.parametrize("agent", ["Missing", "all", "Planner"])
def test_statusbar_requires_a_specific_local_agent(agent):
    config = _config()
    config["middleware"]["statusbar_middleware"][0]["agent"] = agent
    if agent == "Planner":
        config["a2a"] = {"agents": [{"name": "Planner", "enabled": True}]}
    with pytest.raises(ValueError, match="local agent"):
        validate_workflow_context_contract(workflow_name="StatusFlow", workflow_config=config)


class _Provider:
    model = "offline-statusbar"
    provider = "openai"

    def __init__(self):
        self.calls = []
        self.retry_bridge = None

    def copy(self):
        return self

    def create(self):
        async def client(events, context, *, tools, response_schema, serializer):
            self.calls.append({
                "prompt": "\n".join(context.prompt),
                "messages": convert_messages(context.prompt, events, serializer),
            })
            if len(self.calls) == 1 and self.retry_bridge is not None:
                self.retry_bridge.set("phase", "retry-current")
                raise RuntimeError("synthetic provider failure before publishing")
            if len(self.calls) == 1 and tools:
                return ModelResponse(tool_calls=ToolCallsEvent([ToolCallEvent("advance", arguments="{}")]))
            return ModelResponse(ModelMessage("Completed."))

        return client


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("source_type", ["state", "config", "file", "data_reference"])
async def test_factory_status_refreshes_after_real_tool_execution_without_prompt_or_history_drift(monkeypatch, tmp_path, enabled, source_type):
    from mozaiksai.core.tokens.guard import TokenUsageGuard
    from mozaiksai.core.workflow import llm_config
    from mozaiksai.core.workflow.agents import tools
    from mozaiksai.core.workflow.outputs import structured

    config = _config()
    config["context_variables"]["definitions"]["phase"]["source"]["type"] = source_type
    if not enabled:
        config["middleware"] = {}
    provider = _Provider()
    workflow_path = tmp_path / "StatusFlow"
    workflow_path.mkdir()
    if source_type != "state":
        _write_task_batches(workflow_path)
    # Keep the actual file-backed prompt middleware loading path. The hook can
    # still read selected status keys from its bridge without re-projecting them.
    (workflow_path / "middleware.yaml").write_text(
        "prompt_middleware:\n- agent: Planner\n  filename: hook.py\n  function: inject\n", encoding="utf-8",
    )
    (workflow_path / "hook.py").write_text(
        "def inject(agent, messages):\n"
        "    assert agent.context_variables.get('phase') in ('initial-phase', 'tool-current')\n"
        "    agent.update_system_message(agent.system_message + '\\nExisting prompt hook.')\n", encoding="utf-8",
    )
    manager = SimpleNamespace(
        get_config=lambda _: config, get_auto_tool_agents=lambda _: set(),
        resolve_workflow_path=lambda _: workflow_path,
    )
    monkeypatch.setattr(factory, "workflow_manager", manager)
    monkeypatch.setattr(factory, "get_structured_outputs_for_workflow", lambda _: {})
    monkeypatch.setattr(factory, "llm_config_to_ag2_config", lambda _: provider)
    stub_config = AsyncMock(return_value=(None, {"config_list": [{"model": provider.model, "api_key": "test"}]}))
    monkeypatch.setattr(llm_config, "get_llm_config", stub_config)
    monkeypatch.setattr(structured, "get_llm_for_workflow", stub_config)
    monkeypatch.setattr(TokenUsageGuard, "check_or_raise", AsyncMock())

    def advance(context_variables):
        """Advance workflow state without returning the state value to the model."""
        context_variables.set("phase", "tool-current")
        return "State updated."

    monkeypatch.setattr(tools, "load_agent_tool_functions", lambda _: {"Planner": [advance]})
    agents = await factory.create_agents("StatusFlow", context_variables={
        "phase": "initial-phase", "review_phase": "review-only", "stable": "stable-context",
        "unexposed": "UNEXPOSED_SENTINEL", "private_token": "SECRET_SENTINEL",
    })
    planner = agents["Planner"]
    reply = await planner.ask("Execute the work.")
    assert reply.body == "Completed."
    assert len(provider.calls) == 2
    first, second = [json.dumps(call["messages"]) for call in provider.calls]
    assert "initial-phase" in first
    assert "tool-current" in second and "initial-phase" not in second
    for call in provider.calls:
        assert call["prompt"].startswith("Keep the approved instructions.")
        assert call["prompt"].count("Existing prompt hook.") == 1
        assert "stable-context" in call["prompt"]
        serialized = json.dumps(call["messages"])
        assert serialized.count("PHASE:") == 1
        assert "UNEXPOSED_SENTINEL" not in serialized and "SECRET_SENTINEL" not in serialized
        assert "review-only" not in serialized
        assert ("PHASE:" not in call["prompt"]) is enabled
    history = list(await reply.context.stream.history.get_events())
    assert not any(isinstance(event, StatusObservation) for event in history)
    assert "Current workflow context" not in repr(history)
    assert planner._mozaiks_context_bridge.snapshot()["phase"] == "tool-current"
    await agents["Reviewer"].ask("Review the current state.")
    reviewer_request = json.dumps(provider.calls[-1]["messages"])
    assert "review-only" in reviewer_request
    assert "tool-current" not in reviewer_request


@pytest.mark.asyncio
async def test_statusbar_refreshes_inside_provider_retries_without_consuming_context_updates():
    bridge = ContextVariablesBridge({"phase": "first-attempt"})
    provider = _Provider()
    provider.retry_bridge = bridge
    agent = Agent("Planner", prompt="Static instructions.", config=provider, middleware=[
        RetryMiddleware(max_retries=1), build_statusbar_middleware(context_bridge=bridge, variables=["phase"]),
    ])
    reply = await agent.ask("Continue.")
    assert reply.body == "Completed."
    assert len(provider.calls) == 2
    assert "first-attempt" in json.dumps(provider.calls[0]["messages"])
    assert "retry-current" in json.dumps(provider.calls[1]["messages"])
    assert "first-attempt" not in json.dumps(provider.calls[1]["messages"])
    assert bridge.consume_context_updates() == {"set": {"phase": "retry-current"}, "delete": []}


def test_statusbar_reference_validation_does_not_mutate_workflow_state():
    config = _config()
    before = deepcopy(config)
    validate_workflow_context_contract(workflow_name="StatusFlow", workflow_config=config)
    assert config == before


@pytest.mark.asyncio
async def test_no_statusbar_keeps_workflow_path_lookup_optional(monkeypatch):
    from mozaiksai.core.workflow import llm_config
    from mozaiksai.core.workflow.agents import tools
    from mozaiksai.core.workflow.outputs import structured

    config = _config()
    config.pop("middleware")
    # Optional prompt loading has always tolerated a manager without path lookup.
    monkeypatch.setattr(factory, "workflow_manager", SimpleNamespace(
        get_config=lambda _: config, get_auto_tool_agents=lambda _: set(),
    ))
    monkeypatch.setattr(factory, "get_structured_outputs_for_workflow", lambda _: {})
    monkeypatch.setattr(factory, "llm_config_to_ag2_config", lambda _: _Provider())
    stub_config = AsyncMock(return_value=(None, {"config_list": [{"model": "offline-statusbar", "api_key": "test"}]}))
    monkeypatch.setattr(llm_config, "get_llm_config", stub_config)
    monkeypatch.setattr(structured, "get_llm_for_workflow", stub_config)
    monkeypatch.setattr(tools, "load_agent_tool_functions", lambda _: {})

    agents = await factory.create_agents("StatusFlow", context_variables={"phase": "initial-phase"})

    assert set(agents) == {"Planner", "Reviewer"}
    assert "PHASE: initial-phase" in agents["Planner"]._mozaiks_base_system_message


def _write_task_batches(workflow_path):
    path = workflow_path / "extended_orchestration" / "task_batches.yaml"
    path.parent.mkdir()
    path.write_text(yaml.safe_dump({"version": 1, "batches": [{
        "id": "review", "trigger_agent": "Planner",
        "source": {"kind": "context_variable", "path": "items", "task_model": "Item"},
        "result": {"context_key": "phase", "status_key": "review_phase"},
    }]}), encoding="utf-8")


@pytest.mark.parametrize("source_type", ["config", "file", "data_reference"])
@pytest.mark.parametrize("metadata", [{}, {"model_visible": False}, {"authority_class": "tool_only_information"}])
def test_statusbar_uses_task_output_authority_without_overriding_explicit_visibility(monkeypatch, tmp_path, source_type, metadata):
    from mozaiksai.core.workflow import workflow_manager
    from mozaiksai.core.workflow.context.authority import (
        build_context_authority_policy,
        task_batch_written_context_keys,
    )
    from mozaiksai.core.workflow.context.variables import _task_batch_context_keys
    from mozaiksai.core.workflow.task_batches import parse_task_batches_config

    config = _config()
    definition = config["context_variables"]["definitions"]["phase"]
    definition.update(metadata)
    definition["source"]["type"] = source_type
    workflow_path = tmp_path / "ResolvedFolder"
    workflow_path.mkdir()
    _write_task_batches(workflow_path)
    monkeypatch.setattr(workflow_manager, "get_workflow_manager", lambda: SimpleNamespace(
        resolve_workflow_path=lambda _: workflow_path,
    ))
    batch_keys = task_batch_written_context_keys(parse_task_batches_config(yaml.safe_load(
        (workflow_path / "extended_orchestration" / "task_batches.yaml").read_text(encoding="utf-8"),
    )))
    assert batch_keys == _task_batch_context_keys("StatusFlow") == {"phase", "review_phase"}
    policy = build_context_authority_policy(
        workflow_name="StatusFlow", definitions=config["context_variables"]["definitions"],
        task_batch_context_keys=batch_keys,
    )
    assert policy.variables["phase"].model_visible is (not metadata)
    if metadata:
        with pytest.raises(ValueError, match="not model-visible"):
            validate_workflow_context_contract(
                workflow_name="StatusFlow", workflow_config=config, workflow_path=workflow_path,
            )
    else:
        validate_workflow_context_contract(
            workflow_name="StatusFlow", workflow_config=config, workflow_path=workflow_path,
        )
