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

from dataclasses import fields, is_dataclass
from types import MappingProxyType
from typing import Any

_SCALAR_TYPES = (str, int, float, bool, bytes, type(None))


def _sort_key(value: Any) -> str:
    return f"{type(value).__name__}:{repr(value)}"


def freeze(value: Any, *, _seen: set[int] | None = None) -> Any:
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
    if isinstance(value, _SCALAR_TYPES):
        return value
    if _seen is None:
        _seen = set()
    value_id = id(value)
    if value_id in _seen:
        raise ValueError("context value contains a recursive cycle")
    if isinstance(value, tuple):
        _seen.add(value_id)
        try:
            return tuple(freeze(item, _seen=_seen) for item in value)
        finally:
            _seen.remove(value_id)
    if isinstance(value, frozenset):
        _seen.add(value_id)
        try:
            return frozenset(freeze(item, _seen=_seen) for item in value)
        finally:
            _seen.remove(value_id)
    if isinstance(value, MappingProxyType):
        _seen.add(value_id)
        try:
            return MappingProxyType({k: freeze(v, _seen=_seen) for k, v in value.items()})
        finally:
            _seen.remove(value_id)
    if isinstance(value, dict):
        _seen.add(value_id)
        try:
            return MappingProxyType({k: freeze(v, _seen=_seen) for k, v in value.items()})
        finally:
            _seen.remove(value_id)
    if isinstance(value, list):
        _seen.add(value_id)
        try:
            return tuple(freeze(item, _seen=_seen) for item in value)
        finally:
            _seen.remove(value_id)
    if isinstance(value, set):
        _seen.add(value_id)
        try:
            return frozenset(freeze(item, _seen=_seen) for item in value)
        finally:
            _seen.remove(value_id)
    # Pydantic models — detach via model_dump (plain dicts, serializable)
    try:
        from pydantic import BaseModel  # noqa: PLC0415
        if isinstance(value, BaseModel):
            _seen.add(value_id)
            try:
                return freeze(value.model_dump(mode="json"), _seen=_seen)
            finally:
                _seen.remove(value_id)
    except ImportError:
        pass
    if is_dataclass(value) and not isinstance(value, type):
        _seen.add(value_id)
        try:
            return MappingProxyType(
                {field.name: freeze(getattr(value, field.name), _seen=_seen) for field in fields(value)}
            )
        finally:
            _seen.remove(value_id)
    # For other objects: if they look immutable (no __dict__ mutation path),
    # return as-is. Otherwise raise to surface the unsupported type.
    if not hasattr(value, "__dict__") or isinstance(value, type):
        return value
    raise TypeError(
        f"freeze() encountered an unsupported mutable type {type(value).__name__!r}. "
        "Store plain dicts, lists, or Pydantic models in context variables. "
        "Use ScopedContextWriter for authorized mutations."
    )


def detach(value: Any, *, _seen: set[int] | None = None) -> Any:
    """
    Deep copy value into a plain serializable Python structure with no shared
    references to canonical state. Safe to pass to loggers, serializers, and
    persistence layers.
    """
    if isinstance(value, _SCALAR_TYPES):
        return value
    if _seen is None:
        _seen = set()
    value_id = id(value)
    if value_id in _seen:
        raise ValueError("context value contains a recursive cycle")
    if isinstance(value, MappingProxyType):
        _seen.add(value_id)
        try:
            return {k: detach(v, _seen=_seen) for k, v in value.items()}
        finally:
            _seen.remove(value_id)
    if isinstance(value, dict):
        _seen.add(value_id)
        try:
            return {k: detach(v, _seen=_seen) for k, v in value.items()}
        finally:
            _seen.remove(value_id)
    if isinstance(value, (list, tuple)):
        _seen.add(value_id)
        try:
            return [detach(item, _seen=_seen) for item in value]
        finally:
            _seen.remove(value_id)
    if isinstance(value, (set, frozenset)):
        _seen.add(value_id)
        try:
            return [detach(item, _seen=_seen) for item in sorted(value, key=_sort_key)]
        finally:
            _seen.remove(value_id)
    try:
        from pydantic import BaseModel  # noqa: PLC0415
        if isinstance(value, BaseModel):
            _seen.add(value_id)
            try:
                return detach(value.model_dump(mode="json"), _seen=_seen)
            finally:
                _seen.remove(value_id)
    except ImportError:
        pass
    if is_dataclass(value) and not isinstance(value, type):
        _seen.add(value_id)
        try:
            return {field.name: detach(getattr(value, field.name), _seen=_seen) for field in fields(value)}
        finally:
            _seen.remove(value_id)
    if not hasattr(value, "__dict__") or isinstance(value, type):
        return value
    raise TypeError(
        f"detach() encountered an unsupported mutable type {type(value).__name__!r}. "
        "Store plain dicts, lists, dataclasses, or Pydantic models in context variables. "
        "Authority-bearing runtime objects belong in AG2 Context.dependencies."
    )


__all__ = ["detach", "freeze"]
