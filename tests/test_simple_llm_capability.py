from __future__ import annotations

import pytest

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
        "model": (llm_config or {}).get("model") or "gpt-5-nano",
        "api_key": "test-key",
        "api_base": "https://api.openai.com/v1",
    }



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
