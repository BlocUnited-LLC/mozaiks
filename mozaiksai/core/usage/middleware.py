from __future__ import annotations

"""AG2 beta middleware that emits Mozaiks runtime usage events.

OpenTelemetry spans are handled by AG2's built-in TelemetryMiddleware. This
middleware keeps a separate, queryable runtime ledger by emitting neutral
``chat.usage_delta`` events after each LLM call.
"""

import time
from collections.abc import Sequence
from typing import Any

from autogen.beta import Context
from autogen.beta.events import BaseEvent, ModelResponse
from autogen.beta.middleware import BaseMiddleware, LLMCall, Middleware

from logs.logging_config import get_core_logger
from mozaiksai.core.tokens.guard import TokenUsageGuard
from mozaiksai.core.tokens.manager import TokenManager

logger = get_core_logger("ag2_usage_middleware")


def _ctx_get(context_variables: Any, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    if hasattr(context_variables, "get"):
        try:
            return context_variables.get(key, default)
        except Exception:
            return default
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key, default)
    if isinstance(context_variables, dict):
        return context_variables.get(key, default)
    return default


def _int_usage(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _usage_value(usage: Any, *names: str) -> int:
    for name in names:
        if hasattr(usage, name):
            value = getattr(usage, name)
            if value is not None:
                return _int_usage(value)
        if isinstance(usage, dict) and usage.get(name) is not None:
            return _int_usage(usage.get(name))
    return 0


def _text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() == "none" else text


class MozaiksUsageMiddleware(BaseMiddleware):
    """Emit factual usage deltas after AG2 beta LLM calls."""

    def __init__(
        self,
        event: BaseEvent,
        context: Context,
        *,
        agent_name: str,
        workflow_name: str,
        context_variables: Any,
        model_name: str | None = None,
    ) -> None:
        super().__init__(event, context)
        self._agent_name = agent_name
        self._workflow_name = workflow_name
        self._context_variables = context_variables
        self._model_name = model_name

    async def on_llm_call(
        self,
        call_next: LLMCall,
        events: Sequence[BaseEvent],
        context: Context,
    ) -> ModelResponse:
        await TokenUsageGuard().check_or_raise(
            app_id=_text(_ctx_get(self._context_variables, "app_id", "")),
            user_id=_text(_ctx_get(self._context_variables, "user_id", "anonymous")) or "anonymous",
            tenant_id=_text(_ctx_get(self._context_variables, "tenant_id", "")) or None,
            required_tokens=1,
        )

        started = time.perf_counter()
        response = await call_next(events, context)
        duration = time.perf_counter() - started

        usage = getattr(response, "usage", None)
        if usage is None:
            return response

        prompt_tokens = _usage_value(usage, "prompt_tokens", "input_tokens")
        completion_tokens = _usage_value(usage, "completion_tokens", "output_tokens")
        total_tokens = _usage_value(usage, "total_tokens")
        if total_tokens == 0:
            total_tokens = prompt_tokens + completion_tokens
        if total_tokens == 0:
            return response

        model_name = getattr(response, "model", None) or self._model_name
        cached_tokens = _usage_value(usage, "cached_tokens", "cache_read_input_tokens")

        try:
            await TokenManager.emit_usage_delta(
                chat_id=_text(_ctx_get(self._context_variables, "chat_id", "")),
                app_id=_text(_ctx_get(self._context_variables, "app_id", "")),
                user_id=_text(_ctx_get(self._context_variables, "user_id", "anonymous")) or "anonymous",
                tenant_id=_text(_ctx_get(self._context_variables, "tenant_id", "")) or None,
                workflow_name=_text(_ctx_get(self._context_variables, "workflow_name", self._workflow_name)),
                agent_name=self._agent_name,
                model_name=str(model_name) if model_name else self._model_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cached=cached_tokens > 0,
                duration_sec=duration,
                invocation_id=getattr(response, "id", None),
            )
        except Exception as exc:  # pragma: no cover - usage must not break runs
            logger.debug("usage middleware emit skipped: %s", exc)
        return response


def build_ag2_usage_middleware(
    *,
    agent_name: str,
    workflow_name: str,
    context_variables: Any,
    model_name: str | None = None,
) -> Middleware:
    """Build AG2 beta middleware for neutral runtime usage metering."""

    return Middleware(
        MozaiksUsageMiddleware,
        agent_name=agent_name,
        workflow_name=workflow_name,
        context_variables=context_variables,
        model_name=model_name,
    )


__all__ = ["MozaiksUsageMiddleware", "build_ag2_usage_middleware"]
