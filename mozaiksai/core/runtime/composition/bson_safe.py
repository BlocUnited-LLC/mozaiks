"""Closed JSON transport contract for module action results.

The ModuleExecutor success boundary is the one point every module action
result crosses before any serializer (HTTP module routes, profile panels and
tabs, page hydration, relationship rows, WebSocket delivery). This module
defines that boundary's closed value contract:

* mapping keys must already be strings — Mongo documents use string field
  names, and converting non-string keys can silently collide (ObjectId vs its
  hex string, ``1`` vs ``"1"``), so every non-string key is rejected;
* BSON identifier types are converted (``ObjectId`` → 24-hex string,
  ``Decimal128`` → lossless decimal string);
* values FastAPI's encoder already serialized for existing modules keep their
  exact wire semantics through explicit conversions (datetime/date/time →
  ISO strings, ``UUID`` → string, ``Decimal`` → FastAPI's decimal encoding,
  ``Enum`` → its value, pydantic models → their JSON dump, ``bytes`` →
  UTF-8 text, sets → lists);
* everything else fails closed with a typed error — no ``str``/``repr``
  fallback can leak object internals onto the wire;
* cyclic containers and absurd nesting fail closed instead of exhausting the
  stack. A shared non-cyclic child is encoded once per occurrence.

The input is never mutated; containers are rebuilt.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi.encoders import decimal_encoder
from pydantic import BaseModel

MAX_RESULT_DEPTH = 100


class ModuleResultNormalizationError(ValueError):
    """A module action result cannot be represented in the JSON transport contract."""


def _fail(path: str, reason: str) -> ModuleResultNormalizationError:
    location = path or "$"
    return ModuleResultNormalizationError(f"{reason} at {location}")


def _normalize(value: Any, *, path: str, depth: int, active: set[int]) -> Any:
    if depth > MAX_RESULT_DEPTH:
        raise _fail(path, f"result nesting exceeds {MAX_RESULT_DEPTH} levels")

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise _fail(path, "non-finite float is not JSON-safe")
        return value
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, Decimal128):
        return str(value.to_decimal())
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return decimal_encoder(value)
    if isinstance(value, Enum):
        return _normalize(value.value, path=path, depth=depth + 1, active=active)
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _fail(path, "bytes value is not valid UTF-8") from exc
    if isinstance(value, BaseModel):
        return _normalize(
            value.model_dump(mode="json"), path=path, depth=depth + 1, active=active
        )
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise _fail(path, "cyclic mapping is not JSON-safe")
        active.add(identity)
        try:
            normalized: dict[str, Any] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise _fail(
                        path,
                        "mapping keys must be strings; got "
                        f"{type(key).__name__} key",
                    )
                normalized[key] = _normalize(
                    item, path=f"{path}.{key}", depth=depth + 1, active=active
                )
            return normalized
        finally:
            active.discard(identity)
    if isinstance(value, (list, tuple, set, frozenset)):
        identity = id(value)
        if identity in active:
            raise _fail(path, "cyclic sequence is not JSON-safe")
        active.add(identity)
        try:
            items = list(value)
            return [
                _normalize(item, path=f"{path}[{index}]", depth=depth + 1, active=active)
                for index, item in enumerate(items)
            ]
        finally:
            active.discard(identity)

    raise _fail(path, f"unsupported value type {type(value).__name__}")


def json_safe_bson(value: Any) -> Any:
    """Normalize a module action result into the closed JSON transport domain.

    Raises :class:`ModuleResultNormalizationError` when the result contains a
    non-string mapping key, an unsupported value type, a non-finite float, a
    cyclic container, or nesting beyond :data:`MAX_RESULT_DEPTH`.
    """
    return _normalize(value, path="", depth=0, active=set())


__all__ = [
    "MAX_RESULT_DEPTH",
    "ModuleResultNormalizationError",
    "json_safe_bson",
]
