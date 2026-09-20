"""Self-versioned workflow documents retain exact version authority."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys

import pytest
import yaml
from pydantic import ValidationError

from mozaiksai.core.semantics.refs import ChildContractRef, ExecutionAccessScopeRef
from mozaiksai.core.workflow.declarative.contracts import (
    OrchestratorConfig,
    StructuredOutputsConfig,
    parse_orchestrator_config,
    parse_structured_outputs_config,
)
from mozaiksai.core.workflow.structured_output_contracts import stable_digest

CASES = (
    (OrchestratorConfig, parse_orchestrator_config, {
        "schema_version": "mozaiks.orchestrator.v1", "workflow_name": "VersionProbe",
        "workflow_startup_mode": "AgentDriven", "max_turns": 7,
        "human_in_the_loop": True, "orchestration_pattern": "Pipeline",
        "initial_agent": "Author", "initial_message": "Begin.",
        "triggers": [{"type": "event", "event": "domain.probe.ready"}],
    }),
    (StructuredOutputsConfig, parse_structured_outputs_config, {
        "schema_version": "mozaiks.structured_outputs.v1",
        "registry": {"Author": "Output"}, "models": {
            "Output": {"type": "model", "fields": {"message": {"type": "str"}}},
        },
    }),
)


@pytest.mark.parametrize("model,parser,document", CASES)
@pytest.mark.parametrize("mutation", ["missing", "null", "unknown", "misspelled", "extra", "whitespace", "numeric"])
def test_versions_are_required_exact_literals(model, parser, document, mutation):
    candidate = copy.deepcopy(document)
    if mutation == "missing":
        del candidate["schema_version"]
    elif mutation == "null":
        candidate["schema_version"] = None
    elif mutation == "unknown":
        candidate["schema_version"] += ".unknown"
    elif mutation == "misspelled":
        candidate["schema_verison"] = candidate.pop("schema_version")
    elif mutation == "extra":
        candidate["unknown_key"] = True
    elif mutation == "whitespace":
        candidate["schema_version"] = " " + candidate["schema_version"] + " "
    else:
        candidate["schema_version"] = 1
    with pytest.raises(ValidationError):
        model.model_validate(candidate)
    with pytest.raises(ValueError):
        parser(candidate)


@pytest.mark.parametrize("model,parser,document", CASES)
def test_parsed_version_is_serialized_and_survives_cold_round_trip(model, parser, document):
    parsed = parser(copy.deepcopy(document))
    assert parsed["schema_version"] == document["schema_version"]
    validated = model.model_validate(parsed)
    assert validated.model_dump()["schema_version"] == document["schema_version"]
    assert model.model_validate_json(validated.model_dump_json()) == validated
    stripped = {key: value for key, value in parsed.items() if key != "schema_version"}
    with pytest.raises(ValidationError, match="schema_version"):
        model.model_validate(stripped)
    with pytest.raises(ValueError, match="schema_version"):
        parser(stripped)
    assert model.model_fields["schema_version"].is_required()


@pytest.mark.parametrize("model,parser,document", CASES)
def test_child_reference_version_equality_uses_the_parsed_document(model, parser, document):
    del model
    raw = yaml.safe_dump(document, sort_keys=False).encode()
    parsed = parser(yaml.safe_load(raw))
    orchestrator = "workflow_name" in document
    ref = ChildContractRef(
        subject_id="version-probe", subject_version=1,
        scope=ExecutionAccessScopeRef(tenant_id="tenant", workspace_id="workspace"),
        content_digest=hashlib.sha256(raw).hexdigest(),
        artifact_family="workflow_manifest" if orchestrator else "workflow_config",
        canonical_relative_path="orchestrator.yaml" if orchestrator else "structured_outputs.yaml",
        contract_schema_version=parsed["schema_version"],
    )
    assert ref.contract_schema_version == parsed["schema_version"]
    different = ChildContractRef.model_validate({**ref.model_dump(), "contract_schema_version": "different.v1"})
    assert different.contract_schema_version != parsed["schema_version"]
    assert different.content_digest == ref.content_digest


@pytest.mark.parametrize("model,parser,document", CASES)
def test_semantic_parse_identity_is_stable_without_claiming_equal_source_bytes(model, parser, document):
    del model
    first_yaml = yaml.safe_dump(document, sort_keys=False)
    reordered_yaml = yaml.safe_dump(dict(reversed(list(document.items()))), sort_keys=False)
    assert first_yaml != reordered_yaml
    assert hashlib.sha256(first_yaml.encode()).digest() != hashlib.sha256(reordered_yaml.encode()).digest()
    first = parser(yaml.safe_load(first_yaml))
    second = parser(yaml.safe_load(reordered_yaml))
    assert first == second == parser(yaml.safe_load(first_yaml))
    assert stable_digest(first) == stable_digest(second)
    script = f"""import json, sys, yaml
from mozaiksai.core.workflow.declarative.contracts import {parser.__name__}
from mozaiksai.core.workflow.structured_output_contracts import stable_digest
print(stable_digest({parser.__name__}(yaml.safe_load(sys.stdin.read()))))
"""
    result = subprocess.run([sys.executable, "-c", script], input=reordered_yaml, text=True, capture_output=True, check=True)
    assert result.stdout.strip() == stable_digest(first)


def test_orchestrator_version_changes_only_document_metadata():
    _, parser, document = CASES[0]
    parsed = parser(copy.deepcopy(document))
    assert parsed == {
        **document,
        "triggers": [{
            **document["triggers"][0], "endpoint": None, "method": None,
            "description": None, "capability_id": None,
        }],
    }


def test_document_version_never_becomes_an_output_model_field():
    from mozaiksai.core.workflow.outputs.structured import (
        build_models_from_config,
        get_provider_response_model,
    )
    from mozaiksai.core.workflow.structured_output_contracts import (
        build_structured_output_contract_ref,
        canonical_structured_output_schema,
    )

    _, parser, document = CASES[1]
    parsed = parser(copy.deepcopy(document))
    assert parsed["models"] == document["models"]
    assert parsed["registry"] == document["registry"]
    model = build_models_from_config(parsed["models"])["Output"]
    wire = get_provider_response_model(model)
    assert "schema_version" not in model.model_fields
    assert "schema_version" not in json.dumps(canonical_structured_output_schema(model))
    assert "schema_version" not in json.dumps(wire.model_json_schema())
    reference = build_structured_output_contract_ref(workflow_name="VersionProbe", model_id="Output", configs={"VersionProbe": parsed}, exact_model_ids=frozenset())
    assert reference.acceptance_profile == "mozaiks.structured_output_acceptance_profile.v1"
