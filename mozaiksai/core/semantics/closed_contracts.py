"""Finite immutable contracts for application-semantic compatibility proofs.

``mozaiks.closed_contract_profile.v1`` supports only null, scalar, homogeneous
array, and closed object contracts. Nullable values and optional properties
are separate facts. No provider dialect, references, schema importer, or
assignability implementation belongs to this algebra.

The profile permits at most 32 contract levels (the root is level one) and
1,024 contract occurrences, including repeated children. Property wrappers do
not add a contract level or node. An iterative preflight enforces those limits
and rejects cycles before recursive validation. Exceeding either limit is
unsupported. INTEGER enums contain exact signed-64-bit ints; NUMBER enums
contain exact finite floats. The two enum representations never coerce each
other or booleans. Every identity uses the existing canonical JSON primitives.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    TypeAdapter,
    ValidationInfo,
    field_validator,
    model_validator,
)

from mozaiksai.core.semantics.canonical import canonical_digest, canonical_json
from mozaiksai.core.semantics.refs import SemanticsModel

CLOSED_CONTRACT_PROFILE_VERSION: Literal["mozaiks.closed_contract_profile.v1"] = (
    "mozaiks.closed_contract_profile.v1"
)
MAX_CONTRACT_DEPTH = 32
MAX_CONTRACT_NODES = 1024


class ClosedContractKind(StrEnum):
    NULL = "null"
    BOOLEAN = "boolean"
    INTEGER = "integer"
    NUMBER = "number"
    STRING = "string"
    ARRAY = "array"
    OBJECT = "object"


class ClosedContractUnsupported(ValueError):
    """The input exceeds the finite proof profile; it cannot receive a proof."""


def _check_contract_limits(value: Any) -> None:
    """Bound traversal without recursion or serializing unvalidated models."""
    stack: list[tuple[Any, int, bool]] = [(value, 1, False)]
    active: set[int] = set()
    count = 0
    while stack:
        current, depth, leaving = stack.pop()
        if leaving:
            active.remove(id(current))
            continue
        raw = current.__dict__ if isinstance(current, ContractModel) else current
        if not isinstance(raw, Mapping):
            continue  # The closed field validator rejects malformed shapes.
        if id(current) in active:
            raise ClosedContractUnsupported("UNSUPPORTED: cyclic closed contract")
        active.add(id(current))
        stack.append((current, depth, True))
        if "contract" in raw and "kind" not in raw:
            stack.append((raw["contract"], depth, False))
            continue
        count += 1
        if depth > MAX_CONTRACT_DEPTH:
            raise ClosedContractUnsupported(
                f"UNSUPPORTED: contract depth exceeds {MAX_CONTRACT_DEPTH} "
                f"under {CLOSED_CONTRACT_PROFILE_VERSION}"
            )
        if count > MAX_CONTRACT_NODES:
            raise ClosedContractUnsupported(
                f"UNSUPPORTED: contract nodes exceed {MAX_CONTRACT_NODES} "
                f"under {CLOSED_CONTRACT_PROFILE_VERSION}"
            )
        if "items" in raw:
            stack.append((raw["items"], depth + 1, False))
        properties = raw.get("properties", ())
        if isinstance(properties, (list, tuple)):
            if len(properties) + count > MAX_CONTRACT_NODES:
                raise ClosedContractUnsupported(
                    f"UNSUPPORTED: contract nodes exceed {MAX_CONTRACT_NODES} "
                    f"under {CLOSED_CONTRACT_PROFILE_VERSION}"
                )
            stack.extend((prop, depth + 1, False) for prop in properties)


class ContractModel(SemanticsModel):
    """Deeply immutable authority with no unchecked update-copy path."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    @model_validator(mode="before")
    @classmethod
    def _proof_limits(cls, value: Any) -> Any:
        _check_contract_limits(value)
        return value

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        if update:
            raise TypeError("contract updates require model_validate; model_copy(update=...) is forbidden")
        return type(self).model_validate(self)

    def copy(self, *, include=None, exclude=None, update=None, deep: bool = False):
        if include is not None or exclude is not None or update:
            raise TypeError("contract changes require model_validate; copy changes are forbidden")
        return self.model_copy(deep=deep)

    @property
    def identity_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @property
    def contract_digest(self) -> str:
        return canonical_digest(self.identity_payload)


class NullContract(ContractModel):
    kind: Literal[ClosedContractKind.NULL] = ClosedContractKind.NULL


class ScalarContract(ContractModel):
    kind: Literal[
        ClosedContractKind.BOOLEAN,
        ClosedContractKind.INTEGER,
        ClosedContractKind.NUMBER,
        ClosedContractKind.STRING,
    ]
    nullable: StrictBool
    enum: tuple[StrictBool | StrictInt | StrictFloat | StrictStr, ...] | None = None

    @field_validator("enum", mode="before")
    @classmethod
    def _canonical_enum(cls, value: Any, info: ValidationInfo) -> Any:
        if value is None:
            return None
        if type(value) not in (tuple, list) or not value:
            raise ValueError("scalar enum requires a non-empty scalar sequence")
        kind = info.data.get("kind")
        if not isinstance(kind, ClosedContractKind):
            raise ValueError("enum requires a valid scalar kind")
        expected = {
            ClosedContractKind.BOOLEAN: bool,
            ClosedContractKind.INTEGER: int,
            ClosedContractKind.NUMBER: float,
            ClosedContractKind.STRING: str,
        }.get(kind)
        if expected is None or any(type(item) is not expected for item in value):
            raise ValueError("enum values must have the exact scalar kind; null and coercion are forbidden")
        if expected is float and any(not math.isfinite(item) for item in value):
            raise ValueError("non-finite enum numbers are unsupported")
        normalized = tuple(0.0 if type(item) is float and item == 0.0 else item for item in value)
        # Reuse the existing signed-64-bit/finite scalar authority, not a second
        # serialization domain with values that cannot acquire canonical identity.
        canonical_json(normalized)
        if len(set(normalized)) != len(normalized):
            raise ValueError("duplicate scalar enum values")
        return tuple(sorted(normalized))


class ArrayContract(ContractModel):
    kind: Literal[ClosedContractKind.ARRAY] = ClosedContractKind.ARRAY
    nullable: StrictBool
    items: ClosedContract


class ContractProperty(ContractModel):
    name: StrictStr = Field(min_length=1)
    required: StrictBool
    contract: ClosedContract


class ObjectContract(ContractModel):
    kind: Literal[ClosedContractKind.OBJECT] = ClosedContractKind.OBJECT
    nullable: StrictBool
    properties: tuple[ContractProperty, ...]
    additional_properties: Literal[False]

    @field_validator("properties", mode="before")
    @classmethod
    def _property_sequence(cls, value: Any) -> Any:
        if type(value) not in (list, tuple):
            raise ValueError("properties require an explicit list or tuple")
        return value

    @field_validator("additional_properties", mode="before")
    @classmethod
    def _closed_object(cls, value: Any) -> bool:
        if value is not False:
            raise ValueError("additional_properties must be explicitly false")
        return False

    @field_validator("properties")
    @classmethod
    def _properties(cls, value: tuple[ContractProperty, ...]) -> tuple[ContractProperty, ...]:
        ordered = tuple(sorted(value, key=lambda prop: prop.name))
        if len({prop.name for prop in ordered}) != len(ordered):
            raise ValueError("duplicate contract property names")
        return ordered


ClosedContract = Annotated[
    NullContract | ScalarContract | ArrayContract | ObjectContract,
    Field(discriminator="kind"),
]

ArrayContract.model_rebuild()
ContractProperty.model_rebuild()
ObjectContract.model_rebuild()
_CONTRACT_ADAPTER: TypeAdapter[ClosedContract] = TypeAdapter(ClosedContract)


def parse_closed_contract(value: Any) -> ClosedContract:
    """Validate only the canonical algebra; provider/schema documents reject."""
    _check_contract_limits(value)
    return _CONTRACT_ADAPTER.validate_python(value)


__all__ = [
    "CLOSED_CONTRACT_PROFILE_VERSION",
    "MAX_CONTRACT_DEPTH",
    "MAX_CONTRACT_NODES",
    "ArrayContract",
    "ClosedContract",
    "ClosedContractKind",
    "ClosedContractUnsupported",
    "ContractModel",
    "ContractProperty",
    "NullContract",
    "ObjectContract",
    "ScalarContract",
    "parse_closed_contract",
]
