from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path


def _stub_agent_factory() -> None:
    app_pkg = sys.modules.get("app")
    if app_pkg is None:
        app_pkg = types.ModuleType("app")
        app_pkg.__path__ = []
        sys.modules["app"] = app_pkg

    modules_pkg = sys.modules.get("app.modules")
    if modules_pkg is None:
        modules_pkg = types.ModuleType("app.modules")
        modules_pkg.__path__ = []
        sys.modules["app.modules"] = modules_pkg

    agent_factory_mod = types.ModuleType("app.modules.agent_factory")

    def _compose_prompt_sections(sections):
        rendered = []
        for section in sections or []:
            if not isinstance(section, dict):
                continue
            heading = section.get("heading") or ""
            content = section.get("content") or ""
            rendered.append("\n".join(part for part in (heading, content) if part))
        return "\n\n".join(part for part in rendered if part)

    agent_factory_mod._compose_prompt_sections = _compose_prompt_sections
    sys.modules["app.modules.agent_factory"] = agent_factory_mod
    app_pkg.plugins = modules_pkg
    modules_pkg.agent_factory = agent_factory_mod


def _load_update_agent_state_pattern_module():
    _stub_agent_factory()
    workspace = Path(__file__).resolve().parents[1]
    file_path = (
        workspace
        / "factory_app"
        / "workflows"
        / "AgentGenerator"
        / "tools"
        / "update_agent_state_pattern.py"
    )
    module_name = "tests.update_agent_state_pattern_direct"
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


update_agent_state_pattern = _load_update_agent_state_pattern_module()


class _Agent:
    name = "UIFileGenerator"

    def __init__(self) -> None:
        self.context_variables = {
            "PatternSelection": {
                "workflows": [
                    {
                        "pattern_id": 1,
                        "pattern_name": "context_aware_routing",
                    }
                ]
            },
            "TechnicalBlueprint": {
                "ui_components": [
                    {
                        "component": "ContextAwareRoutingReviewPanel",
                        "tool": "review_context_aware_routing_panel",
                        "display": "artifact",
                        "ui_pattern": "approval",
                        "summary": "Review the generated routing proposal.",
                    }
                ]
            },
        }
        self._mozaiks_prompt_sections = [
            {
                "id": "pattern_guidance_and_examples",
                "heading": "[PATTERN GUIDANCE AND EXAMPLES]",
                "content": "{{PATTERN_GUIDANCE_AND_EXAMPLES}}",
            }
        ]
        self._system_message = ""


def test_build_ui_file_generator_example_uses_canonical_runtime_contract() -> None:
    example = json.loads(
        update_agent_state_pattern._build_ui_file_generator_example("Context-Aware Routing")
    )

    assert len(example["tools"]) == 2
    python_file = example["tools"][0]
    react_file = example["tools"][1]

    assert python_file["filename"] == "tools/review_context_aware_routing_panel.py"
    assert "from mozaiksai.core.workflow.ui_tools import UIToolError, use_ui_tool" in python_file["content"]
    assert "app.modules.ui_tools" not in python_file["content"]

    assert react_file["filename"] == "ui/ContextAwareRoutingWorkflow/ContextAwareRoutingReviewPanel.jsx"
    assert "WorkflowUIRouter" not in react_file["content"]
    assert "payload = {}" in react_file["content"]
    assert "useAppEventBus" not in react_file["content"]
    assert "artifactDesignSystem" not in react_file["content"]


def test_inject_ui_file_generator_guidance_replaces_legacy_examples() -> None:
    agent = _Agent()

    update_agent_state_pattern.inject_ui_file_generator_guidance(agent, [])

    injected = agent._mozaiks_prompt_sections[0]["content"]

    assert "[UI FILE GENERATOR CONTRACT]" in injected
    assert "WorkflowUIRouter" in injected
    assert "from mozaiksai.core.workflow.ui_tools import UIToolError, use_ui_tool" in injected
    assert "shipped shared components" in injected
    assert "app.modules.ui_tools" in injected  # negative rule only
    assert "artifactDesignSystem" not in injected
    assert "subscribes via `useAppEventBus`" not in injected


def test_ui_file_generator_prompt_uses_runtime_helper_contract() -> None:
    workspace = Path(__file__).resolve().parents[1]
    agents_yaml = (
        workspace
        / "factory_app"
        / "workflows"
        / "AgentGenerator"
        / "agents.yaml"
    ).read_text(encoding="utf-8")

    ui_section = agents_yaml.split("- name: UIFileGenerator", 1)[1].split("- name:", 1)[0]

    assert "use_ui_tool(...)" in ui_section
    assert "WorkflowUIRouter" in ui_section
    assert "workflow_primitive" in ui_section
    assert "shipped shared component" in ui_section
    assert "subscribes via `useAppEventBus`" not in ui_section
    assert "calls `send_ui_tool_event(component_name, display_type, payload)`" not in ui_section


def test_agents_agent_guidance_omits_agent_auto_tool_field_from_examples() -> None:
    agent = _Agent()
    agent.name = "AgentsAgent"

    update_agent_state_pattern.inject_agents_agent_guidance(agent, [])

    injected = agent._mozaiks_prompt_sections[0]["content"]

    assert "[RUNTIME AGENT CONTRACT]" in injected
    assert "Do NOT emit any agent-level auto-tool field" in injected
    assert "auto_tool_mode" not in injected


def test_agent_tools_guidance_marks_integration_as_planning_only() -> None:
    agent = _Agent()
    agent.name = "AgentToolsFileGenerator"

    update_agent_state_pattern.inject_agent_tools_file_generator_guidance(agent, [])

    injected = agent._mozaiks_prompt_sections[0]["content"]

    assert "[PLANNING CONTRACT]" in injected
    assert "Do NOT invent runtime manifest fields from them." in injected
