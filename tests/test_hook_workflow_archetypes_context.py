"""
Tests for hook_workflow_archetypes_context.py and the hooks.yaml registrations
that close Gap #7 (archetype library gap for WorkflowBundleBuilderAgent).

Verifies:
- hooks.yaml registers hook_workflow_archetypes_context.py for WorkflowBundleBuilderAgent
- hooks.yaml registers hook_ai_pack_archetype_context.py for WorkflowBundleBuilderAgent
- inject_workflow_archetypes_context fires for AI-native capability_ids
- Injected section header contains archetype name
- Canonical agent sequence and hard_constraints appear in injected content
- No-op for non-AI-native workflows
- No-op for non-WorkflowBundleBuilderAgent agents
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

_AGENTGEN_DIR = (
    Path(__file__).parent.parent
    / "factory_app"
    / "workflows"
    / "AgentGenerator"
)
_HOOKS_YAML = _AGENTGEN_DIR / "hooks.yaml"
_APPGEN_TOOLS_DIR = (
    Path(__file__).parent.parent
    / "factory_app"
    / "workflows"
    / "AppGenerator"
    / "tools"
)
_WORKFLOW_ARCHETYPES_YAML = _APPGEN_TOOLS_DIR / "workflow_archetypes.yaml"


class _FakeAgent:
    def __init__(self, name: str, context_variables: Dict[str, Any] | None = None):
        self.name = name
        self.system_message = "Base prompt."
        self.context_variables = context_variables or {}

    def update_system_message(self, msg: str) -> None:
        self.system_message = msg


def _run_hook(agent: _FakeAgent) -> None:
    from factory_app.workflows.AgentGenerator.tools.hook_workflow_archetypes_context import (
        inject_workflow_archetypes_context,
    )
    inject_workflow_archetypes_context(agent, [])


# ---------------------------------------------------------------------------
# hooks.yaml registration checks
# ---------------------------------------------------------------------------

class TestHooksYamlRegistration:
    def _hooks(self):
        with _HOOKS_YAML.open(encoding="utf-8") as f:
            return yaml.safe_load(f).get("hooks", [])

    def test_hook_workflow_archetypes_registered_for_workflowbundlebuilderagent(self):
        hooks = self._hooks()
        match = [
            h for h in hooks
            if h.get("hook_agent") == "WorkflowBundleBuilderAgent"
            and "hook_workflow_archetypes_context" in str(h.get("filename", ""))
        ]
        assert match, (
            "hooks.yaml must register hook_workflow_archetypes_context.py "
            "for WorkflowBundleBuilderAgent"
        )

    def test_hook_ai_pack_archetype_registered_for_workflowbundlebuilderagent(self):
        hooks = self._hooks()
        match = [
            h for h in hooks
            if h.get("hook_agent") == "WorkflowBundleBuilderAgent"
            and "hook_ai_pack_archetype_context" in str(h.get("filename", ""))
        ]
        assert match, (
            "hooks.yaml must register hook_ai_pack_archetype_context.py "
            "for WorkflowBundleBuilderAgent to fire the [AI PACK CALLBACK CONTRACT] injection"
        )


# ---------------------------------------------------------------------------
# Functional injection tests
# ---------------------------------------------------------------------------

class TestInjectWorkflowArchetypesContext:
    def test_injects_for_review_workflow(self):
        agent = _FakeAgent(
            "WorkflowBundleBuilderAgent",
            {"current_task": {"capability_id": "proposals-review-workflow"}},
        )
        _run_hook(agent)
        assert "[WORKFLOW ARCHETYPE]" in agent.system_message
        assert "ai_review" in agent.system_message

    def test_injects_for_analysis_workflow(self):
        agent = _FakeAgent(
            "WorkflowBundleBuilderAgent",
            {"current_task": {"capability_id": "documents-analysis-workflow"}},
        )
        _run_hook(agent)
        assert "[WORKFLOW ARCHETYPE]" in agent.system_message
        assert "ai_analysis" in agent.system_message

    def test_injects_for_extraction_workflow(self):
        agent = _FakeAgent(
            "WorkflowBundleBuilderAgent",
            {"current_task": {"capability_id": "invoices-extraction-workflow"}},
        )
        _run_hook(agent)
        assert "[WORKFLOW ARCHETYPE]" in agent.system_message
        assert "ai_extraction" in agent.system_message

    def test_injected_content_includes_canonical_agent_sequence(self):
        agent = _FakeAgent(
            "WorkflowBundleBuilderAgent",
            {"current_task": {"capability_id": "proposals-review-workflow"}},
        )
        _run_hook(agent)
        msg = agent.system_message
        # ai_review canonical sequence: IntakeAgent, ReviewerAgent, ResultAgent
        assert "IntakeAgent" in msg
        assert "ReviewerAgent" in msg
        assert "ResultAgent" in msg

    def test_injected_content_includes_hard_constraints(self):
        agent = _FakeAgent(
            "WorkflowBundleBuilderAgent",
            {"current_task": {"capability_id": "proposals-review-workflow"}},
        )
        _run_hook(agent)
        assert "HARD CONSTRAINTS" in agent.system_message
        assert "BackendOnly" in agent.system_message

    def test_noop_for_non_ai_native_workflow(self):
        agent = _FakeAgent(
            "WorkflowBundleBuilderAgent",
            {"current_task": {"capability_id": "RecommendationEngine"}},
        )
        _run_hook(agent)
        assert "[WORKFLOW ARCHETYPE]" not in agent.system_message

    def test_noop_for_other_agent(self):
        agent = _FakeAgent(
            "PatternAgent",
            {"current_task": {"capability_id": "proposals-review-workflow"}},
        )
        _run_hook(agent)
        assert "[WORKFLOW ARCHETYPE]" not in agent.system_message

    def test_noop_when_no_current_task(self):
        agent = _FakeAgent("WorkflowBundleBuilderAgent", {})
        _run_hook(agent)
        assert "[WORKFLOW ARCHETYPE]" not in agent.system_message

    def test_preserves_existing_system_message(self):
        agent = _FakeAgent(
            "WorkflowBundleBuilderAgent",
            {"current_task": {"capability_id": "docs-analysis-workflow"}},
        )
        _run_hook(agent)
        assert "Base prompt." in agent.system_message
        assert "[WORKFLOW ARCHETYPE]" in agent.system_message


# ---------------------------------------------------------------------------
# workflow_archetypes.yaml content checks (ai_review / ai_analysis / ai_extraction)
# ---------------------------------------------------------------------------

class TestWorkflowArchetypesYamlAiNativePacks:
    def _archetypes(self) -> dict:
        with _WORKFLOW_ARCHETYPES_YAML.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("archetypes", {})

    def test_ai_review_archetype_declared(self):
        assert "ai_review" in self._archetypes()

    def test_ai_analysis_archetype_declared(self):
        assert "ai_analysis" in self._archetypes()

    def test_ai_extraction_archetype_declared(self):
        assert "ai_extraction" in self._archetypes()

    def test_ai_review_has_canonical_agent_sequence(self):
        seq = self._archetypes()["ai_review"].get("canonical_agent_sequence") or []
        names = [k for entry in seq if isinstance(entry, dict) for k in entry]
        assert "ResultAgent" in names, "ai_review must include ResultAgent in canonical sequence"

    def test_ai_extraction_requires_task_batches(self):
        assert self._archetypes()["ai_extraction"].get("task_batches_required") is True
