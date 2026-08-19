"""
Recursively immutable view helpers for canonical context state.

Values read from ContextVariablesBridge must not expose mutable references
to canonical state. This module provides two primitives:

- freeze(value) — wraps mutable containers in immutable views recursively.
  The result CANNOT be used to modify canonical state.
- detach(value) — produces a deep copy of the value with no shared references.
  The result is a plain serializable Python value safe to pass to loggers,
  serializers, and persistence layers.

Only ScopedContextWriter / ContextAuthorityPolicy can produce authorized
mutations that reach canonical state.
"""
from __future__ import annotations

import copy
from types import MappingProxyType
from typing import Any


def freeze(value: Any) -> Any:
    """
    Wrap value in a recursively immutable view.

    - dict → MappingProxyType with frozen values
    - list → tuple with frozen elements
    - set → frozenset with frozen elements
    - Pydantic BaseModel → MappingProxyType over model_dump()
    - All others → returned as-is (str, int, float, bool, None, bytes, frozenset, tuple)

    Raises TypeError for unsupported mutable types that cannot be safely frozen
    (e.g., custom mutable objects with __setattr__ that are not Pydantic models).
    """
    if value is None or isinstance(value, (str, int, float, bool, bytes, frozenset)):
        return value
    if isinstance(value, tuple):
        return tuple(freeze(item) for item in value)
    if isinstance(value, MappingProxyType):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, dict):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(freeze(item) for item in value)
    # Pydantic models — detach via model_dump (plain dicts, serializable)
    try:
        from pydantic import BaseModel  # noqa: PLC0415
        if isinstance(value, BaseModel):
            return freeze(value.model_dump())
    except ImportError:
        pass
    # For other objects: if they look immutable (no __dict__ mutation path),
    # return as-is. Otherwise raise to surface the unsupported type.
    if not hasattr(value, "__dict__") or isinstance(value, type):
        return value
    raise TypeError(
        f"freeze() encountered an unsupported mutable type {type(value).__name__!r}. "
        "Store plain dicts, lists, or Pydantic models in context variables. "
        "Use ScopedContextWriter for authorized mutations."
    )


def detach(value: Any) -> Any:
    """
    Deep copy value into a plain serializable Python structure with no shared
    references to canonical state. Safe to pass to loggers, serializers, and
    persistence layers.
    """
    if value is None or isinstance(value, (str, int, float, bool, bytes)):
        return value
    if isinstance(value, MappingProxyType):
        return {k: detach(v) for k, v in value.items()}
    if isinstance(value, dict):
        return {k: detach(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [detach(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [detach(item) for item in value]
    try:
        from pydantic import BaseModel  # noqa: PLC0415
        if isinstance(value, BaseModel):
            return value.model_dump()
    except ImportError:
        pass
    return copy.deepcopy(value)


__all__ = ["detach", "freeze"]
