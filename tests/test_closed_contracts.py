"""Finite contract profile: exact domains, bounded validation, and immutable identity."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

import pytest
from pydantic import ValidationError

from mozaiksai.core.semantics.canonical import canonical_digest, canonical_json
from mozaiksai.core.semantics.closed_contracts import (
    CLOSED_CONTRACT_PROFILE_VERSION,
    MAX_CONTRACT_DEPTH,
    MAX_CONTRACT_NODES,
    ArrayContract,
    ClosedContractUnsupported,
    ContractProperty,
    NullContract,
    ObjectContract,
    ScalarContract,
    parse_closed_contract,
)


def _object(*properties: dict[str, Any], nullable: bool = False) -> dict[str, Any]:
    return {
        "kind": "object",
        "nullable": nullable,
        "properties": list(properties),
        "additional_properties": False,
    }


def _property(name="value", *, required=True, contract=None):
    return {
        "name": name,
        "required": required,
        "contract": contract if contract is not None else {"kind": "null"},
    }


def _scalar(kind="string", *, nullable=False, enum=None):
    return {"kind": kind, "nullable": nullable, "enum": enum}


@pytest.mark.parametrize(
    ("kind", "values", "expected"),
    [
        ("boolean", [True, False], (False, True)),
        ("integer", [3, -1, 0], (-1, 0, 3)),
        ("integer", [2**63 - 1, -(2**63)], (-(2**63), 2**63 - 1)),
        ("number", [3.5, -0.0, -1.0], (-1.0, 0.0, 3.5)),
        ("string", ["é", "a", "A"], ("A", "a", "é")),
    ],
)
def test_scalar_enums_keep_exact_types_and_canonical_order(kind, values, expected):
    contract = parse_closed_contract(_scalar(kind, nullable=True, enum=values))
    assert isinstance(contract, ScalarContract)
    assert contract.enum == expected
    assert tuple(type(item) for item in contract.enum) == tuple(type(item) for item in expected)
    assert contract.nullable is True
    assert contract.contract_digest == canonical_digest(contract.model_dump(mode="json"))


@pytest.mark.parametrize("kind", ["boolean", "integer", "number", "string"])
def test_unrestricted_scalar_omitted_and_explicit_no_enum_share_one_identity(kind):
    omitted = parse_closed_contract({"kind": kind, "nullable": False})
    explicit = parse_closed_contract(_scalar(kind))
    assert omitted == explicit
    assert omitted.model_dump_json() == explicit.model_dump_json()
    assert omitted.contract_digest == explicit.contract_digest


@pytest.mark.parametrize(
    "document",
    [
        {"kind": "unknown"},
        {"kind": "null", "unknown": 1},
        {"kind": "null", "nullable": False},
        {"kind": "null", "enum": [None]},
        {"kind": "null", "items": {"kind": "null"}},
        {"kind": "null", "properties": []},
        {"kind": "null", "additional_properties": False},
        {**_scalar(), "items": {"kind": "null"}},
        {**_scalar(), "properties": []},
        {"kind": "array", "nullable": False},
        {"kind": "array", "nullable": False, "items": [{"kind": "null"}]},
        {**_object(), "additional_properties": True},
        {**_object(), "additional_properties": {}},
        {**_object(), "additional_properties": 0},
        {**_object(), "additional_properties": "false"},
        {key: value for key, value in _object().items() if key != "additional_properties"},
        {key: value for key, value in _object().items() if key != "properties"},
        {key: value for key, value in _object().items() if key != "nullable"},
        _object(_property("same"), _property("same")),
        _object(_property("")),
        _object(_property(123)),
        _object(_property(required=1)),
        _object({"name": "value", "contract": {"kind": "null"}}),
        _scalar("string", enum=[]),
        _scalar("string", enum="value"),
        _scalar("string", enum=[1]),
        _scalar("string", enum=["a", 1]),
        _scalar("string", enum=[None]),
        _scalar("string", enum=["a", "a"]),
        _scalar("string", enum=[["a"]]),
        _scalar("string", enum=[{"value": "a"}]),
        _scalar("integer", enum=[True]),
        _scalar("integer", enum=[1.0]),
        _scalar("integer", enum=[1, 2.0]),
        _scalar("integer", enum=[-(2**63) - 1]),
        _scalar("integer", enum=[2**63]),
        _scalar("number", enum=[1]),
        _scalar("number", enum=[True]),
        _scalar("number", enum=[1.0, 2]),
        _scalar("number", enum=[float("nan")]),
        _scalar("number", enum=[float("inf")]),
        _scalar("number", enum=[float("-inf")]),
        _scalar("number", enum=[-0.0, 0.0]),
        _scalar("boolean", enum=[0]),
        _scalar("boolean", enum=[False, 1]),
        _scalar(nullable=0),
        _scalar(nullable="false"),
    ],
)
def test_hostile_shapes_fail_closed(document):
    with pytest.raises(ValueError):
        parse_closed_contract(document)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("format", "email"),
        ("pattern", "^a"),
        ("minimum", 0),
        ("maximum", 10),
        ("exclusiveMinimum", 0),
        ("multipleOf", 2),
        ("minLength", 1),
        ("maxLength", 10),
        ("minItems", 1),
        ("maxItems", 10),
        ("uniqueItems", True),
        ("contains", {"kind": "null"}),
        ("prefixItems", [{"kind": "null"}]),
        ("$ref", "https://example.invalid/schema.json"),
        ("$ref", "#"),
        ("$dynamicRef", "#recursive"),
        ("anyOf", [{"kind": "string"}, {"kind": "integer"}]),
        ("anyOf", [_scalar(), {"kind": "null"}]),
        ("oneOf", [_scalar(), {"kind": "null"}]),
        ("allOf", [_scalar()]),
        ("not", {"kind": "null"}),
        ("if", {"kind": "null"}),
        ("then", {"kind": "null"}),
        ("else", {"kind": "null"}),
        ("dependentSchemas", {}),
        ("validator", "custom.hook"),
        ("additionalProperties", False),
    ],
)
def test_deferred_assertions_refs_and_general_unions_are_never_erased(field, value):
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        parse_closed_contract({**_scalar(), field: value})


def test_absent_null_present_null_only_and_nullable_scalar_are_distinct_authorities():
    absent_allowed = parse_closed_contract(_object(_property(required=False)))
    present_null_only = parse_closed_contract(_object(_property(required=True)))
    null_only_value = NullContract()
    nullable_string = parse_closed_contract(_object(_property(contract=_scalar(nullable=True))))
    authorities = (absent_allowed, present_null_only, null_only_value, nullable_string)
    assert len({contract.contract_digest for contract in authorities}) == 4
    assert absent_allowed.properties[0].required is False
    assert present_null_only.properties[0].required is True
    assert isinstance(present_null_only.properties[0].contract, NullContract)
    assert nullable_string.properties[0].contract.nullable is True
    assert "nullable" not in null_only_value.model_dump()


def test_empty_closed_object_and_exact_property_names_remain_explicit():
    empty = ObjectContract(nullable=False, properties=(), additional_properties=False)
    assert empty.model_dump(mode="json") == _object()
    named = parse_closed_contract(_object(_property(" name "), _property("name"), _property("Name")))
    assert tuple(prop.name for prop in named.properties) == (" name ", "Name", "name")


def _array_chain(depth):
    value = {"kind": "null"}
    for _ in range(depth - 1):
        value = {"kind": "array", "nullable": False, "items": value}
    return value


def test_profile_depth_boundary_is_exact_and_checked_before_recursive_validation():
    assert CLOSED_CONTRACT_PROFILE_VERSION == "mozaiks.closed_contract_profile.v1"
    assert MAX_CONTRACT_DEPTH == 32
    accepted = parse_closed_contract(_array_chain(MAX_CONTRACT_DEPTH))
    assert isinstance(accepted, ArrayContract)
    for depth in (MAX_CONTRACT_DEPTH + 1, 2000):
        with pytest.raises(ClosedContractUnsupported, match="UNSUPPORTED: contract depth"):
            parse_closed_contract(_array_chain(depth))
        with pytest.raises(ValueError, match="UNSUPPORTED: contract depth"):
            ArrayContract.model_validate(_array_chain(depth))


def test_profile_node_boundary_counts_every_contract_occurrence():
    assert MAX_CONTRACT_NODES == 1024
    leaf = {"kind": "null"}
    allowed = _object(*(_property(f"p{index}", contract=leaf) for index in range(MAX_CONTRACT_NODES - 1)))
    assert len(parse_closed_contract(allowed).properties) == MAX_CONTRACT_NODES - 1
    allowed["properties"].append(_property("too_many", contract=leaf))
    with pytest.raises(ClosedContractUnsupported, match="UNSUPPORTED: contract nodes"):
        parse_closed_contract(allowed)
    with pytest.raises(ValueError, match="UNSUPPORTED: contract nodes"):
        ObjectContract.model_validate(allowed)


@pytest.mark.parametrize("shape", ["array", "object", "forged_model"])
def test_recursive_cycles_fail_unsupported_without_serializing_or_recursing(shape):
    if shape == "array":
        value = {"kind": "array", "nullable": False}
        value["items"] = value
    elif shape == "object":
        value = _object()
        value["properties"].append(_property(contract=value))
    else:
        value = ArrayContract(nullable=False, items=NullContract())
        object.__setattr__(value, "items", value)
    with pytest.raises(ClosedContractUnsupported, match="UNSUPPORTED: cyclic"):
        parse_closed_contract(value)


def test_input_lists_and_dicts_cannot_mutate_nested_contract_authority():
    raw = _object(_property(contract={"kind": "array", "nullable": False, "items": _scalar(enum=["b", "a"])}))
    contract = parse_closed_contract(raw)
    before = contract.model_dump_json()
    raw["properties"][0]["name"] = "changed"
    raw["properties"][0]["contract"]["items"]["enum"].append("later")
    raw["properties"].append(_property("later"))
    assert contract.model_dump_json() == before
    assert isinstance(contract.properties, tuple)
    assert isinstance(contract.properties[0].contract.items.enum, tuple)
    with pytest.raises(ValidationError, match="frozen"):
        contract.properties[0].required = False
    with pytest.raises(ValidationError, match="frozen"):
        contract.properties[0].contract.items.enum += ("later",)
    with pytest.raises(TypeError):
        contract.properties[0] = ContractProperty(name="new", required=True, contract=NullContract())
    with pytest.raises(ValidationError, match="frozen"):
        contract.nullable = True


@pytest.mark.parametrize("model", [NullContract(), ScalarContract(kind="string", nullable=False), ArrayContract(nullable=False, items=NullContract()), ObjectContract(nullable=False, properties=(), additional_properties=False), ContractProperty(name="value", required=False, contract=NullContract())])
def test_update_copy_paths_reject_and_safe_copies_revalidate(model):
    with pytest.raises(TypeError, match="model_validate"):
        model.model_copy(update={"nullable": True})
    with pytest.raises(TypeError, match="model_validate"):
        model.copy(update={"nullable": True})
    with pytest.raises(TypeError, match="model_validate"):
        model.copy(exclude={"kind"})
    with pytest.raises(TypeError, match="model_validate"):
        model.copy(include=set())
    assert model.model_copy() == model
    assert model.model_copy(deep=True) == model
    assert model.copy() == model


def test_forged_existing_and_nested_models_are_revalidated_not_trusted():
    scalar = ScalarContract.model_construct(kind="integer", nullable=False, enum=(True,))
    with pytest.raises(ValueError, match="exact scalar kind"):
        parse_closed_contract(scalar)
    with pytest.raises(ValueError, match="exact scalar kind"):
        ArrayContract(nullable=False, items=scalar)
    with pytest.raises(ValueError, match="exact scalar kind"):
        scalar.model_copy()
    opened = ObjectContract.model_construct(kind="object", nullable=False, properties=(), additional_properties=0)
    with pytest.raises(ValueError, match="explicitly false"):
        ContractProperty(name="value", required=True, contract=opened)
    nullable_null = NullContract.model_construct(kind="null")
    object.__setattr__(nullable_null, "nullable", True)
    with pytest.raises(ValueError, match="Extra inputs"):
        parse_closed_contract(nullable_null)


@pytest.mark.parametrize("collection", ["generator", "set", "iterator", "mapping"])
def test_property_collection_cannot_bypass_whole_contract_bounds(collection):
    prop = ContractProperty(name="value", required=True, contract=NullContract())
    values = {
        "generator": (item for item in (prop,)),
        "set": {prop},
        "iterator": iter((prop,)),
        "mapping": {"value": prop},
    }[collection]
    with pytest.raises(ValueError, match="explicit list or tuple"):
        ObjectContract(nullable=False, properties=values, additional_properties=False)


def test_nested_forged_instances_cannot_reintroduce_mutable_or_unvalidated_authority():
    bad_scalar = ScalarContract.model_construct(kind="integer", nullable=False, enum=[True])
    bad_property = ContractProperty.model_construct(name="value", required=True, contract=bad_scalar)
    bad_object = ObjectContract.model_construct(
        kind="object", nullable=False, properties=[bad_property], additional_properties=False,
    )
    for validate in (
        lambda: ObjectContract.model_validate(bad_object),
        lambda: bad_object.model_copy(deep=True),
        lambda: ArrayContract(nullable=False, items=bad_object),
        lambda: ObjectContract(nullable=False, properties=[bad_property], additional_properties=False),
    ):
        with pytest.raises(ValueError, match="exact scalar kind"):
            validate()
    invalid_required = ContractProperty.model_construct(name="value", required=1, contract=NullContract())
    with pytest.raises(ValueError, match="valid boolean"):
        ObjectContract(nullable=False, properties=[invalid_required], additional_properties=False)


def _deterministic_example(reverse=False):
    properties = [
        _property("zeta", required=False, contract=_scalar("number", enum=[1.5, -0.0, -2.0])),
        _property("alpha", contract={"kind": "array", "nullable": True, "items": _object(_property("child", contract=_scalar(enum=["z", "a"])))}),
    ]
    if reverse:
        properties.reverse()
        properties = [dict(reversed(tuple(prop.items()))) for prop in properties]
        number = next(prop["contract"] for prop in properties if prop["name"] == "zeta")
        number["enum"].reverse()
    raw = _object(*properties)
    if reverse:
        raw = dict(reversed(tuple(raw.items())))
    return parse_closed_contract(raw)


def test_property_enum_mapping_order_and_round_trip_preserve_exact_canonical_identity():
    first = _deterministic_example()
    second = _deterministic_example(reverse=True)
    assert first == second
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.model_dump_json() == second.model_dump_json()
    assert first.contract_digest == second.contract_digest
    assert canonical_json(first.identity_payload) == canonical_json(second.identity_payload)
    assert parse_closed_contract(json.loads(first.model_dump_json())) == first
    assert ObjectContract.model_validate_json(first.model_dump_json()) == first
    negative = ScalarContract(kind="number", nullable=False, enum=(-0.0,))
    positive = ScalarContract(kind="number", nullable=False, enum=(0.0,))
    assert negative.model_dump_json() == positive.model_dump_json()
    assert negative.contract_digest == positive.contract_digest


def test_contract_identity_is_identical_in_repeated_processes():
    script = """
import json
from tests.test_closed_contracts import _deterministic_example
contract = _deterministic_example(reverse=True)
print(json.dumps([contract.model_dump_json(), contract.contract_digest]))
"""
    first = subprocess.check_output([sys.executable, "-c", script], text=True)
    second = subprocess.check_output([sys.executable, "-c", script], text=True)
    assert first == second
    assert json.loads(first) == [_deterministic_example().model_dump_json(), _deterministic_example().contract_digest]
