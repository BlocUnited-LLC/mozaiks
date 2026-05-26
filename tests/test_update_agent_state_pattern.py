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


def test_inject_ui_file_generator_guidance_replaces_removed_examples() -> None:
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


def test_agents_agent_prompt_declares_turn_limit_derivation_rules() -> None:
    workspace = Path(__file__).resolve().parents[1]
    agents_yaml = (
        workspace
        / "factory_app"
        / "workflows"
        / "AgentGenerator"
        / "agents.yaml"
    ).read_text(encoding="utf-8")

    agents_section = agents_yaml.split("- name: AgentsAgent", 1)[1].split("- name:", 1)[0]
    roster_section = agents_yaml.split("- name: AgentRosterAgent", 1)[1].split("- name:", 1)[0]

    assert "### Step 1B: Derive `max_consecutive_auto_reply`" in agents_section
    assert "Single-turn workers" in agents_section
    assert "Open-ended coordinators or creative hubs" in agents_section
    assert "MFJ child worker agents should usually be 1-2; MFJ resume/synthesis agents are usually 5." in agents_section
    assert "Do NOT emit or reason about `max_consecutive_auto_reply` here." in roster_section
    assert "AgentsAgent owns runtime turn limits later" in roster_section


def test_agents_agent_prompt_uses_compact_prompt_engineering_guidance() -> None:
    workspace = Path(__file__).resolve().parents[1]
    agents_yaml = (
        workspace
        / "factory_app"
        / "workflows"
        / "AgentGenerator"
        / "agents.yaml"
    ).read_text(encoding="utf-8")

    agents_section = agents_yaml.split("- name: AgentsAgent", 1)[1].split("- name:", 1)[0]

    assert "Generate the final `RuntimeAgentsOutput` JSON directly." in agents_section
    assert "Do not include private reasoning, chain-of-thought, or meta commentary" in agents_section
    assert "Keep examples minimal. Prefer a compact shape skeleton over long worked examples." in agents_section
    assert "Use compact, contract-driven prompt section content." in agents_section
    assert "Example Agent (Generic Pipeline Stage)" not in agents_section
    assert "DERIVATION PROCESS (Chain-of-Thought)" not in agents_section


def test_context_variables_and_handoffs_prompts_omit_chain_of_thought_wording() -> None:
    workspace = Path(__file__).resolve().parents[1]
    agents_yaml = (
        workspace
        / "factory_app"
        / "workflows"
        / "AgentGenerator"
        / "agents.yaml"
    ).read_text(encoding="utf-8")

    context_section = agents_yaml.split("- name: ContextVariablesAgent", 1)[1].split("- name:", 1)[0]
    handoffs_section = agents_yaml.split("- name: HandoffsAgent", 1)[1].split("- name:", 1)[0]

    assert "Generate the final `ContextVariablesPlanOutput` JSON directly." in context_section
    assert "Output MUST be a valid JSON object matching `ContextVariablesPlanOutput` and NO additional text:" in context_section
    assert "DERIVATION PROCESS (Chain-of-Thought)" not in context_section

    assert "Generate the final `HandoffRulesOutput` JSON directly." in handoffs_section
    assert "Output MUST be a valid JSON object matching `HandoffRulesOutput` and NO additional text:" in handoffs_section
    assert "DERIVATION PROCESS (Chain-of-Thought)" not in handoffs_section


def test_hooks_bind_agents_guidance_to_agents_agent() -> None:
    workspace = Path(__file__).resolve().parents[1]
    hooks_yaml = (
        workspace
        / "factory_app"
        / "workflows"
        / "AgentGenerator"
        / "hooks.yaml"
    ).read_text(encoding="utf-8")

    assert "hook_agent: AgentsAgent\n  filename: update_agent_state_pattern.py\n  function: inject_agents_agent_guidance" in hooks_yaml
    assert "hook_agent: AgentRosterAgent\n  filename: update_agent_state_pattern.py\n  function: inject_agents_agent_guidance" not in hooks_yaml


def test_agent_tools_guidance_marks_integration_as_planning_only() -> None:
    agent = _Agent()
    agent.name = "AgentToolsFileGenerator"

    update_agent_state_pattern.inject_agent_tools_file_generator_guidance(agent, [])

    injected = agent._mozaiks_prompt_sections[0]["content"]

    assert "[PLANNING CONTRACT]" in injected
    assert "Do NOT invent runtime manifest fields from them." in injected
