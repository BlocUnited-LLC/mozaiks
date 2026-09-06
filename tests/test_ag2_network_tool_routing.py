"""Real factory/tool/Network acceptance with only provider HTTP replaced."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from ag2 import Agent
from ag2.events import BaseEvent, ToolCallEvent, ToolErrorEvent, ToolResultEvent
from ag2.network import EV_PACKET, TransitionGraph
from ag2.observers import observer

from mozaiksai.core.adapters.ag2_network_runner import (
    AG2NetworkRunner,
    AG2NetworkRunnerRequest,
    AG2NetworkRunnerResult,
)
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow.agents import factory
from mozaiksai.core.workflow.context.adapter import create_context_container
from mozaiksai.core.workflow.context.authority import (
    ContextAuthorityError,
    ContextAuthorityPolicy,
    build_context_authority_policy,
)
from mozaiksai.core.workflow.execution.network_graph import compile_transition_rules_to_graph

_WORKFLOW = "IndependentToolRouting"
_KEY = "review_scope_confirmed"


def _after(source: str, target: str = "terminate") -> dict[str, Any]:
    return {"source_agent": source, "target_agent": target, "transition_type": "after_turn"}


def _rules(condition: str = "tool_called", *, pause: bool = False) -> list[dict[str, Any]]:
    rule: dict[str, Any] = {
        "source_agent": "AgentA",
        "target_agent": "AgentB",
        "transition_type": "condition",
        "condition_type": condition,
    }
    if condition == "tool_called":
        rule["tool_name"] = "complete_intake"
    else:
        rule.update(condition_key=_KEY, condition_value=True)
    return [
        rule,
        _after("AgentA"),
        _after("AgentB", "user" if pause else "terminate"),
        _after("AgentC"),
        _after("user"),
    ]


def _policy(rules: list[dict[str, Any]], *, allowed: bool = True) -> ContextAuthorityPolicy:
    return build_context_authority_policy(
        workflow_name=_WORKFLOW,
        definitions={
            _KEY: {
                "type": "boolean",
                "source": {"type": "state", "default": False},
                "authority_class": "closed_writer_routing_state",
                "writer_ids": ["deterministic_tool" if allowed else "transition_router"],
                "routing": True,
            },
        },
        transition_rules=rules,
    )


@dataclass
class _SDKScenario:
    tool_names: tuple[str, ...] = ("complete_intake",)
    source: str = "AgentA"
    final_text: str = "Intake finished."
    mutate_context: bool = False
    requests: Counter[str] = field(default_factory=Counter)
    calls: list[str] = field(default_factory=list)
    events: dict[str, list[BaseEvent]] = field(default_factory=lambda: defaultdict(list))
    request_bodies: list[dict[str, Any]] = field(default_factory=list)

    def respond(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "provider.invalid"
        payload = json.loads(request.content)
        self.request_bodies.append(payload)
        agent = payload["model"]
        self.requests[agent] += 1
        call_tools = agent == self.source and self.requests[agent] == 1 and self.tool_names
        message: dict[str, Any] = {"role": "assistant", "content": self.final_text}
        if call_tools:
            available = {item["function"]["name"] for item in payload["tools"]}
            assert set(self.tool_names) <= available
            message.update(
                content=None,
                tool_calls=[
                    {
                        "id": f"call-{index}",
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": "{}",
                        },
                    }
                    for index, name in enumerate(self.tool_names)
                ],
            )
        elif agent != self.source:
            message["content"] = f"{agent} executed."
        return httpx.Response(
            200,
            json={
                "id": f"completion-{agent}-{self.requests[agent]}",
                "object": "chat.completion",
                "created": 1,
                "model": agent,
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": "tool_calls" if call_tools else "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )


class _WorkflowManager:
    def get_config(self, workflow_name: str) -> dict[str, Any]:
        assert workflow_name == _WORKFLOW
        return {
            "agents": [
                {
                    "name": name,
                    "system_message": f"Act as {name}.",
                    "structured_outputs_required": False,
                }
                for name in ("AgentA", "AgentB", "AgentC")
            ]
        }

    def get_auto_tool_agents(self, workflow_name: str) -> set[str]:
        return set()

    def resolve_workflow_path(self, workflow_name: str) -> None:
        return None


async def _agents(
    monkeypatch: pytest.MonkeyPatch,
    scenario: _SDKScenario,
    client: httpx.AsyncClient,
    policy: ContextAuthorityPolicy,
) -> dict[str, Agent]:
    from mozaiksai.core import observability
    from mozaiksai.core.workflow import llm_config
    from mozaiksai.core.workflow.agents import tools
    from mozaiksai.core.workflow.outputs import structured

    async def complete_intake(context_variables: Any) -> str:
        """Record the completed intake through the injected workflow context."""
        scenario.calls.append("complete_intake")
        if scenario.mutate_context:
            context_variables[_KEY] = True
        return "intake recorded"

    async def other_tool(context_variables: Any) -> str:
        """Perform a different deterministic operation."""
        scenario.calls.append("other_tool")
        return "other operation recorded"

    async def config(
        *args: Any, agent_name: str = "AgentA", **kwargs: Any
    ) -> tuple[None, dict[str, Any]]:
        return None, {
            "config_list": [
                {
                    "model": agent_name,
                    "api_type": "openai",
                    "api_key": "test-only",
                    "base_url": "https://provider.invalid/v1",
                }
            ],
            "streaming": False,
        }

    original_converter = factory.llm_config_to_ag2_config

    def convert(value: dict[str, Any]) -> Any:
        return original_converter(value).copy(http_client=client)

    def observers(*, agent_name: str, **kwargs: Any) -> list[Any]:
        async def record(event: BaseEvent) -> None:
            scenario.events[agent_name].append(event)

        return [observer(ToolCallEvent | ToolResultEvent, record)]

    monkeypatch.setattr(factory, "workflow_manager", _WorkflowManager())
    monkeypatch.setattr(factory, "get_structured_outputs_for_workflow", lambda _workflow: {})
    monkeypatch.setattr(factory, "llm_config_to_ag2_config", convert)
    monkeypatch.setattr(llm_config, "get_llm_config", config)
    monkeypatch.setattr(structured, "get_llm_for_workflow", config)
    monkeypatch.setattr(
        tools,
        "load_agent_tool_functions",
        lambda _workflow: {name: [complete_intake, other_tool] for name in ("AgentA", "AgentC")},
    )
    monkeypatch.setattr(observability, "build_ag2_token_watchdog_observers", observers)
    context = create_context_container(
        initial={_KEY: False},
        chat_id="tool-routing-chat",
        app_id="tool-routing-app",
        authority_policy=policy,
    )
    agents = await factory.create_agents(_WORKFLOW, context_variables=context)
    assert set(agents) == {"AgentA", "AgentB", "AgentC"}
    assert all(type(agent) is Agent for agent in agents.values())
    return agents


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    scenario: _SDKScenario,
    rules: list[dict[str, Any]],
    *,
    allowed: bool = True,
) -> AG2NetworkRunnerResult:
    policy = _policy(rules, allowed=allowed)
    graph = compile_transition_rules_to_graph(
        rules,
        initial_agent_name=scenario.source,
        context_authority_policy=policy,
        agent_id_by_name={name: name for name in ("AgentA", "AgentB", "AgentC")},
    )
    assert TransitionGraph.loads(json.dumps(graph.to_dict())).to_dict() == graph.to_dict()
    async with httpx.AsyncClient(transport=httpx.MockTransport(scenario.respond)) as client:
        agents = await _agents(monkeypatch, scenario, client, policy)
        result = await AG2NetworkRunner().run(
            AG2NetworkRunnerRequest(
                workflow_name=_WORKFLOW,
                chat_id="tool-routing-chat",
                app_id="tool-routing-app",
                agents=agents,
                transition_rules=rules,
                initial_agent_name=scenario.source,
                initial_message="Complete the current deterministic operation.",
                context_variables={_KEY: False},
                context_authority_policy=policy,
                close_timeout_seconds=3.0,
            )
        )
    return result


def _packets(result: AG2NetworkRunnerResult, agent: str) -> list[dict[str, Any]]:
    return [
        entry["event_data"]
        for entry in result.wal
        if entry["event_type"] == EV_PACKET
        and result.agent_name_by_id.get(entry["sender_id"]) == agent
    ]


def _assert_real_tool_events(scenario: _SDKScenario, names: tuple[str, ...]) -> None:
    events = scenario.events[scenario.source]
    calls = [event for event in events if isinstance(event, ToolCallEvent)]
    results = [event for event in events if isinstance(event, ToolResultEvent)]
    assert Counter(event.name for event in calls) == Counter(names)
    assert {event.parent_id for event in results} == {event.id for event in calls}
    assert Counter(scenario.calls) == Counter(names)
    for payload in scenario.request_bodies:
        for tool in payload.get("tools", []):
            assert "context_variables" not in tool["function"]["parameters"].get("properties", {})


@pytest.mark.asyncio
async def test_factory_tool_authority_reaches_network_context_equals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _SDKScenario(mutate_context=True)
    result = await _run(monkeypatch, scenario, _rules("context_equals"))
    assert result.status is RunStatus.COMPLETED, result.error
    _assert_real_tool_events(scenario, ("complete_intake",))
    assert not [event for event in scenario.events["AgentA"] if isinstance(event, ToolErrorEvent)]
    assert result.context_variables[_KEY] is True
    assert scenario.requests == {"AgentA": 2, "AgentB": 1}
    assert _packets(result, "AgentA")[0]["context_updates"]["set"] == {_KEY: True}
    assert len(_packets(result, "AgentB")) == 1


@pytest.mark.asyncio
async def test_factory_tool_forbidden_write_never_reaches_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _SDKScenario(mutate_context=True)
    result = await _run(monkeypatch, scenario, _rules("context_equals"), allowed=False)
    _assert_real_tool_events(scenario, ("complete_intake",))
    errors = [event for event in scenario.events["AgentA"] if isinstance(event, ToolErrorEvent)]
    assert len(errors) == 1
    assert isinstance(errors[0].error, ContextAuthorityError)
    assert "context_authority.rejected" in str(errors[0].error)
    assert result.context_variables[_KEY] is False
    assert scenario.calls == ["complete_intake"]
    assert scenario.requests["AgentB"] == 0
    assert all(
        _KEY not in packet["context_updates"]["set"] for packet in _packets(result, "AgentA")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("final_text", ["Intake finished.", ""])
async def test_real_tool_called_handoff_survives_graph_reload(
    monkeypatch: pytest.MonkeyPatch,
    final_text: str,
) -> None:
    scenario = _SDKScenario(final_text=final_text)
    result = await _run(monkeypatch, scenario, _rules())
    assert result.status is RunStatus.COMPLETED, result.error
    _assert_real_tool_events(scenario, ("complete_intake",))
    assert scenario.requests == {"AgentA": 2, "AgentB": 1}
    packet = _packets(result, "AgentA")[0]
    assert packet["routing"]["kind"] == "handoff"
    assert packet["routing"]["tool"] == "complete_intake"
    assert packet["body"] == final_text
    assert len(_packets(result, "AgentB")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source,names", [("AgentC", ("complete_intake",)), ("AgentA", ("other_tool",)), ("AgentA", ())]
)
async def test_tool_called_does_not_route_wrong_source_tool_or_text(
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    names: tuple[str, ...],
) -> None:
    scenario = _SDKScenario(
        source=source, tool_names=names, final_text="complete_intake was called"
    )
    result = await _run(monkeypatch, scenario, _rules())
    assert result.status is RunStatus.COMPLETED, result.error
    _assert_real_tool_events(scenario, names)
    assert scenario.requests["AgentB"] == 0
    assert _packets(result, "AgentB") == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "names,target",
    [
        (("complete_intake", "other_tool"), "AgentB"),
        (("other_tool", "complete_intake"), "AgentC"),
    ],
)
async def test_multiple_real_tool_calls_preserve_native_first_emitted_precedence(
    monkeypatch: pytest.MonkeyPatch,
    names: tuple[str, ...],
    target: str,
) -> None:
    rules = _rules()
    rules.insert(
        0,
        {
            "source_agent": "AgentA",
            "target_agent": "AgentC",
            "transition_type": "condition",
            "condition_type": "tool_called",
            "tool_name": "other_tool",
        },
    )
    scenario = _SDKScenario(tool_names=names)
    result = await _run(monkeypatch, scenario, rules)
    assert result.status is RunStatus.COMPLETED, result.error
    _assert_real_tool_events(scenario, names)
    assert scenario.requests == {"AgentA": 2, target: 1}
    assert _packets(result, "AgentA")[0]["routing"]["tool"] == names[0]
    assert len(_packets(result, target)) == 1


@pytest.mark.asyncio
async def test_tool_handoff_pauses_and_resumes_without_duplicate_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _SDKScenario()
    paused = await _run(monkeypatch, scenario, _rules(pause=True))
    assert paused.status is RunStatus.PAUSED, paused.error
    assert paused.live_run is not None
    try:
        assert scenario.requests == {"AgentA": 2, "AgentB": 1}
        assert len(_packets(paused, "AgentA")) == len(_packets(paused, "AgentB")) == 1
        continued = await paused.live_run.continue_with_user_message("Continue after review.")
        assert continued.status is RunStatus.COMPLETED, continued.error
        assert scenario.requests == {"AgentA": 2, "AgentB": 1}
        assert scenario.calls == ["complete_intake"]
        assert not _packets(continued, "AgentA")
        assert not _packets(continued, "AgentB")
    finally:
        await paused.live_run.close()
