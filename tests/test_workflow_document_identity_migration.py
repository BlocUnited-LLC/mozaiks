"""Exact-base evidence separates document metadata from compiled model identity."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.canonical_json import CanonicalJsonObject
from mozaiksai.core.semantics.plan_authority import compilation_plan_authority_digest
from mozaiksai.core.workflow.declarative.contracts import (
    parse_orchestrator_config,
    parse_structured_outputs_config,
)
from mozaiksai.core.workflow.structured_output_contracts import stable_digest
from tests.slice_5b_composition_helpers import composition_fixture
from tests.test_plan_taxonomy_sources import _corpus_plan
from tests.test_semantic_payload_graph_v2 import _corpus_graph
from tests.test_structured_output_canonical_identity import _model, _ref
from tests.test_workflow_interface_rematerialization import _direct_bytes, _state, _unit

ROOT = Path(__file__).resolve().parents[1]
BASELINE = json.loads((ROOT / "tests/fixtures/workflow-document-version-migration.json").read_text(encoding="utf-8"))
VERSIONS = {
    "orchestrator.yaml": ("mozaiks.orchestrator.v1", parse_orchestrator_config),
    "structured_outputs.yaml": ("mozaiks.structured_outputs.v1", parse_structured_outputs_config),
}


def test_exact_base_capture_and_governed_document_census():
    assert BASELINE["base_commit"] == "5ff00cb1c040d694632e2ec530678c4e9571dc0d"
    assert BASELINE["base_tree"] == "dd750e01833fb127061d085fc2f718a081d8266c"
    assert canonical_digest(BASELINE) == "a3d53b6dc39f4bd110bd1a51b52e74fed98ae260cce9f792eb4f214a31011a94"
    actual = sorted(
        path.relative_to(ROOT).as_posix()
        for directory in (ROOT / "factory_app/workflows", ROOT / "examples")
        for filename in VERSIONS
        for path in directory.rglob(filename)
    )
    assert actual == [row["path"] for row in BASELINE["documents"]]
    assert len(actual) == 30
    assert sum(Path(path).name == "orchestrator.yaml" for path in actual) == 15


@pytest.mark.parametrize("before", BASELINE["documents"], ids=lambda row: row["path"])
def test_only_document_version_changes_static_source_and_parser_identity(before):
    path = ROOT / before["path"]
    version, parser = VERSIONS[path.name]
    raw = path.read_bytes()
    document = yaml.safe_load(raw)
    parsed = parser(copy.deepcopy(document))
    assert document["schema_version"] == parsed["schema_version"] == version
    assert hashlib.sha256(raw).hexdigest() != before["source_bytes_fingerprint"]
    assert canonical_digest(document) != before["source_document_fingerprint"]
    assert canonical_digest(parsed) != before["parser_document_fingerprint"]
    # Comparison to the immutable pre-migration capture; neither restored
    # dictionary is accepted as an unversioned runtime document.
    del document["schema_version"]
    del parsed["schema_version"]
    assert canonical_digest(document) == before["source_document_fingerprint"]
    assert canonical_digest(parsed) == before["parser_document_fingerprint"]


def test_current_corpus_has_no_changed_units_plans_graph_or_payloads():
    before = BASELINE["corpus"]
    plan = _corpus_plan()
    graph, payloads = _corpus_graph()
    records = [{
        "unit_id": unit.unit_id, "document": unit.model_dump(mode="json"),
        "identity": unit.identity_payload, "serialized_json": unit.model_dump_json(),
        "unit_digest": unit.unit_digest,
    } for unit in plan.units]
    assert len(records) == before["unit_count"] == 61
    assert canonical_digest(records) == before["unit_records_fingerprint"]
    assert plan.plan_digest == before["plan_digest"]
    assert hashlib.sha256(plan.model_dump_json().encode()).hexdigest() == before["serialized_plan_fingerprint"]
    assert graph.graph_digest == before["graph_digest"]
    assert [{"node_id": payload.node_id, "payload_digest": payload.payload_digest} for payload in payloads] == before["payload_fingerprints"]


def test_document_version_changes_only_whole_source_authority_identity():
    before = BASELINE["executable"]
    current = composition_fixture()
    assert stable_digest(current["configs"]) != before["configs_fingerprint"]
    authority = current["authority_inputs"]
    assert compilation_plan_authority_digest(authority) != before["input_document_fingerprint"]
    assert hashlib.sha256(authority.model_dump_json().encode()).hexdigest() != before["input_document_bytes_fingerprint"]
    original_configs = copy.deepcopy(current["configs"])
    for config in original_configs.values():
        assert config.pop("schema_version") == "mozaiks.structured_outputs.v1"
    assert stable_digest(original_configs) == before["configs_fingerprint"]
    original_document = authority.model_dump(mode="json")
    original_document["structured_output_configs"] = CanonicalJsonObject.from_python(original_configs).model_dump(mode="json")
    # Restore only metadata for historical comparison, never runtime parsing.
    restored = type(authority).model_validate(original_document)
    assert compilation_plan_authority_digest(restored) == before["input_document_fingerprint"]
    assert hashlib.sha256(restored.model_dump_json().encode()).hexdigest() == before["input_document_bytes_fingerprint"]

    assert current["base"].plan_digest == before["base_plan_digest"]
    assert current["successor"].plan_digest == before["successor_plan_digest"]
    units = current["successor"].units
    assert len(units) == 6
    assert canonical_digest([unit.model_dump(mode="json") for unit in units]) == before["unit_records_fingerprint"]
    assert current["assignments"].assignment_set_digest == before["assignment_set_fingerprint"]
    assert current["result"].result_digest == before["artifact_result_fingerprint"]
    selected = [unit.required_structured_output_ref.model_dump(mode="json") for unit in units if unit.required_structured_output_ref is not None]
    assert selected == [before["selected_reference"]]


def test_canonical_model_acceptance_and_workflow_interface_bytes_are_unchanged():
    assert _ref().model_dump(mode="json") == BASELINE["canonical_probe_reference"]
    assert canonical_digest(_model().model_json_schema()) == BASELINE["canonical_probe_model_schema_fingerprint"]
    state = _state()
    unit = _unit(state)
    assert {
        "unit_id": unit.unit_id, "unit_digest": unit.unit_digest,
        "identity": unit.identity_payload, "content_hex": _direct_bytes(state).hex(),
    } == BASELINE["workflow_interface"]
