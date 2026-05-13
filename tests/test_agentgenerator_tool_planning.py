from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_tool_planning_module():
    workspace = Path(__file__).resolve().parents[1]
    file_path = (
        workspace
        / "factory_app"
        / "workflows"
        / "AgentGenerator"
        / "tools"
        / "tool_planning.py"
    )
    module_name = "tests.agentgenerator_tool_planning_direct"
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool_planning_module = _load_tool_planning_module()


class _Context:
    def __init__(self) -> None:
        self.data = {}

    def set(self, key, value) -> None:
        self.data[key] = value


def test_tool_planning_normalizes_and_caches_available_primitives() -> None:
    context = _Context()

    result = tool_planning_module.tool_planning(
        ToolPlanning={
            "agent_tools": [],
            "lifecycle_tools": [],
            "system_hooks": [],
            "ui_requirements": [
                {
                    "workflow_primitive": "diff_review",
                    "component": "ChangeReviewCard",
                    "primitives_hint": ["Panel", "Button", "Panel"],
                }
            ],
        },
        context_variables=context,
    )

    assert "1 UI requirements" in result
    assert context.data["ToolPlanning"]["ui_requirements"][0]["workflow_primitive"] == "diff_review"
    assert context.data["ToolPlanning"]["ui_requirements"][0]["realization"] == "generated_component"
    assert context.data["ToolPlanning"]["ui_requirements"][0]["primitives_hint"] == ["Panel", "Button"]
    assert "Panel" in context.data["available_ui_primitives"]
    assert "DataTable" in context.data["available_page_primitives"]
    assert "approval_card" in context.data["available_workflow_ui_primitives"]
    assert "ApprovalCard" in context.data["available_shipped_workflow_components"]


def test_tool_planning_rejects_unknown_primitives() -> None:
    with pytest.raises(ValueError, match="Unsupported|unsupported"):
        tool_planning_module.tool_planning(
            ToolPlanning={
                "ui_requirements": [
                    {
                        "workflow_primitive": "approval_card",
                        "component": "ApprovalCard",
                        "primitives_hint": ["Panel", "Wizard"],
                    }
                ]
            },
            context_variables=_Context(),
        )


def test_tool_planning_normalizes_composer_reply_requirements() -> None:
    context = _Context()

    tool_planning_module.tool_planning(
        ToolPlanning={
            "ui_requirements": [
                {
                    "workflow_primitive": "composer_reply",
                    "component": "ShouldBeCleared",
                    "display": "artifact",
                    "primitives_hint": ["Panel"],
                }
            ]
        },
        context_variables=context,
    )

    requirement = context.data["ToolPlanning"]["ui_requirements"][0]
    assert requirement["workflow_primitive"] == "composer_reply"
    assert requirement["realization"] == "shell_builtin"
    assert requirement["component"] is None
    assert requirement["display"] == "composer"
    assert requirement["primitives_hint"] == []


def test_tool_planning_defaults_to_shipped_component_for_shared_workflow_primitive() -> None:
    context = _Context()

    tool_planning_module.tool_planning(
        ToolPlanning={
            "ui_requirements": [
                {
                    "workflow_primitive": "approval_card",
                    "component": "",
                    "display": "inline",
                    "primitives_hint": ["Panel", "Button"],
                }
            ]
        },
        context_variables=context,
    )

    requirement = context.data["ToolPlanning"]["ui_requirements"][0]
    assert requirement["component"] == "ApprovalCard"
    assert requirement["realization"] == "shipped_component"
    assert requirement["primitives_hint"] == []


def test_tool_planning_marks_noncanonical_component_as_workflow_wrapper() -> None:
    context = _Context()

    tool_planning_module.tool_planning(
        ToolPlanning={
            "ui_requirements": [
                {
                    "workflow_primitive": "approval_card",
                    "component": "BrandedApprovalCard",
                    "display": "inline",
                    "primitives_hint": ["Panel", "Button"],
                }
            ]
        },
        context_variables=context,
    )

    requirement = context.data["ToolPlanning"]["ui_requirements"][0]
    assert requirement["realization"] == "workflow_wrapper"
