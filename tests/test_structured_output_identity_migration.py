"""Exact-base evidence isolates the provider-neutral reference migration."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.plan_authority import compilation_plan_authority_digest
from mozaiksai.core.workflow.structured_output_contracts import stable_digest
from tests.slice_5b_composition_helpers import composition_fixture

_FIXTURE = Path(__file__).parent / "fixtures/structured-output-identity-migration.json"
_BASE = "430d3ffaeab0b27843d7fbeba275c5be316ff586"
_FIXTURE_FINGERPRINT = "55e1b12cabae2fff5c2ec6f612f4306821c4b66b40c70ff8490c7af89176bc4a"


def _baseline():
    value = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert value["base_commit"] == _BASE
    assert canonical_digest(value) == _FIXTURE_FINGERPRINT
    return value


def _restore_unit_locator(document, original):
    restored = copy.deepcopy(document)
    restored["compilation_plan_ref"]["content_digest"] = original["compilation_plan_ref"]["content_digest"]
    restored["unit_digest"] = original["unit_digest"]
    assert restored == original
    return restored


def test_exact_base_capture_preserves_provider_influenced_identity_evidence():
    baseline = _baseline()
    assert baseline["reference"]["ref_schema_version"] == "mozaiks.structured_output_contract_ref.v1"
    assert stable_digest(baseline["provider_influenced_schema"]) == baseline["reference"]["schema_digest"]
    assert baseline["provider_influenced_schema"] != baseline["unmodified_basemodel_schema"]
    for name in ("base_plan", "successor_plan"):
        captured = baseline[name]
        assert json.loads(captured["serialized_json"]) == captured["document"]
        assert len(captured["units"]) == 6
        for unit in captured["units"]:
            assert json.loads(unit["serialized_json"]) == unit["document"]
            assert canonical_digest(unit["identity_payload"]) == unit["unit_digest"]


def test_reference_migration_changes_only_exact_ref_dependent_unit_and_plan_identity():
    baseline = _baseline()
    current = composition_fixture()
    assert stable_digest(current["configs"]) == baseline["config_fingerprint"]
    authority = current["authority_inputs"]
    assert authority.assignment_contract_registry.model_dump(mode="json") == baseline["descriptor_snapshot"]
    assert compilation_plan_authority_digest(authority) == baseline["input_document_fingerprint"]
    assert hashlib.sha256(authority.model_dump_json().encode()).hexdigest() == baseline["input_document_bytes_fingerprint"]
    old_ref = baseline["reference"]
    assert current["configs"][old_ref["workflow_name"]]["models"][old_ref["model_id"]] == baseline["selected_model_config"]
    for key, fixture_key in (("base", "base_plan"), ("successor", "successor_plan")):
        plan = current[key]
        captured = baseline[fixture_key]
        before_by_id = {unit["unit_id"]: unit for unit in captured["units"]}
        assert [unit.unit_id for unit in plan.units] == list(before_by_id)
        changed = []
        for unit in plan.units:
            before = before_by_id[unit.unit_id]
            document = unit.model_dump(mode="json")
            if unit.required_structured_output_ref is None:
                assert document == before["document"]
                assert unit.identity_payload == before["identity_payload"]
                assert unit.model_dump_json() == before["serialized_json"]
                assert unit.unit_digest == before["unit_digest"]
            else:
                changed.append(unit.unit_id)
                assert document["required_structured_output_ref"] != old_ref
                assert unit.unit_digest != before["unit_digest"]
                document["required_structured_output_ref"] = old_ref
                assert document == before["document"]
                identity = unit.identity_payload
                identity["required_structured_output_ref"] = old_ref
                assert identity == before["identity_payload"]
        assert changed == ["module_backend_helper/report_hook-reports/a641fcf1cb52"]
        restored = plan.canonical_payload(include_digest=False)
        restored["units"] = captured["document"]["units"]
        assert canonical_digest(restored) == captured["document"]["plan_digest"]
        restored["plan_digest"] = captured["document"]["plan_digest"]
        assert restored == captured["document"]


def test_assignment_and_artifact_migration_preserves_all_non_identity_content():
    baseline = _baseline()
    current = composition_fixture()
    assignments = current["assignments"].model_dump(mode="json")
    original_set = baseline["compiled_assignments"]
    assert len(assignments["ordered_assignments"]) == len(original_set["ordered_assignments"]) == 1
    assignment = assignments["ordered_assignments"][0]
    original = original_set["ordered_assignments"][0]
    assert assignment["assignment_digest"] != original["assignment_digest"]
    assignment["plan_unit_ref"] = _restore_unit_locator(assignment["plan_unit_ref"], original["plan_unit_ref"])
    assignment["required_structured_output_ref"] = original["required_structured_output_ref"]
    assignment["assignment_digest"] = stable_digest({key: value for key, value in assignment.items() if key not in {"assignment_id", "assignment_digest"}})
    assignment["assignment_id"] = f"wa_{assignment['assignment_digest'][:24]}"
    assert assignment == original
    assignments["assignment_set_digest"] = stable_digest([assignment["assignment_digest"]])
    assert assignments == original_set

    result = current["result"].model_dump(mode="json")
    original_result = baseline["artifact_result"]
    assert result["result_digest"] != original_result["result_digest"]
    result["assignment_id"] = original_result["assignment_id"]
    result["assignment_digest"] = original_result["assignment_digest"]
    result["plan_unit_ref"] = _restore_unit_locator(result["plan_unit_ref"], original_result["plan_unit_ref"])
    result["result_digest"] = stable_digest({key: value for key, value in result.items() if key != "result_digest"})
    assert result == original_result
    assert [{"path": path, "content_hex": content.hex()} for path, content in sorted(current["materialized"].files().items())] == baseline["materialized_files"]
