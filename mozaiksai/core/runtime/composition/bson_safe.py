"""Canonical BSON identifier normalization for module action results.

Module actions routinely return raw Mongo documents; ``ObjectId`` and
``Decimal128`` are not JSON-encodable and previously escaped the executor to
the FastAPI serializer as a bare HTTP 500. This is the one normalization
authority for that boundary: only BSON-specific identifier/decimal types are
converted. Values the platform's existing encoder already owns (``datetime``,
``UUID``, sets, Pydantic models) pass through unchanged, and unknown types are
never coerced through an arbitrary repr.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bson import ObjectId
from bson.decimal128 import Decimal128


def _json_safe_key(key: Any) -> Any:
    if isinstance(key, ObjectId):
        return str(key)
    return key


def json_safe_bson(value: Any) -> Any:
    """Return ``value`` with BSON identifier types converted to JSON-safe forms.

    * ``ObjectId`` becomes its stable 24-hex-character string, wherever it
      appears — ``_id`` fields, nested documents, list items, or mapping keys.
    * ``Decimal128`` becomes its lossless decimal string representation.
    * Mappings, lists, and tuples are rebuilt recursively (tuples become
      lists, matching JSON array semantics); the input is never mutated.
    * Every other value is returned unchanged.
    """
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, Decimal128):
        return str(value.to_decimal())
    if isinstance(value, Mapping):
        return {
            _json_safe_key(key): json_safe_bson(item) for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [json_safe_bson(item) for item in value]
    return value


__all__ = ["json_safe_bson"]
