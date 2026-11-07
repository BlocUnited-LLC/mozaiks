from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mozaiksai.core.tokens.guard import TokenUsageDecision, TokenUsageDenied

usage_mod = importlib.import_module("mozaiksai.core.usage.middleware")


class _ContextBridge:
    data = {
        "app_id": "app-1",
        "chat_id": "chat-1",
        "user_id": "user-1",
        "tenant_id": "tenant-1",
        "workspace_id": "workspace-1",
        "workflow_name": "AppGenerator",
    }

    def get(self, key, default=None):
        return self.data.get(key, default)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", [None, "genesis", "refinement"])
async def test_ag2_usage_middleware_emits_usage_delta(monkeypatch, phase):
    emitted = {}

    async def fake_emit_usage_delta(**payload):
        emitted.update(payload)

    monkeypatch.setattr(usage_mod.TokenManager, "emit_usage_delta", fake_emit_usage_delta)
    context_variables = {**_ContextBridge.data, "build_id": "untrusted-build"}
    if phase:
        context_variables["run_build_binding"] = {
            "build_registry_id": "registry-1",
            "target_app_id": "generated-target",
            "build_id": f"build-{phase}",
            "phase": phase,
        }

    middleware = usage_mod.MozaiksUsageMiddleware(
        event=SimpleNamespace(),
        context=SimpleNamespace(),
        agent_name="PlannerAgent",
        workflow_name="AppGenerator",
        context_variables=context_variables,
        model_name="gpt-test",
    )

    async def call_next(events, context):
        return SimpleNamespace(
            id="response-1",
            model="gpt-test",
            usage=SimpleNamespace(
                prompt_tokens=12,
                completion_tokens=8,
                total_tokens=20,
                cached_tokens=5,
            ),
        )

    response = await middleware.on_llm_call(call_next, [], SimpleNamespace())

    assert response.id == "response-1"
    assert emitted["app_id"] == "app-1"
    assert emitted["chat_id"] == "chat-1"
    assert emitted["user_id"] == "user-1"
    assert emitted["tenant_id"] == "tenant-1"
    assert emitted["workspace_id"] == "workspace-1"
    assert emitted["workflow_name"] == "AppGenerator"
    assert emitted["build_id"] == (f"build-{phase}" if phase else None)
    assert emitted["agent_name"] == "PlannerAgent"
    assert emitted["model_name"] == "gpt-test"
    assert emitted["prompt_tokens"] == 12
    assert emitted["completion_tokens"] == 8
    assert emitted["total_tokens"] == 20
    assert emitted["cached"] is True
    assert emitted["cached_tokens"] == 5


@pytest.mark.asyncio
async def test_ag2_usage_middleware_rejects_invalid_binding_before_model_call():
    middleware = usage_mod.MozaiksUsageMiddleware(
        event=SimpleNamespace(),
        context=SimpleNamespace(),
        agent_name="PlannerAgent",
        workflow_name="AppGenerator",
        context_variables={**_ContextBridge.data, "run_build_binding": {"build_id": "forged"}},
    )
    called = False

    async def call_next(events, context):
        nonlocal called
        called = True
        return SimpleNamespace(usage=None)

    with pytest.raises(ValueError):
        await middleware.on_llm_call(call_next, [], SimpleNamespace())
    assert not called


def test_build_ag2_usage_middleware_returns_ag2_middleware():
    middleware = usage_mod.build_ag2_usage_middleware(
        agent_name="PlannerAgent",
        workflow_name="AppGenerator",
        context_variables=_ContextBridge(),
        model_name="gpt-test",
    )

    assert middleware._cls.__name__ == "MozaiksUsageMiddleware"
    assert middleware._options["agent_name"] == "PlannerAgent"
    assert middleware._options["workflow_name"] == "AppGenerator"


@pytest.mark.asyncio
async def test_ag2_usage_middleware_checks_token_balance_before_llm_call(monkeypatch):
    class _DenyingGuard:
        async def check_or_raise(self, **kwargs):  # noqa: ANN003
            raise TokenUsageDenied(
                TokenUsageDecision(
                    allowed=False,
                    reason="insufficient_balance",
                    error_code="INSUFFICIENT_TOKENS",
                    wallet_id="ai_tokens",
                    balance=0,
                    required_tokens=kwargs.get("required_tokens"),
                )
            )

    monkeypatch.setattr(usage_mod, "TokenUsageGuard", lambda: _DenyingGuard())

    middleware = usage_mod.MozaiksUsageMiddleware(
        event=SimpleNamespace(),
        context=SimpleNamespace(),
        agent_name="PlannerAgent",
        workflow_name="AppGenerator",
        context_variables=_ContextBridge(),
        model_name="gpt-test",
    )
    called = False

    async def call_next(events, context):
        nonlocal called
        called = True
        return SimpleNamespace(id="response-1", usage=None)

    with pytest.raises(TokenUsageDenied):
        await middleware.on_llm_call(call_next, [], SimpleNamespace())

    assert called is False


@pytest.mark.asyncio
async def test_ag2_usage_middleware_uses_context_preflight_required_tokens(monkeypatch):
    seen = {}

    class _AllowingGuard:
        async def check_or_raise(self, **kwargs):  # noqa: ANN003
            seen.update(kwargs)
            return TokenUsageDecision(allowed=True, reason="sufficient_balance")

    class _BudgetContext(_ContextBridge):
        data = {
            **_ContextBridge.data,
            "token_preflight_required_tokens": 2500,
        }

    monkeypatch.setattr(usage_mod, "TokenUsageGuard", lambda: _AllowingGuard())

    middleware = usage_mod.MozaiksUsageMiddleware(
        event=SimpleNamespace(),
        context=SimpleNamespace(),
        agent_name="PlannerAgent",
        workflow_name="AppGenerator",
        context_variables=_BudgetContext(),
        model_name="gpt-test",
    )

    async def call_next(events, context):
        return SimpleNamespace(id="response-1", usage=None)

    await middleware.on_llm_call(call_next, [], SimpleNamespace())

    assert seen["required_tokens"] == 2500


@pytest.mark.asyncio
@pytest.mark.parametrize("cache_read", [None, 0.0], ids=["cache-not-reported", "cache-measured-zero"])
async def test_ag2_usage_middleware_receipt_for_ag2_anthropic_usage_shape(monkeypatch, cache_read):
    """AG2-WP-014: the AG2 1.1 Anthropic usage shape gives the same receipt.

    ``ag2.config.anthropic.mappers.normalize_usage`` reports a cache read the
    provider measured as zero as ``0`` instead of ``None``, and fills
    ``thinking_tokens`` as a part of ``completion_tokens``, not an addition to it.
    That mapper imports the Anthropic SDK, which CI does not install, so this
    builds the ``Usage`` it returns for input 10, output 7 (3 of them thinking).
    """
    from ag2.events.types import Usage

    emitted = {}

    async def fake_emit_usage_delta(**payload):
        emitted.update(payload)

    monkeypatch.setattr(usage_mod.TokenManager, "emit_usage_delta", fake_emit_usage_delta)
    middleware = usage_mod.MozaiksUsageMiddleware(
        event=SimpleNamespace(),
        context=SimpleNamespace(),
        agent_name="PlannerAgent",
        workflow_name="AppGenerator",
        context_variables=dict(_ContextBridge.data),
        model_name="claude-test",
    )
    usage = Usage(
        prompt_tokens=10.0,
        completion_tokens=7.0,
        total_tokens=17.0,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=None,
        thinking_tokens=3.0,
    )

    async def call_next(events, context):
        return SimpleNamespace(id="response-1", model="claude-test", usage=usage)

    await middleware.on_llm_call(call_next, [], SimpleNamespace())

    assert emitted["prompt_tokens"] == 10
    assert emitted["completion_tokens"] == 7
    assert emitted["total_tokens"] == 17
    assert emitted["cached"] is False
    assert emitted["cached_tokens"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai-chat", "openai-responses", "gemini"])
@pytest.mark.parametrize("cache_read", [0, 2])
async def test_native_provider_usage_keeps_thinking_and_cache_writes_distinct(monkeypatch, provider, cache_read):
    """AG2-normalized usage reaches the receipt without double counting or a cache-write discount."""
    from ag2.events import ModelResponse

    if provider == "openai-chat":
        from ag2.config.openai.mappers import normalize_usage
        from openai.types.completion_usage import CompletionUsage

        usage = normalize_usage(CompletionUsage(
            prompt_tokens=12, completion_tokens=8, total_tokens=20,
            prompt_tokens_details={"cached_tokens": cache_read, "cache_write_tokens": 4},
            completion_tokens_details={"reasoning_tokens": 3},
        ))
    elif provider == "openai-responses":
        from ag2.config.openai.mappers import normalize_responses_usage
        from openai.types.responses.response_usage import ResponseUsage

        usage = normalize_responses_usage(ResponseUsage(
            input_tokens=12, output_tokens=8, total_tokens=20,
            input_tokens_details={"cached_tokens": cache_read, "cache_write_tokens": 4},
            output_tokens_details={"reasoning_tokens": 3},
        ))
    else:
        pytest.importorskip("google.genai", reason="ag2[gemini] extra not installed")
        from ag2.config.gemini.mappers import normalize_usage
        from google.genai.types import GenerateContentResponseUsageMetadata

        usage = normalize_usage(GenerateContentResponseUsageMetadata(
            prompt_token_count=12, candidates_token_count=5, thoughts_token_count=3,
            total_token_count=20, cached_content_token_count=cache_read,
        ))

    if provider.startswith("openai"):
        assert usage.cache_creation_input_tokens == 4
    assert usage.thinking_tokens == 3
    emit = AsyncMock()
    monkeypatch.setattr(usage_mod.TokenManager, "emit_usage_delta", emit)
    monkeypatch.setattr(usage_mod.TokenUsageGuard, "check_or_raise", AsyncMock())
    middleware = usage_mod.MozaiksUsageMiddleware(
        event=SimpleNamespace(), context=SimpleNamespace(), agent_name="PlannerAgent",
        workflow_name="AppGenerator", context_variables=dict(_ContextBridge.data),
    )
    response = ModelResponse(usage=usage, model=provider)
    assert await middleware.on_llm_call(AsyncMock(return_value=response), [], SimpleNamespace()) is response

    emit.assert_awaited_once()
    receipt = emit.await_args.kwargs
    assert (receipt["prompt_tokens"], receipt["completion_tokens"], receipt["total_tokens"]) == (12, 8, 20)
    assert receipt["cached_tokens"] == cache_read
    assert receipt["cached"] is bool(cache_read)


@pytest.mark.asyncio
async def test_native_anthropic_continuation_emits_one_aggregated_receipt(monkeypatch):
    pytest.importorskip("anthropic", reason="ag2[anthropic] extra not installed")
    import httpx2
    from ag2 import Agent
    from ag2.config import AnthropicConfig

    requests = []

    def respond(request):
        assert request.url.host == "provider.invalid"
        requests.append(request)
        first = len(requests) == 1
        return httpx2.Response(200, json={
            "id": f"message-{len(requests)}", "type": "message", "role": "assistant",
            "model": "claude-test", "content": [{"type": "text", "text": "Working" if first else "Done"}],
            "stop_reason": "pause_turn" if first else "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10 if first else 13, "output_tokens": 2 if first else 5},
        })

    emit = AsyncMock()
    monkeypatch.setattr(usage_mod.TokenManager, "emit_usage_delta", emit)
    monkeypatch.setattr(usage_mod.TokenUsageGuard, "check_or_raise", AsyncMock())
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        agent = Agent(
            "PlannerAgent",
            config=AnthropicConfig(
                model="claude-test", api_key="test", base_url="https://provider.invalid",
                http_client=http, streaming=False, max_retries=0,
            ),
            middleware=[usage_mod.build_ag2_usage_middleware(
                agent_name="PlannerAgent", workflow_name="AppGenerator",
                context_variables=dict(_ContextBridge.data),
            )],
        )
        reply = await agent.ask("Complete the task.")

    assert reply.body == "Done"
    assert len(requests) == 2
    emit.assert_awaited_once()
    receipt = emit.await_args.kwargs
    assert (receipt["prompt_tokens"], receipt["completion_tokens"], receipt["total_tokens"]) == (23, 7, 30)
    assert receipt["cached_tokens"] == 0
