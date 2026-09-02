"""Closed JSON transport contract for module action results.

The ModuleExecutor success boundary is the one point every module action
result crosses before any serializer (HTTP module routes, profile panels and
tabs, page hydration, relationship rows, WebSocket delivery). This module
defines that boundary's closed value contract:

* the container domain is exactly ``dict``, ``list``, and ``tuple`` — the
  types trusted code and Mongo documents actually produce. Sets have no JSON
  representation and hash-order-dependent output, and subclasses or custom
  Mapping/iterable types can run arbitrary code during iteration, so every
  container that is not one of the three exact builtins is rejected *before*
  any ``.items()`` call or iteration executes;
* mapping keys must already be strings — Mongo documents use string field
  names, and converting non-string keys can silently collide (ObjectId vs its
  hex string, ``1`` vs ``"1"``), so every non-string key is rejected;
* BSON identifier types are converted (``ObjectId`` → 24-hex string,
  ``Decimal128`` → lossless decimal string);
* values FastAPI's encoder already serialized for existing modules keep their
  exact wire semantics through explicit conversions (datetime/date/time →
  ISO strings, ``UUID`` → string, finite ``Decimal`` → FastAPI's decimal
  encoding, ``Enum`` → its recursively normalized value, pydantic models →
  their JSON-mode dump, ``bytes`` → UTF-8 text). Non-finite floats and
  Decimals are rejected: the wire renderer forbids NaN/Infinity;
* everything else fails closed with :class:`ModuleResultNormalizationError` —
  no ``str``/``repr`` fallback can leak object internals onto the wire, and
  no conversion failure (Decimal, pydantic serialization, UTF-8 decode)
  escapes as a raw exception;
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
from bson.int64 import Int64
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

    if value is None or type(value) is bool:
        return value
    # Enum before the scalar checks: IntEnum/StrEnum members are int/str
    # instances but their transport value is the recursively normalized
    # member value, not the member object.
    if isinstance(value, Enum):
        return _normalize(value.value, path=path, depth=depth + 1, active=active)
    if type(value) is int:
        return value
    # PyMongo decodes BSON int64 as bson.Int64, an int subclass produced by
    # the driver itself; convert to the exact builtin. Arbitrary user int
    # subclasses stay outside the domain.
    if type(value) is Int64:
        return int(value)
    if type(value) is float:
        if value != value or value in (float("inf"), float("-inf")):
            raise _fail(path, "non-finite float is not JSON-safe")
        return value
    if type(value) is str:
        return value
    # Conversion types use unbound base-class methods so a hostile subclass
    # cannot substitute its own conversion code.
    if isinstance(value, ObjectId):
        return ObjectId.__str__(value)
    if isinstance(value, Decimal128):
        return str(Decimal128.to_decimal(value))
    if isinstance(value, datetime):
        return datetime.isoformat(value)
    if isinstance(value, date):
        return date.isoformat(value)
    if isinstance(value, time):
        return time.isoformat(value)
    if isinstance(value, UUID):
        return UUID.__str__(value)
    if isinstance(value, Decimal):
        if not Decimal.is_finite(value):
            raise _fail(path, "non-finite Decimal is not JSON-safe")
        try:
            return decimal_encoder(value)
        except Exception as exc:
            raise _fail(path, "Decimal value could not be encoded") from exc
    if isinstance(value, bytes):
        try:
            # Unbound builtin decode: a hostile bytes subclass cannot swap in
            # its own decode implementation.
            return bytes.decode(value, "utf-8")
        except UnicodeDecodeError as exc:
            raise _fail(path, "bytes value is not valid UTF-8") from exc
    if isinstance(value, BaseModel):
        try:
            dumped = value.model_dump(mode="json")
        except Exception as exc:
            raise _fail(
                path,
                f"pydantic model {type(value).__name__} could not be serialized",
            ) from exc
        return _normalize(dumped, path=path, depth=depth + 1, active=active)

    # Exact container domain. The type check runs BEFORE any .items() call or
    # iteration so a hostile Mapping/sequence subclass never executes code.
    if type(value) is dict:
        identity = id(value)
        if identity in active:
            raise _fail(path, "cyclic mapping is not JSON-safe")
        active.add(identity)
        try:
            normalized: dict[str, Any] = {}
            for key, item in value.items():
                if type(key) is not str:
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
    if type(value) is list or type(value) is tuple:
        identity = id(value)
        if identity in active:
            raise _fail(path, "cyclic sequence is not JSON-safe")
        active.add(identity)
        try:
            return [
                _normalize(item, path=f"{path}[{index}]", depth=depth + 1, active=active)
                for index, item in enumerate(value)
            ]
        finally:
            active.discard(identity)

    # Typed rejections for near-miss containers. Sets are rejected outright —
    # they have no JSON form and hash-order-dependent iteration; subclasses
    # and custom Mappings/iterables are rejected without being iterated.
    if isinstance(value, set | frozenset):
        raise _fail(
            path,
            f"{type(value).__name__} values have no deterministic JSON form",
        )
    if isinstance(value, Mapping | dict):
        raise _fail(
            path,
            f"mapping type {type(value).__name__} is outside the exact "
            "dict transport domain",
        )
    if isinstance(value, list | tuple):
        raise _fail(
            path,
            f"sequence type {type(value).__name__} is outside the exact "
            "list/tuple transport domain",
        )

    raise _fail(path, f"unsupported value type {type(value).__name__}")


def json_safe_bson(value: Any) -> Any:
    """Normalize a module action result into the closed JSON transport domain.

    Raises :class:`ModuleResultNormalizationError` — and only that type —
    for any value outside the contract: non-string mapping keys, containers
    other than exact dict/list/tuple (sets, subclasses, custom Mappings,
    generators), unsupported value types, non-finite floats or Decimals,
    malformed UTF-8 bytes, failing pydantic serializers, cyclic containers,
    or nesting beyond :data:`MAX_RESULT_DEPTH`. Unexpected failures from the
    approved conversions are wrapped into the same typed error by exception
    type name only, so no hostile payload contents leak into the message.
    """
    try:
        return _normalize(value, path="", depth=0, active=set())
    except ModuleResultNormalizationError:
        raise
    except Exception as exc:
        raise ModuleResultNormalizationError(
            f"result normalization failed: {type(exc).__name__}"
        ) from exc


__all__ = [
    "MAX_RESULT_DEPTH",
    "ModuleResultNormalizationError",
    "json_safe_bson",
]
