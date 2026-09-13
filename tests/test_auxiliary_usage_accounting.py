"""Real AG2 correction turns, with HTTP and persistence isolated from services."""
from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from mozaiksai.core.adapters import ag2_agent_runner as runner_mod
from mozaiksai.core.adapters.llm_fallback import llm_config_to_ag2_config
from mozaiksai.core.events import unified_event_dispatcher as dispatcher_mod
from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig
from mozaiksai.core.tokens.guard import TokenUsageDecision, TokenUsageDenied
from mozaiksai.core.tokens.usage_ingest import TokenWalletUsageIngestClient
from mozaiksai.core.tokens.wallet import TokenWalletLedger
from mozaiksai.core.usage.context import AuxiliaryUsageContext
from mozaiksai.core.usage.ledger import RuntimeUsageLedger, summarize_usage_events


class _Output(BaseModel):
    status: str


@pytest.fixture
def accounting(monkeypatch):
    receipts = []
    collection = SimpleNamespace(update_one=AsyncMock())
    ledger = RuntimeUsageLedger()
    monkeypatch.setattr(ledger, "_coll", AsyncMock(return_value=collection))
    wallet = TokenWalletLedger()
    monkeypatch.setattr(wallet, "debit", AsyncMock())
    monkeypatch.setattr(wallet, "ensure_plan_allowances", AsyncMock())
    ingest = TokenWalletUsageIngestClient(ledger=wallet)
    ingest._config_cache = SubscriptionsConfig.model_validate({
        "schema_version": "mozaiks.subscriptions.v1", "label": "Test", "default_plan_id": "test",
        "plans": [{"plan_id": "test", "label": "Test", "capabilities": []}],
        "token_wallets": [{"wallet_id": "tokens", "scope": "user", "auto_debit_usage": True}],
    })
    monkeypatch.setattr(
        "mozaiksai.core.tokens.usage_ingest.ConfiguredEntitlementAdapter.current_plan_id",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr("mozaiksai.core.usage.get_runtime_usage_ledger", lambda: ledger)
    monkeypatch.setattr(
        "mozaiksai.core.tokens.usage_ingest.get_token_wallet_usage_ingest_client", lambda: ingest,
    )
    dispatcher = dispatcher_mod.UnifiedEventDispatcher()
    dispatcher.register_handler("chat.usage_delta", receipts.append)
    monkeypatch.setattr(dispatcher_mod, "get_event_dispatcher", lambda: dispatcher)
    guard = AsyncMock()
    monkeypatch.setattr("mozaiksai.core.usage.middleware.TokenUsageGuard.check_or_raise", guard)
    monkeypatch.setenv("USAGE_EVENTS_ENABLED", "true")
    return SimpleNamespace(
        receipts=receipts, collection=collection, wallet=wallet, guard=guard,
        ledger=ledger, dispatcher=dispatcher,
    )


async def _run_http(monkeypatch, replies, *, context=None, retry_count=0, schema_retries=1, flat=False):
    requests = []

    def respond(request):
        assert request.url.host == "provider.invalid"
        body = json.loads(request.content)
        requests.append(body)
        assert body["max_completion_tokens"] == 128
        assert body.get("stream", False) is False
        reply = replies[len(requests) - 1]
        if isinstance(reply, int):
            return httpx.Response(reply, json={"error": {"message": "test failure", "type": "server_error"}})
        content, usage = reply
        payload = {
            "id": "http-response", "object": "chat.completion", "created": 1, "model": "test-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        }
        if usage is not None:
            payload["usage"] = usage
        return httpx.Response(200, json=payload)

    config = {
        "config_list": [{"model": "test-model", "api_key": "test", "base_url": "https://provider.invalid/v1"}],
        "streaming": False, "max_completion_tokens": 128, "max_retries": 0, "timeout": 3,
    }
    if flat:
        config = {**config.pop("config_list")[0], **config}
    original = deepcopy(config)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        def convert(source):
            converted = llm_config_to_ag2_config(source)
            assert converted.max_retries == 0
            assert converted.timeout == 3
            return converted.copy(http_client=http)
        monkeypatch.setattr(runner_mod, "llm_config_to_ag2_config", convert)
        result = await runner_mod.AG2StructuredAgentRunner().run(
            agent_name="ChangeClassifier", system_prompt="Return status.", user_prompt="Classify.",
            llm_config=config, response_schema=_Output,
            usage_context=context or AuxiliaryUsageContext(
                app_id="factory", user_id="owner", tenant_id="tenant", workspace_id="workspace",
            ),
            retry_count=retry_count, schema_validation_retries=schema_retries,
        )
    assert config == original
    return result, requests


_USAGE = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


@pytest.mark.asyncio
@pytest.mark.parametrize("flat", [False, True])
@pytest.mark.parametrize("correction", [False, True])
async def test_native_responses_reach_collector_and_debit_with_same_identity(monkeypatch, accounting, flat, correction):
    replies = [('{"status":"ok"}', _USAGE)]
    if correction:
        replies.insert(0, ('{"wrong":"schema"}', _USAGE))
    result, requests = await _run_http(monkeypatch, replies, flat=flat)
    assert result.status == "ok"
    count = 2 if correction else 1
    assert len(requests) == len(accounting.receipts) == accounting.guard.await_count == count
    assert accounting.collection.update_one.await_count == accounting.wallet.debit.await_count == count
    assert len({r["event_id"] for r in accounting.receipts}) == count
    for index, receipt in enumerate(accounting.receipts):
        assert receipt["execution_kind"] == "auxiliary"
        assert (receipt["app_id"], receipt["user_id"]) == ("factory", "owner")
        assert (receipt["tenant_id"], receipt["workspace_id"]) == ("tenant", "workspace")
        assert receipt["chat_id"] is receipt["workflow_name"] is receipt["build_id"] is None
        assert receipt["total_tokens"] == 15
        doc = accounting.collection.update_one.await_args_list[index].args[1]["$setOnInsert"]
        assert doc["event_id"] == receipt["event_id"]
        assert doc["execution_kind"] == "auxiliary"
        debit = accounting.wallet.debit.await_args_list[index].kwargs
        assert (debit["app_id"], debit["user_id"], debit["tenant_id"]) == ("factory", "owner", "tenant")
        assert debit["idempotency_key"] == "usage:" + receipt["event_id"]
        assert debit["usage_event_id"] == receipt["event_id"]
        assert debit["amount"] == 15
        assert debit["metadata"]["chat_id"] is None
    summary = summarize_usage_events(accounting.receipts)
    assert summary["totals"]["total_tokens"] == 15 * count
    assert summary["by_run"] == summary["by_workflow"] == []


@pytest.mark.asyncio
async def test_schema_exhaustion_keeps_usage_from_both_returned_responses(monkeypatch, accounting):
    with pytest.raises(ValidationError):
        await _run_http(monkeypatch, [('{}', _USAGE), ('{}', _USAGE)])
    assert len(accounting.receipts) == accounting.wallet.debit.await_count == 2


@pytest.mark.asyncio
async def test_correction_turn_retains_provider_retry_middleware(monkeypatch, accounting):
    result, requests = await _run_http(
        monkeypatch, [('{}', _USAGE), 503, ('{"status":"ok"}', _USAGE)], retry_count=1,
    )
    assert result.status == "ok"
    assert len(requests) == 3
    assert len(accounting.receipts) == accounting.guard.await_count == 2


@pytest.mark.asyncio
async def test_correction_guard_denial_does_not_retry_or_call_provider(monkeypatch, accounting):
    denial = TokenUsageDenied(TokenUsageDecision(allowed=False, reason="test"))
    accounting.guard.side_effect = [None, denial]
    with pytest.raises(TokenUsageDenied):
        await _run_http(monkeypatch, [('{}', _USAGE)], retry_count=2)
    assert accounting.guard.await_count == 2
    assert len(accounting.receipts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("usage", [None, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}])
async def test_unmeasured_or_zero_response_does_not_create_a_token_receipt(monkeypatch, accounting, usage):
    await _run_http(monkeypatch, [('{"status":"ok"}', usage)])
    assert accounting.receipts == []
    accounting.wallet.debit.assert_not_awaited()


@pytest.mark.asyncio
async def test_exhausted_provider_failure_does_not_invent_usage(monkeypatch, accounting):
    from openai import InternalServerError
    with pytest.raises(InternalServerError):
        await _run_http(monkeypatch, [503], retry_count=0)
    assert accounting.receipts == []


@pytest.mark.asyncio
async def test_real_optional_run_and_build_are_preserved(monkeypatch, accounting):
    from mozaiksai.core.session.build_binding import RunBuildBinding
    context = AuxiliaryUsageContext(
        app_id="factory", user_id="owner", chat_id="actual-chat", workflow_name="ActualWorkflow",
        run_build_binding=RunBuildBinding(
            target_app_id="target", build_registry_id="registry", build_id="revision", phase="refinement",
        ),
    )
    await _run_http(monkeypatch, [('{"status":"ok"}', _USAGE)], context=context)
    receipt = accounting.receipts[0]
    assert (receipt["chat_id"], receipt["workflow_name"], receipt["build_id"]) == (
        "actual-chat", "ActualWorkflow", "revision",
    )
    assert receipt["app_id"] == "factory"
