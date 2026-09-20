"""Exact schema import and offline request-authority projection fail closed."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from mozaiksai.core.runtime.app.module_loader import ModuleDefinition
from mozaiksai.core.semantics.closed_contract_schema import import_closed_contract_schema
from mozaiksai.core.semantics.closed_contracts import (
    MAX_CONTRACT_DEPTH,
    MAX_CONTRACT_NODES,
    ArrayContract,
    ClosedContractUnsupported,
    NullContract,
    ObjectContract,
    ScalarContract,
)
from mozaiksai.core.semantics.offline_projection import (
    ProjectionDisposition,
    ProjectionError,
    ProjectionGapKind,
    project_semantic_graph,
)
from mozaiksai.core.semantics.payloads import ActionPayload
from tests.test_semantic_offline_projection import SCOPE, _pinned_registry


def _object(properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object", "properties": properties or {}, "required": required or [],
        "additionalProperties": False,
    }


def _source(schema: Any) -> dict[str, Any]:
    return {"modules": [{"manifest": {
        "module": {"id": "probe"},
        "actions": [{"id": "write", "input_schema": schema}],
    }}]}


def _project(source: dict[str, Any]):
    return project_semantic_graph(
        source, graph_id="request-contract-import", version=1, scope=SCOPE,
        taxonomy_registry=_pinned_registry(),
    )


def _nested_schema(depth: int) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "integer"}
    for _ in range(depth - 1):
        schema = {"type": "array", "items": schema}
    return schema


def test_nested_schema_import_preserves_exact_properties_requiredness_and_scalar_enums() -> None:
    schema = _object({
        "optional": {"type": "null"},
        "exact name": {"type": "string", "enum": ["z", "a"]},
        "records": {"type": "array", "items": _object({"enabled": {"type": "boolean"}}, ["enabled"])},
    }, ["records", "exact name"])
    before = copy.deepcopy(schema)
    contract = import_closed_contract_schema(schema)
    assert isinstance(contract, ObjectContract)
    assert contract.nullable is False
    assert contract.additional_properties is False
    assert [prop.name for prop in contract.properties] == ["exact name", "optional", "records"]
    assert [prop.required for prop in contract.properties] == [True, False, True]
    assert isinstance(contract.properties[0].contract, ScalarContract)
    assert contract.properties[0].contract.enum == ("a", "z")
    assert isinstance(contract.properties[1].contract, NullContract)
    assert isinstance(contract.properties[2].contract, ArrayContract)
    assert schema == before
    schema["properties"].clear()
    assert len(contract.properties) == 3


@pytest.mark.parametrize("schema", [
    {"type": "null"}, {"type": "boolean", "enum": [True, False]},
    {"type": "integer", "enum": [2, 1]}, {"type": "number", "enum": [2.0, 1.5]},
    {"type": "string"}, {"type": "array", "items": {"type": "null"}},
    _object(), {"type": "object", "additionalProperties": False},
])
def test_supported_exact_schema_shapes_import(schema: dict[str, Any]) -> None:
    contract = import_closed_contract_schema(schema)
    assert contract.contract_digest


def test_property_and_enum_order_do_not_change_imported_identity() -> None:
    first = _object({"z": {"type": "integer", "enum": [2, 1]}, "a": {"type": "string"}}, ["z", "a"])
    second = _object({"a": {"type": "string"}, "z": {"type": "integer", "enum": [1, 2]}}, ["a", "z"])
    assert import_closed_contract_schema(first) == import_closed_contract_schema(second)
    assert import_closed_contract_schema(first).model_dump_json() == import_closed_contract_schema(second).model_dump_json()


@pytest.mark.parametrize("schema", [
    {}, True, False, {"type": "unknown"}, {"type": ["string", "null"]},
    {"type": "object"}, {"type": "object", "additionalProperties": True},
    {"type": "object", "additionalProperties": {}},
    {"type": "object", "additionalProperties": None},
    {"type": "object", "additionalProperties": 0},
    {"type": "array"}, {"type": "array", "items": [{"type": "string"}]},
    {"type": "array", "items": {}}, {"type": "null", "enum": [None]},
    {"type": "null", "nullable": True}, {"type": "string", "items": {"type": "string"}},
    {"type": "integer", "enum": [True]}, {"type": "integer", "enum": [1.0]},
    {"type": "number", "enum": [1]}, {"type": "number", "enum": [1.0, 2]},
    {"type": "string", "enum": ["x", None]}, {"type": "string", "enum": ["x", "x"]},
    {"type": "string", "enum": []}, {"type": "string", "enum": None},
    {"type": "number", "enum": [float("nan")]},
    {"type": "number", "enum": [float("inf")]},
    {"type": "number", "enum": [-float("inf")]},
    _object({"": {"type": "string"}}), _object({1: {"type": "string"}}),
    _object({"x": {"type": "string"}}, ["missing"]),
    _object({"x": {"type": "string"}}, ["x", "x"]),
    {"type": "object", "properties": [], "additionalProperties": False},
])
def test_malformed_or_unrepresentable_schema_is_unsupported(schema: Any) -> None:
    with pytest.raises(ClosedContractUnsupported, match="UNSUPPORTED"):
        import_closed_contract_schema(schema)


@pytest.mark.parametrize("keyword,value", [
    ("format", "email"), ("pattern", "[a-z]+"), ("minimum", 0), ("maximum", 3),
    ("exclusiveMinimum", 0), ("exclusiveMaximum", 3), ("multipleOf", 2),
    ("minLength", 1), ("maxLength", 5), ("minItems", 1), ("maxItems", 5),
    ("uniqueItems", True), ("contains", {"type": "string"}),
    ("prefixItems", [{"type": "string"}]),
    ("$ref", "https://example.invalid/schema"), ("$ref", "#/$defs/recursive"),
    ("$dynamicRef", "#dynamic"), ("$defs", {"recursive": {"$ref": "#/$defs/recursive"}}),
    ("anyOf", [{"type": "string"}, {"type": "null"}]),
    ("oneOf", [{"type": "string"}, {"type": "integer"}]),
    ("allOf", [{"type": "string"}]), ("not", {"type": "null"}),
    ("if", {"type": "string"}), ("then", {"type": "string"}), ("else", {"type": "string"}),
    ("dependentSchemas", {"x": {"type": "string"}}), ("validator", "custom.validate"),
])
def test_unsupported_schema_assertions_are_never_silently_discarded(keyword: str, value: Any) -> None:
    with pytest.raises(ClosedContractUnsupported, match=keyword.replace("$", r"\$")):
        import_closed_contract_schema({"type": "string", keyword: value})


def test_schema_depth_and_node_limits_are_shared_with_the_canonical_profile() -> None:
    assert import_closed_contract_schema(_nested_schema(MAX_CONTRACT_DEPTH))
    assert import_closed_contract_schema(_object({f"p{i}": {"type": "null"} for i in range(MAX_CONTRACT_NODES - 1)}))
    with pytest.raises(ClosedContractUnsupported, match="depth exceeds"):
        import_closed_contract_schema(_nested_schema(MAX_CONTRACT_DEPTH + 1))
    with pytest.raises(ClosedContractUnsupported, match="nodes exceed"):
        import_closed_contract_schema(_object({f"p{i}": {"type": "null"} for i in range(MAX_CONTRACT_NODES)}))


def test_shared_acyclic_schema_occurrences_are_allowed_but_cycles_reject() -> None:
    shared = {"type": "string"}
    contract = import_closed_contract_schema(_object({"a": shared, "b": shared}))
    assert isinstance(contract, ObjectContract)
    assert len(contract.properties) == 2
    cyclic: dict[str, Any] = {"type": "array"}
    cyclic["items"] = cyclic
    with pytest.raises(ClosedContractUnsupported, match="cyclic"):
        import_closed_contract_schema(cyclic)


def test_projector_uses_explicit_request_contract_and_marks_every_schema_fact_projected() -> None:
    schema = _object({"message": {"type": "string"}, "count": {"type": "integer", "enum": [2, 1]}}, ["message"])
    result = _project(_source(schema))
    action = next(payload for payload in result.payloads if isinstance(payload, ActionPayload))
    assert action.request_contract == import_closed_contract_schema(schema)
    rows = [row for row in result.coverage if ".input_schema" in row.source_path]
    assert rows
    assert all(row.disposition is ProjectionDisposition.PROJECTED for row in rows)
    assert result.source_facts == result.represented_facts


def test_missing_schema_and_surface_only_action_reject_as_missing_authority() -> None:
    missing = _source(_object())
    missing["modules"][0]["manifest"]["actions"][0].pop("input_schema")
    with pytest.raises(ProjectionError) as failure:
        _project(missing)
    assert failure.value.gaps[0].kind is ProjectionGapKind.MISSING
    surface = {"app_build_plan": {"surface_map": {"surfaces": [{
        "surface_id": "probe", "surface_kind": "module", "owner": "app",
        "owned_mutations": ["write"],
    }]}}}
    with pytest.raises(ProjectionError) as failure:
        _project(surface)
    assert failure.value.gaps[0].kind is ProjectionGapKind.MISSING
    assert "input_schema" in failure.value.gaps[0].reason
    combined = {**surface, **_source(_object())}
    assert any(isinstance(payload, ActionPayload) for payload in _project(combined).payloads)


@pytest.mark.parametrize("schema", [None, {}, {"type": "object"}, {"type": "string"}, {"type": "array", "items": {"type": "null"}}])
def test_unknown_or_non_object_projection_request_rejects(schema: Any) -> None:
    with pytest.raises(ProjectionError) as failure:
        _project(_source(schema))
    expected = ProjectionGapKind.MISSING if schema is None else ProjectionGapKind.UNSUPPORTED
    assert failure.value.gaps[0].kind is expected


def test_typed_runtime_manifest_default_schema_is_not_closed_request_authority() -> None:
    module = ModuleDefinition.model_validate({
        "schema_version": "mozaiks.module.v1",
        "module": {"id": "probe", "handler": "backend.handler:Handler"},
        "actions": [{"id": "write", "description": "Write a record", "handler_method": "write"}],
    })
    with pytest.raises(ProjectionError) as failure:
        _project({"modules": [{"manifest": module}]})
    assert failure.value.gaps[0].kind is ProjectionGapKind.UNSUPPORTED


def test_schema_bombs_reject_before_projectors_recursive_source_copy() -> None:
    cyclic: dict[str, Any] = _object()
    cyclic["properties"]["self"] = cyclic
    for schema in (cyclic, _object({"deep": _nested_schema(2000)})):
        with pytest.raises(ProjectionError) as failure:
            _project(_source(schema))
        assert failure.value.gaps[0].kind is ProjectionGapKind.UNSUPPORTED
