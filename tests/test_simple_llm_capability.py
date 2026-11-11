from __future__ import annotations

import json

import httpx
import pytest

from mozaiksai.core.capabilities import simple_llm
from mozaiksai.core.capabilities.simple_llm import SimpleLLMCapabilityService
from mozaiksai.core.tokens.guard import TokenUsageDecision, TokenUsageDenied


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls: list[dict] = []
        self.is_closed = False

    async def post(self, url, *, headers=None, json=None):  # noqa: ANN001
        self.calls.append({"url": url, "headers": headers, "json": json})
        return _FakeResponse(self.payload)

    async def aclose(self) -> None:
        self.is_closed = True


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


@pytest.mark.asyncio
async def test_generate_json_completion_uses_responses_api_for_codex_models() -> None:
    service = SimpleLLMCapabilityService()
    service._client = _FakeClient(
        {
            "output_text": (
                '{"summary":"Patch the file.","owned_paths":["app/ui/pages/Dashboard.jsx"],'
                '"validation_strategy":"skip","validation_commands":[],"start_preview":false,'
                '"needs_human_review":false,"rationale":"Scoped UI patch."}'
            ),
            "usage": {"total_tokens": 42},
        }
    )
    service._select_provider = _fake_select_provider  # type: ignore[method-assign]

    response = await service.generate_json_completion(
        system_prompt="system prompt",
        user_prompt="user prompt",
        llm_config={"model": "gpt-5.2-codex", "temperature": 0.1},
        app_id="factory_app",
        user_id="user_1",
    )

    assert response["parsed"]["summary"] == "Patch the file."
    assert service._client.calls[0]["url"].endswith("/responses")
    payload = service._client.calls[0]["json"]
    assert payload["model"] == "gpt-5.2-codex"
    assert payload["instructions"] == "system prompt"
    assert payload["input"] == "user prompt"
    assert "temperature" not in payload


@pytest.mark.asyncio
async def test_generate_json_completion_keeps_chat_completions_for_non_codex_models() -> None:
    service = SimpleLLMCapabilityService()
    service._client = _FakeClient(
        {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"summary":"Classify request.","owned_paths":[],"validation_strategy":"skip",'
                            '"validation_commands":[],"start_preview":false,'
                            '"needs_human_review":false,"rationale":"Non-coding classification."}'
                        )
                    }
                }
            ],
            "usage": {"total_tokens": 21},
        }
    )
    service._select_provider = _fake_select_provider  # type: ignore[method-assign]

    response = await service.generate_json_completion(
        system_prompt="system prompt",
        user_prompt="user prompt",
        llm_config={"model": "gpt-5-nano", "temperature": 0.0},
        app_id="factory_app",
        user_id="user_1",
    )

    assert response["parsed"]["summary"] == "Classify request."
    assert service._client.calls[0]["url"].endswith("/chat/completions")
    payload = service._client.calls[0]["json"]
    assert payload["model"] == "gpt-5-nano"
    assert payload["temperature"] == 0.0
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1]["role"] == "user"


@pytest.mark.asyncio
async def test_generate_json_completion_omits_temperature_when_not_provided() -> None:
    service = SimpleLLMCapabilityService()
    service._client = _FakeClient(
        {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"summary":"Classify request.","owned_paths":[],"validation_strategy":"skip",'
                            '"validation_commands":[],"start_preview":false,'
                            '"needs_human_review":false,"rationale":"Non-coding classification."}'
                        )
                    }
                }
            ],
            "usage": {"total_tokens": 21},
        }
    )
    service._select_provider = _fake_select_provider  # type: ignore[method-assign]

    response = await service.generate_json_completion(
        system_prompt="system prompt",
        user_prompt="user prompt",
        llm_config={"model": "gpt-5"},
        app_id="factory_app",
        user_id="user_1",
    )

    assert response["parsed"]["summary"] == "Classify request."
    assert service._client.calls[0]["url"].endswith("/chat/completions")
    payload = service._client.calls[0]["json"]
    assert payload["model"] == "gpt-5"
    assert "temperature" not in payload


@pytest.mark.asyncio
async def test_generate_chat_completion_checks_token_balance_before_http_call() -> None:
    service = SimpleLLMCapabilityService(token_usage_guard=_DenyingGuard())
    service._client = _FakeClient({"choices": [{"message": {"content": "never"}}]})
    service._select_provider = _fake_select_provider  # type: ignore[method-assign]

    with pytest.raises(TokenUsageDenied):
        await service.generate_chat_completion(
            messages=[{"role": "user", "content": "spend tokens"}],
            llm_config={"model": "gpt-5-nano"},
            app_id="app_1",
            user_id="user_1",
        )

    assert service._client.calls == []


async def _fake_select_provider(llm_config=None):  # noqa: ANN001
    return {
        **(llm_config or {}),
        "model": (llm_config or {}).get("model") or "gpt-5-nano",
        "api_key": "test-key",
        "api_base": "https://api.openai.com/v1",
    }


@pytest.mark.asyncio
async def test_default_responses_configuration_reaches_ask_request_and_guard(monkeypatch) -> None:
    async def fake_config(**kwargs):
        return None, {
            "config_list": [{
                "model": "gpt-6-luna",
                "api_type": "openai",
                "api_key": "openai-test-key",
                "use_responses_api": True,
                "reasoning": {"effort": "medium"},
                "max_output_tokens": 2048,
            }],
        }

    class RecordingGuard:
        required_tokens = 0

        async def check_or_raise(self, **kwargs):
            self.required_tokens = kwargs["required_tokens"]

    monkeypatch.setattr(simple_llm, "get_llm_config", fake_config)
    guard = RecordingGuard()
    service = SimpleLLMCapabilityService(token_usage_guard=guard)
    await service._client.aclose()
    service._client = _FakeClient({"output_text": "Hello", "usage": {"total_tokens": 12}})

    response = await service.generate_chat_completion(
        messages=[{"role": "user", "content": "Hi"}], app_id="app_1", user_id="user_1",
    )
    await service.aclose()

    assert response == {"content": "Hello", "usage": {"total_tokens": 12}}
    call = service._client.calls[0]
    assert call["url"] == "https://api.openai.com/v1/responses"
    assert call["json"] == {
        "model": "gpt-6-luna", "input": "Hi",
        "reasoning": {"effort": "medium"}, "max_output_tokens": 2048,
    }
    assert guard.required_tokens == 2049


@pytest.mark.asyncio
@pytest.mark.parametrize("api_flag", ["use_responses_api", "responses_api"])
async def test_responses_request_honors_explicit_options_and_extra_payload(api_flag) -> None:
    service = SimpleLLMCapabilityService()
    await service._client.aclose()
    service._client = _FakeClient({"output_text": '{"ok":true}'})
    service._select_provider = _fake_select_provider

    result = await service.generate_json_completion(
        system_prompt="Return JSON", user_prompt="Hello", app_id=None, user_id=None,
        llm_config={
            "model": "custom-model", api_flag: True,
            "reasoning": {"effort": "medium"}, "max_output_tokens": 200,
        },
        extra_payload={"max_output_tokens": 300, "store": False, "text": {"format": {"type": "json_object"}}},
    )
    await service.aclose()

    assert result["parsed"] == {"ok": True}
    payload = service._client.calls[0]["json"]
    assert payload["reasoning"] == {"effort": "medium"}
    assert payload["max_output_tokens"] == 300
    assert payload["store"] is False
    assert payload["text"] == {"format": {"type": "json_object"}}


@pytest.mark.asyncio
async def test_responses_rejects_incompatible_output_limit_before_http_call() -> None:
    service = SimpleLLMCapabilityService()
    await service._client.aclose()
    service._client = _FakeClient({})
    service._select_provider = _fake_select_provider

    with pytest.raises(ValueError, match="max_output_tokens"):
        await service.generate_chat_completion(
            messages=[{"role": "user", "content": "Hello"}], app_id=None, user_id=None,
            llm_config={"use_responses_api": True, "max_tokens": 128},
        )
    await service.aclose()
    assert service._client.calls == []


@pytest.mark.asyncio
async def test_google_default_uses_real_ag2_provider_with_mock_http(monkeypatch) -> None:
    async def fake_config(**kwargs):
        return None, {
            "config_list": [{"model": "gemini-3.8-flash", "api_type": "google", "api_key": "gemini-test-key"}],
            "max_output_tokens": 256,
            "temperature": 0.2,
        }

    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={
            "candidates": [{"content": {"role": "model", "parts": [{"text": '{"ok":true}'}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 4, "totalTokenCount": 14},
        })

    monkeypatch.setattr(simple_llm, "get_llm_config", fake_config)
    service = SimpleLLMCapabilityService(timeout=47.0)
    await service._client.aclose()
    service._client = simple_llm.create_gemini_http_client(
        service._timeout, transport=httpx.MockTransport(handle),
    )
    result = await service.generate_json_completion(
        system_prompt="Return JSON", user_prompt="Hello", app_id=None, user_id=None,
    )
    await service.aclose()

    assert result["parsed"] == {"ok": True}
    assert result["usage"]["prompt_tokens"] == 10
    assert result["usage"]["completion_tokens"] == 4
    assert result["usage"]["total_tokens"] == 14
    assert len(requests) == 1
    request = requests[0]
    assert request.url.host == "generativelanguage.googleapis.com"
    assert request.extensions["timeout"]["read"] == 47.0
    assert "gemini-3.8-flash:generateContent" in request.url.path
    assert request.headers["x-goog-api-key"] == "gemini-test-key"
    assert "authorization" not in request.headers
    payload = json.loads(request.content)
    assert payload["systemInstruction"]["parts"] == [{"text": "Return JSON"}]
    assert payload["contents"] == [{"parts": [{"text": "Hello"}], "role": "user"}]
    assert payload["generationConfig"]["maxOutputTokens"] == 256
    assert payload["generationConfig"]["temperature"] == 0.2


@pytest.mark.asyncio
async def test_provider_override_does_not_reuse_another_providers_key(monkeypatch) -> None:
    async def fake_config(**kwargs):
        return None, {"config_list": [{
            "model": "gemini-3.8-flash", "api_type": "google", "api_key": "gemini-test-key",
        }]}

    monkeypatch.setattr(simple_llm, "get_llm_config", fake_config)
    service = SimpleLLMCapabilityService()
    provider = await service._select_provider({
        "config_list": [{"api_type": "openai", "model": "gpt-6-luna", "api_key": "openai-test-key"}],
        "use_responses_api": True, "reasoning": {"effort": "medium"},
    })
    await service.aclose()

    assert provider["api_type"] == "openai"
    assert provider["api_key"] == "openai-test-key"
    assert provider["use_responses_api"] is True
    assert provider["reasoning"] == {"effort": "medium"}


@pytest.mark.asyncio
@pytest.mark.parametrize(("explicit_key", "primary_api_type"), [(True, "google"), (False, "google"), (False, "openai")])
async def test_complete_provider_override_does_not_require_default_provider_key(
    monkeypatch, explicit_key, primary_api_type,
) -> None:
    from mozaiksai.core import secrets

    async def unavailable_default(**kwargs):
        pytest.fail("An explicit provider must not load the unconfigured Gemini default")

    secret_handles = []

    def resolve_secret(handle):
        secret_handles.append(handle)
        return "selected-test-key" if handle == "OPENAI_API_KEY" else ""

    monkeypatch.setattr(simple_llm, "get_llm_config", unavailable_default)
    monkeypatch.setattr(secrets, "resolve_secret", resolve_secret)
    monkeypatch.setenv("LLM_PRIMARY_API_TYPE", primary_api_type)
    service = SimpleLLMCapabilityService()
    await service._client.aclose()
    service._client = _FakeClient({"output_text": "Hello"})
    config = {"api_type": "openai", "model": "gpt-6-luna", "use_responses_api": True}
    if explicit_key:
        config["api_key"] = "selected-test-key"

    response = await service.generate_chat_completion(
        messages=[{"role": "user", "content": "Hi"}], llm_config={"config_list": [config]},
        app_id=None, user_id=None,
    )
    await service.aclose()

    assert response["content"] == "Hello"
    call = service._client.calls[0]
    assert call["url"] == "https://api.openai.com/v1/responses"
    assert call["headers"]["Authorization"] == "Bearer selected-test-key"
    expected_handles = [] if explicit_key else ["OPENAI_API_KEY"]
    if not explicit_key and primary_api_type == "openai":
        expected_handles.insert(0, "LLM_PRIMARY_API_KEY")
    assert secret_handles == expected_handles


@pytest.mark.asyncio
@pytest.mark.parametrize("selected_key_available", [True, False])
async def test_explicit_provider_cannot_use_another_providers_primary_key(monkeypatch, selected_key_available) -> None:
    from mozaiksai.core import secrets

    secret_handles = []

    def resolve_secret(handle):
        secret_handles.append(handle)
        return {
            "LLM_PRIMARY_API_KEY": "google-key-must-not-leak",
            "OPENAI_API_KEY": "openai-test-key" if selected_key_available else "",
        }.get(handle, "")

    monkeypatch.setattr(secrets, "resolve_secret", resolve_secret)
    monkeypatch.setenv("LLM_PRIMARY_API_TYPE", "google")
    service = SimpleLLMCapabilityService()
    await service._client.aclose()
    service._client = _FakeClient({"output_text": "Hello"})
    call = service.generate_chat_completion(
        messages=[{"role": "user", "content": "Hi"}],
        llm_config={"api_type": "openai", "model": "gpt-6-luna", "use_responses_api": True},
        app_id=None, user_id=None,
    )
    if selected_key_available:
        await call
        assert service._client.calls[0]["headers"]["Authorization"] == "Bearer openai-test-key"
    else:
        with pytest.raises(RuntimeError, match="No API key available for the selected capability provider"):
            await call
        assert service._client.calls == []
    await service.aclose()
    assert secret_handles == ["OPENAI_API_KEY"]


@pytest.mark.asyncio
async def test_partial_same_provider_override_inherits_default_model(monkeypatch) -> None:
    async def fake_config(**kwargs):
        return None, {"config_list": [{
            "api_type": "openai", "model": "gpt-5-nano", "api_key": "default-test-key",
        }]}

    monkeypatch.setattr(simple_llm, "get_llm_config", fake_config)
    service = SimpleLLMCapabilityService()
    provider = await service._select_provider({"api_type": "openai", "max_completion_tokens": 256})
    await service.aclose()

    assert provider == {
        "api_type": "openai", "model": "gpt-5-nano", "api_key": "default-test-key",
        "max_completion_tokens": 256,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("default_provider", "explicit_provider", "expected_limit"),
    [
        (
            {"api_type": "google", "model": "gemini-3.8-flash", "max_output_tokens": 8192},
            {"api_type": "openai", "model": "gpt-5-nano", "max_completion_tokens": 256, "reasoning_effort": "low"},
            "max_completion_tokens",
        ),
        (
            {"api_type": "openai", "model": "gpt-5-nano", "max_tokens": 8192, "reasoning_effort": "high"},
            {"api_type": "google", "model": "gemini-3.8-flash", "max_output_tokens": 256},
            "max_output_tokens",
        ),
    ],
)
async def test_provider_switch_discards_native_defaults_and_preserves_explicit_limits(
    monkeypatch, default_provider, explicit_provider, expected_limit,
) -> None:
    async def fake_config(**kwargs):
        return None, {"config_list": [{**default_provider, "api_key": "default-test-key"}]}

    requests = []

    def handle(request):
        requests.append(request)
        if explicit_provider["api_type"] == "google":
            return httpx.Response(200, json={
                "candidates": [{"content": {"role": "model", "parts": [{"text": "Hello"}]}, "finishReason": "STOP"}],
            })
        return httpx.Response(200, json={"choices": [{"message": {"content": "Hello"}}]})

    monkeypatch.setattr(simple_llm, "get_llm_config", fake_config)
    service = SimpleLLMCapabilityService()
    await service._client.aclose()
    service._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    config = {**explicit_provider, "api_key": "explicit-test-key"}
    selected = await service._select_provider(config)
    assert selected == config
    result = await service.generate_chat_completion(
        messages=[{"role": "user", "content": "Hi"}], llm_config=config, app_id=None, user_id=None,
    )
    await service.aclose()

    assert result["content"] == "Hello"
    assert len(requests) == 1
    request = requests[0]
    payload = json.loads(request.content)
    if explicit_provider["api_type"] == "google":
        assert request.url.host == "generativelanguage.googleapis.com"
        assert payload["generationConfig"] == {"maxOutputTokens": 256}
        assert request.headers["x-goog-api-key"] == "explicit-test-key"
    else:
        assert request.url.path == "/v1/chat/completions"
        assert payload[expected_limit] == 256
        assert payload["reasoning_effort"] == "low"
        assert "max_output_tokens" not in payload
        assert "temperature" not in payload
        assert request.headers["authorization"] == "Bearer explicit-test-key"


def test_explicit_responses_flag_takes_precedence_over_alias() -> None:
    assert not SimpleLLMCapabilityService._should_use_responses_api(
        model="custom-model", llm_config={"use_responses_api": False, "responses_api": True},
    )


@pytest.mark.asyncio
async def test_google_call_is_blocked_by_token_guard_before_http(monkeypatch) -> None:
    async def fake_config(**kwargs):
        return None, {"config_list": [{
            "model": "gemini-3.8-flash", "api_type": "google", "api_key": "gemini-test-key",
        }]}

    monkeypatch.setattr(simple_llm, "get_llm_config", fake_config)
    service = SimpleLLMCapabilityService(token_usage_guard=_DenyingGuard())
    await service._client.aclose()
    service._client = _FakeClient({})
    with pytest.raises(TokenUsageDenied):
        await service.generate_chat_completion(
            messages=[{"role": "user", "content": "Hello"}], app_id="app_1", user_id="user_1",
        )
    await service.aclose()
    assert service._client.calls == []


@pytest.mark.asyncio
async def test_ollama_default_does_not_require_api_key(monkeypatch) -> None:
    async def fake_config(**kwargs):
        return None, {"config_list": [{"model": "qwen3:latest", "api_type": "ollama"}]}

    monkeypatch.setattr(simple_llm, "get_llm_config", fake_config)
    service = SimpleLLMCapabilityService()
    assert (await service._select_provider())["api_type"] == "ollama"
    await service.aclose()



@pytest.mark.asyncio
async def test_generate_response_appends_page_context_to_system_prompt(monkeypatch) -> None:
    from mozaiksai.core.runtime.app import ai_config as ai_config_module

    monkeypatch.setattr(
        ai_config_module,
        "load_runtime_ai_config",
        lambda: {"ask": {"ask_mode_prompt": "You are the Studio assistant."}},
    )
    service = SimpleLLMCapabilityService()
    captured: dict = {}

    async def fake_chat_completion(**kwargs):
        captured.update(kwargs)
        return {"content": "ok", "usage": {}}

    monkeypatch.setattr(service, "generate_chat_completion", fake_chat_completion)

    await service.generate_response(
        prompt="what page am I on?",
        workflows=[],
        app_id="app_1",
        user_id="user_1",
        ui_context={"page_context": "The user is on the Integrations page."},
    )
    await service.aclose()

    system_message = captured["messages"][0]
    assert system_message["role"] == "system"
    assert "The user is currently on this screen: The user is on the Integrations page." in system_message["content"]
    assert captured.get("temperature") is None


@pytest.mark.asyncio
async def test_generate_response_omits_page_context_line_when_absent(monkeypatch) -> None:
    from mozaiksai.core.runtime.app import ai_config as ai_config_module

    monkeypatch.setattr(
        ai_config_module,
        "load_runtime_ai_config",
        lambda: {"ask": {"ask_mode_prompt": "You are the Studio assistant."}},
    )
    service = SimpleLLMCapabilityService()
    captured: dict = {}

    async def fake_chat_completion(**kwargs):
        captured.update(kwargs)
        return {"content": "ok", "usage": {}}

    monkeypatch.setattr(service, "generate_chat_completion", fake_chat_completion)

    await service.generate_response(
        prompt="hello",
        workflows=[],
        app_id="app_1",
        user_id="user_1",
        ui_context=None,
    )
    await service.aclose()

    system_message = captured["messages"][0]
    assert "currently on this screen" not in system_message["content"]


@pytest.mark.asyncio
async def test_generate_response_renders_workspace_context_lines(monkeypatch) -> None:
    from mozaiksai.core.runtime.app import ai_config as ai_config_module

    monkeypatch.setattr(
        ai_config_module,
        "load_runtime_ai_config",
        lambda: {"ask": {"ask_mode_prompt": "You are the Studio assistant."}},
    )
    service = SimpleLLMCapabilityService()
    captured: dict = {}

    async def fake_chat_completion(**kwargs):
        captured.update(kwargs)
        return {"content": "ok", "usage": {}}

    monkeypatch.setattr(service, "generate_chat_completion", fake_chat_completion)

    await service.generate_response(
        prompt="how many apps do I have?",
        workflows=[{"workflow_name": "ValueEngine"}],
        app_id="app_1",
        user_id="user_1",
        ui_context=None,
        workspace_context={"Workspace apps": "8 total — 8 draft", "Empty": "  "},
    )
    await service.aclose()

    system_content = captured["messages"][0]["content"]
    assert "Workspace apps: 8 total — 8 draft" in system_content
    assert "Active workflows: ValueEngine" in system_content
    assert "Empty:" not in system_content
