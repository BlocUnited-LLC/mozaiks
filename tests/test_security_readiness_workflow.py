from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppReview.tools.review_context import build_review_summary_payload
from factory_app.workflows.SecurityReadiness.tools.inspect_generated_app_security import (
    inspect_generated_app_security,
)
from factory_app.workflows.SecurityReadiness.tools.record_security_findings import (
    record_security_findings,
)
from mozaiksai.core.workflow.pack.config import load_global_pack_graph

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_security_readiness_detects_secret_and_contract_findings() -> None:
    ctx = {
        "app_id": "app_1",
        "generated_files": {
            "app/app.json": json.dumps({"authRequired": True}),
            "app/security/secrets.yaml": "version: 1\nsecrets:\n  - id: stripe\n    value: test_sentinel_raw_value\n",
            "app/modules/orders/module.yaml": "schema_version: mozaiks.module.v1\nmodule:\n  id: orders\nactions:\n  - id: create_order\n    api_surface: private\n    permissions: []\n",
            "Dockerfile": "FROM python:3.13-slim\n",
        },
    }

    result = await inspect_generated_app_security(context_variables=ctx)

    assert result["status"] == "attention_required"
    finding_ids = {item["finding_id"] for item in result["findings"]}
    assert "secret_contract:raw_value_field:0" in finding_ids
    assert "auth_contract:missing_auth_yaml" in finding_ids
    assert "module_permissions:missing:orders:create_order" in finding_ids
    assert "production_operations:missing" in finding_ids
    assert ctx["security_readiness_summary"]["summary"]["open"] >= 4


@pytest.mark.asyncio
async def test_record_security_findings_updates_context_without_persistence() -> None:
    ctx = {"app_id": "app_1", "security_readiness_findings": [{"finding_id": "x"}]}

    result = await record_security_findings(context_variables=ctx)

    assert result["success"] is True
    assert result["persisted"] is False
    assert ctx["security_readiness_recorded"] is True
    assert ctx["security_readiness_summary"]["finding_count"] == 1


def test_security_readiness_build_context_declares_workflow_assets() -> None:
    path = ROOT / "factory_app" / "build_context" / "SecurityReadiness" / "context.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert data["context_id"] == "SecurityReadiness"
    assert data["applies_to_workflows"] == ["SecurityReadiness"]
    asset_paths = {asset["path"] for asset in data["assets"]}
    assert {
        "baseline_security_controls.yaml",
        "app_security_file_contracts.yaml",
        "finding_taxonomy.yaml",
    } <= asset_paths


def test_security_readiness_is_between_app_generator_and_review(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_PATH", str(ROOT / "__no_active_app__"))
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(ROOT / "factory_app" / "workflows"))

    graph = load_global_pack_graph()
    assert graph is not None
    workflow_ids = {workflow.id for workflow in graph.workflows}
    assert "SecurityReadiness" in workflow_ids

    build = next(seq for seq in graph.journeys if seq.id == "build")
    flattened = [workflow for step in build.steps for workflow in step.workflows]
    assert flattened.index("AppGenerator") < flattened.index("SecurityReadiness")

    review_index = next(
        index for index, step in enumerate(build.steps) if step.transition == "app_review"
    )
    security_index = next(
        index for index, step in enumerate(build.steps) if step.workflows == ["SecurityReadiness"]
    )
    assert security_index < review_index


def test_app_review_payload_carries_security_readiness_summary() -> None:
    payload = build_review_summary_payload(
        {
            "build_registry_id": "reg_1",
            "artifact_version_id": "art_1",
            "lifecycle_state": "review",
            "app_validation_status": "passed",
            "app_bundle_acceptance_status": "passed",
            "integration_tests_passed": True,
            "security_readiness_summary": {"status": "attention_required", "finding_count": 1},
        }
    )

    assert payload["can_promote"] is True
    assert payload["security_readiness_summary"] == {
        "status": "attention_required",
        "finding_count": 1,
    }
