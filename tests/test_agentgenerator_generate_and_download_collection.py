"""Tests for generate_and_download reading from workflow_bundle_results (task batch output)."""

from __future__ import annotations

import asyncio
import zipfile
from importlib import import_module
from pathlib import Path
from unittest.mock import AsyncMock


def _load_generate_and_download_module():
    return import_module("factory_app.workflows.AgentGenerator.tools.generate_and_download")


generate_and_download_module = _load_generate_and_download_module()


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
    for i, wf in enumerate(workflows):
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


def test_generate_and_download_writes_bundle_files_and_creates_zip(
    monkeypatch, tmp_path: Path
) -> None:
    """generate_and_download writes WorkflowBundleBuilderOutput files to disk and zips them."""
    bundle_results = _make_bundle_results([
        {
            "workflow_name": "ReviewWorkflow",
            "files": [
                {"filename": "orchestrator.yaml", "content": "workflow_name: ReviewWorkflow\n"},
                {"filename": "agents.yaml", "content": "agents: {}\n"},
            ],
        }
    ])

    context = _Context(
        {
            "chat_id": "chat-1",
            "app_id": "app-1",
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
    assert len(result["ui_files"]) == 1
    zip_entry = result["ui_files"][0]
    assert zip_entry["type"] == "zip"
    assert "ReviewWorkflow" in zip_entry["name"]

    zip_path = Path(zip_entry["path"])
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert any("orchestrator.yaml" in n for n in names)
    assert any("agents.yaml" in n for n in names)


def test_generate_and_download_multi_workflow_pack_zips_all_bundles(
    monkeypatch, tmp_path: Path
) -> None:
    """Multi-workflow packs write all bundles into a single zip."""
    bundle_results = _make_bundle_results([
        {
            "workflow_name": "OnboardingWorkflow",
            "files": [
                {"filename": "orchestrator.yaml", "content": "workflow_name: OnboardingWorkflow\n"},
            ],
        },
        {
            "workflow_name": "NotificationWorkflow",
            "files": [
                {"filename": "orchestrator.yaml", "content": "workflow_name: NotificationWorkflow\n"},
            ],
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
            "files": [{"filename": "orchestrator.yaml", "content": "workflow_name: SimpleWorkflow\n"}],
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
