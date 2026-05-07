from __future__ import annotations

import asyncio
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


class _FakePersistenceManager:
    def __init__(self, collected):
        self._collected = collected

    async def gather_latest_agent_jsons(self, *, chat_id: str, app_id: str):
        return self._collected


def test_agentgenerator_generate_and_download_only_forwards_valid_dict_outputs(monkeypatch, tmp_path: Path) -> None:
    workflow_dir = tmp_path / "generated" / "ReviewWorkflow"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "orchestrator.yaml").write_text("workflow_name: ReviewWorkflow\n", encoding="utf-8")
    (workflow_dir / "agents.yaml").write_text("agents: {}\n", encoding="utf-8")

    collected = {
        "OrchestratorAgent": {
            "workflow_name": "ReviewWorkflow",
            "startup_mode": "BackendOnly",
        },
        "AgentsAgent": {
            "agents": [
                {
                    "name": "ReviewAgent",
                    "prompt_sections": [],
                }
            ]
        },
        "HandoffsAgent": "{not valid json}",
        "ContextVariablesAgent": {
            "definitions": [],
            "agents": [],
        },
        "ToolsManagerAgent": {
            "tools": [],
            "lifecycle_tools": [],
        },
        "WorkflowStrategyAgent": {
            "WorkflowStrategy": {
                "workflow_name": "Review Workflow",
                "decomposition": {"required": False},
            }
        },
    }

    captured_payload = {}

    async def _fake_create_workflow_files(payload, context_variables):
        captured_payload.clear()
        captured_payload.update(payload)
        return {
            "status": "success",
            "files": ["orchestrator.yaml", "agents.yaml"],
            "workflow_dir": str(workflow_dir),
            "workflow_config": payload,
        }

    monkeypatch.setattr(
        generate_and_download_module,
        "AG2PersistenceManager",
        lambda: _FakePersistenceManager(collected),
    )
    monkeypatch.setattr(generate_and_download_module, "create_workflow_files", _fake_create_workflow_files)
    monkeypatch.setattr(generate_and_download_module, "_promote_workflow_to_app_workspace", lambda *args, **kwargs: None)
    monkeypatch.setattr(generate_and_download_module, "record_workflow_export", AsyncMock())
    monkeypatch.setattr(generate_and_download_module, "record_workflow_artifacts", AsyncMock())
    monkeypatch.setattr(generate_and_download_module, "resolve_agent_api_url", lambda app_id: f"https://api.test/{app_id}")
    monkeypatch.setattr(
        generate_and_download_module,
        "resolve_agent_websocket_url",
        lambda app_id: f"wss://ws.test/{app_id}",
    )
    monkeypatch.setattr(
        generate_and_download_module,
        "use_ui_tool",
        AsyncMock(return_value={"status": "completed", "data": {}, "agentContext": {}}),
    )

    context = _Context(
        {
            "chat_id": "chat-1",
            "app_id": "app-1",
            "workflow_name": "ReviewWorkflow",
            "user_id": "user-1",
        }
    )

    result = asyncio.run(
        generate_and_download_module.generate_and_download(
            DownloadRequest={"confirmation_only": False, "storage_backend": "none", "description": None},
            agent_message="Preparing workflow bundle.",
            context_variables=context,
        )
    )

    assert result["status"] == "success"
    assert captured_payload["workflow_name"] == "ReviewWorkflow"
    assert captured_payload["agents_output"] == collected["AgentsAgent"]
    assert captured_payload["context_variables_output"] == collected["ContextVariablesAgent"]
    assert captured_payload["tools_manager_output"] == collected["ToolsManagerAgent"]
    assert captured_payload["workflow_strategy_output"] == collected["WorkflowStrategyAgent"]
    assert "handoffs_output" not in captured_payload
