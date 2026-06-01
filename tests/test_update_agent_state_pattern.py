from __future__ import annotations

import importlib.util
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
    name = "WorkflowBundleBuilderAgent"

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


def test_pattern_selection_guidance_uses_shared_ag2_patternbook() -> None:
    agent = _Agent()
    agent.name = "PatternAgent"

    update_agent_state_pattern.inject_pattern_selection_guidance(agent, [])

    injected = agent._mozaiks_prompt_sections[0]["content"]

    assert "[AG2 NETWORK PATTERNBOOK]" in injected
    assert "5. Coordinator" in injected
    assert "llm or string_llm" in injected
    assert "Organic" not in injected


def test_pattern_agent_prompt_uses_injected_patternbook_instead_of_static_legend() -> None:
    workspace = Path(__file__).resolve().parents[1]
    agents_yaml = (
        workspace
        / "factory_app"
        / "workflows"
        / "AgentGenerator"
        / "agents.yaml"
    ).read_text(encoding="utf-8")

    pattern_section = agents_yaml.split("- name: PatternAgent", 1)[1].split("- name:", 1)[0]

    assert "{{PATTERN_GUIDANCE_AND_EXAMPLES}}" in pattern_section
    assert "Use the injected AG2 Network patternbook as the canonical source of truth" in pattern_section
    assert "**Pattern Legend:**" not in pattern_section
    assert "Organic" not in pattern_section


def test_hooks_bind_agents_guidance_to_agents_agent() -> None:
    workspace = Path(__file__).resolve().parents[1]
    hooks_yaml = (
        workspace
        / "factory_app"
        / "workflows"
        / "AgentGenerator"
        / "hooks.yaml"
    ).read_text(encoding="utf-8")

    assert "hook_agent: PatternAgent\n  filename: update_agent_state_pattern.py\n  function: inject_pattern_selection_guidance" in hooks_yaml
    assert "hook_agent: WorkflowBundleBuilderAgent\n  filename: update_agent_state_pattern.py\n  function: inject_workflow_bundle_builder_guidance" in hooks_yaml


def test_workflow_bundle_builder_guidance_reads_current_task_pattern() -> None:
    agent = _Agent()
    agent.context_variables["current_task"] = {
        "pattern_id": 3,
        "pattern_name": "Feedback Loop",
        "workflow_name": "ReviewWorkflow",
    }

    update_agent_state_pattern.inject_workflow_bundle_builder_guidance(agent, [])

    injected = agent._mozaiks_prompt_sections[0]["content"]

    assert "[PATTERN GUIDANCE: " in injected
