from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from ag2 import Agent, MemoryStream
from ag2.middleware.builtin import RetryMiddleware
from ag2.observers import TokenMonitor

from mozaiksai.core.adapters.llm_fallback import llm_config_to_ag2_config
from pydantic import BaseModel

ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)


class AG2StructuredAgentRunner:
    """Small AG2 adapter for one-agent structured-output calls.

    Mozaiks code should use this adapter when it needs a single AG2 agent turn
    that returns a strict Pydantic model. Deterministic routing, artifact
    lifecycle, and product policy stay outside this class.
    """

    def __init__(
        self,
        *,
        agent_factory: Callable[[str, dict[str, Any]], Any] | None = None,
        stream_factory: Callable[[], Any] = MemoryStream,
    ) -> None:
        self._agent_factory = agent_factory
        self._stream_factory = stream_factory

    async def run(
        self,
        *,
        agent_name: str,
        system_prompt: str,
        user_prompt: str,
        llm_config: dict[str, Any] | None,
        response_schema: type[ResponseModelT],
        retry_count: int = 2,
    ) -> ResponseModelT:
        agent = self._make_agent(
            agent_name=agent_name,
            system_prompt=system_prompt,
            llm_config=dict(llm_config or {}),
        )
        reply = await agent.ask(
            user_prompt,
            stream=self._stream_factory(),
            middleware=[RetryMiddleware(max_retries=retry_count)],
            observers=[TokenMonitor()],
            response_schema=response_schema,
        )
        result = await reply.content()
        if result is None:
            raise ValueError(f"{agent_name} returned an empty response")
        if isinstance(result, response_schema):
            return result
        return response_schema.model_validate(result)

    def _make_agent(
        self,
        *,
        agent_name: str,
        system_prompt: str,
        llm_config: dict[str, Any],
    ) -> Any:
        if self._agent_factory is not None:
            return self._agent_factory(system_prompt, llm_config)
        # Normalise flat llm_config dict into the config_list shape expected by
        # llm_config_to_ag2_config so provider routing works correctly.
        config_list_llm: dict[str, Any] = {
            "config_list": [{
                "model": llm_config.get("model") or "gpt-4o",
                "api_type": llm_config.get("api_type", "openai"),
                "api_key": llm_config.get("api_key"),
                "base_url": llm_config.get("base_url"),
            }],
            "temperature": llm_config.get("temperature"),
        }
        return Agent(agent_name, system_prompt, config=llm_config_to_ag2_config(config_list_llm))


__all__ = ["AG2StructuredAgentRunner"]
