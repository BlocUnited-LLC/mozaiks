from __future__ import annotations

import asyncio
import json
import sys

import httpx
import pytest
from openai import AsyncOpenAI

from scripts.journey_provider_meter import (
    Limits,
    ProviderGateway,
    main,
    make_app,
    pricing_rates,
    reconcile_attempts,
)

MODEL = "gpt-4o-mini-2024-07-18"
USAGE = {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110,
         "prompt_tokens_details": {"cached_tokens": 20}}


def limits(*, attempts=2, budget=1.0):
    return Limits(MODEL, attempts, 100, budget, 0.15, 0.075, 0.60)


def body(**changes):
    return {"model": MODEL, "max_completion_tokens": 100,
            "messages": [{"role": "user", "content": "SYNTHETIC_PRIVATE_PROMPT"}], **changes}


def event_records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.asyncio
async def test_gateway_forwards_only_approved_call_and_records_no_secrets(tmp_path):
    async def upstream(request):
        assert str(request.url) == "https://api.openai.com/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer UPSTREAM_SECRET"
        assert json.loads(request.content)["messages"][0]["content"] == "SYNTHETIC_PRIVATE_PROMPT"
        return httpx.Response(200, json={"model": MODEL, "usage": USAGE, "choices": [{"message": {"content": "PRIVATE_RESPONSE"}}]})

    journal = tmp_path / "usage.jsonl"
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as provider:
        gateway = ProviderGateway(limits=limits(attempts=1), token="LOCAL_SECRET", upstream_key="UPSTREAM_SECRET",
                                  client=provider, journal=journal)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app(gateway)), base_url="http://test") as caller:
            result = await caller.post("/v1/chat/completions", json=body(), headers={"authorization": "Bearer LOCAL_SECRET"})
            denied = await caller.post("/v1/chat/completions", json=body(), headers={"authorization": "Bearer LOCAL_SECRET"})
    assert result.status_code == 200 and denied.status_code == 429
    records = event_records(journal)
    assert [row["phase"] for row in records] == ["attempt", "result"]
    assert records[-1]["usage"] == USAGE
    assert records[-1]["estimated_usd"] == pytest.approx(0.0000195)
    text = journal.read_text(encoding="utf-8")
    assert all(secret not in text for secret in ("UPSTREAM_SECRET", "LOCAL_SECRET", "SYNTHETIC_PRIVATE_PROMPT", "PRIVATE_RESPONSE"))


@pytest.mark.asyncio
async def test_actual_openai_sdk_uses_gateway_without_retries(tmp_path):
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json={
            "id": "chatcmpl_synthetic", "object": "chat.completion", "created": 1,
            "model": MODEL, "usage": USAGE,
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
        })
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as provider:
        gateway = ProviderGateway(limits=limits(), token="local", upstream_key="upstream", client=provider,
                                  journal=tmp_path / "journal")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app(gateway))) as local_http:
            sdk = AsyncOpenAI(api_key="local", base_url="http://gateway/v1", http_client=local_http, max_retries=0)
            response = await sdk.chat.completions.create(
                model=MODEL, messages=[{"role": "user", "content": "synthetic"}], max_completion_tokens=100,
            )
    assert response.usage.prompt_tokens == 100
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_actual_openai_sdk_reads_buffered_stream(tmp_path):
    calls = []
    def upstream(request):
        calls.append(request)
        content = (
            f'data: {{"id":"synthetic","object":"chat.completion.chunk","created":1,"model":"{MODEL}",'
            '"choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":null}]}\n\n'
            f'data: {json.dumps({"id": "synthetic", "object": "chat.completion.chunk", "created": 1, "model": MODEL, "choices": [], "usage": USAGE})}\n\n'
            'data: [DONE]\n\n'
        )
        return httpx.Response(200, content=content, headers={"content-type": "text/event-stream"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as provider:
        gateway = ProviderGateway(limits=limits(), token="local", upstream_key="upstream", client=provider,
                                  journal=tmp_path / "journal")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app(gateway))) as local_http:
            sdk = AsyncOpenAI(api_key="local", base_url="http://gateway/v1", http_client=local_http, max_retries=0)
            stream = await sdk.chat.completions.create(
                model=MODEL, messages=[{"role": "user", "content": "synthetic"}], max_completion_tokens=100,
                stream=True,
            )
            chunks = [chunk async for chunk in stream]
    assert len(calls) == 1 and chunks[0].choices[0].delta.content == "ok"
    assert chunks[-1].usage.prompt_tokens == 100


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"stream": "true"}, {"model": "other"}, {"n": 2}, {"n": True}, {"max_completion_tokens": 101},
    {"max_tokens": 100},
    {"max_completion_tokens": None}, {"tools": [{"type": "web_search"}]},
    {"service_tier": "priority"}, {"modalities": ["audio"]}, {"audio": {}},
    {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://invalid"}}]}]},
])
async def test_unsupported_requests_do_not_reach_upstream(tmp_path, changes):
    calls = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: calls.append(request))) as provider:
        gateway = ProviderGateway(limits=limits(), token="local", upstream_key="upstream", client=provider,
                                  journal=tmp_path / "journal")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app(gateway)), base_url="http://test") as caller:
            result = await caller.post("/v1/chat/completions", json=body(**changes), headers={"authorization": "Bearer local"})
            unknown = await caller.post("/v1/responses", json=body(), headers={"authorization": "Bearer local"})
    assert result.status_code == 400 and unknown.status_code == 404 and not calls
    assert (tmp_path / "journal").read_text(encoding="utf-8") == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing_usage", "http_error", "network", "wrong_model"])
async def test_unknown_provider_outcome_closes_gate(tmp_path, failure):
    calls = []
    def upstream(request):
        calls.append(request)
        if failure == "network":
            raise httpx.ConnectError("sensitive exception")
        return httpx.Response(500 if failure == "http_error" else 200, json={
            "model": "wrong" if failure == "wrong_model" else MODEL,
            "usage": {} if failure == "missing_usage" else USAGE,
        })
    journal = tmp_path / "journal"
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as provider:
        gateway = ProviderGateway(limits=limits(), token="local", upstream_key="upstream", client=provider, journal=journal)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app(gateway)), base_url="http://test") as caller:
            first = await caller.post("/v1/chat/completions", json=body(), headers={"authorization": "Bearer local"})
            second = await caller.post("/v1/chat/completions", json=body(), headers={"authorization": "Bearer local"})
    assert first.status_code == 502 and second.status_code == 429 and len(calls) == 1
    assert event_records(journal)[-1]["outcome"] == "unknown"
    assert "sensitive exception" not in journal.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_parallel_calls_are_queued_and_attempts_remain_bounded(tmp_path):
    started, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def upstream(request):
        calls.append(request)
        started.set()
        await release.wait()
        return httpx.Response(200, json={"model": MODEL, "usage": USAGE})
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as provider:
        gateway = ProviderGateway(limits=limits(), token="local", upstream_key="upstream", client=provider,
                                  journal=tmp_path / "journal")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app(gateway)), base_url="http://test") as caller:
            first = asyncio.create_task(caller.post("/v1/chat/completions", json=body(), headers={"authorization": "Bearer local"}))
            await started.wait()
            parallel = asyncio.create_task(caller.post("/v1/chat/completions", json=body(), headers={"authorization": "Bearer local"}))
            await asyncio.sleep(0)
            assert not parallel.done() and len(calls) == 1
            release.set()
            assert (await first).status_code == 200
            assert (await parallel).status_code == 200
            later = await caller.post("/v1/chat/completions", json=body(), headers={"authorization": "Bearer local"})
    assert later.status_code == 429 and len(calls) == 2


@pytest.mark.asyncio
async def test_budget_stops_next_call_after_completed_response(tmp_path):
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json={"model": MODEL, "usage": USAGE})
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as provider:
        gateway = ProviderGateway(limits=limits(budget=0.00001), token="local", upstream_key="upstream", client=provider,
                                  journal=tmp_path / "journal")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app(gateway)), base_url="http://test") as caller:
            assert (await caller.post("/v1/chat/completions", json=body(), headers={"authorization": "Bearer local"})).status_code == 200
            assert (await caller.post("/v1/chat/completions", json=body(), headers={"authorization": "Bearer local"})).status_code == 429
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["valid", "missing_usage", "missing_done", "wrong_model"])
async def test_streaming_requires_terminal_usage_and_done(tmp_path, mode):
    calls = []
    def upstream(request):
        calls.append(request)
        assert json.loads(request.content)["stream_options"] == {"include_usage": True}
        chunk = {"model": "wrong" if mode == "wrong_model" else MODEL, "choices": [{"delta": {"content": "PRIVATE"}}]}
        usage = {"model": MODEL, "usage": USAGE, "choices": []}
        parts = [f"data: {json.dumps(chunk)}\n\n"]
        if mode != "missing_usage":
            parts.append(f"data: {json.dumps(usage)}\n\n")
        if mode != "missing_done":
            parts.append("data: [DONE]\n\n")
        return httpx.Response(200, content="".join(parts), headers={"content-type": "text/event-stream"})
    journal = tmp_path / "journal"
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as provider:
        gateway = ProviderGateway(limits=limits(), token="local", upstream_key="upstream", client=provider, journal=journal)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app(gateway)), base_url="http://test") as caller:
            first = await caller.post("/v1/chat/completions", json=body(stream=True), headers={"authorization": "Bearer local"})
            if mode != "valid":
                second = await caller.post("/v1/chat/completions", json=body(stream=True), headers={"authorization": "Bearer local"})
                assert second.status_code == 429
    assert first.status_code == (200 if mode == "valid" else 502)
    assert len(calls) == 1 and "PRIVATE" not in journal.read_text(encoding="utf-8")
    if mode == "missing_done":
        assert event_records(journal)[-1]["partial_usage"]["prompt_tokens"] == 100


@pytest.mark.asyncio
async def test_cancelled_provider_attempt_is_journaled_unknown_and_gate_stops(tmp_path):
    started = asyncio.Event()
    async def upstream(request):
        started.set()
        await asyncio.Event().wait()
    journal = tmp_path / "journal"
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as provider:
        gateway = ProviderGateway(limits=limits(), token="local", upstream_key="upstream", client=provider, journal=journal)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app(gateway)), base_url="http://test") as caller:
            task = asyncio.create_task(caller.post("/v1/chat/completions", json=body(), headers={"authorization": "Bearer local"}))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert (await caller.post("/v1/chat/completions", json=body(), headers={"authorization": "Bearer local"})).status_code == 429
    assert event_records(journal)[-1]["outcome"] == "unknown"


def test_reconciliation_refuses_missing_extra_ambiguous_and_unknown():
    attempt = {"phase": "attempt", "attempt": 1, "model": MODEL}
    result = {"phase": "result", "attempt": 1, "model": MODEL, "outcome": "completed", "usage": USAGE}
    receipt = {"event_id": "one", "app_id": "app", "user_id": "user", "chat_id": "chat",
               "model_name": MODEL, "prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110,
               "cached_prompt_tokens": 20}
    scope = {"app_id": "app", "user_id": "user", "chat_ids": {"chat"}}
    assert reconcile_attempts([attempt, result], [receipt], **scope)["reconciled"]
    assert not reconcile_attempts([], [], **scope)["reconciled"]
    assert not reconcile_attempts([attempt], [], **scope)["reconciled"]
    assert not reconcile_attempts([result, attempt], [receipt], **scope)["reconciled"]
    assert not reconcile_attempts([attempt, result], [], **scope)["reconciled"]
    assert not reconcile_attempts([attempt, result], [receipt, receipt], **scope)["reconciled"]
    assert not reconcile_attempts([attempt, result], [{**receipt, "user_id": "wrong"}], **scope)["reconciled"]
    auxiliary = {**receipt, "chat_id": None, "execution_kind": "auxiliary"}
    assert not reconcile_attempts([attempt, result], [auxiliary], **scope)["reconciled"]
    assert reconcile_attempts([attempt, result], [auxiliary], **scope, allow_isolated_chatless_auxiliary=True)["reconciled"]
    chatless_scope = {**scope, "chat_ids": set(), "allow_isolated_chatless_auxiliary": True}
    assert reconcile_attempts([attempt, result], [auxiliary], **chatless_scope)["reconciled"]
    assert not reconcile_attempts([attempt, result], [receipt], **chatless_scope)["reconciled"]
    assert not reconcile_attempts([attempt, result], [{**auxiliary, "execution_kind": "workflow"}],
                                  **scope, allow_isolated_chatless_auxiliary=True)["reconciled"]
    other_attempt, other_result = {**attempt, "attempt": 2}, {**result, "attempt": 2}
    assert not reconcile_attempts([attempt, result, other_attempt, other_result], [receipt, {**receipt, "event_id": "two"}], **scope)["reconciled"]


def test_journal_must_be_created_exclusively(tmp_path):
    path = tmp_path / "journal"
    path.write_text("previous evidence", encoding="utf-8")
    with pytest.raises(FileExistsError):
        ProviderGateway(limits=limits(), token="local", upstream_key="upstream", client=httpx.AsyncClient(), journal=path)
    assert path.read_text(encoding="utf-8") == "previous evidence"


@pytest.mark.parametrize("mode", ["complete", "unknown", "killed", "invalid"])
def test_reconcile_cli_reports_known_subtotal_and_never_guesses_unknown(
    tmp_path, monkeypatch, capsys, mode,
):
    pricing = tmp_path / "prices.json"
    pricing.write_text(json.dumps({"models": {MODEL: {
        "provider": "openai", "mode": "chat", "input_per_1m_usd": 0.15,
        "cached_input_per_1m_usd": 0.075, "output_per_1m_usd": 0.60,
    }}}), encoding="utf-8")
    _, price_hash = pricing_rates(pricing, MODEL)
    records = [{"phase": "attempt", "attempt": 1, "model": MODEL, "pricing_sha256": price_hash},
               {"phase": "result", "attempt": 1, "model": MODEL, "outcome": "completed", "usage": USAGE}]
    if mode in {"unknown", "killed"}:
        records.append({"phase": "attempt", "attempt": 2, "model": MODEL, "pricing_sha256": price_hash})
        if mode == "unknown":
            records.append({"phase": "result", "attempt": 2, "model": MODEL, "outcome": "unknown"})
    elif mode == "invalid":
        records.append({"phase": "result", "attempt": 1, "model": MODEL, "outcome": "completed", "usage": USAGE})
    journal = tmp_path / "journal.jsonl"
    journal.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"events": [{"event_id": "one", "app_id": "app", "user_id": "user",
        "chat_id": "chat", "workflow_name": "DesignDocs", "model_name": MODEL,
        "prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110, "cached_prompt_tokens": 20}]}),
        encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["journey_provider_meter", "reconcile", "--model", MODEL,
        "--pricing-file", str(pricing), "--journal", str(journal), "--ledger", str(ledger),
        "--app-id", "app", "--user-id", "user", "--chat-id", "chat"])
    with pytest.raises(SystemExit) as result:
        main()
    report = json.loads(capsys.readouterr().out)
    assert result.value.code == (0 if mode == "complete" else 1)
    assert report["pricing_sha256"] == price_hash
    assert report["known_estimated_usd"] == (None if mode == "invalid" else pytest.approx(0.0000195))
    assert report["complete_estimated_usd"] == (pytest.approx(0.0000195) if mode == "complete" else None)
    assert report["receipt_stage_observations"]["DesignDocs"]["cached_prompt_tokens"] == 20
