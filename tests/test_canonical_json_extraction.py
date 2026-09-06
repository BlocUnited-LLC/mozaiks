"""Shared JSON extraction preserves the exact pre-extraction authority contract."""

from __future__ import annotations

import hashlib
import json
from collections import UserDict
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from mozaiksai.core.runtime.app.layout_registry import build_app_layout_registry
from mozaiksai.core.semantics import canonical_json, plan_authority
from mozaiksai.core.semantics.canonical import CanonicalSerializationError, canonical_digest
from mozaiksai.core.semantics.canonical_json import (
    CanonicalJsonArray,
    CanonicalJsonEntry,
    CanonicalJsonObject,
)
from mozaiksai.core.semantics.graph import SemanticNodeV2, build_semantic_graph_v2
from mozaiksai.core.semantics.payloads import (
    WorkflowPayload,
    build_semantic_payload,
    semantic_payload_ref,
)
from mozaiksai.core.semantics.refs import ExecutionAccessScopeRef

# Captured before extraction at exact main e2713d64205079bad8c7b3dbc44186f26998e652.
# The workflow-only authority intentionally excludes ActionPayload so the
# separate request-contract migration cannot account for any drift here.
_REFERENCE_JSON = (
    '{"entries":[{"key":"ordered","value":{"entries":[{"key":"z","value":null},'
    '{"key":"a","value":{"items":[true,false,0,1,-7,1.25,"\u00e9",'
    '{"entries":[{"key":"empty","value":{"entries":[]}},'
    '{"key":"array","value":{"items":[]}}]}]}}]}},'
    '{"key":"integer_boundary","value":{"items":[-9223372036854775808,9223372036854775807]}},'
    '{"key":"nested","value":{"entries":[{"key":"quote","value":"a\\\"b"},'
    '{"key":"backslash","value":"a\\\\b"},{"key":"zero","value":-0.0}]}}]}'
)
_REFERENCE_VALUE_DIGEST = "539a466bcae96dc6afc1dcc911e13fe135122984d7a361916042f20b6d397d93"
_REFERENCE_DOCUMENT_DIGEST = "f40bbcad06a16663633f63b97c18f3649211295f42397bcf1d34e3703413ec7f"
_REFERENCE_DOCUMENT_BYTES_SHA256 = "0adbcd2aae3eca7a7fa32cbbbb676d38d902bbbd4ad0f3566b0fcdb7f32c2dd7"
_REFERENCE_AUTHORITY_BYTE_COUNT = 59230


def _source() -> dict[str, Any]:
    return {
        "ordered": {"z": None, "a": [True, False, 0, 1, -7, 1.25, "\u00e9", {"empty": {}, "array": []}]},
        "integer_boundary": [-(2**63), 2**63 - 1],
        "nested": {"quote": 'a"b', "backslash": "a\\b", "zero": -0.0},
    }


def _authority():
    scope = ExecutionAccessScopeRef(tenant_id="tenant-json", workspace_id="workspace-json")
    payload = build_semantic_payload(
        WorkflowPayload,
        node_id="mozaiks.workflow.probe",
        payload_version=1,
        scope=scope,
        workflow_id="probe",
        description="No-action authority preserves extraction evidence",
        startup_mode=None,
        topology=None,
    )
    graph = build_semantic_graph_v2(
        graph_id="canonical-json-extraction",
        version=1,
        scope=scope,
        nodes=[SemanticNodeV2(
            node_id=payload.node_id, kind=payload.payload_kind, payload_ref=semantic_payload_ref(payload),
        )],
        edges=[],
    )
    return plan_authority.build_compilation_plan_authority_inputs(
        graph=graph, payloads=[payload], registry=build_app_layout_registry(()),
        structured_output_configs=_source(),
    )


def test_plan_authority_uses_the_same_shared_types_without_parallel_models() -> None:
    for name in ("CanonicalJsonArray", "CanonicalJsonEntry", "CanonicalJsonObject", "CanonicalJsonValue"):
        assert getattr(plan_authority, name) is getattr(canonical_json, name)
    assert plan_authority._AuthorityModel is canonical_json._AuthorityModel
    assert issubclass(plan_authority.CompilationPlanAuthorityInputs, canonical_json._AuthorityModel)


def test_value_serialization_and_digest_are_byte_identical_to_exact_base() -> None:
    value = CanonicalJsonObject.from_python(_source())
    assert value.model_dump_json().encode("utf-8") == _REFERENCE_JSON.encode("utf-8")
    assert value.model_dump(mode="json") == json.loads(_REFERENCE_JSON)
    assert canonical_digest(value.model_dump(mode="json")) == _REFERENCE_VALUE_DIGEST
    assert value.to_python() == _source()
    assert CanonicalJsonObject.model_validate_json(_REFERENCE_JSON) == value


def test_complete_plan_authority_serialization_and_digest_are_unchanged() -> None:
    authority = _authority()
    encoded = authority.model_dump_json().encode("utf-8")
    assert len(encoded) == _REFERENCE_AUTHORITY_BYTE_COUNT
    assert hashlib.sha256(encoded).hexdigest() == _REFERENCE_DOCUMENT_BYTES_SHA256
    assert plan_authority.compilation_plan_authority_digest(authority) == _REFERENCE_DOCUMENT_DIGEST
    reloaded = plan_authority.CompilationPlanAuthorityInputs.model_validate_json(encoded)
    assert reloaded == authority
    assert reloaded.model_dump_json().encode("utf-8") == encoded


def test_declaration_and_array_order_remain_part_of_authority_identity() -> None:
    first = CanonicalJsonObject.from_python({"z": [1, 2], "a": False})
    reordered_properties = CanonicalJsonObject.from_python({"a": False, "z": [1, 2]})
    reordered_array = CanonicalJsonObject.from_python({"z": [2, 1], "a": False})
    assert [entry.key for entry in first.entries] == ["z", "a"]
    assert list(first.to_python()) == ["z", "a"]
    assert len({canonical_digest(value.model_dump(mode="json")) for value in (
        first, reordered_properties, reordered_array,
    )}) == 3


@pytest.mark.parametrize("value,expected", [
    (None, None), (True, True), (False, False), (0, 0), (1, 1), (-4, -4),
    (1.5, 1.5), ("text", "text"), ([], []), ((), []),
    ([1, (True, {"nested": None})], [1, [True, {"nested": None}]]),
    (UserDict({"b": 1, "a": False}), {"b": 1, "a": False}),
])
def test_existing_python_value_domain_and_scalar_types_remain_accepted(value: Any, expected: Any) -> None:
    wrapped = CanonicalJsonObject.from_python({"value": value})
    result = wrapped.to_python()["value"]
    assert result == expected
    assert type(result) is type(expected)
    assert CanonicalJsonObject.model_validate_json(wrapped.model_dump_json()) == wrapped


def test_boolean_integer_and_float_values_remain_distinct_in_serialization() -> None:
    value = CanonicalJsonObject.from_python({"values": [True, False, 1, 0, 1.0, 0.0]})
    assert [type(item) for item in value.to_python()["values"]] == [bool, bool, int, int, float, float]
    assert value.model_dump_json() == '{"entries":[{"key":"values","value":{"items":[true,false,1,0,1.0,0.0]}}]}'


def test_value_acceptance_does_not_expand_or_narrow_canonical_digest_integer_limits() -> None:
    # The pre-existing value algebra accepts Python integers; the separately
    # versioned canonical byte encoder owns its signed-64-bit digest limit.
    value = CanonicalJsonObject.from_python({"integer": 2**100})
    assert value.to_python()["integer"] == 2**100
    with pytest.raises(CanonicalSerializationError, match="signed 64-bit"):
        canonical_digest(value.model_dump(mode="json"))


@pytest.mark.parametrize("value", [
    float("nan"), float("inf"), -float("inf"), {"nested": float("nan")},
    [float("inf")], {1, 2}, frozenset({1}), b"bytes", bytearray(b"bytes"),
    Decimal("1.5"), complex(1, 2), date(2026, 1, 1), object(), {1: "not a string key"},
])
def test_non_json_values_still_reject_at_construction(value: Any) -> None:
    with pytest.raises(ValueError):
        CanonicalJsonObject.from_python({"value": value})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_direct_typed_construction_still_rejects_nonfinite_scalars(value: float) -> None:
    with pytest.raises(ValidationError, match="NaN and infinite"):
        CanonicalJsonEntry(key="number", value=value)
    with pytest.raises(ValidationError, match="NaN and infinite"):
        CanonicalJsonArray(items=(value,))


def test_duplicate_keys_and_unknown_model_fields_still_reject() -> None:
    with pytest.raises(ValidationError, match="duplicate keys"):
        CanonicalJsonObject(entries=(CanonicalJsonEntry(key="same", value=1), CanonicalJsonEntry(key="same", value=2)))
    with pytest.raises(ValidationError, match="Extra inputs"):
        CanonicalJsonObject.model_validate({"entries": [], "extra": True})


def test_nested_authority_is_detached_from_mutable_inputs_and_outputs() -> None:
    source = {"object": {"list": [1, {"value": "original"}]}}
    value = CanonicalJsonObject.from_python(source)
    before = value.model_dump_json()
    source["object"]["list"][1]["value"] = "mutated input"
    source["object"]["list"].append(False)
    exported = value.to_python()
    exported["object"]["list"][1]["value"] = "mutated output"
    assert value.model_dump_json() == before
    outer = value.entries[0]
    nested = outer.value
    assert isinstance(nested, CanonicalJsonObject)
    array = nested.entries[0].value
    assert isinstance(array, CanonicalJsonArray)
    assert isinstance(array.items, tuple)
    assert isinstance(array.items[1], CanonicalJsonObject)
    with pytest.raises(ValidationError, match="frozen"):
        value.entries = ()
    with pytest.raises(ValidationError, match="frozen"):
        outer.value = None
    with pytest.raises(ValidationError, match="frozen"):
        array.items = ()
    with pytest.raises(TypeError):
        array.items[0] = 2


def test_copy_semantics_preserve_deep_immutability_without_update_bypass() -> None:
    value = CanonicalJsonObject.from_python({"nested": [{"item": 1}]})
    array = value.entries[0].value
    assert isinstance(array, CanonicalJsonArray)
    for model in (value, value.entries[0], array, array.items[0], _authority()):
        assert model.model_copy() == model
        assert model.model_copy(deep=True) == model
        assert model.model_copy(update={}) == model
        with pytest.raises(TypeError, match="does not support model_copy"):
            model.model_copy(update={"unvalidated": []})
