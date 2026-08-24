from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tests.test_generated_app_archetype_matrix import _matrix_specs


ROOT = Path(__file__).resolve().parents[1]
DEMO_DATA = ROOT / "factory_app" / "app" / "admin" / "pages" / "studioDemoData.js"
EXPECTED_APP_IDS = {
    "campaign-revision-workbench",
    "member-growth-studio",
    "partner-delivery-studio",
}
REMOVED_PSEUDO_APP_IDS = {
    "client-intake-copilot",
    "revenue-review-studio",
    "support-ops-assistant",
}


def _load_demo_fleet() -> dict:
    script = f"""
      import {{
        buildStudioDemoApps,
        buildStudioDemoAppSummary,
        getStudioDemoActivity,
        getStudioDemoAdminStats,
        getStudioDemoBillingRecord,
        getStudioDemoBuildHistory,
        getStudioDemoDeploymentRecord,
        getStudioDemoRuns,
        getStudioDemoSessions,
        getStudioDemoUsageRecord,
        getStudioDemoUsersRecord,
        getStudioDemoWorkflowNames,
        getStudioDemoWorkspaceUsage,
        listStudioDemoAppConnectors,
      }} from {json.dumps(DEMO_DATA.as_uri())};

      const apps = buildStudioDemoApps();
      const records = Object.fromEntries(apps.map((app) => [app.app_id, {{
        summary: buildStudioDemoAppSummary(app.app_id),
        connectors: listStudioDemoAppConnectors(app.app_id),
        usage: getStudioDemoUsageRecord(app.app_id),
        billing: getStudioDemoBillingRecord(app.app_id),
        deployment: getStudioDemoDeploymentRecord(app.app_id),
        users: getStudioDemoUsersRecord(app.app_id),
        activity: getStudioDemoActivity(app.app_id),
        workflows: getStudioDemoWorkflowNames(app.app_id),
        runs: getStudioDemoRuns(app.app_id),
        sessions: getStudioDemoSessions(app.app_id),
        build_history: getStudioDemoBuildHistory(app.app_id),
        admin_stats: getStudioDemoAdminStats(app.app_id),
      }}]));
      console.log(JSON.stringify({{ apps, records, workspace_usage: getStudioDemoWorkspaceUsage() }}));
    """
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_studio_demo_fleet_is_exactly_three_accepted_canonical_archetypes() -> None:
    payload = _load_demo_fleet()
    apps = payload["apps"]
    matrix_archetypes = {spec.archetype_id for spec in _matrix_specs()}

    assert len(apps) == 3
    assert {app["app_id"] for app in apps} == EXPECTED_APP_IDS
    assert {app["archetype_id"] for app in apps} <= matrix_archetypes
    assert all(app["canonical_structure"] == "mozaiks.app.v1" for app in apps)
    assert all(app["functional_acceptance"] == "passed" for app in apps)


def test_every_demo_portal_uses_the_same_closed_app_fleet() -> None:
    payload = _load_demo_fleet()

    assert set(payload["records"]) == EXPECTED_APP_IDS
    assert {row["app_id"] for row in payload["workspace_usage"]["by_run"]} == EXPECTED_APP_IDS

    for app_id, record in payload["records"].items():
        assert record["summary"]["app"]["functional_acceptance"] == "passed", app_id
        assert record["connectors"], app_id
        assert record["usage"]["workflow_runs"] > 0, app_id
        assert record["billing"]["active_customers"] > 0, app_id
        assert record["deployment"]["failed"] is False, app_id
        assert record["deployment"]["domain_count"] > 0, app_id
        assert record["users"]["users"], app_id
        assert record["activity"], app_id
        assert record["workflows"], app_id
        assert record["runs"], app_id
        assert record["sessions"], app_id
        versions = record["build_history"]["artifact_versions"]
        assert any(
            version["lifecycle_status"] == "current"
            and version["validation_status"] == "passed"
            for version in versions
        ), app_id


def test_removed_pseudo_apps_leave_no_legacy_fixture_data() -> None:
    source = DEMO_DATA.read_text(encoding="utf-8")
    assert not (REMOVED_PSEUDO_APP_IDS & set(source.split()))
    for app_id in REMOVED_PSEUDO_APP_IDS:
        assert app_id not in source

