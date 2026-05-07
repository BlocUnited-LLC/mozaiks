from __future__ import annotations

import pytest

from mozaiksai.core.capabilities.simple_llm import SimpleLLMCapabilityService


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
        llm_config={"model": "gpt-4o-mini", "temperature": 0.0},
        app_id="factory_app",
        user_id="user_1",
    )

    assert response["parsed"]["summary"] == "Classify request."
    assert service._client.calls[0]["url"].endswith("/chat/completions")
    payload = service._client.calls[0]["json"]
    assert payload["model"] == "gpt-4o-mini"
    assert payload["temperature"] == 0.0
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1]["role"] == "user"


async def _fake_select_provider(llm_config=None):  # noqa: ANN001
    return {
        "model": (llm_config or {}).get("model") or "gpt-4o-mini",
        "api_key": "test-key",
        "api_base": "https://api.openai.com/v1",
    }
