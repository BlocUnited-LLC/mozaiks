from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, TypeVar

from ag2 import Agent, MemoryStream
from ag2.middleware.base import MiddlewareFactory
from ag2.middleware.builtin import RetryMiddleware
from ag2.observers import TokenMonitor
from pydantic import BaseModel

from mozaiksai.core.adapters.llm_fallback import llm_config_to_ag2_config
from mozaiksai.core.usage.context import AuxiliaryUsageContext
from mozaiksai.core.usage.middleware import build_ag2_usage_middleware

ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)

_MAX_STRUCTURED_AGENT_RETRIES = 10


class StructuredAgentFactory(Protocol):
    def __call__(
        self, system_prompt: str, llm_config: dict[str, Any], *, middleware: list[MiddlewareFactory],
    ) -> Any: ...


class AG2StructuredAgentRunner:
    """Small AG2 adapter for one-agent structured-output calls.

    Mozaiks code should use this adapter when it needs a single AG2 agent turn
    that returns a strict Pydantic model. Deterministic routing, artifact
    lifecycle, and product policy stay outside this class.

    Retry layers
    ------------
    Two independent retry mechanisms cover distinct failure layers:

    - ``retry_count`` → ``RetryMiddleware(max_retries=retry_count)``
      Retries the LLM API call on provider or execution failures (network
      errors, rate limits, transient model unavailability). Fires before any
      response content is available.

    - ``schema_validation_retries`` → ``reply.content(retries=schema_validation_retries)``
      Retries schema validation when the model returns a response that fails
      Pydantic/structured-output parsing. AG2 sends the validation error back
      to the model as context and asks it to correct the response. Fires after
      the model responds.

    These cover different failure layers: ``RetryMiddleware`` never sees schema
    validation failures; ``content(retries=...)`` never sees provider errors.
    Mozaiks Pydantic validation on the returned value runs after both layers.
    """

    def __init__(
        self,
        *,
        agent_factory: StructuredAgentFactory | None = None,
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
        usage_context: AuxiliaryUsageContext,
        retry_count: int = 2,
        schema_validation_retries: int = 2,
    ) -> ResponseModelT:
        """Execute one structured-output agent turn and return a validated model.

        Parameters
        ----------
        retry_count:
            Maximum additional LLM API call attempts on provider/execution
            failures. Passed to ``RetryMiddleware``. Caller-controlled; the LLM
            cannot influence this value.
        schema_validation_retries:
            Maximum additional model turns when the response fails schema
            validation. Passed to ``reply.content(retries=...)``. Caller-
            controlled; the LLM cannot influence this value. Zero means one
            attempt only — no correction turns.
        """
        retry_count = _validate_retry_limit(
            name="retry_count",
            value=retry_count,
        )
        schema_validation_retries = _validate_retry_limit(
            name="schema_validation_retries",
            value=schema_validation_retries,
        )
        if not isinstance(usage_context, AuxiliaryUsageContext):
            raise TypeError("Structured agent calls require a server-owned AuxiliaryUsageContext")
        middleware = [
            build_ag2_usage_middleware(
                agent_name=agent_name,
                workflow_name=usage_context.workflow_name,
                context_variables=usage_context.model_dump(mode="python"),
                execution_kind="auxiliary",
            ),
            RetryMiddleware(max_retries=retry_count),
        ]
        agent = self._make_agent(
            agent_name=agent_name,
            system_prompt=system_prompt,
            llm_config=dict(llm_config or {}),
            middleware=middleware,
        )
        reply = await agent.ask(
            user_prompt,
            stream=self._stream_factory(),
            observers=[TokenMonitor()],
            response_schema=response_schema,
        )
        result = await reply.content(retries=schema_validation_retries)
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
        middleware: list[MiddlewareFactory],
    ) -> Any:
        if self._agent_factory is not None:
            return self._agent_factory(system_prompt, llm_config, middleware=middleware)
        # Normalise flat llm_config dict into the config_list shape expected by
        # llm_config_to_ag2_config so provider routing works correctly.
        config_list_llm = dict(llm_config)
        if "config_list" not in config_list_llm:
            config_list_llm["config_list"] = [{**llm_config, "model": llm_config.get("model") or "gpt-4o"}]
        return Agent(
            agent_name, system_prompt, config=llm_config_to_ag2_config(config_list_llm), middleware=middleware,
        )


def _validate_retry_limit(*, name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer between 0 and {_MAX_STRUCTURED_AGENT_RETRIES}")
    if value < 0 or value > _MAX_STRUCTURED_AGENT_RETRIES:
        raise ValueError(f"{name} must be between 0 and {_MAX_STRUCTURED_AGENT_RETRIES}")
    return value


__all__ = ["AG2StructuredAgentRunner"]
