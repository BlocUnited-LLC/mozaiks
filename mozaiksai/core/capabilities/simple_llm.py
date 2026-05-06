from __future__ import annotations

import os
import json
from typing import Any, Dict, List, Optional

import httpx

from mozaiksai.core.workflow.llm_config import get_llm_config
from logs.logging_config import get_workflow_logger

logger = get_workflow_logger("capabilities.simple_llm")


class SimpleLLMCapabilityService:
    """Minimal, product-agnostic LLM bridge for non-AG2 capabilities.

    Notes:
    - No hardcoded product prompts or business semantics.
    - No authorization, entitlements, pricing, gating, or enforcement.
    - Host control planes should provide any capability-specific configuration.
    """

    def __init__(self, *, timeout: float = 30.0) -> None:
        self._timeout = float(timeout)
        self._client = httpx.AsyncClient(timeout=self._timeout)

    async def aclose(self) -> None:
        if not self._client.is_closed:
            await self._client.aclose()

    async def generate_response(
        self,
        *,
        prompt: str,
        workflows: List[Dict[str, Any]],
        app_id: Optional[str],
        user_id: Optional[str],
        ui_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute a single chat completion call and return content + usage."""
        messages: List[Dict[str, str]] = [{"role": "user", "content": str(prompt or "")}]
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
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        llm_config: Optional[Dict[str, Any]] = None,
        app_id: Optional[str],
        user_id: Optional[str],
        ui_context: Optional[Dict[str, Any]] = None,
        workflows: Optional[List[Dict[str, Any]]] = None,
        extra_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute a raw chat completion call and return content + usage."""
        provider = await self._select_provider(llm_config)
        api_key = provider["api_key"]
        model = provider["model"]

        api_base = provider.get("api_base") or provider.get("base_url")
        api_base = (api_base or os.getenv("CAPABILITY_LLM_API_BASE") or "https://api.openai.com/v1").rstrip("/")

        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        payload: Dict[str, Any] = {
            "model": model,
            "temperature": float(temperature),
            "messages": messages,
        }
        if extra_payload:
            payload.update(extra_payload)

        logger.info(
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

    async def generate_json_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        llm_config: Optional[Dict[str, Any]] = None,
        temperature: Optional[float] = None,
        app_id: Optional[str],
        user_id: Optional[str],
        ui_context: Optional[Dict[str, Any]] = None,
        extra_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute a chat completion expected to return a JSON object."""
        resolved_temperature = float(temperature) if temperature is not None else 0.0
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
    def _parse_json_object(content: str) -> Dict[str, Any]:
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

    async def _select_provider(self, llm_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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

    async def _select_default_provider(self) -> Dict[str, Any]:
        """Select the default provider entry with a usable API key."""
        _, llm_config = await get_llm_config(cache=True)
        config_list = llm_config.get("config_list", [])
        for entry in config_list:
            if entry.get("api_key") and entry.get("model"):
                return entry
        raise RuntimeError("No LLM provider available for non-AG2 capability execution")


_service: Optional[SimpleLLMCapabilityService] = None


def get_general_capability_service() -> SimpleLLMCapabilityService:
    """Module-level singleton for the default non-AG2 capability executor."""
    global _service
    if _service is None:
        _service = SimpleLLMCapabilityService()
    return _service

