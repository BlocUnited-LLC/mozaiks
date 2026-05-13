from __future__ import annotations

import importlib.util
from importlib import import_module
from pathlib import Path

import pytest
import yaml


def _load_workflow_ui_quality_module():
    workspace = Path(__file__).resolve().parents[1]
    file_path = (
        workspace
        / "factory_app"
        / "workflows"
        / "AgentGenerator"
        / "tools"
        / "workflow_ui_quality.py"
    )
    spec = importlib.util.spec_from_file_location(
        "tests.agentgenerator_workflow_ui_quality_direct", file_path
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


workflow_ui_quality_module = _load_workflow_ui_quality_module()
generate_and_download_module = import_module(
    "factory_app.workflows.AgentGenerator.tools.generate_and_download"
)


class _Context:
    def __init__(self, initial=None) -> None:
        self.data = dict(initial or {})

    def set(self, key, value) -> None:
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)


def _read_yaml(relative_path: str):
    workspace = Path(__file__).resolve().parents[1]
    return yaml.safe_load((workspace / relative_path).read_text(encoding="utf-8"))


def test_save_workflow_ui_files_output_persists_output_and_flags_noisy_ui() -> None:
    context = _Context(
        {
            "ToolPlanning": {
                "ui_requirements": [
                    {
                        "workflow_primitive": "diff_review",
                        "realization": "generated_component",
                        "component": "ChangeReviewPanel",
                    },
                    {
                        "workflow_primitive": "approval_card",
                        "realization": "shipped_component",
                        "component": "ApprovalCard",
                    },
                ]
            }
        }
    )

    result = workflow_ui_quality_module.save_workflow_ui_files_output(
        tools=[
            {
                "filename": "ui/review/ChangeReviewPanel.jsx",
                "content": (
                    "import { Card, Button } from '@mozaiks/chat-ui/ui';\n"
                    "export default function ChangeReviewPanel(){ return <Card><Button label=\"Approve\" /></Card>; }\n"
                ),
                "installRequirements": [],
            },
            {
                "filename": "ui/review/ApprovalCard.jsx",
                "content": "export default function ApprovalCard(){ return null; }\n",
                "installRequirements": [],
            },
        ],
        context_variables=context,
    )

    assert result["saved"] is True
    assert context.data["workflow_ui_files_output"]["tools"][0]["filename"] == "ui/review/ChangeReviewPanel.jsx"
    assert context.data["workflow_ui_quality_status"] == "pending"
    warnings = context.data["workflow_ui_quality_warnings"]
    assert any("non-canonical component primitives: Card" in warning for warning in warnings)
    assert any("renders non-canonical component primitive <Card>" in warning for warning in warnings)
    assert any("shipped shared component ApprovalCard" in warning for warning in warnings)


def test_save_workflow_ui_files_output_accepts_clean_generated_component_subset() -> None:
    context = _Context(
        {
            "ToolPlanning": {
                "ui_requirements": [
                    {
                        "workflow_primitive": "diff_review",
                        "realization": "generated_component",
                        "component": "ChangeReviewPanel",
                    }
                ]
            }
        }
    )

    result = workflow_ui_quality_module.save_workflow_ui_files_output(
        tools=[
            {
                "filename": "ui/review/ChangeReviewPanel.jsx",
                "content": (
                    "import { Alert, Button, InlineEmptyState, Panel, StatusPill } from '@mozaiks/chat-ui/ui';\n"
                    "export default function ChangeReviewPanel({ payload = {}, onResponse }) {\n"
                    "  return <Panel title={payload.title} action={<StatusPill label=\"Ready\" tone=\"primary\" />}><InlineEmptyState title=\"Nothing to review yet\" /></Panel>;\n"
                    "}\n"
                ),
                "installRequirements": [],
            }
        ],
        context_variables=context,
    )

    assert result["warning_count"] == 0
    assert context.data["workflow_ui_quality_warnings"] == []


def test_review_workflow_ui_quality_passes_without_warnings() -> None:
    context = _Context({"workflow_ui_quality_warnings": []})

    result = workflow_ui_quality_module.review_workflow_ui_quality(
        context_variables=context
    )

    assert result["status"] == "passed"
    assert context.data["workflow_ui_quality_status"] == "passed"
    assert context.data["workflow_ui_quality_revision_request"] is None


def test_review_workflow_ui_quality_routes_warnings_back_to_uifilegenerator() -> None:
    context = _Context(
        {
            "workflow_ui_quality_warnings": [
                "ui/review/ChangeReviewPanel.jsx imports non-canonical component primitives: Card."
            ],
            "workflow_ui_quality_revision_count": 0,
        }
    )

    result = workflow_ui_quality_module.review_workflow_ui_quality(
        context_variables=context
    )

    assert result["status"] == "needs_revision"
    assert result["revision_count"] == 1
    assert context.data["workflow_ui_quality_status"] == "needs_revision"
    assert "ChangeReviewPanel.jsx imports non-canonical component primitives" in context.data[
        "workflow_ui_quality_revision_request"
    ]


def test_review_workflow_ui_quality_blocks_after_revision_budget() -> None:
    context = _Context(
        {
            "workflow_ui_quality_warnings": [
                "ui/review/ChangeReviewPanel.jsx hardcodes color values; use semantic theme tokens instead."
            ],
            "workflow_ui_quality_revision_count": 2,
        }
    )

    result = workflow_ui_quality_module.review_workflow_ui_quality(
        max_revision_attempts=2,
        context_variables=context,
    )

    assert result["status"] == "blocked"
    assert context.data["workflow_ui_quality_status"] == "blocked"
    assert "operator review" in context.data["workflow_ui_quality_revision_request"]


@pytest.mark.asyncio
async def test_generate_and_download_requires_passed_workflow_ui_quality_gate() -> None:
    context = _Context(
        {
            "chat_id": "chat-1",
            "app_id": "app-1",
            "workflow_name": "ReviewWorkflow",
            "user_id": "user-1",
            "workflow_ui_quality_status": "needs_revision",
            "workflow_ui_quality_warnings": [
                "ui/review/ChangeReviewPanel.jsx imports non-canonical component primitives: Card."
            ],
        }
    )

    with pytest.raises(ValueError, match="workflow_ui_quality_status must be 'passed'"):
        await generate_and_download_module.generate_and_download(
            DownloadRequest={
                "confirmation_only": False,
                "storage_backend": "none",
                "description": None,
            },
            agent_message="Preparing workflow bundle.",
            context_variables=context,
        )


def test_agentgenerator_workflow_ui_quality_handoffs_and_tools_are_canonical() -> None:
    handoffs = _read_yaml("factory_app/workflows/AgentGenerator/handoffs.yaml")
    tools = _read_yaml("factory_app/workflows/AgentGenerator/tools.yaml")
    agents = (
        Path(__file__).resolve().parents[1]
        / "factory_app"
        / "workflows"
        / "AgentGenerator"
        / "agents.yaml"
    ).read_text(encoding="utf-8")
    doc = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "architecture"
        / "frontend"
        / "generated-frontend-surface-contract.md"
    ).read_text(encoding="utf-8")

    handoff_pairs = {
        (rule["source_agent"], rule["target_agent"]): rule
        for rule in handoffs["handoff_rules"]
    }
    tool_entries = {
        (entry["agent"], entry["function"]): entry
        for entry in tools["tools"]
    }

    assert ("UIFileGenerator", "WorkflowUIQualityAgent") in handoff_pairs
    assert handoff_pairs[("WorkflowUIQualityAgent", "UIFileGenerator")]["condition"] == (
        '${workflow_ui_quality_status} == "needs_revision"'
    )
    assert handoff_pairs[("WorkflowUIQualityAgent", "AgentToolsFileGenerator")]["condition"] == (
        '${workflow_ui_quality_status} == "passed"'
    )
    assert handoff_pairs[("WorkflowUIQualityAgent", "user")]["condition"] == (
        '${workflow_ui_quality_status} == "blocked"'
    )

    assert ("UIFileGenerator", "save_workflow_ui_files_output") in tool_entries
    assert tool_entries[("UIFileGenerator", "save_workflow_ui_files_output")]["auto_tool_call"] is True
    assert ("WorkflowUIQualityAgent", "review_workflow_ui_quality") in tool_entries

    assert "- name: WorkflowUIQualityAgent" in agents
    assert "workflow_ui_quality_revision_request" in agents
    assert "workflow_ui_quality_status == \"passed\"" in agents
    assert "Workflow-local React now has a deterministic gate" in doc
