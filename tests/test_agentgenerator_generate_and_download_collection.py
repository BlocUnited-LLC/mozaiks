"""Tests for generate_and_download reading from workflow_bundle_results (task batch output)."""

from __future__ import annotations

import asyncio
import copy
import zipfile
from importlib import import_module
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml


def _load_generate_and_download_module():
    return import_module("factory_app.workflows.AgentGenerator.tools.generate_and_download")


generate_and_download_module = _load_generate_and_download_module()
workflow_quality_gate_module = import_module(
    "factory_app.workflows.AgentGenerator.tools.workflow_quality_gate"
)


class _Context:
    def __init__(self, initial=None) -> None:
        self.data = dict(initial or {})

    def set(self, key, value) -> None:
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)


def _make_bundle_results(workflows: list[dict]) -> dict:
    """Build a workflow_bundle_results dict as the task batch executor would."""
    results = {}
    for _i, wf in enumerate(workflows):
        task_id = wf["workflow_name"].lower().replace(" ", "_")
        results[task_id] = {
            "workflow_name": wf["workflow_name"],
            "pattern_id": wf.get("pattern_id", 1),
            "pattern_name": wf.get("pattern_name", "Pipeline"),
            "agent_message": "Generated successfully.",
            "files": wf.get("files", []),
            "_task_id": task_id,
            "_worker_agent": "WorkflowBundleBuilderAgent",
        }
    results["_meta"] = {"batch_id": "workflow_generation_tasks", "task_count": len(workflows)}
    return results


def _minimal_workflow_files(
    workflow_name: str,
    *,
    startup_mode: str = "AgentDriven",
    triggers: list[dict] | None = None,
    include_task_batches: bool = False,
) -> list[dict[str, str]]:
    agents = ["PlannerAgent", "WorkerAgent", "ReviewerAgent"]
    context_definitions = {
        "tenant_id": {"type": "string", "source": {"type": "state", "default": None}},
        "app_id": {"type": "string", "source": {"type": "state", "default": None}},
        "user_id": {"type": "string", "source": {"type": "state", "default": None}},
    }
    if include_task_batches:
        context_definitions["review_tasks_results"] = {
            "type": "array",
            "source": {"type": "state", "default": []},
        }
        context_definitions["review_tasks_status"] = {
            "type": "object",
            "source": {"type": "state", "default": {}},
        }
    files = {
        "orchestrator.yaml": f"""
schema_version: mozaiks.orchestrator.v1
workflow_name: {workflow_name}
max_turns: 4
human_in_the_loop: false
workflow_startup_mode: {startup_mode}
orchestration_pattern: ag2_network
initial_agent: PlannerAgent
initial_message: Run the generated workflow.
triggers: {triggers or [{"type": "chat", "description": "Start the generated workflow."}]}
""",
        "agents.yaml": "\n".join(
            [
                "agents:",
                *[
                    f"  - name: {agent}\n    system_message: {agent} executes workflow work."
                    for agent in agents
                ],
            ]
        )
        + "\n",
        "transition_graph.yaml": """
transition_rules:
  - source_agent: PlannerAgent
    target_agent: terminate
    transition_type: after_turn
""",
        "context_variables.yaml": (
            "definitions:\n"
            + "\n".join(
                f"  {name}:\n    type: {definition['type']}\n    source:\n"
                f"      type: {definition['source']['type']}\n"
                f"      default: {definition['source']['default']!r}"
                for name, definition in context_definitions.items()
            )
            + "\nagents:\n  PlannerAgent:\n    variables:\n      - tenant_id\n      - app_id\n      - user_id\n"
        ),
        "structured_outputs.yaml": """
models: {}
schema_version: mozaiks.structured_outputs.v1
registry:
  PlannerAgent: null
  WorkerAgent: null
  ReviewerAgent: null
""",
        "tools.yaml": "tools: []\nlifecycle_tools: []\n",
        "middleware.yaml": "prompt_middleware: []\n",
        "ui_config.yaml": "visual_agents: []\n",
    }
    if include_task_batches:
        files["extended_orchestration/task_batches.yaml"] = """
version: 1
conveyors:
  - id: review_tasks
    decomposition_agent: PlannerAgent
    execution_agents:
      - WorkerAgent
      - ReviewerAgent
    concurrency: 2
    require_owned_paths: false
"""
    return [
        {"filename": filename, "content": content}
        for filename, content in files.items()
    ]


def test_generate_and_download_writes_bundle_files_and_creates_zip(
    monkeypatch, tmp_path: Path
) -> None:
    """generate_and_download writes WorkflowBundleBuilderOutput files to disk and zips them."""
    bundle_results = _make_bundle_results([
        {
            "workflow_name": "ReviewWorkflow",
            "files": _minimal_workflow_files("ReviewWorkflow"),
        }
    ])

    context = _Context(
        {
            "chat_id": "chat-1",
            "app_id": "app-1",
            "build_id": "build-1",
            "workflow_name": "AgentGenerator",
            "user_id": "user-1",
            "pack_name": "ReviewWorkflow",
            "is_multi_workflow": False,
            "workflow_bundle_results": bundle_results,
        }
    )

    # Redirect output to tmp_path
    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path / "generated"))
    monkeypatch.setattr(generate_and_download_module, "_promote_workflow_to_app_workspace", lambda *a, **kw: None)
    monkeypatch.setattr(generate_and_download_module, "record_workflow_export", AsyncMock())
    monkeypatch.setattr(generate_and_download_module, "record_workflow_artifacts", AsyncMock())
    monkeypatch.setattr(generate_and_download_module, "resolve_agent_api_url", lambda app_id: f"https://api.test/{app_id}")
    monkeypatch.setattr(generate_and_download_module, "resolve_agent_websocket_url", lambda app_id: f"wss://ws.test/{app_id}")
    monkeypatch.setattr(
        generate_and_download_module,
        "_register_workflow_bundle_artifact_version",
        AsyncMock(return_value=type("AV", (), {"id": "av_1"})()),
    )
    monkeypatch.setattr(
        generate_and_download_module,
        "use_ui_tool",
        AsyncMock(return_value={"status": "completed", "data": {}, "agentContext": {}}),
    )

    result = asyncio.run(
        generate_and_download_module.generate_and_download(
            DownloadRequest={"confirmation_only": False, "storage_backend": "none"},
            agent_message="Workflow bundle ready.",
            context_variables=context,
        )
    )

    assert result["status"] == "success"
    assert context.data["workflow_bundle_validation_status"] == "passed"
    assert len(result["ui_files"]) == 1
    zip_entry = result["ui_files"][0]
    assert zip_entry["type"] == "zip"
    assert "ReviewWorkflow" in zip_entry["name"]

    zip_path = Path(zip_entry["path"])
    assert zip_path.exists()
    assert zip_path.parent == tmp_path / "generated" / "workflows" / "app-1" / "build-1"
    assert (zip_path.parent / "ReviewWorkflow" / "orchestrator.yaml").exists()
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert any("orchestrator.yaml" in n for n in names)
    assert any("agents.yaml" in n for n in names)


@pytest.mark.parametrize("filename", ["orchestrator.yaml", "structured_outputs.yaml"])
@pytest.mark.parametrize("mutation", ["missing", "null", "unknown", "whitespace"])
def test_workflow_quality_gate_rejects_invalid_document_versions_without_mutating_input(
    filename: str, mutation: str,
) -> None:
    entries = [{"workflow_name": "ReviewWorkflow", "files": _minimal_workflow_files("ReviewWorkflow")}]
    assert workflow_quality_gate_module.validate_workflow_bundle_structure(bundle_entries=entries)["valid"]
    selected = next(item for item in entries[0]["files"] if item["filename"] == filename)
    document = yaml.safe_load(selected["content"])
    if mutation == "missing":
        del document["schema_version"]
    elif mutation == "null":
        document["schema_version"] = None
    elif mutation == "unknown":
        document["schema_version"] = "mozaiks.unsupported.v99"
    else:
        document["schema_version"] = f" {document['schema_version']} "
    selected["content"] = yaml.safe_dump(document, sort_keys=False)
    before = copy.deepcopy(entries)

    report = workflow_quality_gate_module.validate_workflow_bundle_structure(bundle_entries=entries)

    assert report["valid"] is False
    assert len(report["errors"]) == 1
    assert filename in report["errors"][0]
    assert "schema_version" in report["errors"][0]
    assert entries == before


def test_generate_and_download_blocks_unversioned_document_before_packaging(monkeypatch, tmp_path: Path) -> None:
    files = _minimal_workflow_files("ReviewWorkflow")
    selected = next(item for item in files if item["filename"] == "structured_outputs.yaml")
    document = yaml.safe_load(selected["content"])
    del document["schema_version"]
    selected["content"] = yaml.safe_dump(document, sort_keys=False)
    bundle_results = _make_bundle_results([{"workflow_name": "ReviewWorkflow", "files": files}])
    before = copy.deepcopy(bundle_results)
    context = _Context({
        "chat_id": "chat-version-blocked", "app_id": "app-version-blocked",
        "user_id": "user-version-blocked", "pack_name": "ReviewWorkflow",
        "workflow_bundle_results": bundle_results,
    })
    generated_root = tmp_path / "generated"
    ui_mock = AsyncMock()
    registration_mock = AsyncMock()
    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(generated_root))
    monkeypatch.setattr(generate_and_download_module, "use_ui_tool", ui_mock)
    monkeypatch.setattr(generate_and_download_module, "_register_workflow_bundle_artifact_version", registration_mock)

    result = asyncio.run(generate_and_download_module.generate_and_download(
        DownloadRequest={"confirmation_only": False, "storage_backend": "none"},
        agent_message="Workflow bundle ready.", context_variables=context,
    ))

    assert result["status"] == "blocked"
    assert context.data["workflow_bundle_validation_status"] == "failed"
    assert any("structured_outputs.yaml" in error and "schema_version" in error for error in result["validation_errors"])
    assert bundle_results == before
    assert not generated_root.exists()
    ui_mock.assert_not_awaited()
    registration_mock.assert_not_awaited()


def test_generate_and_download_multi_workflow_pack_zips_all_bundles(
    monkeypatch, tmp_path: Path
) -> None:
    """Multi-workflow packs write all bundles into a single zip."""
    bundle_results = _make_bundle_results([
        {
            "workflow_name": "OnboardingWorkflow",
            "files": _minimal_workflow_files("OnboardingWorkflow"),
        },
        {
            "workflow_name": "NotificationWorkflow",
            "files": _minimal_workflow_files("NotificationWorkflow"),
        },
    ])

    context = _Context(
        {
            "chat_id": "chat-2",
            "app_id": "app-2",
            "user_id": "user-2",
            "pack_name": "UserPlatformPack",
            "is_multi_workflow": True,
            "workflow_bundle_results": bundle_results,
        }
    )

    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path / "generated"))
    monkeypatch.setattr(generate_and_download_module, "_promote_workflow_to_app_workspace", lambda *a, **kw: None)
    monkeypatch.setattr(generate_and_download_module, "record_workflow_export", AsyncMock())
    monkeypatch.setattr(generate_and_download_module, "record_workflow_artifacts", AsyncMock())
    monkeypatch.setattr(generate_and_download_module, "resolve_agent_api_url", lambda app_id: f"https://api.test/{app_id}")
    monkeypatch.setattr(generate_and_download_module, "resolve_agent_websocket_url", lambda app_id: f"wss://ws.test/{app_id}")
    monkeypatch.setattr(
        generate_and_download_module,
        "_register_workflow_bundle_artifact_version",
        AsyncMock(return_value=type("AV", (), {"id": "av_2"})()),
    )
    monkeypatch.setattr(
        generate_and_download_module,
        "use_ui_tool",
        AsyncMock(return_value={"status": "completed", "data": {}, "agentContext": {}}),
    )

    result = asyncio.run(
        generate_and_download_module.generate_and_download(
            DownloadRequest={"confirmation_only": False, "storage_backend": "none"},
            agent_message="Pack bundle ready.",
            context_variables=context,
        )
    )

    assert result["status"] == "success"
    zip_entry = result["ui_files"][0]
    assert "UserPlatformPack" in zip_entry["name"]

    with zipfile.ZipFile(Path(zip_entry["path"])) as zf:
        names = zf.namelist()
    assert any("OnboardingWorkflow" in n for n in names)
    assert any("NotificationWorkflow" in n for n in names)


def test_generate_and_download_returns_error_when_bundle_results_missing(
    monkeypatch, tmp_path: Path
) -> None:
    """Returns an error when workflow_bundle_results is absent from context."""
    context = _Context(
        {
            "chat_id": "chat-3",
            "app_id": "app-3",
            "user_id": "user-3",
        }
    )

    result = asyncio.run(
        generate_and_download_module.generate_and_download(
            DownloadRequest={"confirmation_only": False, "storage_backend": "none"},
            agent_message="Bundle ready.",
            context_variables=context,
        )
    )

    assert result["status"] == "error"
    assert "workflow_bundle_results" in result["message"].lower()


def test_generate_and_download_skips_meta_key(monkeypatch, tmp_path: Path) -> None:
    """The _meta key in workflow_bundle_results is ignored when writing files."""
    bundle_results = _make_bundle_results([
        {
            "workflow_name": "SimpleWorkflow",
            "files": _minimal_workflow_files("SimpleWorkflow"),
        }
    ])

    context = _Context(
        {
            "chat_id": "chat-4",
            "app_id": "app-4",
            "user_id": "user-4",
            "pack_name": "SimpleWorkflow",
            "workflow_bundle_results": bundle_results,
        }
    )

    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path / "generated"))
    monkeypatch.setattr(generate_and_download_module, "_promote_workflow_to_app_workspace", lambda *a, **kw: None)
    monkeypatch.setattr(generate_and_download_module, "record_workflow_export", AsyncMock())
    monkeypatch.setattr(generate_and_download_module, "record_workflow_artifacts", AsyncMock())
    monkeypatch.setattr(generate_and_download_module, "resolve_agent_api_url", lambda app_id: "https://api.test")
    monkeypatch.setattr(generate_and_download_module, "resolve_agent_websocket_url", lambda app_id: "wss://ws.test")
    monkeypatch.setattr(
        generate_and_download_module,
        "_register_workflow_bundle_artifact_version",
        AsyncMock(return_value=type("AV", (), {"id": "av_4"})()),
    )
    monkeypatch.setattr(
        generate_and_download_module,
        "use_ui_tool",
        AsyncMock(return_value={"status": "completed", "data": {}, "agentContext": {}}),
    )

    result = asyncio.run(
        generate_and_download_module.generate_and_download(
            DownloadRequest={"confirmation_only": False, "storage_backend": "none"},
            agent_message="Done.",
            context_variables=context,
        )
    )

    assert result["status"] == "success"
    with zipfile.ZipFile(Path(result["ui_files"][0]["path"])) as zf:
        # _meta key must not produce a directory in the zip
        assert not any("_meta" in n for n in zf.namelist())


def test_generate_and_download_blocks_missing_required_workflow_file_before_ui(
    monkeypatch,
    tmp_path: Path,
) -> None:
    files = [
        file_entry
        for file_entry in _minimal_workflow_files("BrokenWorkflow")
        if file_entry["filename"] != "ui_config.yaml"
    ]
    bundle_results = _make_bundle_results([
        {
            "workflow_name": "BrokenWorkflow",
            "files": files,
        }
    ])
    context = _Context(
        {
            "chat_id": "chat-blocked-1",
            "app_id": "app-blocked-1",
            "user_id": "user-blocked-1",
            "pack_name": "BrokenWorkflow",
            "workflow_bundle_results": bundle_results,
        }
    )
    ui_mock = AsyncMock(return_value={"status": "completed", "data": {}, "agentContext": {}})

    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path / "generated"))
    monkeypatch.setattr(generate_and_download_module, "use_ui_tool", ui_mock)

    result = asyncio.run(
        generate_and_download_module.generate_and_download(
            DownloadRequest={"confirmation_only": False, "storage_backend": "none"},
            agent_message="Done.",
            context_variables=context,
        )
    )

    assert result["status"] == "blocked"
    assert context.data["workflow_bundle_validation_status"] == "failed"
    assert "ui_config.yaml" in "\n".join(result["validation_errors"])
    ui_mock.assert_not_awaited()


def test_generate_and_download_blocks_event_trigger_semantic_drift_before_ui(
    monkeypatch,
    tmp_path: Path,
) -> None:
    bundle_results = _make_bundle_results([
        {
            "workflow_name": "TicketBatchTriageWorkflow",
            "files": _minimal_workflow_files(
                "TicketBatchTriageWorkflow",
                startup_mode="BackendOnly",
                triggers=[
                    {
                        "type": "event",
                        "event": "domain.support_ticket.batch_requested",
                        "description": "Trigger for batch requested event.",
                    }
                ],
                include_task_batches=True,
            ),
        }
    ])
    context = _Context(
        {
            "chat_id": "chat-blocked-2",
            "app_id": "app-blocked-2",
            "user_id": "user-blocked-2",
            "pack_name": "TicketBatchTriageWorkflow",
            "workflow_bundle_results": bundle_results,
        }
    )
    ui_mock = AsyncMock(return_value={"status": "completed", "data": {}, "agentContext": {}})

    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path / "generated"))
    monkeypatch.setattr(generate_and_download_module, "use_ui_tool", ui_mock)

    result = asyncio.run(
        generate_and_download_module.generate_and_download(
            DownloadRequest={"confirmation_only": False, "storage_backend": "none"},
            agent_message="Done.",
            context_variables=context,
        )
    )

    assert result["status"] == "blocked"
    assert context.data["workflow_bundle_validation_status"] == "failed"
    check_ids = {
        item["check_id"]
        for item in context.data["workflow_bundle_semantic_drift"]["workflows"][0]["semantic_drifts"]
        if item["severity"] == "error"
    }
    assert "event_trigger_capability_id_semantic_drift" in check_ids
    assert "event_trigger_description_semantic_drift" in check_ids
    ui_mock.assert_not_awaited()


def test_generate_and_download_blocks_single_worker_conveyor_before_ui(
    monkeypatch,
    tmp_path: Path,
) -> None:
    files = _minimal_workflow_files(
        "TicketBatchTriageWorkflow",
        startup_mode="BackendOnly",
        triggers=[
            {
                "type": "event",
                "event": "domain.support_ticket.batch_requested",
                "capability_id": "ticket-batch-triage-workflow",
                "description": "Requests parallel support ticket triage for queued tickets.",
            }
        ],
        include_task_batches=True,
    )
    for file_entry in files:
        if file_entry["filename"] == "extended_orchestration/task_batches.yaml":
            file_entry["content"] = """
version: 1
conveyors:
  - id: review_tasks
    decomposition_agent: PlannerAgent
    execution_agents:
      - WorkerAgent
    concurrency: 1
    require_owned_paths: false
"""
    bundle_results = _make_bundle_results([
        {
            "workflow_name": "TicketBatchTriageWorkflow",
            "files": files,
        }
    ])
    context = _Context(
        {
            "chat_id": "chat-blocked-3",
            "app_id": "app-blocked-3",
            "user_id": "user-blocked-3",
            "pack_name": "TicketBatchTriageWorkflow",
            "workflow_bundle_results": bundle_results,
        }
    )
    ui_mock = AsyncMock(return_value={"status": "completed", "data": {}, "agentContext": {}})

    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path / "generated"))
    monkeypatch.setattr(generate_and_download_module, "use_ui_tool", ui_mock)

    result = asyncio.run(
        generate_and_download_module.generate_and_download(
            DownloadRequest={"confirmation_only": False, "storage_backend": "none"},
            agent_message="Done.",
            context_variables=context,
        )
    )

    assert result["status"] == "blocked"
    assert any(
        item["check_id"] == "task_conveyor_parallel_execution_agent_drift"
        for item in context.data["workflow_bundle_semantic_drift"]["workflows"][0]["semantic_drifts"]
    )
    ui_mock.assert_not_awaited()


def test_generate_and_download_schedules_bounded_repair_for_failed_workflow(
    monkeypatch,
    tmp_path: Path,
) -> None:
    good_files = _minimal_workflow_files("GoodWorkflow")
    broken_files = [
        file_entry
        for file_entry in _minimal_workflow_files("BrokenWorkflow")
        if file_entry["filename"] != "ui_config.yaml"
    ]
    bundle_results = _make_bundle_results([
        {"workflow_name": "GoodWorkflow", "files": good_files},
        {"workflow_name": "BrokenWorkflow", "files": broken_files},
    ])
    context = _Context(
        {
            "chat_id": "chat-repair-1",
            "app_id": "app-repair-1",
            "user_id": "user-repair-1",
            "pack_name": "RepairPack",
            "workflow_bundle_results": bundle_results,
            "workflows_spec": [
                {
                    "name": "GoodWorkflow",
                    "task_id": "goodworkflow",
                    "initial_agent": "WorkflowBundleBuilderAgent",
                    "initial_message": "Generate GoodWorkflow.",
                },
                {
                    "name": "BrokenWorkflow",
                    "task_id": "brokenworkflow",
                    "initial_agent": "WorkflowBundleBuilderAgent",
                    "initial_message": "Generate BrokenWorkflow.",
                },
            ],
        }
    )
    ui_mock = AsyncMock(return_value={"status": "completed", "data": {}, "agentContext": {}})

    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path / "generated"))
    monkeypatch.setattr(generate_and_download_module, "use_ui_tool", ui_mock)

    result = asyncio.run(
        generate_and_download_module.generate_and_download(
            DownloadRequest={"confirmation_only": False, "storage_backend": "none"},
            agent_message="Done.",
            context_variables=context,
        )
    )

    assert result["status"] == "blocked"
    assert result["workflow_bundle_repair"]["status"] == "needs_revision"
    assert context.data["workflow_bundle_repair_status"] == "needs_revision"
    assert context.data["workflow_bundle_repair_count"] == 1
    assert context.data["workflow_bundle_repair_failed_workflows"] == ["BrokenWorkflow"]
    assert context.data["workflow_bundle_repair_base_results"] == bundle_results
    assert [item["name"] for item in context.data["workflows_spec"]] == ["BrokenWorkflow"]
    assert "[WORKFLOW BUNDLE REPAIR REQUEST]" in context.data["workflows_spec"][0]["initial_message"]
    ui_mock.assert_not_awaited()


def test_merge_workflow_bundle_repair_results_preserves_successful_outputs() -> None:
    base_results = _make_bundle_results([
        {"workflow_name": "GoodWorkflow", "files": _minimal_workflow_files("GoodWorkflow")},
        {
            "workflow_name": "BrokenWorkflow",
            "files": _minimal_workflow_files("BrokenWorkflow")[:2],
        },
    ])
    repaired_results = _make_bundle_results([
        {
            "workflow_name": "BrokenWorkflow",
            "files": _minimal_workflow_files("BrokenWorkflow"),
        }
    ])
    context = _Context(
        {
            "workflow_bundle_repair_active": True,
            "workflow_bundle_repair_count": 1,
            "workflow_bundle_repair_base_results": base_results,
            "workflow_bundle_results": repaired_results,
            "workflow_bundle_repair_original_workflows_spec": [
                {"name": "GoodWorkflow"},
                {"name": "BrokenWorkflow"},
            ],
            "workflows_spec": [{"name": "BrokenWorkflow"}],
        }
    )

    result = workflow_quality_gate_module.merge_workflow_bundle_repair_results(context)

    assert result["status"] == "merged"
    assert context.data["workflow_bundle_repair_status"] == "merged"
    assert context.data["workflow_bundle_repair_active"] is False
    assert [item["name"] for item in context.data["workflows_spec"]] == [
        "GoodWorkflow",
        "BrokenWorkflow",
    ]
    merged = context.data["workflow_bundle_results"]
    merged_workflow_names = {
        value["workflow_name"]
        for key, value in merged.items()
        if not key.startswith("_")
    }
    assert merged_workflow_names == {"GoodWorkflow", "BrokenWorkflow"}
    repaired_entry = next(
        value
        for key, value in merged.items()
        if not key.startswith("_") and value["workflow_name"] == "BrokenWorkflow"
    )
    assert len(repaired_entry["files"]) == len(_minimal_workflow_files("BrokenWorkflow"))
