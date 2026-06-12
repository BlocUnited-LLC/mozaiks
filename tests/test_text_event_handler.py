from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from mozaiksai.core.events.runtime_events import RUNTIME_AGENT_OUTPUT_VALIDATED
from mozaiksai.core.workflow.outputs.runtime_events import (
    emit_validated_agent_output as _emit_validated_agent_output,
)


class _RuntimeSmokeResult(BaseModel):
    agent_message: str
    summary: str


class _FakeDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def emit(self, kind: str, payload: dict) -> None:
        self.calls.append((kind, payload))


class _FakeLogger:
    def __init__(self) -> None:
        self.warnings: list[tuple] = []

    def warning(self, *args, **kwargs) -> None:
        self.warnings.append((args, kwargs))

    def debug(self, *args, **kwargs) -> None:
        pass


@pytest.mark.asyncio
async def test_emit_validated_agent_output_uses_auto_tool_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    dispatcher = _FakeDispatcher()
    context_bridge = SimpleNamespace(data={"stage": "test"})

    monkeypatch.setattr(
        "mozaiksai.core.events.unified_event_dispatcher.get_event_dispatcher",
        lambda: dispatcher,
    )

    structured = await _emit_validated_agent_output(
        current_agent_name="RuntimeSmokeAgent",
        last_reply=SimpleNamespace(body='{"agent_message":"done","summary":"ok"}'),
        workflow_name="RuntimeSmoke",
        chat_id="chat-1",
        app_id="app-1",
        user_id="user-1",
        turn_sequence=3,
        context_vars_dict={"workflow_name": "RuntimeSmoke", "stage": "test"},
        context_bridge=context_bridge,
        structured_registry={"RuntimeSmokeAgent": _RuntimeSmokeResult},
        auto_tool_agents={"RuntimeSmokeAgent"},
        wf_logger=_FakeLogger(),
    )

    assert structured == {"agent_message": "done", "summary": "ok"}
    assert len(dispatcher.calls) == 1
    kind, payload = dispatcher.calls[0]
    assert kind == RUNTIME_AGENT_OUTPUT_VALIDATED
    assert payload["kind"] == RUNTIME_AGENT_OUTPUT_VALIDATED
    assert payload["auto_tool_call"] is True
    assert payload["model_name"] == "_RuntimeSmokeResult"
    assert payload["_pattern_context_ref"] is context_bridge


@pytest.mark.asyncio
async def test_emit_validated_agent_output_does_not_emit_for_invalid_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = _FakeDispatcher()
    monkeypatch.setattr(
        "mozaiksai.core.events.unified_event_dispatcher.get_event_dispatcher",
        lambda: dispatcher,
    )

    structured = await _emit_validated_agent_output(
        current_agent_name="RuntimeSmokeAgent",
        last_reply=SimpleNamespace(body='{"agent_message":"done"}'),
        workflow_name="RuntimeSmoke",
        chat_id="chat-1",
        app_id="app-1",
        user_id="user-1",
        turn_sequence=4,
        context_vars_dict={},
        context_bridge=SimpleNamespace(data={}),
        structured_registry={"RuntimeSmokeAgent": _RuntimeSmokeResult},
        auto_tool_agents={"RuntimeSmokeAgent"},
        wf_logger=_FakeLogger(),
    )

    assert structured is None
    assert dispatcher.calls == []
