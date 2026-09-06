"""Canonical acceptance identity is independent of provider and ambient state."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys

import pytest
from pydantic import ValidationError

from mozaiksai.core.workflow import assignment_kinds
from mozaiksai.core.workflow.outputs import structured
from mozaiksai.core.workflow.structured_output_contracts import (
    STRUCTURED_OUTPUT_ACCEPTANCE_PROFILE,
    StructuredOutputContractRef,
    build_structured_output_contract_ref,
    canonical_structured_output_schema,
    resolve_structured_output_contract_ref,
    stable_digest,
    structured_output_schema_digest,
)


def _config():
    return {"models": {
        "Child": {"type": "model", "fields": {"label": {"type": "str"}}},
        "Output": {"type": "model", "fields": {
            "child": {"type": "Child"},
            "message": {"type": "str"},
            "note": {"type": "str", "default": None},
            "items": {"type": "list[str]"},
            "status": {"type": "literal", "values": ["ready", "waiting"]},
        }},
    }, "registry": {"Author": "Output"}}


def _ref(config=None, *, exact=frozenset()):
    return build_structured_output_contract_ref(
        workflow_name="Probe", model_id="Output",
        configs={"Probe": _config() if config is None else config}, exact_model_ids=exact,
    )


def _model(config=None, *, exact=frozenset()):
    config = _config() if config is None else config
    return resolve_structured_output_contract_ref(
        _ref(config, exact=exact), configs={"Probe": config}, exact_model_ids=exact,
    )


def test_ref_versions_profile_and_canonical_optionality():
    ref = _ref()
    assert ref.ref_schema_version == "mozaiks.structured_output_contract_ref.v2"
    assert ref.acceptance_profile == STRUCTURED_OUTPUT_ACCEPTANCE_PROFILE
    assert StructuredOutputContractRef.model_validate_json(ref.model_dump_json()) == ref
    model = _model()
    schema = canonical_structured_output_schema(model)
    assert "note" not in schema["required"]
    assert "$defs" in schema
    assert "additionalProperties" not in schema
    assert ref.schema_digest == stable_digest({
        "acceptance_profile": STRUCTURED_OUTPUT_ACCEPTANCE_PROFILE, "schema": schema,
    })
    for field, value in (("ref_schema_version", "mozaiks.structured_output_contract_ref.v1"),
                         ("acceptance_profile", "mozaiks.closed_contract_profile.v1"),
                         ("provider", "openai")):
        with pytest.raises(ValidationError):
            StructuredOutputContractRef.model_validate({**ref.model_dump(), field: value})
    with pytest.raises(ValidationError, match="frozen"):
        ref.acceptance_profile = "changed"


@pytest.mark.parametrize("policy", ["inlining", "closure", "required", "wrapper"])
def test_provider_preparation_cannot_change_canonical_identity(monkeypatch, policy):
    model = _model()
    before = canonical_structured_output_schema(model)
    digest = structured_output_schema_digest(model)
    reference = _ref()
    if policy == "inlining":
        monkeypatch.setattr(structured, "_inline_schema_refs", lambda node, defs, stack=None: node)
    elif policy == "closure":
        monkeypatch.setattr(structured, "_patch_model_schema", lambda cls: None)
    elif policy == "required":
        def patch(cls):
            cls.model_json_schema = classmethod(lambda cls, **kwargs: {"type": "object", "required": []})
        monkeypatch.setattr(structured, "_patch_model_schema", patch)
    else:
        monkeypatch.setattr(structured, "get_provider_response_model", lambda cls: structured.build_models_from_config({
            "Wire": {"type": "model", "fields": {"provider_field": {"type": "int"}}},
        })["Wire"])
    wire = structured.get_provider_response_model(model)
    assert wire.model_json_schema() != before
    # Even a direct class-method override is presentation, not core-model authority.
    monkeypatch.setattr(model, "model_json_schema", classmethod(lambda cls, **kwargs: wire.model_json_schema()))
    assert canonical_structured_output_schema(model) == before
    assert structured_output_schema_digest(model) == digest
    assert _ref() == reference


@pytest.mark.parametrize("mutation", ["add", "remove", "optional", "required", "scalar", "nested", "enum", "items", "closure"])
def test_actual_acceptance_changes_move_identity(mutation):
    config = _config()
    fields = config["models"]["Output"]["fields"]
    if mutation == "add":
        fields["extra"] = {"type": "str"}
    elif mutation == "remove":
        del fields["message"]
    elif mutation == "optional":
        fields["message"]["default"] = None
    elif mutation == "required":
        del fields["note"]["default"]
    elif mutation == "scalar":
        fields["message"]["type"] = "int"
    elif mutation == "nested":
        config["models"]["Child"]["fields"]["count"] = {"type": "int"}
    elif mutation == "enum":
        fields["status"]["values"].append("finished")
    elif mutation == "items":
        fields["items"]["type"] = "list[int]"
    exact = frozenset({"Output"}) if mutation == "closure" else frozenset()
    assert _ref(config, exact=exact).schema_digest != _ref().schema_digest


def test_equivalent_declaration_order_and_repeated_compilation_are_identical():
    original = _config()
    reverse = copy.deepcopy(original)
    reverse["models"] = dict(reversed(list(reverse["models"].items())))
    for definition in reverse["models"].values():
        definition["fields"] = dict(reversed(list(definition["fields"].items())))
    reverse["models"]["Output"]["fields"]["status"]["values"].reverse()
    assert _ref(reverse) == _ref(original) == _ref(original)
    assert canonical_structured_output_schema(_model(reverse)) == canonical_structured_output_schema(_model(original))


def test_default_data_order_is_not_schema_required_order():
    config = _config()
    config["models"]["Output"]["fields"]["data"] = {"type": "dict", "default": {"required": ["z", "a"]}}
    model = _model(config)
    assert canonical_structured_output_schema(model)["properties"]["data"]["default"] == {"required": ["z", "a"]}


def test_both_ref_boundaries_require_explicit_exactness_and_cold_mismatch_rejects():
    with pytest.raises(TypeError, match="exact_model_ids"):
        build_structured_output_contract_ref(workflow_name="Probe", model_id="Output", configs={"Probe": _config()})
    ref = _ref(exact=frozenset({"Output"}))
    with pytest.raises(TypeError, match="exact_model_ids"):
        resolve_structured_output_contract_ref(ref, configs={"Probe": _config()})
    with pytest.raises(ValueError, match="schema digest mismatch"):
        resolve_structured_output_contract_ref(ref, configs={"Probe": _config()}, exact_model_ids=frozenset())


def test_cold_process_and_hash_seed_preserve_identity():
    script = """import json
from tests.test_structured_output_canonical_identity import _config, _ref, _model
from mozaiksai.core.workflow.outputs.structured import get_provider_response_model
model = _model(exact=frozenset({'Output', 'Child'}))
get_provider_response_model(model)
print(json.dumps(_ref(exact=frozenset({'Output', 'Child'})).model_dump(mode='json'), sort_keys=True))
"""
    expected = _ref(exact=frozenset({"Output", "Child"})).model_dump(mode="json")
    for seed in ("1", "17"):
        completed = subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, text=True, env={**os.environ, "PYTHONHASHSEED": seed})
        assert json.loads(completed.stdout.splitlines()[-1]) == expected


def test_fixed_plan_authority_is_independent_of_ambient_descriptors(monkeypatch):
    from dataclasses import replace

    from mozaiksai.core.semantics import compilation_plan
    from mozaiksai.core.semantics.plan_authority import validate_compilation_plan_against_authority
    from mozaiksai.core.workflow.plan_assignment_compiler import ApprovedPlan, compile_approved_plan
    from tests.test_plan_assignment_compiler import _AUTHORITY, _fixture

    _, plan, _, resolver, spec = _fixture()
    inputs = _AUTHORITY["inputs"]
    expected = compile_approved_plan(ApprovedPlan(assignments=(spec,)), resolver=resolver, authority_inputs=inputs)
    originals = assignment_kinds.ASSIGNMENT_CONTRACT_DESCRIPTORS
    altered = {kind: replace(row, structured_output_model_id="OtherModel", identity_bindings=()) for kind, row in originals.items()}
    for ambient in ({}, altered):
        monkeypatch.setattr(assignment_kinds, "ASSIGNMENT_CONTRACT_DESCRIPTORS", ambient)
        monkeypatch.setattr(compilation_plan, "ASSIGNMENT_CONTRACT_DESCRIPTORS", ambient)
        assert validate_compilation_plan_against_authority(plan, inputs) == plan
        assert compile_approved_plan(ApprovedPlan(assignments=(spec,)), resolver=resolver, authority_inputs=inputs) == expected


def test_admission_and_artifact_boundaries_pin_explicit_exactness(monkeypatch):
    from mozaiksai.core.workflow.assignment_admission import resolve_assignment_admission
    from mozaiksai.core.workflow.assignment_artifacts import build_assignment_artifact_result
    from tests.slice_5b_helpers import agent_config, compiled_assignment

    assignment, config = compiled_assignment()
    configs = {"AppGenerator": config}
    monkeypatch.setattr(assignment_kinds, "ASSIGNMENT_CONTRACT_DESCRIPTORS", {})
    for exact in (frozenset(), frozenset({"ArtifactOutput"})):
        def admit(exact=exact):
            return resolve_assignment_admission(
                assignment, structured_output_configs=configs, exact_model_ids=exact,
                workflow_agent_configs={"AppGenerator": agent_config()},
            )

        def artifact(exact=exact):
            return build_assignment_artifact_result(
                assignment=assignment, structured_output={"message": "ready"},
                artifacts={assignment.owned_paths[0]: "{}"},
                structured_output_configs=configs, exact_model_ids=exact,
                validator_runner=lambda _validator, _files: True,
            )

        for boundary in (admit, artifact):
            if exact:
                with pytest.raises(ValueError, match="schema digest mismatch"):
                    boundary()
            else:
                assert boundary()
