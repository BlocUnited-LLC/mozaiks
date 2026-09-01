from __future__ import annotations

"""AG2 1.0 middleware that emits Mozaiks runtime usage events.

OpenTelemetry spans are handled by AG2's built-in TelemetryMiddleware. This
middleware keeps a separate, queryable runtime ledger by emitting neutral
``chat.usage_delta`` events after each LLM call.
"""

import os
import time
from collections.abc import Sequence
from typing import Any

from ag2 import Context
from ag2.events import BaseEvent, ModelResponse
from ag2.middleware import BaseMiddleware, LLMCall, Middleware

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


def _positive_int(value: Any, *, default: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, parsed)


def _required_tokens_for_call(context_variables: Any) -> int:
    """Resolve conservative preflight spend requirement for the next LLM call."""

    for key in (
        "token_preflight_required_tokens",
        "token_watchdog_required_tokens",
        "token_budget_required_tokens",
    ):
        value = _ctx_get(context_variables, key)
        if value is not None:
            return _positive_int(value)
    return _positive_int(os.getenv("MOZAIKS_TOKEN_PREFLIGHT_REQUIRED_TOKENS"), default=1)


class MozaiksUsageMiddleware(BaseMiddleware):
    """Emit factual usage deltas after AG2 1.0 LLM calls."""

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
            workspace_id=_text(_ctx_get(self._context_variables, "workspace_id", "")) or None,
            required_tokens=_required_tokens_for_call(self._context_variables),
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
                workspace_id=_text(_ctx_get(self._context_variables, "workspace_id", "")) or None,
                workflow_name=_text(_ctx_get(self._context_variables, "workflow_name", self._workflow_name)),
                build_id=_text(_ctx_get(self._context_variables, "build_id", "")) or None,
                agent_name=self._agent_name,
                model_name=str(model_name) if model_name else self._model_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cached=cached_tokens > 0,
                cached_tokens=cached_tokens,
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
    """Build AG2 1.0 middleware for neutral runtime usage metering."""

    return Middleware(
        MozaiksUsageMiddleware,
        agent_name=agent_name,
        workflow_name=workflow_name,
        context_variables=context_variables,
        model_name=model_name,
    )


__all__ = ["MozaiksUsageMiddleware", "build_ag2_usage_middleware"]
