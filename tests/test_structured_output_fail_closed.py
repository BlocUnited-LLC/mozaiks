from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from ag2.middleware.builtin.llm_retry import _RetryMiddleware
from pydantic import BaseModel, ConfigDict


class _RequiredModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str


class _OpenObjectModel(BaseModel):
    payload: dict[str, Any]


class _FakeManager:
    def __init__(self, agent_config: dict[str, Any]) -> None:
        self.agent_config = agent_config

    def get_config(self, workflow_name: str) -> dict[str, Any]:
        return {"agents": [self.agent_config]}

    def get_auto_tool_agents(self, workflow_name: str) -> set[str]:
        return set()

    def resolve_workflow_path(self, workflow_name: str) -> None:
        return None


class _FakeAgent:
    created: list[_FakeAgent] = []

    def __init__(self, name: str, **kwargs: Any) -> None:
        self.name = name
        self.kwargs = kwargs
        self.tools = kwargs.get("tools", ())
        _FakeAgent.created.append(self)


async def _fake_llm_config(*args: Any, **kwargs: Any) -> tuple[None, dict[str, Any]]:
    return None, {
        "config_list": [
            {"model": "gpt-4o-mini", "api_type": "openai", "api_key": "test-key"}
        ],
        "tools": [],
    }


def _patch_agent_factory(
    monkeypatch: pytest.MonkeyPatch,
    *,
    agent_config: dict[str, Any],
    registry: dict[str, type[BaseModel]] | Exception,
) -> None:
    from mozaiksai.core.workflow import llm_config
    from mozaiksai.core.workflow.agents import factory
    from mozaiksai.core.workflow.agents import tools as agent_tools
    from mozaiksai.core.workflow.outputs import structured

    _FakeAgent.created.clear()

    if isinstance(registry, Exception):
        def _registry_loader(_workflow: str) -> dict[str, type[BaseModel]]:
            raise registry
    else:
        def _registry_loader(_workflow: str) -> dict[str, type[BaseModel]]:
            return registry

    monkeypatch.setattr(factory, "workflow_manager", _FakeManager(agent_config))
    monkeypatch.setattr(factory, "load_a2a_agent_specs", lambda _config: {})
    monkeypatch.setattr(factory, "get_structured_outputs_for_workflow", _registry_loader)
    monkeypatch.setattr(factory, "llm_config_to_ag2_config", lambda _config: object())
    monkeypatch.setattr(factory, "Agent", _FakeAgent)
    monkeypatch.setattr(llm_config, "get_llm_config", _fake_llm_config)
    monkeypatch.setattr(structured, "get_llm_for_workflow", _fake_llm_config)
    monkeypatch.setattr(agent_tools, "load_agent_tool_functions", lambda _workflow: {})


@pytest.mark.asyncio
async def test_required_schema_registry_load_failure_aborts_agent_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mozaiksai.core.workflow.agents import factory

    _patch_agent_factory(
        monkeypatch,
        agent_config={"name": "StrictAgent", "structured_outputs_required": True},
        registry=ValueError("broken registry"),
    )

    with pytest.raises(ValueError, match="structured output registry could not load"):
        await factory.create_agents("StrictWorkflow", context_variables={})

    assert _FakeAgent.created == []


@pytest.mark.asyncio
async def test_required_model_resolution_failure_aborts_agent_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mozaiksai.core.workflow.agents import factory

    _patch_agent_factory(
        monkeypatch,
        agent_config={"name": "StrictAgent", "structured_outputs_required": True},
        registry={},
    )

    with pytest.raises(ValueError, match="has no structured output registry entry"):
        await factory.create_agents("StrictWorkflow", context_variables={})

    assert _FakeAgent.created == []


@pytest.mark.asyncio
async def test_required_openai_provider_schema_preparation_failure_aborts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mozaiksai.core.workflow.agents import factory

    _patch_agent_factory(
        monkeypatch,
        agent_config={"name": "StrictAgent", "structured_outputs_required": True},
        registry={"StrictAgent": _OpenObjectModel},
    )

    with pytest.raises(ValueError, match="cannot be prepared for provider strict response_schema"):
        await factory.create_agents("StrictWorkflow", context_variables={})

    assert _FakeAgent.created == []


@pytest.mark.asyncio
async def test_required_provider_schema_compile_failure_aborts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mozaiksai.core.workflow.agents import factory

    _patch_agent_factory(
        monkeypatch,
        agent_config={"name": "StrictAgent", "structured_outputs_required": True},
        registry={"StrictAgent": _RequiredModel},
    )
    monkeypatch.setattr(
        factory,
        "get_provider_response_model",
        lambda _model: (_ for _ in ()).throw(ValueError("provider schema failed")),
    )

    with pytest.raises(ValueError, match="provider schema failed"):
        await factory.create_agents("StrictWorkflow", context_variables={})

    assert _FakeAgent.created == []


@pytest.mark.asyncio
async def test_explicitly_unstructured_agent_still_loads_without_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mozaiksai.core.workflow.agents import factory

    _patch_agent_factory(
        monkeypatch,
        agent_config={"name": "PlainAgent", "structured_outputs_required": False},
        registry=ValueError("registry unavailable"),
    )

    agents = await factory.create_agents("PlainWorkflow", context_variables={})

    assert "PlainAgent" in agents
    assert _FakeAgent.created[0].kwargs["response_schema"] is None


@pytest.mark.asyncio
async def test_required_structured_agent_reaches_ag2_with_response_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mozaiksai.core.workflow.agents import factory

    _patch_agent_factory(
        monkeypatch,
        agent_config={"name": "StrictAgent", "structured_outputs_required": True},
        registry={"StrictAgent": _RequiredModel},
    )

    await factory.create_agents("StrictWorkflow", context_variables={})

    assert _FakeAgent.created
    assert _FakeAgent.created[0].kwargs["response_schema"] is not None


@pytest.mark.asyncio
async def test_retry_middleware_retries_provider_exception() -> None:
    calls = 0
    middleware = _RetryMiddleware(None, None, max_retries=2)

    async def _provider_call(events: Any, context: Any) -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("provider unavailable")
        return "ok"

    result = await middleware.on_llm_call(_provider_call, [], {})

    assert result == "ok"
    assert calls == 3


@pytest.mark.asyncio
async def test_retry_middleware_does_not_repair_malformed_output() -> None:
    calls = 0
    middleware = _RetryMiddleware(None, None, max_retries=2)

    async def _provider_call(events: Any, context: Any) -> str:
        nonlocal calls
        calls += 1
        return "{}"

    result = await middleware.on_llm_call(_provider_call, [], {})

    assert result == "{}"
    assert calls == 1


@dataclass(slots=True)
class _Envelope:
    event_type: str
    sender_id: str
    event_data: dict[str, Any]


def test_malformed_network_output_fails_terminally_with_typed_validation_error() -> None:
    from ag2.network import EV_PACKET

    from mozaiksai.core.adapters.ag2_network_runner import _validate_wal_structured_outputs

    outputs, error = _validate_wal_structured_outputs(
        wal=[
            _Envelope(
                event_type=EV_PACKET,
                sender_id="agent-1",
                event_data={"body": "{}"},
            )
        ],
        agent_name_by_id={"agent-1": "StrictAgent"},
        structured_registry={"StrictAgent": _RequiredModel},
    )

    assert outputs == []
    assert error is not None
    assert error.startswith("structured output validation failed for StrictAgent:")
    assert "status" in error
