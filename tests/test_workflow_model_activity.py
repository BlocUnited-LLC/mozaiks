"""Real Factory/AG2 calls with only the model and UI delivery controlled."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from ag2.events import ModelMessage, ModelResponse

from mozaiksai.core.transport.simple_transport import SimpleTransport
from mozaiksai.core.workflow.agents import factory


async def _agent(monkeypatch, *, chat_id="activity-chat", provider=None, guard=None):
    from mozaiksai.core import observability
    from mozaiksai.core.tokens.guard import TokenUsageGuard
    from mozaiksai.core.workflow import llm_config
    from mozaiksai.core.workflow.agents import tools
    from mozaiksai.core.workflow.outputs import structured

    class Config:
        model = "offline-model"
        provider = "openai"

        def copy(self):
            return self

        def create(self):
            async def client(events, context, **kwargs):
                if provider is not None:
                    await provider(events, context)
                return ModelResponse(ModelMessage("A synthetic response."))

            return client

    manager = SimpleNamespace(
        get_config=lambda _: {
            "visual_agents": None,
            "agents": [{
                "name": "PrivatePlanningAgent",
                "system_message": "Private instructions.",
                "structured_outputs_required": False,
            }],
            "context_variables": {
                "definitions": {"requested_changes": {"type": "string", "source": {"type": "state"}}},
                "agents": {"PrivatePlanningAgent": {"variables": ["requested_changes"]}},
            },
        },
        get_auto_tool_agents=lambda _: set(),
        resolve_workflow_path=lambda _: None,
    )
    config = AsyncMock(return_value=(None, {"config_list": [{"model": "offline-model", "api_type": "openai"}]}))
    monkeypatch.setattr(factory, "workflow_manager", manager)
    monkeypatch.setattr(factory, "get_structured_outputs_for_workflow", lambda _: {})
    monkeypatch.setattr(factory, "llm_config_to_ag2_config", lambda _: Config())
    monkeypatch.setattr(llm_config, "get_llm_config", config)
    monkeypatch.setattr(structured, "get_llm_for_workflow", config)
    monkeypatch.setattr(tools, "load_agent_tool_functions", lambda _: {})
    monkeypatch.setattr(observability, "build_ag2_token_watchdog_observers", lambda **_: [])
    monkeypatch.setattr(TokenUsageGuard, "check_or_raise", guard or AsyncMock())
    agents = await factory.create_agents("HiddenWorkflow", context_variables={
        "app_id": "activity-app", "chat_id": chat_id, "user_id": "activity-owner",
        "requested_changes": "Preserve the private draft.",
    })
    return agents["PrivatePlanningAgent"]


@pytest.mark.asyncio
async def test_factory_activity_precedes_each_real_call_without_exposing_hidden_agent(monkeypatch):
    delivered = []
    entered = asyncio.Event()
    release = asyncio.Event()
    preflight = AsyncMock()

    async def send(event, chat_id):
        assert preflight.await_count == len(delivered) + 1
        delivered.append((chat_id, event))

    transport = SimpleNamespace(send_event_to_ui=send)
    monkeypatch.setattr(SimpleTransport, "get_instance", AsyncMock(return_value=transport))

    async def provider(events, context):
        assert "Preserve the private draft." in "\n".join(context.prompt)
        entered.set()
        await release.wait()

    agent = await _agent(monkeypatch, provider=provider, guard=preflight)
    assert delivered == [], "Constructing an agent is not model activity"
    stream = None
    for call_index in range(2):
        entered.clear()
        release.clear()

        async def ask(current_stream=stream):
            return await agent.ask("Revise the draft.", stream=current_stream)

        pending = asyncio.create_task(ask())
        try:
            await asyncio.wait_for(entered.wait(), timeout=3)
            assert not pending.done(), "The provider must still be blocked"
            assert len(delivered) == call_index + 1, "Activity must arrive before the response"
            assert delivered[-1] == ("activity-chat", {
                "kind": "select_speaker", "agent": "Assistant", "selected_speaker": "Assistant",
            })
        finally:
            release.set()
            reply = await asyncio.wait_for(pending, timeout=3)
        stream = reply.context.stream
        assert await reply.content() == "A synthetic response."
        events = await stream.history.get_events()
        assert events
        assert len(delivered) == call_index + 1, "Reading saved history must not emit model activity"


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_id", [None, "", "   "])
async def test_background_model_call_without_chat_does_not_resolve_ui_transport(monkeypatch, chat_id):
    resolve = AsyncMock(side_effect=AssertionError("No UI transport belongs to this call"))
    monkeypatch.setattr(SimpleTransport, "get_instance", resolve)
    agent = await _agent(monkeypatch, chat_id=chat_id)
    assert await (await agent.ask("Run in the background.")).content() == "A synthetic response."
    resolve.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_boundary", ["resolve", "send"])
async def test_ui_transport_failure_does_not_block_model_call(monkeypatch, failure_boundary):
    failure = RuntimeError("UI unavailable")
    send = AsyncMock(side_effect=failure if failure_boundary == "send" else None)
    resolve = AsyncMock(return_value=SimpleNamespace(send_event_to_ui=send),
                        side_effect=failure if failure_boundary == "resolve" else None)
    monkeypatch.setattr(SimpleTransport, "get_instance", resolve)
    provider = AsyncMock()
    agent = await _agent(monkeypatch, provider=provider)
    assert await (await agent.ask("Run without a UI.")).content() == "A synthetic response."
    resolve.assert_awaited_once()
    provider.assert_awaited_once()


@pytest.mark.asyncio
async def test_activity_projection_does_not_swallow_cancellation(monkeypatch):
    monkeypatch.setattr(SimpleTransport, "get_instance", AsyncMock(side_effect=asyncio.CancelledError))
    provider = AsyncMock()
    agent = await _agent(monkeypatch, provider=provider)
    with pytest.raises(asyncio.CancelledError):
        await agent.ask("Cancel before the model call.")
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejected_preflight_emits_no_activity_or_model_call(monkeypatch):
    resolve = AsyncMock()
    monkeypatch.setattr(SimpleTransport, "get_instance", resolve)
    provider = AsyncMock()
    agent = await _agent(monkeypatch, provider=provider, guard=AsyncMock(side_effect=RuntimeError("Budget denied")))
    with pytest.raises(RuntimeError, match="Budget denied"):
        await agent.ask("Do not start.")
    resolve.assert_not_awaited()
    provider.assert_not_awaited()
