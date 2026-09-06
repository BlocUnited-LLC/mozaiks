"""Canonical model compilation stays separate from provider response formatting."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys

import pytest
from pydantic import BaseModel, ValidationError, create_model

from mozaiksai.core.workflow.outputs import structured

MODELS = {
    # Declare the dependent first to exercise the compiler's deferred pass.
    "Envelope": {
        "type": "model",
        "fields": {
            "child": {"type": "Child"},
            "trace": {"type": "optional_str"},
            "attempts": {"type": "int", "default": 1},
            "rows": {"type": "optional_list", "items": "Child"},
        },
    },
    "Child": {
        "type": "model",
        "fields": {
            "label": {"type": "str"},
            "note": {"type": "optional_str"},
        },
    },
}


@pytest.fixture(autouse=True)
def isolated_caches():
    caches = (
        structured._workflow_models,
        structured._workflow_registries,
        structured._workflow_structured_agents,
        structured._provider_response_model_cache,
    )
    saved = [dict(cache) for cache in caches]
    for cache in caches:
        cache.clear()
    try:
        yield
    finally:
        for cache, prior in zip(caches, saved, strict=True):
            cache.clear()
            cache.update(prior)


def test_canonical_compilation_never_invokes_provider_schema_patch(monkeypatch):
    def forbidden_patch(_model):
        raise AssertionError("canonical model compilation reached provider preparation")

    monkeypatch.setattr(structured, "_patch_model_schema", forbidden_patch)
    models = structured.build_models_from_config(copy.deepcopy(MODELS))
    assert set(models) == {"Child", "Envelope"}
    for model in models.values():
        assert "model_json_schema" not in model.__dict__
        assert "__mozaiks_schema_patched" not in model.__dict__
        assert model.model_json_schema() == BaseModel.model_json_schema.__func__(model)


def test_optional_canonical_fields_remain_optional_while_provider_wire_stays_strict():
    models = structured.build_models_from_config(copy.deepcopy(MODELS))
    canonical = models["Envelope"]
    original = canonical.model_json_schema()
    assert original["required"] == ["child"]
    assert original["$defs"]["Child"]["required"] == ["label"]
    assert original["properties"]["trace"]["default"] is None
    assert original["properties"]["attempts"]["default"] == 1
    assert "additionalProperties" not in original
    assert canonical.model_validate({"child": {"label": "value"}}).trace is None

    provider = structured.get_provider_response_model(canonical)
    wire = provider.model_json_schema()
    assert provider is not canonical
    assert set(wire["required"]) == set(original["properties"])
    assert wire["additionalProperties"] is False
    assert wire["properties"]["child"]["required"] == ["label", "note"]
    assert wire["properties"]["child"]["additionalProperties"] is False
    assert "$defs" not in wire and "$ref" not in json.dumps(wire)
    assert all(field.is_required() for field in provider.model_fields.values())
    with pytest.raises(ValidationError):
        provider.model_validate({"child": {"label": "value"}})
    provider.model_validate({
        "child": {"label": "value", "note": None},
        "trace": None, "attempts": 1, "rows": None,
    })
    assert canonical.model_json_schema() == original
    assert models["Child"].model_fields["note"].is_required() is False
    assert structured.get_provider_response_model(canonical) is provider


@pytest.mark.parametrize("provider_first", [False, True])
def test_workflow_and_provider_cache_order_preserves_canonical_schema(monkeypatch, provider_first):
    from mozaiksai.core.workflow.structured_output_contracts import (
        build_structured_output_contract_ref,
        canonical_structured_output_schema,
        resolve_structured_output_contract_ref,
        structured_output_schema_digest,
    )

    config = {"structured_outputs": {"models": copy.deepcopy(MODELS), "registry": {"ProbeAgent": "Envelope"}}}
    monkeypatch.setattr(structured.workflow_manager, "get_config", lambda _name: config)
    expected_model = structured.build_models_from_config(
        copy.deepcopy(MODELS), exact_model_ids=frozenset(),
    )["Envelope"]
    expected = expected_model.model_json_schema()
    expected_acceptance = canonical_structured_output_schema(expected_model)
    expected_digest = structured_output_schema_digest(expected_model)
    authority = {
        "configs": {"ProviderBoundaryProbe": config["structured_outputs"]},
        "exact_model_ids": frozenset(),
    }
    expected_ref = build_structured_output_contract_ref(
        workflow_name="ProviderBoundaryProbe", model_id="Envelope", **authority,
    )
    assert expected_ref.schema_digest == expected_digest

    def assert_canonical_authority(model):
        assert model.model_json_schema() == expected
        assert canonical_structured_output_schema(model) == expected_acceptance
        assert structured_output_schema_digest(model) == expected_digest
        reference = build_structured_output_contract_ref(
            workflow_name="ProviderBoundaryProbe", model_id="Envelope", **authority,
        )
        assert reference == expected_ref
        assert reference.model_dump_json() == expected_ref.model_dump_json()
        cold_ref = type(reference).model_validate_json(reference.model_dump_json())
        resolved = resolve_structured_output_contract_ref(cold_ref, **authority)
        assert canonical_structured_output_schema(resolved) == expected_acceptance
        assert structured_output_schema_digest(resolved) == expected_digest

    models, registry = structured.load_workflow_structured_outputs("ProviderBoundaryProbe")
    canonical = registry["ProbeAgent"]
    if not provider_first:
        assert_canonical_authority(canonical)
    provider = structured.get_provider_response_model(canonical)
    assert_canonical_authority(canonical)
    assert provider.model_json_schema() != expected
    assert structured._workflow_models["ProviderBoundaryProbe"]["Envelope"] is canonical
    assert structured._workflow_registries["ProviderBoundaryProbe"]["ProbeAgent"] is canonical
    assert structured._provider_response_model_cache[canonical] is provider
    again, _ = structured.load_workflow_structured_outputs("ProviderBoundaryProbe")
    assert again["Envelope"] is models["Envelope"]

    structured.invalidate_workflow_structured_outputs("ProviderBoundaryProbe")
    assert canonical not in structured._provider_response_model_cache
    assert models["Child"] not in structured._provider_response_model_cache
    refreshed, _ = structured.load_workflow_structured_outputs("ProviderBoundaryProbe")
    assert refreshed["Envelope"] is not canonical
    assert_canonical_authority(refreshed["Envelope"])
    structured.get_provider_response_model(refreshed["Envelope"])
    assert_canonical_authority(refreshed["Envelope"])
    structured.invalidate_all_workflow_structured_outputs()
    assert structured._provider_response_model_cache == {}
    cold, _ = structured.load_workflow_structured_outputs("ProviderBoundaryProbe")
    assert_canonical_authority(cold["Envelope"])


@pytest.mark.parametrize("policy", ["inline_refs", "additional_properties", "required", "wrapper"])
def test_provider_policy_changes_cannot_mutate_canonical_models(monkeypatch, policy):
    model = structured.build_models_from_config(copy.deepcopy(MODELS))["Envelope"]
    baseline = model.model_json_schema()
    original_wire = structured.get_provider_response_model(model).model_json_schema()
    structured._provider_response_model_cache.clear()
    if policy == "inline_refs":
        monkeypatch.setattr(
            structured, "_inline_schema_refs", lambda node, definitions: {**node, "$defs": definitions},
        )
    elif policy == "additional_properties":
        original_prepare = structured._add_additional_properties

        def alternate_objects(schema):
            result = original_prepare(schema)
            if isinstance(result, dict) and result.get("type") == "object":
                result["additionalProperties"] = True
            return result

        monkeypatch.setattr(structured, "_add_additional_properties", alternate_objects)
    elif policy == "required":
        original_prepare = structured._add_additional_properties

        def alternate_required(schema):
            result = original_prepare(schema)
            if isinstance(result, dict) and result.get("type") == "object":
                result["required"] = []
            return result

        monkeypatch.setattr(structured, "_add_additional_properties", alternate_required)
    else:
        monkeypatch.setattr(
            structured, "get_provider_response_model",
            lambda _model: create_model("AlternateWire", provider_only=(str, ...)),
        )
    changed_wire = structured.get_provider_response_model(model).model_json_schema()
    assert changed_wire != original_wire
    assert model.model_json_schema() == baseline
    assert "model_json_schema" not in model.__dict__


def test_canonical_model_schema_is_stable_across_process_and_provider_order():
    script = """
import json
import sys
from mozaiksai.core.workflow.outputs.structured import build_models_from_config, get_provider_response_model
from tests.test_structured_output_provider_boundary import MODELS
model = build_models_from_config(MODELS)['Envelope']
if sys.argv[1] == 'provider-first':
    get_provider_response_model(model).model_json_schema()
first = model.model_json_schema()
get_provider_response_model(model).model_json_schema()
print(json.dumps([first, model.model_json_schema()], sort_keys=True))
"""
    canonical_first = subprocess.check_output([sys.executable, "-c", script, "canonical-first"], text=True)
    provider_first = subprocess.check_output([sys.executable, "-c", script, "provider-first"], text=True)
    assert canonical_first == provider_first
    first, second = json.loads(canonical_first)
    assert first == second


@pytest.mark.asyncio
async def test_agent_factory_still_supplies_the_strict_provider_wrapper(monkeypatch):
    from mozaiksai.core.workflow.agents import factory
    from tests.test_structured_output_fail_closed import _FakeAgent, _patch_agent_factory

    canonical = structured.build_models_from_config(copy.deepcopy(MODELS))["Envelope"]
    original = canonical.model_json_schema()
    _patch_agent_factory(
        monkeypatch,
        agent_config={"name": "StrictAgent", "structured_outputs_required": True},
        registry={"StrictAgent": canonical},
    )
    await factory.create_agents("StrictWorkflow", context_variables={})
    supplied = _FakeAgent.created[0].kwargs["response_schema"]
    assert supplied is structured.get_provider_response_model(canonical)
    assert supplied is not canonical
    assert supplied.model_json_schema()["additionalProperties"] is False
    assert all(field.is_required() for field in supplied.model_fields.values())
    assert canonical.model_json_schema() == original


def test_distinct_scalar_literal_order_does_not_change_compiled_schema_identity():
    from mozaiksai.core.workflow.structured_output_contracts import structured_output_schema_digest

    config = {
        "Status": {"type": "literal", "values": ["z", "a"]},
        "Result": {"type": "model", "fields": {
            "status": {"type": "Status"},
            "amount": {"type": "literal", "values": [3, 1]},
        }},
    }
    reordered = copy.deepcopy(config)
    reordered["Status"]["values"].reverse()
    reordered["Result"]["fields"]["amount"]["values"].reverse()
    first = structured.build_models_from_config(config)["Result"]
    second = structured.build_models_from_config(reordered)["Result"]
    assert first.model_json_schema() == second.model_json_schema()
    assert structured_output_schema_digest(first) == structured_output_schema_digest(second)
    assert config["Status"]["values"] == ["z", "a"]
    assert config["Result"]["fields"]["amount"]["values"] == [3, 1]


@pytest.mark.parametrize("values", [[True, 1], [1, True], [1.0, 1], [1, 1.0], [False, 0]])
def test_equality_alias_literal_order_preserves_existing_enum_value_type(values):
    model = structured.build_models_from_config({
        "Result": {"type": "model", "fields": {"value": {"type": "literal", "values": values}}},
    })["Result"]
    accepted = model.model_validate({"value": values[-1]}).value
    assert type(accepted.value) is type(values[0])
    assert accepted.value == values[0]


@pytest.mark.parametrize("exact_ids", [frozenset({"Envelope"}), frozenset({"Child"}), frozenset({"Envelope", "Child"})])
def test_nested_exact_closure_is_constructed_before_dependents_in_either_declaration_order(exact_ids):
    expected = None
    for definitions in (MODELS, dict(reversed(tuple(MODELS.items())))):
        model = structured.build_models_from_config(
            copy.deepcopy(definitions), exact_model_ids=exact_ids,
        )["Envelope"]
        schema = model.model_json_schema()
        assert (schema.get("additionalProperties") is False) == ("Envelope" in exact_ids)
        assert (schema["$defs"]["Child"].get("additionalProperties") is False) == ("Child" in exact_ids)
        for location in ("Envelope", "Child"):
            value = {"child": {"label": "value"}}
            target = value if location == "Envelope" else value["child"]
            target["unowned"] = 1
            if location in exact_ids:
                with pytest.raises(ValidationError, match="extra_forbidden"):
                    model.model_validate(value)
            else:
                model.model_validate(value)
        if expected is None:
            expected = schema
        assert schema == expected


def test_nested_exact_closure_is_independent_of_process_hash_order():
    script = """
import json
from mozaiksai.core.workflow.outputs.structured import build_models_from_config
from tests.test_structured_output_provider_boundary import MODELS
result = []
for names in ({'Envelope'}, {'Child'}, {'Envelope', 'Child'}):
    models = build_models_from_config(MODELS, exact_model_ids=frozenset(names))
    result.append(models['Envelope'].model_json_schema())
print(json.dumps(result, sort_keys=True))
"""
    outputs = [
        subprocess.check_output(
            [sys.executable, "-c", script],
            text=True, env={**os.environ, "PYTHONHASHSEED": str(seed)},
        )
        for seed in (1, 17)
    ]
    assert outputs[0] == outputs[1]
