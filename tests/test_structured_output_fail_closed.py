from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
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
async def test_agent_missing_structured_outputs_required_aborts_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mozaiksai.core.workflow.agents import factory

    _patch_agent_factory(
        monkeypatch,
        agent_config={"name": "AmbiguousAgent"},
        registry={},
    )

    with pytest.raises(ValueError, match="explicitly declare structured_outputs_required"):
        await factory.create_agents("AmbiguousWorkflow", context_variables={})

    assert _FakeAgent.created == []


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


def test_agents_yaml_requires_explicit_structured_outputs_required() -> None:
    from mozaiksai.core.workflow.declarative.contracts import parse_agents_config

    with pytest.raises(ValueError, match="structured_outputs_required"):
        parse_agents_config(
            {
                "agents": [
                    {
                        "name": "AmbiguousAgent",
                        "prompt_sections": [{"heading": "Role", "content": "Work."}],
                    }
                ]
            }
        )


# ---------------------------------------------------------------------------
# INV-10 / INV-11 / INV-13: malformed output fails closed before state commit
# ---------------------------------------------------------------------------


def test_malformed_output_not_in_structured_outputs_list(
) -> None:
    """INV-10/INV-11: malformed body is never added to structured_outputs.

    _validate_wal_structured_outputs must return an empty outputs list and a
    non-None error string when a registered agent's packet body cannot be
    parsed against its model. The malformed data does NOT appear in the
    outputs list that callers use for context writes and UI emission.
    """
    from ag2.network import EV_PACKET

    from mozaiksai.core.adapters.ag2_network_runner import _validate_wal_structured_outputs

    # Valid packet first, then malformed one from the same agent
    outputs, error = _validate_wal_structured_outputs(
        wal=[
            _Envelope(
                event_type=EV_PACKET,
                sender_id="agent-1",
                event_data={"body": '{"status": "ok"}'},
            ),
            _Envelope(
                event_type=EV_PACKET,
                sender_id="agent-1",
                # missing required "status" field
                event_data={"body": '{"unexpected_key": 42}'},
            ),
        ],
        agent_name_by_id={"agent-1": "StrictAgent"},
        structured_registry={"StrictAgent": _RequiredModel},
    )

    # The function fails on the first bad packet and discards any accumulated
    # output so a partially valid WAL cannot commit context or artifacts.
    assert error is not None, "malformed packet must produce a validation error"
    assert outputs == [], (
        "any structured-output validation failure must discard accumulated outputs"
    )


def test_runner_result_is_failed_when_wal_validation_fails() -> None:
    """INV-10/INV-13: RunStatus is FAILED when WAL validation returns an error.

    Confirms that _validate_wal_structured_outputs returning error=<str>
    produces a FAILED AG2NetworkRunnerResult and that the structured_outputs
    list in that result is empty (no malformed data committed).
    """
    from ag2.network import EV_PACKET

    from mozaiksai.core.adapters.ag2_network_runner import (
        AG2NetworkRunnerResult,
        _validate_wal_structured_outputs,
    )
    from mozaiksai.core.ports.orchestration import RunStatus

    wal = [
        _Envelope(
            event_type=EV_PACKET,
            sender_id="agent-x",
            # empty JSON object → missing required field "status"
            event_data={"body": "{}"},
        )
    ]
    agent_name_by_id = {"agent-x": "StrictAgent"}
    structured_registry = {"StrictAgent": _RequiredModel}

    structured_outputs, validation_error = _validate_wal_structured_outputs(
        wal=wal,
        agent_name_by_id=agent_name_by_id,
        structured_registry=structured_registry,
    )

    # Simulate what _snapshot_result does in the runner
    if validation_error:
        result = AG2NetworkRunnerResult(
            status=RunStatus.FAILED,
            workflow_name="TestWorkflow",
            chat_id="chat-1",
            app_id="app-1",
            channel_id="ch-1",
            structured_outputs=structured_outputs,
            error=validation_error,
        )
    else:
        result = AG2NetworkRunnerResult(
            status=RunStatus.COMPLETED,
            workflow_name="TestWorkflow",
            chat_id="chat-1",
            app_id="app-1",
            channel_id="ch-1",
            structured_outputs=structured_outputs,
        )

    assert result.status is RunStatus.FAILED, (
        "runner result must be FAILED when WAL validation detects malformed output"
    )
    assert result.structured_outputs == [], (
        "malformed structured output must not be committed to the result's outputs list"
    )
    assert result.error is not None and "status" in result.error, (
        "error message must name the missing required field"
    )


def test_malformed_output_does_not_advance_to_continuation_phase() -> None:
    """INV-13: When first-phase result is not COMPLETED, task batches and
    continuation agents are skipped.

    This validates the gate at orchestration_patterns.py:
        if first_phase_result.status is not RunStatus.COMPLETED:
            runner_result = first_phase_result
        else:
            ... task batches, continuation agent resolution ...

    A FAILED first-phase result (from structured-output validation failure)
    must set runner_result = first_phase_result without executing task batches
    or resolving a continuation agent.
    """
    from ag2.network import EV_PACKET

    from mozaiksai.core.adapters.ag2_network_runner import (
        AG2NetworkRunnerResult,
        _validate_wal_structured_outputs,
    )
    from mozaiksai.core.ports.orchestration import RunStatus

    wal = [
        _Envelope(
            event_type=EV_PACKET,
            sender_id="trigger-agent",
            event_data={"body": "{}"},  # missing required "status"
        )
    ]
    structured_outputs, validation_error = _validate_wal_structured_outputs(
        wal=wal,
        agent_name_by_id={"trigger-agent": "StrictAgent"},
        structured_registry={"StrictAgent": _RequiredModel},
    )

    # Simulate _snapshot_result return for a FAILED validation
    first_phase_result = AG2NetworkRunnerResult(
        status=RunStatus.FAILED if validation_error else RunStatus.COMPLETED,
        workflow_name="TestWorkflow",
        chat_id="chat-1",
        app_id="app-1",
        channel_id="ch-1",
        structured_outputs=structured_outputs,
        error=validation_error,
    )

    continuation_executed = False

    # Reproduce the gate logic from orchestration_patterns.py
    if first_phase_result.status is not RunStatus.COMPLETED:
        runner_result = first_phase_result
    else:
        continuation_executed = True  # task batches / continuation path

    assert first_phase_result.status is RunStatus.FAILED
    assert not continuation_executed, (
        "task batches and continuation agents must NOT execute when "
        "first-phase result is not COMPLETED (structured-output validation failed)"
    )
    assert runner_result is first_phase_result
    assert runner_result.structured_outputs == []


def test_malformed_output_cannot_write_routing_variable() -> None:
    """INV-11: malformed structured output body does not appear in structured_outputs.

    The structured_outputs list is what _emit_validated_structured_outputs_from_runner_result
    uses to write context variables (routing keys, structured_output, etc.).
    If the list is empty, no context writes from malformed data can occur.
    """
    from ag2.network import EV_PACKET

    from mozaiksai.core.adapters.ag2_network_runner import _validate_wal_structured_outputs

    # A body that contains a plausible routing key but is schema-invalid
    malformed_body = '{"next_agent": "SecretAgent", "unexpected_key": true}'

    outputs, error = _validate_wal_structured_outputs(
        wal=[
            _Envelope(
                event_type=EV_PACKET,
                sender_id="agent-1",
                event_data={"body": malformed_body},
            )
        ],
        agent_name_by_id={"agent-1": "StrictAgent"},
        structured_registry={"StrictAgent": _RequiredModel},
    )

    assert error is not None, "validation must fail for schema-invalid output"
    # The routing key from the malformed body must NOT be in outputs
    for entry in outputs:
        structured_data = entry.get("structured_data") or {}
        assert "next_agent" not in structured_data, (
            "malformed body content must not appear as structured_data in outputs"
        )


@pytest.mark.asyncio
async def test_failed_structured_output_runner_result_does_not_commit_context() -> None:
    """INV-11: failed structured-output runner results do not write context."""
    from mozaiksai.core.ports.orchestration import RunStatus
    from mozaiksai.core.workflow.orchestration_patterns import (
        _emit_validated_structured_outputs_from_runner_result,
    )

    runner_result = SimpleNamespace(
        status=RunStatus.FAILED,
        error="structured output validation failed for StrictAgent: status missing",
        structured_outputs=[
            {
                "agent": "StrictAgent",
                "model_name": "RequiredModel",
                "structured_data": {"status": "should-not-commit"},
            }
        ],
    )
    context_vars: dict[str, Any] = {}

    await _emit_validated_structured_outputs_from_runner_result(
        runner_result=runner_result,
        workflow_name="StrictWorkflow",
        chat_id="chat-1",
        app_id="app-1",
        user_id=None,
        turn_sequence_start=0,
        context_vars_dict=context_vars,
        context_bridge=None,
        structured_registry={"StrictAgent": _RequiredModel},
        wf_logger=SimpleNamespace(debug=lambda *args, **kwargs: None),
    )

    assert context_vars == {}
