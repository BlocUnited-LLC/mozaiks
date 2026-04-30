from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_tool_planning_module():
    workspace = Path(__file__).resolve().parents[1]
    file_path = (
        workspace
        / "factory_app"
        / "app"
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
                    "component": "ApprovalCard",
                    "primitives_hint": ["Card", "Button", "Card"],
                }
            ],
        },
        context_variables=context,
    )

    assert "1 UI requirements" in result
    assert context.data["ToolPlanning"]["ui_requirements"][0]["primitives_hint"] == ["Card", "Button"]
    assert "Card" in context.data["available_ui_primitives"]
    assert "DataTable" in context.data["available_page_primitives"]


def test_tool_planning_rejects_unknown_primitives() -> None:
    with pytest.raises(ValueError, match="Unsupported|unsupported"):
        tool_planning_module.tool_planning(
            ToolPlanning={
                "ui_requirements": [
                    {
                        "component": "ApprovalCard",
                        "primitives_hint": ["Card", "Wizard"],
                    }
                ]
            },
            context_variables=_Context(),
        )
