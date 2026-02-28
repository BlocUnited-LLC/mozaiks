from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx

from mozaiksai.core.workflow.validation.llm_config import get_llm_config
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
        provider = await self._select_provider()
        api_key = provider["api_key"]
        model = provider["model"]

        api_base = provider.get("api_base") or provider.get("base_url")
        api_base = (api_base or os.getenv("CAPABILITY_LLM_API_BASE") or "https://api.openai.com/v1").rstrip("/")

        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        # Capability configuration is host-owned. The runtime only executes.
        # Avoid embedding product prompts here; send the user prompt as-is.
        messages: List[Dict[str, str]] = [{"role": "user", "content": str(prompt or "")}]

        payload: Dict[str, Any] = {
            "model": model,
            "temperature": 0.3,
            "messages": messages,
        }

        logger.info(
            "[CAPABILITY_LLM] Request",
            extra={"model": model, "app_id": app_id, "user_id": user_id, "workflow_count": len(workflows)},
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

    async def _select_provider(self) -> Dict[str, Any]:
        """Select a provider entry with a usable API key."""
        _, llm_config = await get_llm_config(stream=False, cache=True)
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

