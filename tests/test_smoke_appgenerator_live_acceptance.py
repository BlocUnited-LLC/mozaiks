from __future__ import annotations

import asyncio
import os

import pytest

from factory_app.workflows.AppGenerator.tools.app_validation import run_app_bundle_acceptance_gate
from scripts.smoke_appgenerator_live_acceptance import (
    DEFAULT_TRIGGER_EVENT_TYPE,
    DEFAULT_WORKFLOW_CAPABILITY_ID,
    SmokeContext,
    build_appgenerator_acceptance_files,
    default_workflow_integration,
    run_live_agentgenerator_to_appgenerator_acceptance_smoke,
    validate_appgenerator_acceptance_handoff,
)


def _live_appgenerator_acceptance_smoke_enabled() -> bool:
    raw = str(os.getenv("RUN_LIVE_APPGENERATOR_ACCEPTANCE_SMOKE") or "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@pytest.mark.asyncio
async def test_appgenerator_acceptance_handoff_fixture_passes_deterministic_gate() -> None:
    result = await validate_appgenerator_acceptance_handoff()

    assert result["success"] is True, result["validation_errors"]
    assert result["app_bundle_acceptance_status"] == "passed"
    assert result["export_gate"]["allow_export"] is True
    assert result["app_bundle_validation_evidence"]["failed"] == []
    assert "workflow_integration" in result["app_bundle_validation_evidence"]["completed"]
    assert result["runtime_loader"]["loaded"] is True
    assert result["runtime_loader"]["module_ids"] == ["support_tickets"]
    assert DEFAULT_WORKFLOW_CAPABILITY_ID in result["runtime_loader"]["reaction_capability_ids"]


def test_appgenerator_acceptance_fixture_wires_workflow_capability_not_raw_workflow_name() -> None:
    files = build_appgenerator_acceptance_files()

    reactions_yaml = files["modules/support_tickets/contracts/reactions.yaml"]
    module_yaml = files["modules/support_tickets/module.yaml"]

    assert f"event_type: {DEFAULT_TRIGGER_EVENT_TYPE}" in reactions_yaml
    assert f"capability_id: {DEFAULT_WORKFLOW_CAPABILITY_ID}" in reactions_yaml
    assert "target:\n      kind: capability" in reactions_yaml
    assert "kind: workflow" in module_yaml
    assert "target: TicketBatchTriageWorkflow" in module_yaml


@pytest.mark.asyncio
async def test_appgenerator_acceptance_blocks_missing_workflow_reaction() -> None:
    integration = default_workflow_integration()
    files = build_appgenerator_acceptance_files(integration)
    del files["modules/support_tickets/contracts/reactions.yaml"]
    context = SmokeContext(
        {
            "workflow_name": "AppGenerator",
            "app_id": "support-operations-live-acceptance",
            "chat_id": "test-missing-workflow-reaction",
            "generated_files": files,
            "generated_workflow_name": integration["workflow_name"],
            "generated_workflow_capability_id": integration["capability_id"],
            "generated_workflow_startup_mode": integration["startup_mode"],
            "generated_workflow_trigger_events": integration["trigger_events"],
        }
    )

    result = await run_app_bundle_acceptance_gate(files=files, context_variables=context)

    assert result["passed"] is False
    assert "workflow_integration" in result["validation_evidence"]["failed"]
    assert context.get("workflow_integration_validation_passed") is False
    assert any(
        item["gate"] == "workflow_integration"
        and item["test"] == "workflow_trigger_reaction_declared"
        for item in result["failed_tests"]
    )


@pytest.mark.skipif(
    not _live_appgenerator_acceptance_smoke_enabled(),
    reason=(
        "Set RUN_LIVE_APPGENERATOR_ACCEPTANCE_SMOKE=1 to run the live "
        "AgentGenerator to AppGenerator acceptance smoke"
    ),
)
def test_live_agentgenerator_to_appgenerator_acceptance_smoke() -> None:
    result = asyncio.run(
        run_live_agentgenerator_to_appgenerator_acceptance_smoke(timeout_seconds=600.0)
    )

    assert result["success"] is True, result.get("validation_errors")
    assert result["live_agentgenerator"]["task_run_trace"]["max_overlap"] >= 2
    assert result["appgenerator_acceptance"]["app_bundle_acceptance_status"] == "passed"
    assert result["appgenerator_acceptance"]["export_gate"]["allow_export"] is True
