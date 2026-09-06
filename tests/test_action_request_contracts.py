"""Action request authority replacement and classified semantic identity proof."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from mozaiksai.core.semantics.archive import (
    ArchiveEntry,
    archive_digest,
    build_deterministic_archive,
)
from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.closed_contracts import ObjectContract
from mozaiksai.core.semantics.payloads import ActionPayload, build_semantic_payload
from mozaiksai.core.semantics.refs import ExecutionAccessScopeRef


def _empty_request() -> dict:
    return {"kind": "object", "nullable": False, "properties": [], "additional_properties": False}


def _action(**fields) -> ActionPayload:
    return build_semantic_payload(
        ActionPayload, node_id="mozaiks.action.request", payload_version=1,
        scope=ExecutionAccessScopeRef(tenant_id="tenant1"), description=None, **fields,
    )


def test_action_requires_explicit_non_null_closed_empty_request() -> None:
    with pytest.raises(ValidationError, match="request_contract"):
        _action()
    action = _action(request_contract=_empty_request())
    assert action.request_contract == ObjectContract(
        nullable=False, properties=(), additional_properties=False,
    )
    assert "request_fields" not in ActionPayload.model_fields
    with pytest.raises(ValidationError, match="extra_forbidden"):
        _action(request_contract=_empty_request(), request_fields=())
    with pytest.raises(ValidationError):
        _action(request_fields=())


@pytest.mark.parametrize("contract", [
    None,
    {"kind": "null"},
    {"kind": "string", "nullable": False},
    {"kind": "array", "nullable": False, "items": {"kind": "null"}},
    {**_empty_request(), "nullable": True},
    {"kind": "object", "nullable": False, "properties": []},
    {**_empty_request(), "additional_properties": True},
])
def test_invalid_action_request_roots_reject(contract) -> None:
    with pytest.raises(ValidationError):
        _action(request_contract=contract)


def test_action_owns_detached_deeply_immutable_request_authority() -> None:
    request = _empty_request()
    request["properties"] = [{
        "name": "labels", "required": False,
        "contract": {"kind": "array", "nullable": True, "items": {
            "kind": "string", "nullable": False, "enum": ["z", "a"],
        }},
    }]
    action = _action(request_contract=request)
    before = action.model_dump_json()
    request["properties"][0]["contract"]["items"]["enum"].append("changed")
    request["properties"].clear()
    assert action.model_dump_json() == before
    prop = action.request_contract.properties[0]
    with pytest.raises(ValidationError):
        prop.required = True
    with pytest.raises(ValidationError):
        prop.contract.items.enum += ("changed",)
    with pytest.raises(ValidationError):
        action.request_contract = ObjectContract(nullable=False, properties=(), additional_properties=False)
    for model in (action, action.request_contract, prop, prop.contract, prop.contract.items):
        with pytest.raises(TypeError):
            model.model_copy(update={"request_contract": {}})
        with pytest.raises(TypeError):
            model.copy(update={"request_contract": {}})
        assert model.model_copy() == model
        assert model.model_copy(deep=True) == model
    assert ActionPayload.model_validate_json(before).model_dump_json() == before


def test_action_revalidates_existing_nested_contracts_and_self_digest() -> None:
    action = _action(request_contract=_empty_request())
    raw = action.model_dump(mode="python")
    # Unchecked construction remains untrusted at every normal parse/copy boundary.
    raw["request_contract"] = ObjectContract.model_construct(
        kind="object", nullable=False, properties=[], additional_properties=True,
    )
    forged = ActionPayload.model_construct(**raw)
    for validate in (ActionPayload.model_validate, lambda value: value.model_copy()):
        with pytest.raises(ValidationError):
            validate(forged)
    changed = action.model_dump(mode="json")
    changed["request_contract"]["properties"] = [{
        "name": "value", "required": True, "contract": {"kind": "null"},
    }]
    with pytest.raises(ValidationError, match="payload_digest does not match"):
        ActionPayload.model_validate(changed)


def test_semantic_migration_changes_only_action_request_and_containing_identity() -> None:
    from tests.test_compilation_plan import _plan
    from tests.test_semantic_payload_graph_v2 import _corpus_graph

    baseline = json.loads((Path(__file__).parent / "fixtures/action-request-migration.json").read_text())
    assert baseline["base_commit"] == "e2713d64205079bad8c7b3dbc44186f26998e652"
    graph, payloads = _corpus_graph()
    action = next(payload for payload in payloads if isinstance(payload, ActionPayload))
    original_action = baseline["action_payload"]
    assert action.payload_digest != original_action["payload_digest"]
    assert action.request_contract.model_dump(mode="json") == {
        "kind": "object", "nullable": False, "additional_properties": False,
        "properties": [{"name": "name", "required": True,
                        "contract": {"kind": "string", "nullable": False, "enum": None}}],
    }
    restored_action = action.canonical_payload(include_digest=False)
    restored_action.pop("request_contract")
    restored_action["request_fields"] = original_action["request_fields"]
    assert canonical_digest(restored_action) == original_action["payload_digest"]
    restored_action["payload_digest"] = original_action["payload_digest"]
    assert restored_action == original_action
    original_digests = {
        entry["node_id"]: entry["payload_digest"] for entry in baseline["payload_digests"]
    }
    for payload in payloads:
        if payload.node_id != action.node_id:
            assert payload.payload_digest == original_digests[payload.node_id]

    restored_graph = graph.canonical_payload(include_digest=False)
    node = next(node for node in restored_graph["nodes"] if node["node_id"] == action.node_id)
    node["payload_ref"]["content_digest"] = original_action["payload_digest"]
    assert canonical_digest(restored_graph) == baseline["graph_digest"]
    restored_graph["graph_digest"] = baseline["graph_digest"]
    assert graph.graph_digest != baseline["graph_digest"]

    plan = _plan()
    restored_plan = plan.canonical_payload(include_digest=False)
    restored_plan["graph_digest"] = baseline["graph_digest"]
    assert canonical_digest(restored_plan) == baseline["plan_digest"]
    assert len(plan.units) == 61
    # This exact equality permits no unit/source/row changes hidden by a golden repin.
    assert plan.plan_digest != baseline["plan_digest"]

    entries = [ArchiveEntry(
        path=f"payloads/{payload.node_id}.json",
        content=json.dumps(
            restored_action if payload.node_id == action.node_id else payload.canonical_payload(),
            sort_keys=True, ensure_ascii=True,
        ).encode("ascii"),
    ) for payload in payloads]
    entries.append(ArchiveEntry(path="graph.json", content=json.dumps(
        restored_graph, sort_keys=True, ensure_ascii=True,
    ).encode("ascii")))
    assert archive_digest(build_deterministic_archive(entries)) == baseline["archive_digest"]


def test_referenced_action_request_change_preserves_real_interface_reuse_and_bytes() -> None:
    from mozaiksai.core.semantics import materialization
    from mozaiksai.core.semantics.compilation_plan import plan_regeneration_closure
    from tests import test_workflow_capability_semantics as capability
    from tests.test_workflow_interface_rematerialization import (
        _fixture,
        _mutate,
        _rematerialize,
        _state,
        _unit,
    )

    fixture = _fixture()
    base = _state(fixture, reload=True)
    original = copy.deepcopy(fixture.payloads)
    _mutate(fixture, "action_request_schema")
    changed = fixture.payloads[capability._ACTION_GET]
    before = original[capability._ACTION_GET]
    assert changed.request_contract != before.request_contract
    assert changed.node_id == before.node_id
    assert changed.payload_digest != before.payload_digest
    for node_id, payload in fixture.payloads.items():
        if node_id != changed.node_id:
            assert payload == original[node_id]  # Includes module ownership and capability bindings.
    successor = _state(fixture, version=2, reload=True)
    unit_id = _unit(base).unit_id
    assert _unit(base).identity_payload == _unit(successor).identity_payload
    assert unit_id in plan_regeneration_closure(base["plan"], successor["plan"]).reusable
    bundle = materialization.materialize_plan(**base)
    refreshed = _rematerialize(base, successor, bundle)
    before_output = next(output for output in bundle.outputs if output.unit_id == unit_id)
    after_output = next(output for output in refreshed.outputs if output.unit_id == unit_id)
    assert after_output.origin == "reused"
    assert before_output.content == after_output.content
    assert refreshed.files() == materialization.materialize_plan(**successor).files()


def test_action_request_algebra_is_recursively_closed_strict_and_immutable() -> None:
    from types import UnionType
    from typing import Annotated, Any, Literal, Union, get_args, get_origin

    from pydantic import BaseModel, Strict

    from mozaiksai.core.semantics.closed_contracts import (
        ArrayContract,
        ClosedContract,
        ContractProperty,
        NullContract,
        ScalarContract,
    )

    visited: set[type[BaseModel]] = set()

    def walk(annotation, metadata=()):
        assert annotation is not Any, "request authority cannot contain Any"
        origin = get_origin(annotation)
        if origin is Annotated:
            nested, *details = get_args(annotation)
            walk(nested, (*metadata, *details))
        elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
            if annotation in visited:
                return
            visited.add(annotation)
            assert annotation.model_config.get("extra") == "forbid"
            assert annotation.model_config.get("frozen") is True
            assert annotation.model_config.get("revalidate_instances") == "always"
            for field in annotation.model_fields.values():
                walk(field.annotation, field.metadata)
        elif origin in (Union, UnionType):
            for member in get_args(annotation):
                walk(member)
        elif origin is tuple:
            args = get_args(annotation)
            assert len(args) == 2 and args[1] is Ellipsis, "only immutable homogeneous collections"
            walk(args[0])
        elif origin is Literal:
            assert all(type(value) is bool or isinstance(value, str) for value in get_args(annotation))
        elif annotation is type(None):
            return
        elif annotation in (bool, int, float, str):
            assert any(isinstance(item, Strict) and item.strict for item in metadata)
        else:
            raise AssertionError(f"open or mutable request-contract annotation: {annotation!r}")

    walk(ActionPayload.model_fields["request_contract"].annotation)
    walk(ClosedContract)
    assert visited == {NullContract, ScalarContract, ArrayContract, ObjectContract, ContractProperty}


@pytest.mark.parametrize("nested", [
    {"kind": "integer", "nullable": False, "enum": [True]},
    {"kind": "number", "nullable": False, "enum": [1]},
    {"kind": "number", "nullable": False, "enum": [float("nan")]},
    {"kind": "number", "nullable": False, "enum": [float("inf")]},
    {"kind": "number", "nullable": False, "enum": [float("-inf")]},
    {"kind": "string", "nullable": 0},
])
def test_action_payload_boundary_preserves_nested_strict_and_finite_scalar_domains(nested) -> None:
    request = _empty_request()
    request["properties"] = [{
        "name": "nested", "required": True,
        "contract": {"kind": "array", "nullable": False, "items": nested},
    }]
    with pytest.raises(ValidationError):
        _action(request_contract=request)
