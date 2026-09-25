"""Repair merges use the declared tool lane and retain the full exported pack."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows.AgentGenerator.tools import generate_and_download as download
from factory_app.workflows.AgentGenerator.tools.workflow_quality_gate import (
    merge_workflow_bundle_repair_results,
)
from mozaiksai.core.events import auto_tool_handler
from mozaiksai.core.workflow.agents import tools as tool_loader
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge, _wrap_tool_with_context
from mozaiksai.core.workflow.context.authority import (
    ContextAuthorityError,
    build_context_authority_policy,
)
from mozaiksai.core.workflow.execution.network_graph import (
    compile_transition_rules_to_graph,
    resolve_next_agent,
)
from mozaiksai.core.workflow.outputs import structured
from mozaiksai.core.workflow.workflow_manager import UnifiedWorkflowManager
from tests.test_agentgenerator_generate_and_download_collection import (
    _make_bundle_results,
    _minimal_workflow_files,
)

WORKFLOWS = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"
WORKFLOW = "AgentGenerator"
MERGE_AGENT = "WorkflowBundleMergeAgent"
RUN = (WORKFLOW, "app-repair", "chat-repair")


def _document(name):
    return yaml.safe_load((WORKFLOWS / WORKFLOW / name).read_text(encoding="utf-8"))


def _graph(policy):
    return compile_transition_rules_to_graph(
        _document("transition_graph.yaml")["transition_rules"], initial_agent_name="InterviewAgent",
        agent_id_by_name={agent["name"]: agent["name"] for agent in _document("agents.yaml")["agents"]},
        context_authority_policy=policy,
    )


def _bridge(*, active=True, **state):
    rules = _document("transition_graph.yaml")["transition_rules"]
    batch = _document("extended_orchestration/task_batches.yaml")["batches"][0]
    policy = build_context_authority_policy(
        workflow_name=WORKFLOW,
        definitions=_document("context_variables.yaml")["definitions"],
        transition_rules=rules,
        task_batch_context_keys={batch["result"]["context_key"], batch["result"]["status_key"]},
    )
    context = ContextVariablesBridge({
        "app_id": RUN[1], "chat_id": RUN[2], "user_id": "user-repair", "pack_name": "RepairPack",
        "run_build_binding": {
            "build_registry_id": "registry-repair", "target_app_id": "target-app",
            "build_id": "build-repair", "phase": "genesis",
        },
        "workflow_bundle_repair_active": active,
        "workflow_bundle_repair_count": 1,
        "workflow_bundle_repair_status": "needs_revision",
        "workflow_bundle_repair_base_results": _make_bundle_results([
            {"workflow_name": "GoodWorkflow", "files": _minimal_workflow_files("GoodWorkflow")},
            {"workflow_name": "BrokenWorkflow", "files": _minimal_workflow_files("BrokenWorkflow")[:2]},
        ]),
        "workflow_bundle_results": _make_bundle_results([
            {"workflow_name": "BrokenWorkflow", "files": _minimal_workflow_files("BrokenWorkflow")},
        ]),
        "workflow_bundle_repair_original_workflows_spec": [
            {"name": "GoodWorkflow"}, {"name": "BrokenWorkflow"},
        ],
        "workflows_spec": [{"name": "BrokenWorkflow"}],
        **state,
    }, authority_policy=policy)
    context._bind_run(RUN, policy)
    return context, policy


@pytest.fixture
def runtime_tools(monkeypatch):
    # The production loader intentionally refreshes workflow modules. Restore
    # their identities so later tests can patch the modules they imported.
    namespaces = ("workflows", "factory_app.workflows")
    saved_modules = {
        name: module for name, module in sys.modules.items()
        if any(name == prefix or name.startswith(prefix + ".") for prefix in namespaces)
    }
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(UnifiedWorkflowManager, "_instance", None)
    manager = UnifiedWorkflowManager(workflows_base_path=str(WORKFLOWS))
    for module in (auto_tool_handler, tool_loader, structured):
        monkeypatch.setattr(module, "workflow_manager", manager)
    for cache in ("_workflow_models", "_workflow_registries", "_workflow_structured_agents", "_provider_response_model_cache"):
        monkeypatch.setattr(structured, cache, {})
    assert manager.get_workflow_info(WORKFLOW)["status"] == "loaded"
    handler = auto_tool_handler.AutoToolEventHandler()
    for method in ("_emit_tool_call", "_emit_tool_result", "_persist_context_variables"):
        monkeypatch.setattr(handler, method, AsyncMock())
    try:
        yield handler
    finally:
        for name in list(sys.modules):
            if any(name == prefix or name.startswith(prefix + ".") for prefix in namespaces):
                sys.modules.pop(name, None)
        sys.modules.update(saved_modules)
        for name, module in saved_modules.items():
            parent, _, child = name.rpartition(".")
            if parent in sys.modules:
                setattr(sys.modules[parent], child, module)


def test_unprivileged_merge_cannot_claim_success_or_change_repair_state():
    context, _ = _bridge()
    before = context.snapshot()
    with pytest.raises(ContextAuthorityError):
        merge_workflow_bundle_repair_results(context)
    assert context.snapshot() == before


@pytest.mark.parametrize("results", [{}, {"other": {"workflow_name": "OtherWorkflow"}}])
def test_incomplete_repair_results_cannot_claim_merged(results):
    context, _ = _bridge(workflow_bundle_results=results)
    before = context.snapshot()
    with pytest.raises(ValueError, match="repair results"):
        _wrap_tool_with_context(merge_workflow_bundle_repair_results, context)()
    assert context.snapshot() == before


@pytest.mark.parametrize("active,expected", [(True, MERGE_AGENT), (False, "PackMetadataAgent")])
def test_compiled_batch_exit_merges_only_active_repairs(active, expected):
    context, policy = _bridge(active=active)
    graph = _graph(policy)
    assert resolve_next_agent(
        graph, current_agent_name="PackBuildCoordinator", context_variables=context.snapshot(),
    ) == expected


@pytest.mark.asyncio
async def test_real_auto_tool_merge_preserves_passing_workflows_through_export(runtime_tools, monkeypatch, tmp_path):
    import zipfile

    context, policy = _bridge()
    await runtime_tools.handle_tool_dispatch({
        "agent_name": MERGE_AGENT, "model_name": "WorkflowBundleMergeRequest", "auto_tool_call": True,
        "structured_data": {"agent_message": "Restore the complete workflow pack."},
        "context": {"workflow_name": WORKFLOW, "app_id": RUN[1], "chat_id": RUN[2]},
        "turn_idempotency_key": "repair-merge-1", "_pattern_context_ref": context,
    })
    merged = context.get("workflow_bundle_results")
    assert {value["workflow_name"] for key, value in merged.items() if not key.startswith("_")} == {
        "GoodWorkflow", "BrokenWorkflow",
    }
    assert context.get("workflow_bundle_repair_status") == "merged"
    assert context.get("workflow_bundle_repair_merge_result")["merged_workflow_count"] == 2
    assert runtime_tools._emit_tool_result.call_args.args[4] == "ok"
    assert context.get("workflow_bundle_repair_active") is False
    assert [spec["name"] for spec in context.get("workflows_spec")] == ["GoodWorkflow", "BrokenWorkflow"]
    updates = context.consume_authorized_context_updates(policy=policy, run_identity=RUN)
    assert set(updates["set"]["workflow_bundle_results"]) == {"goodworkflow", "brokenworkflow", "_meta"}
    graph = _graph(policy)
    assert resolve_next_agent(graph, current_agent_name=MERGE_AGENT, context_variables=context.snapshot()) == "PackMetadataAgent"

    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path / "generated"))
    for method in ("record_workflow_export", "record_workflow_artifacts", "_register_workflow_bundle_artifact_version"):
        monkeypatch.setattr(download, method, AsyncMock())
    monkeypatch.setattr(download, "use_ui_tool", AsyncMock(return_value={"status": "completed", "data": {}, "agentContext": {}}))
    result = await _wrap_tool_with_context(download.generate_and_download, context)(
        DownloadRequest={"confirmation_only": False, "storage_backend": "none"}, agent_message="Pack ready.",
    )
    assert result["status"] == "success"
    with zipfile.ZipFile(result["ui_files"][0]["path"]) as archive:
        names = archive.namelist()
    for workflow_name in ("GoodWorkflow", "BrokenWorkflow"):
        assert f"{workflow_name}/orchestrator.yaml" in names


def test_merge_failure_has_no_metadata_fallback():
    context, policy = _bridge()
    graph = _graph(policy)
    assert resolve_next_agent(graph, current_agent_name=MERGE_AGENT, context_variables=context.snapshot()) == "terminate"
