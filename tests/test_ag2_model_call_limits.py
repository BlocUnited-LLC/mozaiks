"""Provider configuration limits must survive the AG2 adapter boundary."""

from __future__ import annotations

import json
from copy import deepcopy

import httpx
import pytest

from mozaiksai.core.adapters.llm_fallback import llm_config_to_ag2_config


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
    from ag2.config import OpenAIConfig

    actual = llm_config_to_ag2_config({"config_list": [{"model": "test"}]})
    defaults = OpenAIConfig(model="test")
    assert actual.max_retries == defaults.max_retries
    assert actual.max_completion_tokens == defaults.max_completion_tokens
    assert actual.max_tokens == defaults.max_tokens


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["max_tokens", "max_completion_tokens"])
async def test_limit_reaches_provider_http_through_real_ag2_agent(limit):
    from ag2 import Agent

    requests = []

    def respond(request):
        assert request.url.host == "provider.invalid"
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
        async with Agent("bounded", config=config).run("Complete the task.") as run:
            await run.result()

    assert len(requests) == 1
    assert requests[0][limit] == 128


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
