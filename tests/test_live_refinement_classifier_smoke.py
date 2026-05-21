from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import smoke_refinement_classifier

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "refinement_classifier_smoke_output.json"
FIXTURE_PRESENT = FIXTURE_PATH.exists()


def test_manual_smoke_script_declares_neutral_cases_only() -> None:
    cases = smoke_refinement_classifier.SMOKE_CASES
    rendered = json.dumps([case.__dict__ for case in cases], sort_keys=True).lower()

    assert len(cases) == 5
    assert {case.id for case in cases} == {
        "ui_experience_spec",
        "module_backend",
        "external_integration",
        "data_model_migration",
        "hosted_capability_facade",
    }
    assert "analytics_provider" in rendered
    assert "projects" in rendered
    assert "app zero" not in rendered
    assert "mozaiks-app" not in rendered


@pytest.mark.skip(
    reason=(
        "Live LLM smoke is manual only. Run: "
        "python scripts/smoke_refinement_classifier.py --save-fixture"
    )
)
class TestLiveRefinementClassifierSmoke:
    def test_live_run_placeholder(self) -> None:
        raise AssertionError("Use scripts/smoke_refinement_classifier.py for the live run.")


@pytest.mark.skipif(
    not FIXTURE_PRESENT,
    reason=(
        "Fixture not present. Generate with: "
        "python scripts/smoke_refinement_classifier.py --save-fixture"
    ),
)
class TestRefinementClassifierFixtureReplay:
    def test_fixture_replay_validates_route_and_impact_output(self) -> None:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        violations = smoke_refinement_classifier.validate_smoke_output(payload)

        assert not violations
        assert payload["success"] is True
        assert payload["llm_profile_used"] == "classifier"

    def test_fixture_has_no_secret_or_proprietary_path_hints(self) -> None:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        rendered = json.dumps(payload, sort_keys=True).lower()

        assert "app zero" not in rendered
        assert "mozaiks-app" not in rendered
        assert "blocunited" not in rendered
        for case in payload["cases"]:
            paths = case["impact"]["affected_bundle_paths"]
            assert not any(smoke_refinement_classifier._path_has_secret_marker(path) for path in paths)

    def test_fixture_contains_expected_case_summaries(self) -> None:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        by_id = {case["id"]: case for case in payload["cases"]}

        assert by_id["module_backend"]["classifier"]["change_class"] in {"feature", "patch"}
        assert by_id["external_integration"]["classifier"]["change_class"] in {"feature", "patch"}
        assert by_id["data_model_migration"]["classifier"]["change_class"] in {"feature", "core"}
        assert by_id["hosted_capability_facade"]["classifier"]["change_class"] in {"design", "feature"}
        assert "modules/projects/module.yaml" in by_id["module_backend"]["impact"]["affected_bundle_paths"]
        assert (
            "backend/integrations/analytics_provider_client.py"
            in by_id["external_integration"]["impact"]["affected_bundle_paths"]
        )
        assert (
            "Destructive changes require explicit review."
            in by_id["data_model_migration"]["impact"]["scope_summary"]
        )
        assert (
            "backend/integrations/hosted_analytics_client.py"
            in by_id["hosted_capability_facade"]["impact"]["affected_bundle_paths"]
        )
