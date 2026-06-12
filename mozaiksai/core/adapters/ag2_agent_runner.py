from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from autogen.beta import Agent, MemoryStream
from autogen.beta.config import OpenAIConfig
from autogen.beta.middleware import RetryMiddleware
from autogen.beta.observers import TokenMonitor
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
        return Agent(
            agent_name,
            system_prompt,
            config=OpenAIConfig(
                model=llm_config.get("model") or "gpt-4o",
                temperature=llm_config.get("temperature"),
            ),
        )


__all__ = ["AG2StructuredAgentRunner"]
