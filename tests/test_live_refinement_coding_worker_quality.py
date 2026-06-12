from __future__ import annotations

import json
from typing import cast

import pytest

from scripts.smoke_refinement_live_coding_worker import MATRIX_FIXTURE_PATH

EXPECTED_SCENARIOS: dict[str, dict[str, object]] = {
    "ui_patch": {
        "path": "ui/pages/dashboard.yaml",
        "content_contains": ["title: Reports Dashboard"],
        "content_not_contains": [
            "archive_project",
            "retry behavior",
            "project_phase",
            "hosted analytics display mapping",
        ],
        "summary_contains": ["reports dashboard"],
        "rationale_contains": ["single text update", "dashboard yaml"],
    },
    "module_backend": {
        "path": "modules/projects/backend/service.py",
        "content_contains": [
            "def archive_project(project_id):",
            'return {"archived": False, "project_id": project_id}',
        ],
        "content_not_contains": ["migration", "provider", "secret", "token"],
        "summary_contains": ["archive_project", "projects service"],
        "rationale_contains": ["no-op", "stub"],
    },
    "integration_adapter": {
        "path": "services/integrations/analytics_provider_client.py",
        "content_contains": ["Retry behavior", "backoff", "retry storms"],
        "content_not_contains": ["token", "secret", "password", "endpoint"],
        "summary_contains": ["retry behavior", "analytics provider"],
        "rationale_contains": ["comment update", "documentation"],
    },
    "data_model_comment": {
        "path": "modules/projects/backend/schemas.py",
        "content_contains": [
            "TODO: project_phase will require a future migration",
            'PROJECT_PHASE = "draft"',
        ],
        "content_not_contains": ["data_contract", "migration.json", "repo.py"],
        "summary_contains": ["future migration", "project_phase"],
        "rationale_contains": ["todo", "schema"],
    },
    "hosted_facade": {
        "path": "modules/analytics_dashboard/backend/service.py",
        "content_contains": [
            "Hosted analytics display mapping",
            "canonical display labels",
            "upstream provider",
        ],
        "content_not_contains": ["secret", "token", "credential", "api key"],
        "summary_contains": ["hosted analytics display mapping", "analytics dashboard service"],
        "rationale_contains": ["scoped change", "without altering behavior"],
    },
}


def _load_matrix_payload() -> dict:
    if not MATRIX_FIXTURE_PATH.exists():
        pytest.skip(
            f"Matrix fixture not found: {MATRIX_FIXTURE_PATH.name}. "
            "Run: python scripts/smoke_refinement_live_coding_worker.py --run-live --scenario all --save-fixture"
        )
    return json.loads(MATRIX_FIXTURE_PATH.read_text(encoding="utf-8"))


def _assert_contains(text: str, fragments: list[str]) -> None:
    lowered = text.lower()
    for fragment in fragments:
        assert fragment.lower() in lowered, f"missing expected fragment: {fragment!r}"


def _assert_not_contains(text: str, fragments: list[str]) -> None:
    lowered = text.lower()
    for fragment in fragments:
        assert fragment.lower() not in lowered, f"unexpected fragment present: {fragment!r}"


def test_live_refinement_coding_worker_matrix_quality() -> None:
    payload = _load_matrix_payload()

    assert payload["schema_version"] == "mozaiks.refinement_live_coding_worker_matrix.v1"
    assert payload["overall_status"] == "success"
    assert payload["failed_count"] == 0
    assert payload["success_count"] == len(EXPECTED_SCENARIOS)

    scenarios = {scenario["name"]: scenario for scenario in payload["scenarios"]}
    assert set(scenarios) == set(EXPECTED_SCENARIOS)

    for scenario_name, expected in EXPECTED_SCENARIOS.items():
        scenario = scenarios[scenario_name]
        changed_path = str(expected["path"])
        content = scenario["staged_snapshot"]["workspace"][changed_path]
        worker_result = scenario["worker_result"]
        coding_result = scenario["coding_worker_result"]
        plan = coding_result["plan"]
        changed_file = scenario["staging_result"]["changed_files"][0]
        content_contains = cast(list[str], expected["content_contains"])
        content_not_contains = cast(list[str], expected["content_not_contains"])
        summary_contains = cast(list[str], expected["summary_contains"])
        rationale_contains = cast(list[str], expected["rationale_contains"])

        assert scenario["status"] == "success"
        assert scenario["source_file_unchanged"] is True
        assert scenario["source_snapshot"]["before"] == scenario["source_snapshot"]["after"]
        assert scenario["expected_changed_paths"] == [changed_path]
        assert list(scenario["staged_snapshot"]["workspace"].keys()) == [changed_path]
        assert {"path", "reason", "staged_path", "status"} <= set(changed_file)
        assert changed_file["path"] == changed_path
        assert changed_file["status"] == "updated"
        assert scenario["staging_result"]["source_mutated"] is False
        assert scenario["staging_result"]["mutation_scope"] == "staging_only"
        assert worker_result["request_id"] == scenario["request_id"]
        assert worker_result["source"] == "live_worker"
        assert len(worker_result["changes"]) == 1
        assert worker_result["changes"][0]["path"] == changed_path
        assert worker_result["changes"][0]["new_content"] == content
        assert worker_result["changes"][0]["reason"]
        assert coding_result["status"] == "validated"
        assert coding_result["metadata"].get("artifact_persistence_error") is None
        assert scenario["artifact_store"]["backend"] == "smoke_local_in_memory"
        assert scenario["artifact_store"]["attempted"] is True
        assert scenario["artifact_store"]["created_version_count"] == 1
        assert scenario["artifact_store"]["created_version_id"]
        assert scenario["coding_worker_result"]["validation_result"]["success"] is True
        assert scenario["coding_worker_result"]["validation_result"]["validation_status"] == "skipped"
        assert scenario["coding_worker_result"]["validation_result"]["warnings"]

        reason = worker_result["changes"][0]["reason"]
        assert reason
        if reason == "live worker output for staged refinement smoke":
            assert plan["summary"]
            assert plan["rationale"]
        else:
            assert len(reason) <= 160
            _assert_contains(reason, rationale_contains)

        _assert_contains(content, content_contains)
        _assert_not_contains(content, content_not_contains)
        _assert_contains(plan["summary"], summary_contains)
        _assert_contains(plan["rationale"], rationale_contains)

        scenario_text = json.dumps(scenario, sort_keys=True).lower()
        assert "appgenerator" not in scenario_text
        assert "execute_workflow" not in scenario_text
        forbidden_terms = ["app" + " zero", "app_" + "zero", "mozaiks-" + "app", "bloc" + "united"]
        assert not any(term in scenario_text for term in forbidden_terms)

