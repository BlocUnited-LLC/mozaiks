from __future__ import annotations

import json
import os
from typing import Any

import httpx

from logs.logging_config import get_workflow_logger
from mozaiksai.core.tokens.guard import TokenUsageGuard
from mozaiksai.core.workflow.llm_config import get_llm_config

logger = get_workflow_logger("capabilities.simple_llm")


class SimpleLLMCapabilityService:
    """Minimal, product-agnostic LLM bridge for non-AG2 capabilities.

    Notes:
    - No hardcoded product prompts or business semantics.
    - No authorization, entitlements, pricing, gating, or enforcement.
    - Host control planes should provide any capability-specific configuration.
    """

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        token_usage_guard: TokenUsageGuard | None = None,
    ) -> None:
        self._timeout = float(timeout)
        self._client = httpx.AsyncClient(timeout=self._timeout)
        self._token_usage_guard = token_usage_guard

    async def aclose(self) -> None:
        if not self._client.is_closed:
            await self._client.aclose()

    async def generate_response(
        self,
        *,
        prompt: str,
        workflows: list[dict[str, Any]],
        app_id: str | None,
        user_id: str | None,
        ui_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a single chat completion call and return content + usage."""
        messages: list[dict[str, str]] = [{"role": "user", "content": str(prompt or "")}]
        return await self.generate_chat_completion(
            messages=messages,
            temperature=0.3,
            app_id=app_id,
            user_id=user_id,
            ui_context=ui_context,
            workflows=workflows,
        )

    async def generate_chat_completion(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float | None = 0.3,
        llm_config: dict[str, Any] | None = None,
        app_id: str | None,
        user_id: str | None,
        ui_context: dict[str, Any] | None = None,
        workflows: list[dict[str, Any]] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a raw chat completion call and return content + usage."""
        provider = await self._select_provider(llm_config)
        api_key = provider["api_key"]
        model = provider["model"]
        await (self._token_usage_guard or TokenUsageGuard()).check_or_raise(
            app_id=app_id,
            user_id=user_id,
            required_tokens=self._estimate_required_tokens(
                messages=messages,
                llm_config=llm_config,
                extra_payload=extra_payload,
            ),
        )

        api_base = provider.get("api_base") or provider.get("base_url")
        api_base = (api_base or os.getenv("CAPABILITY_LLM_API_BASE") or "https://api.openai.com/v1").rstrip("/")

        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        if self._should_use_responses_api(model=model, llm_config=llm_config):
            return await self._generate_responses_completion(
                api_base=api_base,
                headers=headers,
                model=model,
                messages=messages,
                llm_config=llm_config,
                app_id=app_id,
                user_id=user_id,
                ui_context=ui_context,
            )

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if temperature is not None:
            payload["temperature"] = float(temperature)
        if extra_payload:
            payload.update(extra_payload)

        logger.debug(
            "[CAPABILITY_LLM] Request",
            extra={
                "model": model,
                "app_id": app_id,
                "user_id": user_id,
                "workflow_count": len(workflows or []),
                "message_count": len(messages),
            },
        )

        response = await self._client.post(f"{api_base}/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

        content = (
            (data.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()
            if isinstance(data, dict)
            else ""
        )
        usage = data.get("usage", {}) if isinstance(data, dict) else {}

        return {"content": content, "usage": usage}

    async def _generate_responses_completion(
        self,
        *,
        api_base: str,
        headers: dict[str, str],
        model: str,
        messages: list[dict[str, str]],
        llm_config: dict[str, Any] | None,
        app_id: str | None,
        user_id: str | None,
        ui_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        instructions, input_payload = self._messages_to_responses_payload(messages)
        payload: dict[str, Any] = {
            "model": model,
            "input": input_payload,
        }
        if instructions:
            payload["instructions"] = instructions

        logger.debug(
            "[CAPABILITY_LLM] Request",
            extra={
                "model": model,
                "app_id": app_id,
                "user_id": user_id,
                "workflow_count": 0,
                "message_count": len(messages),
                "api_mode": "responses",
            },
        )

        response = await self._client.post(f"{api_base}/responses", headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        content = self._extract_responses_text(data)
        usage = data.get("usage", {}) if isinstance(data, dict) else {}
        return {"content": content, "usage": usage}

    async def generate_json_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        llm_config: dict[str, Any] | None = None,
        temperature: float | None = None,
        app_id: str | None,
        user_id: str | None,
        ui_context: dict[str, Any] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a chat completion expected to return a JSON object."""
        resolved_temperature: float | None = None
        if temperature is not None:
            resolved_temperature = float(temperature)
        elif isinstance(llm_config, dict) and llm_config.get("temperature") is not None:
            try:
                resolved_temperature = float(llm_config["temperature"])
            except Exception:
                logger.warning(
                    "LLM_CONFIG_INVALID_TEMPERATURE: %r — ignoring, using API default",
                    llm_config["temperature"],
                )
                resolved_temperature = None
        response = await self.generate_chat_completion(
            messages=[
                {"role": "system", "content": str(system_prompt or "")},
                {"role": "user", "content": str(user_prompt or "")},
            ],
            temperature=resolved_temperature,
            llm_config=llm_config,
            app_id=app_id,
            user_id=user_id,
            ui_context=ui_context,
            workflows=[],
            extra_payload=extra_payload,
        )
        content = str(response.get("content") or "").strip()
        parsed = self._parse_json_object(content)
        return {
            "content": content,
            "parsed": parsed,
            "usage": response.get("usage") or {},
        }

    @staticmethod
    def _should_use_responses_api(*, model: str, llm_config: dict[str, Any] | None) -> bool:
        normalized_model = str(model or "").strip().lower()
        if normalized_model.endswith("-codex") or "-codex-" in normalized_model:
            return True
        config_mode = str((llm_config or {}).get("api_mode") or "").strip().lower()
        return config_mode == "responses"

    @staticmethod
    def _estimate_required_tokens(
        *,
        messages: list[dict[str, str]],
        llm_config: dict[str, Any] | None,
        extra_payload: dict[str, Any] | None,
    ) -> int:
        text = "\n".join(str(message.get("content") or "") for message in messages)
        input_estimate = max(1, (len(text) + 3) // 4)
        output_limit = 0
        for source in (extra_payload or {}, llm_config or {}):
            for key in ("max_completion_tokens", "max_output_tokens", "max_tokens"):
                try:
                    output_limit = max(output_limit, int(source.get(key) or 0))
                except Exception:
                    continue
        return max(1, input_estimate + output_limit)

    @staticmethod
    def _messages_to_responses_payload(messages: list[dict[str, str]]) -> tuple[str | None, Any]:
        instructions: str | None = None
        items: list[dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role") or "user").strip().lower() or "user"
            content = str(message.get("content") or "")
            if role == "system" and instructions is None:
                instructions = content
                continue
            items.append(
                {
                    "role": role,
                    "content": [{"type": "input_text", "text": content}],
                }
            )
        if len(items) == 1 and items[0].get("role") == "user":
            content_items = items[0].get("content") or []
            if (
                isinstance(content_items, list)
                and len(content_items) == 1
                and isinstance(content_items[0], dict)
                and content_items[0].get("type") == "input_text"
            ):
                return instructions, str(content_items[0].get("text") or "")
        return instructions, items

    @staticmethod
    def _extract_responses_text(data: Any) -> str:
        if not isinstance(data, dict):
            return ""
        output_text = data.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()

        chunks: list[str] = []
        for item in data.get("output", []) or []:
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []) or []:
                if not isinstance(content, dict):
                    continue
                text = content.get("text")
                if isinstance(text, str) and text:
                    chunks.append(text)
        return "\n".join(chunk.strip() for chunk in chunks if chunk.strip()).strip()

    @staticmethod
    def _parse_json_object(content: str) -> dict[str, Any]:
        text = str(content or "").strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        if not text:
            raise ValueError("LLM returned empty content; expected a JSON object")
        if text[0] != "{" or text[-1] != "}":
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start : end + 1]
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM returned invalid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("LLM JSON response must be an object")
        return parsed

    async def _select_provider(self, llm_config: dict[str, Any] | None = None) -> dict[str, Any]:
        """Select a provider entry with a usable API key."""
        default_provider = await self._select_default_provider()
        if not llm_config:
            return default_provider

        candidate = llm_config
        config_list = candidate.get("config_list")
        if isinstance(config_list, list):
            first = next((entry for entry in config_list if isinstance(entry, dict)), None)
            if isinstance(first, dict):
                candidate = first

        override = dict(default_provider)
        for key in ("model", "api_base", "base_url", "api_key"):
            value = candidate.get(key)
            if value:
                override[key] = value
        return override

    async def _select_default_provider(self) -> dict[str, Any]:
        """Select the default provider entry with a usable API key."""
        _, llm_config = await get_llm_config(cache=True)
        config_list = llm_config.get("config_list", [])
        for entry in config_list:
            if entry.get("api_key") and entry.get("model"):
                return entry  # type: ignore[no-any-return]
        raise RuntimeError("No LLM provider available for non-AG2 capability execution")


_service: SimpleLLMCapabilityService | None = None


def get_general_capability_service() -> SimpleLLMCapabilityService:
    """Module-level singleton for the default non-AG2 capability executor."""
    global _service
    if _service is None:
        _service = SimpleLLMCapabilityService()
    return _service

