"""A transient provider failure must not end a workflow turn.

Live traversal run 4 (mozaiks#1005, finding 4) lost a whole stage to one
provider ``Connection error``. AG2's ``RetryMiddleware`` repeats an LLM call
only when the failed attempt published nothing, so it cannot help a call that
was streaming when the connection dropped, and it was attached only to agents
with a response schema.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx2
import pytest
from ag2 import Agent
from ag2.events import ModelMessage, ModelResponse
from ag2.middleware import RetryMiddleware

from mozaiksai.core.adapters.llm_fallback import llm_config_to_ag2_config
from mozaiksai.core.workflow.agents import factory


def _openai_llm_config(**extra):
    return {"config_list": [{"api_type": "openai", "model": "gpt-5-nano", "api_key": "test-key"}], **extra}


def test_provider_streaming_follows_the_ag2_default_unless_configured() -> None:
    assert llm_config_to_ag2_config(_openai_llm_config()).streaming is False
    assert llm_config_to_ag2_config(_openai_llm_config(streaming=True)).streaming is True


def test_non_openai_providers_also_default_to_complete_responses() -> None:
    pytest.importorskip("anthropic")
    anthropic = {"config_list": [{"api_type": "anthropic", "model": "claude-sonnet-5", "api_key": "test-key"}]}
    assert llm_config_to_ag2_config(anthropic).streaming is False


class _FlakyProvider:
    """Fails the first call before publishing anything, then answers."""

    model = "offline-retry"
    provider = "openai"

    def __init__(self) -> None:
        self.calls = 0

    def copy(self):
        return self

    def create(self):
        async def client(events, context, *, tools, response_schema, serializer):
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("Connection error.")
            return ModelResponse(ModelMessage("Completed."))

        return client


@pytest.mark.asyncio
async def test_workflow_agent_without_a_schema_retries_a_transient_provider_failure(monkeypatch) -> None:
    from mozaiksai.core.tokens.guard import TokenUsageGuard
    from mozaiksai.core.workflow import llm_config
    from mozaiksai.core.workflow.agents import tools
    from mozaiksai.core.workflow.outputs import structured

    config = {
        "agents": {"agents": {
            "Writer": {"name": "Writer", "system_message": "Write the reply.", "structured_outputs_required": False},
        }},
        "context_variables": {"definitions": {}, "agents": {}},
    }
    provider = _FlakyProvider()
    monkeypatch.setattr(factory, "workflow_manager", SimpleNamespace(
        get_config=lambda _: config, get_auto_tool_agents=lambda _: set(),
    ))
    monkeypatch.setattr(factory, "get_structured_outputs_for_workflow", lambda _: {})
    monkeypatch.setattr(factory, "llm_config_to_ag2_config", lambda _: provider)
    stub_config = AsyncMock(return_value=(None, {"config_list": [{"model": provider.model, "api_key": "test"}]}))
    monkeypatch.setattr(llm_config, "get_llm_config", stub_config)
    monkeypatch.setattr(structured, "get_llm_for_workflow", stub_config)
    monkeypatch.setattr(TokenUsageGuard, "check_or_raise", AsyncMock())
    monkeypatch.setattr(tools, "load_agent_tool_functions", lambda _: {})

    agents = await factory.create_agents("RetryFlow", context_variables={})
    reply = await agents["Writer"].ask("Write it.")

    assert reply.body == "Completed."
    assert provider.calls == 2


def _completion_json(text: str) -> dict:
    return {
        "id": "chatcmpl-retry",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-5-nano",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
    }


class _DroppedStream(httpx2.AsyncByteStream):
    async def __aiter__(self):
        chunk = {
            "id": "chatcmpl-drop",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "gpt-5-nano",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Partial"}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(chunk)}\n\n".encode()
        raise httpx2.ReadError("Connection reset while streaming")


@pytest.mark.asyncio
async def test_a_dropped_provider_connection_is_retried_through_the_real_openai_client() -> None:
    requests: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            if body.get("stream"):
                return httpx2.Response(200, headers={"content-type": "text/event-stream"}, stream=_DroppedStream())
            raise httpx2.ConnectError("Connection error.", request=request)
        return httpx2.Response(200, json=_completion_json("Recovered."))

    # The SDK's own retries are disabled so this proves the agent-level retry.
    model_config = llm_config_to_ag2_config(_openai_llm_config(max_retries=0)).copy(
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    agent = Agent("Designer", prompt="Design the contract.", config=model_config,
                  middleware=[RetryMiddleware(max_retries=2)])

    reply = await agent.ask("Design it.")

    assert reply.body == "Recovered."
    assert len(requests) == 2
