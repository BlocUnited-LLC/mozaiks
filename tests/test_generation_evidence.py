import logging
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from factory_app.eval import GenerationEvidence, collect_generation_evidence


def test_evidence_contains_facts_without_source_warning_text_or_identity():
    facts = collect_generation_evidence({
        "generated_files": {"modules/orders/backend/service.py": "private source"},
        "capability_packs": [{"id": "available-only"}], "owner_id": "private-owner",
        "app_build_plan": {"capability_packs": [{"capability_pack_id": "payments"}]},
        "module_contract_quality_status": "blocked",
        "module_contract_quality_warnings": ["private diagnostic"],
        "app_ui_quality_status": "passed", "model_name": "measured-model",
    })
    assert facts.module_ids == ["orders"]
    assert facts.capability_packs == ["payments"]
    assert facts.gates[1].issue_count == 1
    assert "private" not in facts.model_dump_json()
    with pytest.raises(ValidationError):
        GenerationEvidence.model_validate({"owner_id": "forbidden"})


def test_available_pack_descriptors_are_not_selection_evidence():
    assert collect_generation_evidence({"capability_packs": [{"id": "available"}]}).capability_packs == []


@pytest.mark.parametrize("second_status", ["passed", "blocked"])
def test_evidence_rejects_duplicate_gate_facts(second_status):
    with pytest.raises(ValidationError, match="each gate_type at most once"):
        GenerationEvidence.model_validate({"gates": [
            {"gate_type": "ui_quality", "status": "blocked", "issue_count": 1},
            {"gate_type": "ui_quality", "status": second_status, "issue_count": 0},
        ]})


@pytest.mark.asyncio
async def test_completion_outbox_carries_current_evidence_before_delivery(monkeypatch):
    from factory_app.workflows._shared.platform import build_lifecycle as hooks

    context = {"app_id": "app", "build_id": "build", "build_registry_id": "registry",
               "journey_instance_id": "journey", "execution_id": "execution",
               "session_context": {"module_contract_quality_status": "passed"}}
    monkeypatch.setattr(hooks, "_resolve_build_event_context", AsyncMock(return_value=context))
    monkeypatch.setattr(hooks, "_should_emit_build_completed", lambda **kw: True)
    monkeypatch.setattr(hooks, "get_build_artifacts", AsyncMock(return_value={}))
    stored = AsyncMock(return_value="outbox-1")
    monkeypatch.setattr(hooks, "upsert_outbox_event", stored)
    monkeypatch.setattr(hooks, "_materialize_local_app_registry_event", AsyncMock())
    delivered = []
    monkeypatch.setattr(hooks, "_spawn_delivery", lambda **kw: delivered.append(kw))
    monkeypatch.delenv("MOZAIKS_TELEMETRY_ENDPOINT", raising=False)
    await hooks.emit_build_completed(app_id="app", workflow_name="AppGenerator",
        context_variables={"module_contract_quality_status": "blocked"})
    evidence = stored.call_args.kwargs["payload"]["buildEvidence"]
    assert evidence["gates"][0]["status"] == "blocked"
    assert delivered == [{"outbox_event_id": "outbox-1"}]


@pytest.mark.asyncio
async def test_declared_completion_hook_receives_live_normalized_plan(monkeypatch):
    from factory_app.workflows._shared.platform import build_lifecycle as hooks
    from factory_app.workflows.AppGenerator.tools.app_build_plan import app_build_plan
    from mozaiksai.core.workflow.context.adapter import create_context_container
    from mozaiksai.core.workflow.execution import lifecycle

    live = create_context_container({"capability_packs": [{"id": "available-only"}]})
    app_build_plan(AppBuildPlan={
        "app_kind": "internal_tool", "pages": [{"name": "Orders", "route": "/orders"}],
        "capability_packs": [{"capability_pack_id": "orders", "surface_id": "orders", "capability_source": "custom"}],
        "build_tasks": [],
    }, context_variables=live)
    live.set("generated_files", {"modules/orders/backend/handler.py": "private-source"})
    live.set("module_contract_quality_status", "passed")

    workflow = Path(__file__).resolve().parents[1] / "factory_app/workflows/AppGenerator"
    monkeypatch.setattr(lifecycle, "resolve_workflow_path", lambda name: workflow)
    manager = lifecycle.LifecycleToolManager("AppGenerator")
    manager.load_lifecycle_tools()
    tool = next(t for t in manager.tools[lifecycle.LifecycleTrigger.ON_COMPLETE] if t.function == "emit_build_completed")
    assert tool.accepts_context
    monkeypatch.setitem(tool.callable.__globals__, "_read_build_mode", AsyncMock(return_value=None))
    monkeypatch.setitem(tool.callable.__globals__, "_persist_app_bundle_artifact", AsyncMock())
    monkeypatch.setattr(hooks, "_resolve_build_event_context", AsyncMock(return_value={
        "app_id": "app", "build_id": "build", "build_registry_id": "registry",
        "execution_id": "execution", "journey_instance_id": "journey", "session_context": {},
    }))
    monkeypatch.setattr(hooks, "_should_emit_build_completed", lambda **kw: True)
    monkeypatch.setattr(hooks, "get_build_artifacts", AsyncMock(return_value={}))
    stored = AsyncMock(return_value="outbox-live")
    monkeypatch.setattr(hooks, "upsert_outbox_event", stored)
    monkeypatch.setattr(hooks, "_materialize_local_app_registry_event", AsyncMock())
    monkeypatch.setattr(hooks, "_spawn_delivery", lambda **kw: None)
    await manager._execute_single_tool(tool, live, logging.getLogger(__name__), call_kwargs={
        "app_id": "app", "workflow_name": "AppGenerator",
    })
    evidence = stored.call_args.kwargs["payload"]["buildEvidence"]
    assert evidence["capability_packs"] == ["orders"]
    assert evidence["module_ids"] == ["orders"]
    assert evidence["gates"][0]["status"] == "passed"
    assert "private-source" not in str(evidence)
