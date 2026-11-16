"""Provider configuration limits must survive the AG2 adapter boundary."""

from __future__ import annotations

import json
import warnings
from copy import deepcopy

import httpx
import pytest

from mozaiksai.core.adapters.llm_fallback import (
    close_ag2_session_http_client,
    llm_config_to_ag2_config,
)


@pytest.mark.parametrize(
    ("provider", "responses", "limit", "dependency"),
    [
        ("openai", False, "max_tokens", "openai"),
        ("openai", False, "max_completion_tokens", "openai"),
        ("openai", True, "max_output_tokens", "openai"),
        ("anthropic", False, "max_tokens", "anthropic"),
        ("google", False, "max_output_tokens", "google.genai"),
        ("ollama", False, "max_tokens", "ollama"),
    ],
)
@pytest.mark.parametrize("location", ["shared", "provider"])
def test_output_limit_reaches_real_ag2_config(provider, responses, limit, dependency, location):
    pytest.importorskip(dependency)
    source = {
        "config_list": [{"api_type": provider, "model": "test-model", "api_key": "test"}],
        "use_responses_api": responses,
        "streaming": False,
    }
    target = source if location == "shared" else source["config_list"][0]
    target[limit] = 128
    original = deepcopy(source)

    config = llm_config_to_ag2_config(source)

    assert getattr(config, limit) == 128
    assert source == original


@pytest.mark.parametrize("provider,responses", [("openai", False), ("openai", True), ("anthropic", False)])
def test_provider_retry_override_preserves_zero(provider, responses):
    pytest.importorskip(provider)
    config = llm_config_to_ag2_config({
        "config_list": [{"api_type": provider, "model": "test", "max_retries": 0}],
        "use_responses_api": responses,
        "max_retries": 4,
    })

    assert config.max_retries == 0


def test_provider_limit_overrides_shared_default_without_mutation():
    source = {
        "config_list": [{"model": "test", "max_completion_tokens": 128}],
        "max_completion_tokens": 512,
    }
    original = deepcopy(source)
    assert llm_config_to_ag2_config(source).max_completion_tokens == 128
    assert source == original


def test_gemini_thinking_level_preserves_provider_precedence_without_mutation():
    source = {
        "config_list": [{"api_type": "google", "model": "gemini-3.8-flash", "thinking_level": "medium"}],
        "thinking_level": "low",
        "max_output_tokens": 8192,
    }
    original = deepcopy(source)
    config = llm_config_to_ag2_config(source)
    assert config.thinking_level == "medium"
    assert config.max_output_tokens == 8192
    assert source == original


@pytest.mark.parametrize(
    "provider,responses,key",
    [
        ("openai", False, "max_output_tokens"),
        ("openai", True, "max_completion_tokens"),
        ("openai", True, "max_tokens"),
        ("anthropic", False, "max_output_tokens"),
        ("google", False, "max_tokens"),
        ("google", False, "max_retries"),
        ("ollama", False, "max_retries"),
    ],
)
def test_unsupported_limit_is_not_silently_discarded(provider, responses, key):
    with pytest.raises(ValueError, match=key):
        llm_config_to_ag2_config({
            "config_list": [{"api_type": provider, "model": "test"}],
            "use_responses_api": responses,
            key: 1,
        })


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "128", None])
def test_invalid_output_limit_fails_before_client_creation(value):
    with pytest.raises(ValueError, match="max_completion_tokens"):
        llm_config_to_ag2_config({
            "config_list": [{"model": "test"}],
            "max_completion_tokens": value,
        })


@pytest.mark.parametrize("value", [-1, True, 1.5, "0", None])
def test_invalid_retry_limit_fails_before_client_creation(value):
    with pytest.raises(ValueError, match="max_retries"):
        llm_config_to_ag2_config({"config_list": [{"model": "test"}], "max_retries": value})


def test_conflicting_output_limits_are_rejected():
    with pytest.raises(ValueError, match="one output token limit"):
        llm_config_to_ag2_config({
            "config_list": [{"model": "test", "max_tokens": 128}],
            "max_completion_tokens": 256,
        })


def test_unconfigured_limits_keep_ag2_defaults():
    from ag2.config import OpenAICompletionsConfig

    actual = llm_config_to_ag2_config({"config_list": [{"model": "test"}]})
    defaults = OpenAICompletionsConfig(model="test")
    assert actual.max_retries == defaults.max_retries
    assert actual.max_completion_tokens == defaults.max_completion_tokens
    assert actual.max_tokens == defaults.max_tokens


@pytest.mark.asyncio
async def test_gemini_shared_timeout_uses_httpx_seconds_and_preserves_client_ownership():
    pytest.importorskip("google.genai")
    caller_client = httpx.AsyncClient(timeout=23.0)
    actual = None
    try:
        actual = llm_config_to_ag2_config({
            "config_list": [{"api_type": "google", "model": "gemini-test"}],
            "timeout": 11,
        })
        assert actual.http_client.timeout.read == 11

        supplied = llm_config_to_ag2_config({
            "config_list": [{"api_type": "google", "model": "gemini-test"}],
            "timeout": 11,
            "http_client": caller_client,
        })
        assert supplied.http_client is not caller_client
        assert supplied.http_client.timeout.read == 11
        default_request = supplied.http_client.build_request("GET", "https://provider.invalid")
        explicit_request = supplied.http_client.build_request(
            "GET", "https://provider.invalid", timeout=2.5,
        )
        assert default_request.extensions["timeout"]["read"] == 11
        assert explicit_request.extensions["timeout"]["read"] == 2.5
    finally:
        if actual is not None:
            await actual.http_client.aclose()
        if "supplied" in locals():
            await close_ag2_session_http_client(supplied.http_client)
            assert not caller_client.is_closed
        await caller_client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("thinking_level", [None, "low"])
async def test_real_ag2_gemini_streaming_and_nonstreaming_use_finite_timeout(streaming, thinking_level):
    from ag2 import Agent

    requests = []
    payload = {
        "candidates": [{
            "content": {"role": "model", "parts": [{"text": "Complete."}]},
            "finishReason": "STOP",
        }],
        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
    }

    def respond(request):
        requests.append(request)
        expected_path = ":streamGenerateContent" if streaming else ":generateContent"
        assert expected_path in request.url.path
        assert request.extensions["timeout"]["read"] == 13.0
        if streaming:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=f"data: {json.dumps(payload)}\n\n".encode(),
            )
        return httpx.Response(200, json=payload)

    external_client = httpx.AsyncClient(
        transport=httpx.MockTransport(respond), timeout=7.0,
    )
    config = llm_config_to_ag2_config({
        "config_list": [{
            "api_type": "google", "model": "gemini-test", "api_key": "fake-gemini-key",
        }],
        "timeout": 13,
        "streaming": streaming,
        "thinking_level": thinking_level,
        "max_output_tokens": 8192,
        "http_client": external_client,
    })
    agent = Agent("gemini-timeout", config=config)
    try:
        for _ in range(2):
            async with agent.run("Complete the task.") as run:
                result = await run.result()
            assert result
        assert len(requests) == 2
        assert requests[0].extensions["timeout"]["read"] == 13.0
        assert requests[1].extensions["timeout"]["read"] == 13.0
        body = json.loads(requests[0].content)
        assert body["generationConfig"]["maxOutputTokens"] == 8192
        assert not body.get("tools")
        assert not body["generationConfig"].get("responseModalities")
        if thinking_level is None:
            assert "thinkingConfig" not in body["generationConfig"]
        else:
            assert body["generationConfig"]["thinkingConfig"]["thinking_level"] == "LOW"
    finally:
        await close_ag2_session_http_client(config.http_client)
        assert not external_client.is_closed
        await external_client.aclose()


@pytest.mark.parametrize("timeout", [0, -1, True, float("inf"), float("nan"), "10"])
def test_invalid_gemini_timeout_fails_before_client_creation(timeout):
    with pytest.raises(ValueError, match="timeout must be a finite positive number"):
        llm_config_to_ag2_config({
            "config_list": [{"api_type": "google", "model": "gemini-test"}],
            "timeout": timeout,
        })


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["max_tokens", "max_completion_tokens"])
async def test_limit_reaches_provider_http_through_real_ag2_agent(limit):
    from ag2 import Agent

    requests = []

    def respond(request):
        assert request.url.host == "provider.invalid"
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json={
            "id": "bounded-completion",
            "object": "chat.completion",
            "created": 1,
            "model": "test-model",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "Complete."},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        config = llm_config_to_ag2_config({
            "config_list": [{"model": "test-model", "api_key": "test", "base_url": "https://provider.invalid/v1"}],
            "streaming": False,
            limit: 128,
            "max_retries": 0,
        }).copy(http_client=http)
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            async with Agent("bounded", config=config).run("Complete the task.") as run:
                await run.result()

    assert len(requests) == 1
    assert requests[0][limit] == 128


@pytest.mark.asyncio
async def test_anthropic_shared_temperature_is_omitted_from_provider_request():
    pytest.importorskip("anthropic")
    import httpx2
    from ag2 import Agent

    requests = []

    def respond(request):
        assert request.url.host == "provider.invalid"
        assert request.url.path == "/v1/messages"
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json={
            "id": "anthropic-completion",
            "type": "message",
            "role": "assistant",
            "model": "test-model",
            "content": [{"type": "text", "text": "Complete."}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    source = {
        "config_list": [{"api_type": "anthropic", "model": "test-model", "api_key": "test"}],
        "temperature": 0.2,
        "streaming": False,
        "max_tokens": 128,
        "max_retries": 0,
    }
    original = deepcopy(source)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        config = llm_config_to_ag2_config(source).copy(
            base_url="https://provider.invalid", http_client=http,
        )
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            async with Agent("bounded", config=config).run("Complete the task.") as run:
                await run.result()

    assert source == original
    assert len(requests) == 1
    assert requests[0]["max_tokens"] == 128
    assert not {"temperature", "top_p", "top_k"}.intersection(requests[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("responses", [False, True])
async def test_disabled_sdk_retries_make_only_one_attempt(responses):
    from ag2 import Agent
    from openai import RateLimitError

    requests = []

    def respond(request):
        assert request.url.host == "provider.invalid"
        requests.append(json.loads(request.content))
        return httpx.Response(429, json={"error": {"message": "Rate limited", "type": "rate_limit_error"}})

    limit = "max_output_tokens" if responses else "max_completion_tokens"
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        config = llm_config_to_ag2_config({
            "config_list": [{"model": "test-model", "api_key": "test", "base_url": "https://provider.invalid/v1"}],
            "use_responses_api": responses,
            "streaming": False,
            limit: 128,
            "max_retries": 0,
        }).copy(http_client=http)
        with pytest.raises(RateLimitError, match="Rate limited"):
            async with Agent("bounded", config=config).run("Complete the task.") as run:
                await run.result()

    assert len(requests) == 1
    assert requests[0][limit] == 128


@pytest.mark.asyncio
async def test_luna_reasoning_and_function_tools_reach_responses_http():
    from ag2 import Agent

    requests = []

    def inspect_workspace() -> str:
        """Return the current workspace status."""
        return "ready"

    def respond(request):
        assert request.url.path == "/v1/responses"
        requests.append(json.loads(request.content))
        output = [{
            "type": "function_call", "id": "fc_1", "call_id": "call_1",
            "name": "inspect_workspace", "arguments": "{}", "status": "completed",
        }] if len(requests) == 1 else [{
            "id": "msg_1", "type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": "Ready.", "annotations": []}],
        }]
        return httpx.Response(200, json={
            "id": f"resp_{len(requests)}", "object": "response", "created_at": 1,
            "status": "completed", "model": "gpt-6-luna", "output": output,
            "usage": {
                "input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 0},
            },
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        config = llm_config_to_ag2_config({
            "config_list": [{
                "api_type": "openai", "model": "gpt-6-luna", "api_key": "test",
                "base_url": "https://provider.invalid/v1", "use_responses_api": True,
                "reasoning": {"effort": "medium"},
            }],
            "streaming": False, "max_output_tokens": 128, "max_retries": 0,
        }).copy(http_client=http)
        async with Agent("refiner", config=config, tools=[inspect_workspace]).run("Inspect the workspace.") as run:
            await run.result()

    assert len(requests) == 2
    assert all(body["reasoning"] == {"effort": "medium"} for body in requests)
    assert all(body["max_output_tokens"] == 128 for body in requests)
    assert requests[0]["tools"][0]["name"] == "inspect_workspace"
    assert any(item.get("type") == "function_call_output" and item.get("output") == "ready" for item in requests[1]["input"])
    assert all("temperature" not in body for body in requests)
