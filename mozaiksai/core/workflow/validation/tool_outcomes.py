"""Enforce declared tool results before the existing AG2 graph reads context."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable, Mapping
from functools import wraps
from typing import Any

from pydantic import BaseModel

from ..declarative.contracts import ToolOutcomeSpec

logger = logging.getLogger(__name__)


def wrap_tool_outcome(func: Callable, contract: ToolOutcomeSpec) -> Callable:
    signature = inspect.signature(func)
    if "context_variables" not in signature.parameters:
        raise ValueError(f"outcome tool {func.__name__!r} must accept context_variables")

    def failure(code: str) -> dict[str, Any]:
        return {contract.result_field: contract.error_value, "outcome_error": code}

    def begin(args: tuple, kwargs: dict) -> tuple[Any, int, dict | None]:
        arguments = signature.bind(*args, **kwargs)
        arguments.apply_defaults()
        context = arguments.arguments["context_variables"]
        if not callable(getattr(context, "get", None)) or not callable(getattr(context, "set", None)):
            raise ValueError("outcome tools require runtime context with get/set")
        previous = context.get(contract.context_key)
        attempts = context.get(contract.attempts_key)
        # Clear a prior success before any execution, including cancellation.
        context.set(contract.context_key, contract.error_value)
        if type(attempts) is not int or attempts < 0:
            context.set(contract.attempts_key, contract.max_attempts)
            return context, contract.max_attempts, failure("invalid_attempt_state")
        if attempts >= contract.max_attempts:
            return context, attempts, failure("attempts_exhausted")
        if attempts and previous not in contract.retry_on:
            return context, attempts, failure("retry_not_permitted")
        attempts += 1
        context.set(contract.attempts_key, attempts)
        return context, attempts, None

    def finish(context: Any, attempts: int, result: Any) -> dict | Mapping:
        if isinstance(result, BaseModel):
            result = result.model_dump(mode="json")
        value = result.get(contract.result_field) if isinstance(result, Mapping) else None
        if not isinstance(value, str) or value not in contract.values:
            result = failure("invalid_tool_outcome")
            value = contract.error_value
        context.set(contract.attempts_key, attempts)
        context.set(contract.context_key, value)
        return result

    if inspect.iscoroutinefunction(func):
        @wraps(func)
        async def wrapped(*args, **kwargs):
            context, attempts, blocked = begin(args, kwargs)
            if blocked is not None:
                return finish(context, attempts, blocked)
            try:
                result = await func(*args, **kwargs)
            except Exception:
                logger.exception("Outcome tool %s failed", func.__name__)
                result = failure("tool_execution_failed")
            except BaseException:
                finish(context, attempts, failure("tool_execution_interrupted"))
                raise
            return finish(context, attempts, result)
    else:
        @wraps(func)
        def wrapped(*args, **kwargs):
            context, attempts, blocked = begin(args, kwargs)
            if blocked is not None:
                return finish(context, attempts, blocked)
            try:
                result = func(*args, **kwargs)
            except Exception:
                logger.exception("Outcome tool %s failed", func.__name__)
                result = failure("tool_execution_failed")
            except BaseException:
                finish(context, attempts, failure("tool_execution_interrupted"))
                raise
            return finish(context, attempts, result)

    wrapped._mozaiks_tool_outcome = contract
    return wrapped
