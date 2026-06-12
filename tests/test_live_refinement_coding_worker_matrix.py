from __future__ import annotations

import json
from pathlib import Path

import pytest

from mozaiksai.control_plane import StagedCodingWorkerResult, run_live_staged_coding_worker
from mozaiksai.control_plane.dry_run import RefinementExecutionPlan
from mozaiksai.control_plane.staging import create_refinement_staging_workspace
from scripts.smoke_refinement_live_coding_worker import FIXTURE_PATH, MATRIX_FIXTURE_PATH

REPO_ROOT = Path(__file__).resolve().parents[1]


def _require_fixture() -> dict:
    if not MATRIX_FIXTURE_PATH.exists():
        pytest.skip(
            f"Matrix fixture not found: {MATRIX_FIXTURE_PATH.name}. "
            "Run: python scripts/smoke_refinement_live_coding_worker.py --run-live --scenario all --save-fixture"
        )
    return json.loads(MATRIX_FIXTURE_PATH.read_text(encoding="utf-8"))


def _require_single_scenario_fixture() -> dict | None:
    if not FIXTURE_PATH.exists():
        return None
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _write_source_bundle(root: Path, snapshot: dict[str, str]) -> None:
    for relative_path, content in snapshot.items():
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def test_live_refinement_coding_worker_matrix_replay(tmp_path: Path) -> None:
    payload = _require_fixture()
    assert payload["schema_version"] == "mozaiks.refinement_live_coding_worker_matrix.v1"
    assert payload["overall_status"] in {"success", "partial_success", "failed"}
    scenarios = payload["scenarios"]
    assert scenarios

    single_scenario_fixture = _require_single_scenario_fixture()
    if single_scenario_fixture is not None:
        ui_patch = next((scenario for scenario in scenarios if scenario["name"] == "ui_patch"), None)
        assert ui_patch is not None
        assert ui_patch["request"] == single_scenario_fixture["request"]
        assert (
            ui_patch["worker_result"]["changes"][0]["path"]
            == single_scenario_fixture["worker_result"]["changes"][0]["path"]
        )
        assert ui_patch["worker_result"]["changes"][0]["new_content"] == single_scenario_fixture["worker_result"][
            "changes"
        ][0]["new_content"]

    forbidden_terms = ["app" + " zero", "app_" + "zero", "mozaiks-" + "app", "bloc" + "united"]

    for scenario in scenarios:
        scenario_text = json.dumps(scenario, sort_keys=True).lower()
        assert not any(term in scenario_text for term in forbidden_terms)
        assert "appgenerator" not in scenario_text
        assert "execute_workflow" not in scenario_text
        assert scenario["source_snapshot"]["before"] == scenario["source_snapshot"]["after"]
        assert scenario["source_file_unchanged"] is True
        assert scenario["status"] in {"success", "failed"}

        if scenario["status"] != "success":
            assert scenario["failure_category"] in {
                "model_output_invalid",
                "wrong_path",
                "no_change_produced",
                "scoped_execution_rejected",
                "config_error",
                "harness_error",
            }
            assert scenario["failure_detail"]
            continue

        plan = RefinementExecutionPlan.model_validate(scenario["plan"])
        worker_result = StagedCodingWorkerResult.model_validate(scenario["worker_result"])
        assert worker_result.request_id == scenario["request_id"]
        assert worker_result.changes
        assert all(change.path in scenario["expected_changed_paths"] for change in worker_result.changes)
        assert all(change.path in plan.affected_bundle_paths for change in worker_result.changes)

        source_bundle = tmp_path / scenario["name"] / "source_app"
        source_before = scenario["source_snapshot"]["before"]
        _write_source_bundle(source_bundle, source_before)

        staging_root = tmp_path / scenario["name"] / ".refinement_staging"
        plan = plan.model_copy(
            update={
                "staging_area": str(staging_root / scenario["request_id"]),
                "output_workspace": str(staging_root / scenario["request_id"]),
            }
        )
        staging_result = create_refinement_staging_workspace(
            plan,
            source_bundle_path=source_bundle,
            staging_root=staging_root,
        )
        staged_result = run_live_staged_coding_worker(plan, staging_result, worker_result)

        assert staged_result.source_mutated is False
        for changed_file in staged_result.changed_files:
            assert changed_file.status == "updated"

        for change in worker_result.changes:
            staged_file = Path(staging_result.staging_area) / "workspace" / change.path
            assert staged_file.read_text(encoding="utf-8") == change.new_content
            assert (source_bundle / change.path).read_text(encoding="utf-8") == source_before[change.path]

