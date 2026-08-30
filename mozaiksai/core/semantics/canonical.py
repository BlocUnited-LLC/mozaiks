"""Canonical serialization contract ``mozaiks.canonical_json.v1``.

One explicit, versioned serialization defines the bytes every semantic-compiler
digest is computed over.  Individual models never invent their own digest
rules; they build a canonical payload and call :func:`canonical_digest`.

Rules (all fail closed rather than guess):

- Output is JSON text with keys sorted lexicographically by code point,
  ``,``/``:`` separators, and every non-ASCII character escaped (``ensure_ascii``),
  so the byte stream is pure ASCII and identical on every supported platform.
- Mapping keys must be ``str``.  Non-string keys are rejected — JSON's silent
  key coercion would make two distinct inputs produce identical bytes.
- Supported values: ``None``, ``bool``, ``str``, ``int`` within signed 64-bit
  range, finite ``float``, ``list``/``tuple``, and ``dict``.  Anything else —
  sets, bytes, datetimes, arbitrary objects — is rejected: unordered or
  ambiguous types must be normalized by the declaring schema into ordered,
  supported shapes *before* serialization, never silently normalized here.
- ``float`` values must be finite; ``NaN``/``inf`` are rejected.  Negative zero
  normalizes to ``0.0`` so numerically equal scalars cannot yield different
  bytes.  Floats serialize via Python's shortest round-trip ``repr``, which is
  deterministic for IEEE-754 doubles across supported platforms.  ``bool`` is
  serialized as JSON ``true``/``false`` and never conflated with ``int``.
- Sequences preserve their given order (semantically ordered collections keep
  their meaning); schemas that declare a collection unordered must sort it
  deterministically before serializing.
- Every field present in the payload participates in the digest.  A schema that
  excludes non-semantic metadata must do so by omitting the field from the
  canonical payload it builds — an explicit schema decision, never a
  serializer-side default.
- Input values are never mutated: normalization builds new structures.

The digest is the lowercase hex SHA-256 of the ASCII bytes of the canonical
JSON text.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Literal

CANONICAL_SERIALIZATION_VERSION: Literal["mozaiks.canonical_json.v1"] = "mozaiks.canonical_json.v1"

_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1


class CanonicalSerializationError(ValueError):
    """Raised when a value cannot be canonically serialized."""


def _normalize(value: Any, path: str) -> Any:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if not _INT64_MIN <= value <= _INT64_MAX:
            raise CanonicalSerializationError(
                f"integer at {path} is outside the signed 64-bit range: {value!r}"
            )
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalSerializationError(
                f"non-finite float at {path} cannot be canonically serialized: {value!r}"
            )
        if value == 0.0:
            return 0.0
        return value
    if isinstance(value, (list, tuple)):
        return [_normalize(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalSerializationError(
                    f"mapping key at {path} must be str, got {type(key).__name__}"
                )
            normalized[key] = _normalize(item, f"{path}.{key}")
        return normalized
    raise CanonicalSerializationError(
        f"unsupported type at {path}: {type(value).__name__}; normalize it in the "
        "declaring schema before canonical serialization"
    )


def canonical_json(value: Any) -> str:
    """Serialize ``value`` to the canonical ASCII JSON text."""
    normalized = _normalize(value, "$")
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def canonical_digest(value: Any) -> str:
    """Lowercase hex SHA-256 over the canonical JSON bytes of ``value``."""
    return hashlib.sha256(canonical_json(value).encode("ascii")).hexdigest()


__all__ = [
    "CANONICAL_SERIALIZATION_VERSION",
    "CanonicalSerializationError",
    "canonical_digest",
    "canonical_json",
]
