from __future__ import annotations

import asyncio
import os

import pytest

from factory_app.workflows.AppGenerator.tools.app_validation import run_app_bundle_acceptance_gate
from factory_app.workflows.AppGenerator.tools.export_app_code import resolve_export_gate
from scripts.smoke_appgenerator_live_acceptance import (
    DEFAULT_TRIGGER_EVENT_TYPE,
    DEFAULT_WORKFLOW_CAPABILITY_ID,
    FORBIDDEN_APP_LOCAL_LEDGER_PATH,
    SmokeContext,
    build_appgenerator_acceptance_files,
    default_workflow_integration,
    run_deterministic_appgenerator_repair_loop_smoke,
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


@pytest.mark.asyncio
async def test_appgenerator_repair_loop_smoke_routes_deletes_and_exports() -> None:
    result = await run_deterministic_appgenerator_repair_loop_smoke()

    assert result["success"] is True, result["validation_errors"]
    assert result["forbidden_path"] == FORBIDDEN_APP_LOCAL_LEDGER_PATH
    assert result["initial_acceptance_status"] == "failed"
    assert result["initial_bundle_repair"]["status"] == "needs_revision"
    assert result["initial_bundle_repair"]["target_agent"] == "ServiceAgent"
    assert result["repaired_acceptance_status"] == "passed"
    assert result["repaired_bundle_repair"]["status"] == "passed"
    assert result["export_gate"]["allow_export"] is True
    assert result["packaging"]["removed_forbidden_path"] is True
    assert result["packaging"]["preserved_infra_output"] is True
    assert result["runtime_loader"]["loaded"] is True
    assert FORBIDDEN_APP_LOCAL_LEDGER_PATH not in result["context"]["generated_files"]


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
    assert result["workflow_integration_repair"]["status"] == "needs_revision"
    assert result["workflow_integration_repair"]["attempt"] == 1
    assert context.get("workflow_integration_repair_status") == "needs_revision"
    assert context.get("workflow_integration_repair_count") == 1
    assert "workflow_trigger_reaction_declared" in context.get("workflow_integration_repair_request")
    assert resolve_export_gate(context)["allow_export"] is False
    assert any(
        item["gate"] == "workflow_integration"
        and item["test"] == "workflow_trigger_reaction_declared"
        for item in result["failed_tests"]
    )


@pytest.mark.asyncio
async def test_appgenerator_acceptance_blocks_workflow_trigger_capability_drift() -> None:
    integration = default_workflow_integration()
    files = build_appgenerator_acceptance_files(integration)
    drifted_trigger_events = [
        {
            **integration["trigger_events"][0],
            "capability_id": "wrong-ticket-workflow",
        }
    ]
    context = SmokeContext(
        {
            "workflow_name": "AppGenerator",
            "app_id": "support-operations-live-acceptance",
            "chat_id": "test-workflow-trigger-capability-drift",
            "generated_files": files,
            "generated_workflow_name": integration["workflow_name"],
            "generated_workflow_capability_id": integration["capability_id"],
            "generated_workflow_startup_mode": integration["startup_mode"],
            "generated_workflow_trigger_events": drifted_trigger_events,
            "app_validation_status": "skipped",
            "app_validation_strategy_used": "skip",
        }
    )

    result = await run_app_bundle_acceptance_gate(files=files, context_variables=context)
    gate = resolve_export_gate(context)

    assert result["passed"] is False
    assert gate["allow_export"] is False
    assert "workflow_integration" in result["validation_evidence"]["failed"]
    assert result["workflow_integration_repair"]["status"] == "needs_revision"
    assert context.get("workflow_integration_repair_status") == "needs_revision"
    assert any(
        item["gate"] == "workflow_integration"
        and item["test"] == "workflow_trigger_capability_id_mismatch"
        for item in result["failed_tests"]
    )


@pytest.mark.asyncio
async def test_appgenerator_acceptance_blocks_invented_workflow_route() -> None:
    integration = default_workflow_integration()
    files = build_appgenerator_acceptance_files(integration)
    files["modules/support_tickets/module.yaml"] += """
  - capability_id: wrong-ticket-workflow
    kind: workflow
    target: WrongTicketWorkflow
    title: Wrong generated workflow
"""
    files["modules/support_tickets/contracts/reactions.yaml"] += f"""
  - id: wrong_ticket_workflow_route
    event_type: {DEFAULT_TRIGGER_EVENT_TYPE}
    target:
      kind: capability
      capability_id: wrong-ticket-workflow
    description: Drifted route to an invented workflow capability.
"""
    context = SmokeContext(
        {
            "workflow_name": "AppGenerator",
            "app_id": "support-operations-live-acceptance",
            "chat_id": "test-invented-workflow-route",
            "generated_files": files,
            "generated_workflow_name": integration["workflow_name"],
            "generated_workflow_capability_id": integration["capability_id"],
            "generated_workflow_startup_mode": integration["startup_mode"],
            "generated_workflow_trigger_events": integration["trigger_events"],
            "app_validation_status": "skipped",
            "app_validation_strategy_used": "skip",
        }
    )

    result = await run_app_bundle_acceptance_gate(files=files, context_variables=context)
    failed_tests = {
        item["test"]
        for item in result["failed_tests"]
        if item.get("gate") == "workflow_integration"
    }

    assert result["passed"] is False
    assert resolve_export_gate(context)["allow_export"] is False
    assert "workflow_capability_not_in_metadata" in failed_tests
    assert "workflow_trigger_ambiguous_workflow_reaction" in failed_tests
    assert result["workflow_integration_repair"]["status"] == "needs_revision"
    assert context.get("workflow_integration_repair_status") == "needs_revision"


@pytest.mark.asyncio
async def test_appgenerator_acceptance_requires_deployment_artifacts_when_requested() -> None:
    integration = default_workflow_integration()
    files = build_appgenerator_acceptance_files(integration)
    context = SmokeContext(
        {
            "workflow_name": "AppGenerator",
            "app_id": "support-operations-live-acceptance",
            "chat_id": "test-missing-deployment-artifacts",
            "generated_files": files,
            "generated_workflow_name": integration["workflow_name"],
            "generated_workflow_capability_id": integration["capability_id"],
            "generated_workflow_startup_mode": integration["startup_mode"],
            "generated_workflow_trigger_events": integration["trigger_events"],
            "includeDockerfiles": True,
        }
    )

    result = await run_app_bundle_acceptance_gate(files=files, context_variables=context)

    assert result["passed"] is False
    assert "bundle_scan" in result["validation_evidence"]["failed"]
    assert any(
        "deployment artifacts" in item["error"]
        for item in result["failed_tests"]
        if item.get("gate") == "bundle_scan"
    )


@pytest.mark.asyncio
async def test_appgenerator_acceptance_requires_deployment_artifacts_for_production_profile() -> None:
    integration = default_workflow_integration()
    files = build_appgenerator_acceptance_files(integration)
    context = SmokeContext(
        {
            "workflow_name": "AppGenerator",
            "app_id": "support-operations-live-acceptance",
            "chat_id": "test-missing-production-deployment-artifacts",
            "generated_files": files,
            "generated_workflow_name": integration["workflow_name"],
            "generated_workflow_capability_id": integration["capability_id"],
            "generated_workflow_startup_mode": integration["startup_mode"],
            "generated_workflow_trigger_events": integration["trigger_events"],
            "deployment_profile": "production_container",
        }
    )

    result = await run_app_bundle_acceptance_gate(files=files, context_variables=context)

    assert result["passed"] is False
    assert "bundle_scan" in result["validation_evidence"]["failed"]
    assert any(
        "deployment artifacts" in item["error"]
        for item in result["failed_tests"]
        if item.get("gate") == "bundle_scan"
    )


@pytest.mark.asyncio
async def test_appgenerator_acceptance_blocks_workflow_integration_repair_after_max_attempts() -> None:
    integration = default_workflow_integration()
    files = build_appgenerator_acceptance_files(integration)
    del files["modules/support_tickets/contracts/reactions.yaml"]
    context = SmokeContext(
        {
            "workflow_name": "AppGenerator",
            "app_id": "support-operations-live-acceptance",
            "chat_id": "test-workflow-integration-repair-exhausted",
            "generated_files": files,
            "generated_workflow_name": integration["workflow_name"],
            "generated_workflow_capability_id": integration["capability_id"],
            "generated_workflow_startup_mode": integration["startup_mode"],
            "generated_workflow_trigger_events": integration["trigger_events"],
            "workflow_integration_repair_count": 2,
        }
    )

    result = await run_app_bundle_acceptance_gate(files=files, context_variables=context)

    assert result["passed"] is False
    assert result["workflow_integration_repair"]["status"] == "blocked"
    assert result["workflow_integration_repair"]["attempt"] == 2
    assert result["workflow_integration_repair"]["repairable"] is False
    assert context.get("workflow_integration_repair_status") == "blocked"
    assert context.get("workflow_integration_repair_count") == 2
    assert resolve_export_gate(context)["allow_export"] is False


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
    assert result["live_agentgenerator"]["semantic_drift"]["valid"] is True
    assert result["appgenerator_acceptance"]["app_bundle_acceptance_status"] == "passed"
    assert result["appgenerator_acceptance"]["export_gate"]["allow_export"] is True
