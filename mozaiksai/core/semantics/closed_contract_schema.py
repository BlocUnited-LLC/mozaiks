"""Explicit import of the finite JSON-schema-shaped action request profile.

The canonical closed contract is the resulting semantic authority. This
importer accepts only exact null/scalar types, homogeneous arrays, and
explicitly closed objects. Unsupported assertions are rejected, never erased.
No references, unions, provider dialects, or executable validators are read.
"""

from __future__ import annotations

from typing import Any

from mozaiksai.core.semantics.closed_contracts import (
    CLOSED_CONTRACT_PROFILE_VERSION,
    MAX_CONTRACT_DEPTH,
    MAX_CONTRACT_NODES,
    ArrayContract,
    ClosedContract,
    ClosedContractUnsupported,
    ContractProperty,
    NullContract,
    ObjectContract,
    ScalarContract,
)

_SCALAR_TYPES = frozenset({"boolean", "integer", "number", "string"})
_SCHEMA_KEYS = {
    "null": frozenset({"type"}),
    **{kind: frozenset({"type", "enum"}) for kind in _SCALAR_TYPES},
    "array": frozenset({"type", "items"}),
    "object": frozenset({"type", "properties", "required", "additionalProperties"}),
}


def _unsupported(reason: str) -> ClosedContractUnsupported:
    return ClosedContractUnsupported(f"UNSUPPORTED: {reason} under {CLOSED_CONTRACT_PROFILE_VERSION}")


def _check_schema_profile(value: Any) -> None:
    """Validate the raw document iteratively before any recursive conversion."""
    stack: list[tuple[Any, int, bool]] = [(value, 1, False)]
    active: set[int] = set()
    count = 0
    while stack:
        current, depth, leaving = stack.pop()
        if leaving:
            active.remove(id(current))
            continue
        if type(current) is not dict:
            raise _unsupported("each schema must be an exact object document")
        if id(current) in active:
            raise _unsupported("cyclic schema document")
        count += 1
        if depth > MAX_CONTRACT_DEPTH:
            raise _unsupported(f"contract depth exceeds {MAX_CONTRACT_DEPTH}")
        if count > MAX_CONTRACT_NODES:
            raise _unsupported(f"contract nodes exceed {MAX_CONTRACT_NODES}")
        kind = current.get("type")
        if type(kind) is not str or kind not in _SCHEMA_KEYS:
            raise _unsupported("schema requires one supported exact type")
        if any(type(key) is not str for key in current):
            raise _unsupported("schema keys must be exact strings")
        unexpected = set(current) - _SCHEMA_KEYS[kind]
        if unexpected:
            raise _unsupported(f"schema keywords {sorted(unexpected)!r}")
        active.add(id(current))
        stack.append((current, depth, True))
        if kind in _SCALAR_TYPES and "enum" in current:
            if type(current["enum"]) is not list or not current["enum"]:
                raise _unsupported("scalar enum must be a nonempty exact array")
        elif kind == "array":
            if "items" not in current:
                raise _unsupported("homogeneous array requires an items contract")
            stack.append((current["items"], depth + 1, False))
        elif kind == "object":
            if current.get("additionalProperties") is not False:
                raise _unsupported("object additionalProperties must be explicitly false")
            properties = current.get("properties", {})
            if type(properties) is not dict:
                raise _unsupported("object properties must be an exact mapping")
            if len(properties) > MAX_CONTRACT_NODES - count:
                raise _unsupported(f"contract nodes exceed {MAX_CONTRACT_NODES}")
            if any(type(name) is not str or not name for name in properties):
                raise _unsupported("property names must be nonempty exact strings")
            required = current.get("required", [])
            if type(required) is not list or any(type(name) is not str for name in required):
                raise _unsupported("required must be an exact array of property names")
            if len(required) != len(set(required)) or not set(required) <= set(properties):
                raise _unsupported("required names must be unique declared properties")
            stack.extend((child, depth + 1, False) for child in properties.values())


def _convert_schema(value: dict[str, Any]) -> ClosedContract:
    kind = value["type"]
    if kind == "null":
        return NullContract()
    if kind in _SCALAR_TYPES:
        return ScalarContract.model_validate({"kind": kind, "nullable": False, "enum": value.get("enum")})
    if kind == "array":
        return ArrayContract(nullable=False, items=_convert_schema(value["items"]))
    required = set(value.get("required", []))
    return ObjectContract(
        nullable=False,
        properties=tuple(
            ContractProperty(name=name, required=name in required, contract=_convert_schema(child))
            for name, child in value.get("properties", {}).items()
        ),
        additional_properties=False,
    )


def import_closed_contract_schema(value: Any) -> ClosedContract:
    """Import exactly the bounded proof profile, retaining every supported fact."""
    _check_schema_profile(value)
    try:
        return _convert_schema(value)
    except (TypeError, ValueError) as exc:
        raise _unsupported(f"schema has no exact closed-contract representation: {exc}") from exc


__all__ = ["import_closed_contract_schema"]
