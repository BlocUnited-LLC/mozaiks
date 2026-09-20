"""Shared immutable canonical JSON values for semantic authority documents.

This is the existing plan-authority value algebra, extracted without changing
its accepted values, declaration order, frozen state, or serialized shape.
Canonical byte encoding and digests remain owned by ``semantics.canonical``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)


class _AuthorityModel(BaseModel):
    """Frozen base for authority documents: no unchecked update path exists.

    ``model_copy(update=...)`` bypasses pydantic validation, which would let
    a raw mutable or non-JSON value masquerade as validated authority state.
    Authority models therefore refuse update-copies entirely — reconstruct
    through ``model_validate`` instead. Plain no-update copies stay safe
    because every field is itself frozen/immutable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    def model_copy(self, *, update=None, deep: bool = False):
        if update:
            raise TypeError(
                f"{type(self).__name__} does not support model_copy(update=...); "
                "reconstruct through model_validate so authority content is "
                "always validated"
            )
        return super().model_copy(deep=deep)


def _reject_nonfinite(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("canonical JSON forbids NaN and infinite floats")
    return value


class CanonicalJsonEntry(_AuthorityModel):
    """One key/value pair of a canonical JSON object."""

    key: StrictStr
    value: CanonicalJsonValue

    @field_validator("value")
    @classmethod
    def _finite(cls, value: Any) -> Any:
        return _reject_nonfinite(value)


class CanonicalJsonObject(_AuthorityModel):
    """Recursively closed, immutable, deterministic JSON object.

    Entries carry unique string keys in their exact declaration order —
    declaration order is meaning in structured-output contracts, so the
    authority pins the document precisely as derivation consumed it; the
    stored order is itself deterministic and survives serialization. Values
    are drawn only from the closed JSON algebra: no mutable dict/list
    survives construction and no non-JSON value can enter.
    """

    entries: tuple[CanonicalJsonEntry, ...] = ()

    @model_validator(mode="after")
    def _unique_keys(self) -> CanonicalJsonObject:
        keys = [entry.key for entry in self.entries]
        if len(set(keys)) != len(keys):
            raise ValueError("canonical JSON object declares duplicate keys")
        return self

    @classmethod
    def from_python(cls, value: Mapping[str, Any]) -> CanonicalJsonObject:
        if not isinstance(value, Mapping):
            raise ValueError("canonical JSON object requires a mapping")
        entries = []
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError(
                    f"canonical JSON object keys must be str, got {type(key).__name__}"
                )
            entries.append(CanonicalJsonEntry(key=key, value=_json_value(item)))
        return cls(entries=tuple(entries))

    def to_python(self) -> dict[str, Any]:
        return {entry.key: _python_value(entry.value) for entry in self.entries}


class CanonicalJsonArray(_AuthorityModel):
    """Recursively closed, immutable JSON array preserving element order."""

    items: tuple[CanonicalJsonValue, ...] = ()

    @field_validator("items")
    @classmethod
    def _finite_items(cls, value: tuple[Any, ...]) -> tuple[Any, ...]:
        for item in value:
            _reject_nonfinite(item)
        return value


CanonicalJsonValue = (
    None
    | StrictBool
    | StrictInt
    | StrictFloat
    | StrictStr
    | CanonicalJsonArray
    | CanonicalJsonObject
)

CanonicalJsonEntry.model_rebuild()
CanonicalJsonArray.model_rebuild()
CanonicalJsonObject.model_rebuild()


def _json_value(value: Any) -> Any:
    """Normalize one Python value into the closed algebra, failing closed
    immediately on anything that is not exact JSON."""
    if value is None or type(value) is bool or type(value) is str:
        return value
    if type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("canonical JSON forbids NaN and infinite floats")
        return value
    if isinstance(value, Mapping):
        return CanonicalJsonObject.from_python(value)
    if type(value) in (list, tuple):
        return CanonicalJsonArray(items=tuple(_json_value(item) for item in value))
    raise ValueError(
        "canonical JSON forbids values of type " f"{type(value).__name__}"
    )


def _python_value(value: Any) -> Any:
    if isinstance(value, CanonicalJsonObject):
        return value.to_python()
    if isinstance(value, CanonicalJsonArray):
        return [_python_value(item) for item in value.items]
    return value


__all__ = [
    "CanonicalJsonArray",
    "CanonicalJsonEntry",
    "CanonicalJsonObject",
    "CanonicalJsonValue",
]
