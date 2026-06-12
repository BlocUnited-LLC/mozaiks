from __future__ import annotations

import importlib
from pathlib import Path

import yaml

build_context_projection = importlib.import_module("mozaiksai.core.workflow.context.projection")


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

    build_context_projection.inject_build_context_projections(agent, [])

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


def test_middleware_binds_pattern_guidance_to_agents() -> None:
    workspace = Path(__file__).resolve().parents[1]
    middleware_yaml = (
        workspace
        / "factory_app"
        / "workflows"
        / "AgentGenerator"
        / "middleware.yaml"
    ).read_text(encoding="utf-8")

    projector = "function: mozaiksai.core.workflow.context.projection.inject_build_context_projections"
    assert "agent: PatternAgent\n  " + projector in middleware_yaml
    assert "agent: WorkflowBundleBuilderAgent\n  " + projector in middleware_yaml
    assert "prompt_middleware_pattern.py" not in middleware_yaml


def test_agentgenerator_manifest_declares_patternbook_projections() -> None:
    workspace = Path(__file__).resolve().parents[1]
    manifest = yaml.safe_load((
        workspace
        / "factory_app"
        / "build_context"
        / "AgentGenerator"
        / "context.yaml"
    ).read_text(encoding="utf-8"))

    asset = next(item for item in manifest["assets"] if item["path"] == "ag2_network_patterns.yaml")
    projections = {item["id"]: item for item in asset["projections"]}
    assert projections["ag2_patternbook_summary_for_decomposition"]["recipients"] == ["PatternAgent"]
    selected = projections["ag2_selected_pattern_for_bundle_builder"]
    assert selected["render"] == "selected_record"
    assert selected["selected_by"] == "current_task.pattern_id"
    assert selected["record_id_field"] == "id"


def test_generic_build_context_projection_injects_patternbook_summary() -> None:
    agent = _Agent()
    agent.name = "PatternAgent"

    build_context_projection.inject_build_context_projections(agent, [])

    injected = agent._mozaiks_prompt_sections[0]["content"]
    assert "[AG2 NETWORK PATTERNBOOK]" in injected
    assert "5. Coordinator" in injected
    assert "llm or string_llm" in injected
    assert "Organic" not in injected


def test_generic_build_context_projection_injects_selected_pattern_detail() -> None:
    agent = _Agent()
    agent.context_variables["current_task"] = {
        "pattern_id": 3,
        "pattern_name": "Feedback Loop",
        "workflow_name": "ReviewWorkflow",
    }

    build_context_projection.inject_build_context_projections(agent, [])

    injected = agent._mozaiks_prompt_sections[0]["content"]
    assert "[AG2 NETWORK PATTERN]" in injected
    assert "3. Feedback Loop" in injected
    assert "transition_generation" in injected
    assert "handoff_generation" not in injected


def test_workflow_bundle_builder_guidance_reads_current_task_pattern() -> None:
    agent = _Agent()
    agent.context_variables["current_task"] = {
        "pattern_id": 3,
        "pattern_name": "Feedback Loop",
        "workflow_name": "ReviewWorkflow",
    }

    build_context_projection.inject_build_context_projections(agent, [])

    injected = agent._mozaiks_prompt_sections[0]["content"]

    assert "[AG2 NETWORK PATTERN]" in injected
    assert "3. Feedback Loop" in injected


