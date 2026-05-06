from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from mozaiksai.core.events.runtime_events import RUNTIME_AGENT_OUTPUT_VALIDATED
from mozaiksai.core.workflow.stream.handlers import text_handler as _text_handler_mod
from mozaiksai.core.workflow.stream.handlers.text_handler import TextEventHandler


class _FakeDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def emit(self, kind: str, payload: dict) -> None:
        self.calls.append((kind, payload))


@pytest.mark.asyncio
async def test_emit_agent_output_validated_uses_auto_tool_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = TextEventHandler()
    dispatcher = _FakeDispatcher()
    ag2_calls: list[dict] = []
    ctx = SimpleNamespace(
        chat_id="chat-1",
        app_id="app-1",
        workflow_name="RuntimeSmoke",
        workflow_name_upper="RUNTIMESMOKE",
        user_id="user-1",
        dispatcher=dispatcher,
        wf_logger=logging.getLogger("test.text_event_handler"),
        agents={
            "RuntimeSmokeAgent": SimpleNamespace(
                _mozaiks_structured_model_name="RuntimeSmokeResult"
            )
        },
        context_variables=None,
    )
    state = SimpleNamespace(sequence_counter=3)

    monkeypatch.setattr(_text_handler_mod, "emit_structured_output", lambda **kwargs: ag2_calls.append(kwargs) or True)

    await handler._emit_agent_output_validated(
        "RuntimeSmokeAgent",
        {"agent_message": "done"},
        ctx,
        state,
        auto_tool_call_enabled=True,
    )

    assert len(dispatcher.calls) == 1
    kind, payload = dispatcher.calls[0]
    assert kind == RUNTIME_AGENT_OUTPUT_VALIDATED
    assert payload["kind"] == RUNTIME_AGENT_OUTPUT_VALIDATED
    assert payload["auto_tool_call"] is True
    assert payload["model_name"] == "RuntimeSmokeResult"
    assert ag2_calls == [
        {
            "agent_name": "RuntimeSmokeAgent",
            "chat_id": "chat-1",
            "output_type": "RuntimeSmokeResult",
            "output_data": {"agent_message": "done"},
            "validation_passed": True,
        }
    ]
