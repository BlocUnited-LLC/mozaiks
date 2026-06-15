from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from mozaiksai.core.tokens.guard import TokenUsageDecision, TokenUsageDenied

usage_mod = importlib.import_module("mozaiksai.core.usage.middleware")


class _ContextBridge:
    data = {
        "app_id": "app-1",
        "chat_id": "chat-1",
        "user_id": "user-1",
        "workflow_name": "AppGenerator",
    }

    def get(self, key, default=None):
        return self.data.get(key, default)


@pytest.mark.asyncio
async def test_ag2_usage_middleware_emits_usage_delta(monkeypatch):
    emitted = {}

    async def fake_emit_usage_delta(**payload):
        emitted.update(payload)

    monkeypatch.setattr(usage_mod.TokenManager, "emit_usage_delta", fake_emit_usage_delta)

    middleware = usage_mod.MozaiksUsageMiddleware(
        event=SimpleNamespace(),
        context=SimpleNamespace(),
        agent_name="PlannerAgent",
        workflow_name="AppGenerator",
        context_variables=_ContextBridge(),
        model_name="gpt-test",
    )

    async def call_next(events, context):
        return SimpleNamespace(
            id="response-1",
            model="gpt-test",
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=8, total_tokens=20),
        )

    response = await middleware.on_llm_call(call_next, [], SimpleNamespace())

    assert response.id == "response-1"
    assert emitted["app_id"] == "app-1"
    assert emitted["chat_id"] == "chat-1"
    assert emitted["user_id"] == "user-1"
    assert emitted["workflow_name"] == "AppGenerator"
    assert emitted["agent_name"] == "PlannerAgent"
    assert emitted["model_name"] == "gpt-test"
    assert emitted["prompt_tokens"] == 12
    assert emitted["completion_tokens"] == 8
    assert emitted["total_tokens"] == 20


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
