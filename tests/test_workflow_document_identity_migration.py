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
    # EXPECTED_SEMANTIC_MIGRATION: the typed module-local ActionPayload.action_id
    # (#494 correction) changes exactly one payload digest and the graph/plan
    # identities pinning it. All 61 unit bodies remain byte-identical (proven
    # above); the restoration below recovers the exact captured identities.
    before_payloads = {
        row["node_id"]: row["payload_digest"] for row in before["payload_fingerprints"]
    }
    changed = [
        payload for payload in payloads
        if payload.payload_digest != before_payloads[payload.node_id]
    ]
    assert [payload.node_id for payload in changed] == ["mozaiks.action.create_report"]
    action = changed[0]
    restored_action = action.canonical_payload(include_digest=False)
    assert restored_action.pop("action_id") == "create_report"
    assert canonical_digest(restored_action) == before_payloads[action.node_id]
    restored_graph = graph.canonical_payload(include_digest=False)
    node = next(
        row for row in restored_graph["nodes"] if row["node_id"] == action.node_id
    )
    node["payload_ref"]["content_digest"] = before_payloads[action.node_id]
    assert canonical_digest(restored_graph) == before["graph_digest"]
    restored_plan = plan.canonical_payload(include_digest=False)
    restored_plan["graph_digest"] = before["graph_digest"]
    assert canonical_digest(restored_plan) == before["plan_digest"]
    restored_plan_bytes = (
        plan.model_dump_json()
        .replace(graph.graph_digest, before["graph_digest"])
        .replace(plan.plan_digest, before["plan_digest"])
    )
    assert hashlib.sha256(restored_plan_bytes.encode()).hexdigest() == before["serialized_plan_fingerprint"]


def _pre_action_identity_graph_digest(graph, digest_swaps: dict[str, str]) -> str:
    """The graph's digest with pre-#494 action payload identities restored."""
    document = graph.canonical_payload(include_digest=False)
    for node in document["nodes"]:
        ref = node["payload_ref"]
        ref["content_digest"] = digest_swaps.get(
            ref["content_digest"], ref["content_digest"]
        )
    return canonical_digest(document)


def _strip_typed_action_identity(
    document: dict, serialized: str, graph
) -> tuple[dict[str, str], str]:
    """Restore the pre-#494 shape of one serialized authority document.

    Removes the typed ``ActionPayload.action_id`` fact from every action
    payload, restores the pre-migration payload and graph digests, and applies
    the same restoration to the exact serialized byte stream.  The historical
    document cannot revalidate under current models (``action_id`` is
    required), so restoration operates on the serialized forms only.  Returns
    the ``{new_digest: old_digest}`` swaps and the restored byte stream.
    """
    digest_swaps: dict[str, str] = {}
    for payload_document in document["payloads"]:
        if payload_document["payload_kind"] != "action":
            continue
        new_digest = payload_document.pop("payload_digest")
        action_id = payload_document.pop("action_id")
        old_digest = canonical_digest(payload_document)
        payload_document["payload_digest"] = old_digest
        digest_swaps[new_digest] = old_digest
        fragment = f'"payload_kind":"action","action_id":"{action_id}",'
        assert serialized.count(fragment) == 1
        serialized = serialized.replace(fragment, '"payload_kind":"action",', 1)
    assert digest_swaps
    graph_document = document["graph"]
    for node in graph_document["nodes"]:
        ref = node["payload_ref"]
        ref["content_digest"] = digest_swaps.get(
            ref["content_digest"], ref["content_digest"]
        )
    # The graph digest is a Merkle root over node/edge identity projections,
    # not the full serialized graph document — recompute it canonically.
    new_graph_digest = graph_document["graph_digest"]
    old_graph_digest = _pre_action_identity_graph_digest(graph, digest_swaps)
    graph_document["graph_digest"] = old_graph_digest
    digest_swaps[new_graph_digest] = old_graph_digest
    for new_digest, old_digest in digest_swaps.items():
        serialized = serialized.replace(new_digest, old_digest)
    return digest_swaps, serialized


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
    # EXPECTED_SEMANTIC_MIGRATION (#494 correction): additionally restore the
    # pre-action_id payload/graph identities on the serialized document.
    restored_document = restored.model_dump(mode="json")
    digest_swaps, restored_bytes = _strip_typed_action_identity(
        restored_document, restored.model_dump_json(), current["graph"]
    )
    assert canonical_digest(restored_document) == before["input_document_fingerprint"]
    assert hashlib.sha256(restored_bytes.encode()).hexdigest() == before["input_document_bytes_fingerprint"]

    old_base_graph_digest = _pre_action_identity_graph_digest(
        current["base_graph"], digest_swaps
    )
    for key, digest_key, old_graph_digest in (
        ("base", "base_plan_digest", old_base_graph_digest),
        ("successor", "successor_plan_digest", None),
    ):
        plan = current[key]
        assert plan.plan_digest != before[digest_key]
        restored_plan = plan.canonical_payload(include_digest=False)
        restored_plan["graph_digest"] = (
            old_graph_digest
            if old_graph_digest is not None
            else digest_swaps[restored_plan["graph_digest"]]
        )
        assert canonical_digest(restored_plan) == before[digest_key]
    units = current["successor"].units
    assert len(units) == 6
    assert canonical_digest([unit.model_dump(mode="json") for unit in units]) == before["unit_records_fingerprint"]

    # Assignments and results pin the plan digest; restore it and recompute
    # their content digests exactly as their builders do.
    assignments = current["assignments"].model_dump(mode="json")
    assert current["assignments"].assignment_set_digest != before["assignment_set_fingerprint"]
    assert len(assignments["ordered_assignments"]) == 1
    assignment = assignments["ordered_assignments"][0]
    assignment["plan_unit_ref"]["compilation_plan_ref"]["content_digest"] = before["successor_plan_digest"]
    assignment["assignment_digest"] = stable_digest({
        key: value for key, value in assignment.items()
        if key not in {"assignment_id", "assignment_digest"}
    })
    assignment["assignment_id"] = f"wa_{assignment['assignment_digest'][:24]}"
    assert stable_digest([assignment["assignment_digest"]]) == before["assignment_set_fingerprint"]

    result = current["result"].model_dump(mode="json")
    assert current["result"].result_digest != before["artifact_result_fingerprint"]
    result["assignment_id"] = assignment["assignment_id"]
    result["assignment_digest"] = assignment["assignment_digest"]
    result["plan_unit_ref"] = assignment["plan_unit_ref"]
    result["result_digest"] = stable_digest({
        key: value for key, value in result.items() if key != "result_digest"
    })
    assert result["result_digest"] == before["artifact_result_fingerprint"]

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
