"""Tests for the sandbox_shell declarative field.

Covers:
- AgentSpec contract: field is accepted, defaults to False, extra fields still rejected
- YAML declarations: AppGenerator coding agents declare sandbox_shell: true
- AgentGenerator workflow bundle workers do not use sandbox_shell; validation is deterministic after export
- Auto_tool_call agents are not marked sandbox_shell (they cannot receive tools)
- Factory source: LocalShellTool injection is gated on auto_tool_call_enabled
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKSPACE = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_agents_yaml(workflow_name: str) -> list[dict]:
    path = WORKSPACE / "factory_app" / "workflows" / workflow_name / "agents.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, list) else data.get("agents", [])


def _agent_by_name(agents: list[dict], name: str) -> dict:
    for a in agents:
        if a.get("name") == name:
            return a
    raise KeyError(f"Agent '{name}' not found")


# ---------------------------------------------------------------------------
# AgentSpec contract
# ---------------------------------------------------------------------------

def test_agent_spec_sandbox_shell_accepted() -> None:
    from mozaiksai.core.workflow.declarative.contracts import AgentSpec

    spec = AgentSpec.model_validate({
        "name": "CodingAgent",
        "system_message": "You generate code.",
        "sandbox_shell": True,
    })

    assert spec.sandbox_shell is True


def test_agent_spec_sandbox_shell_defaults_false() -> None:
    from mozaiksai.core.workflow.declarative.contracts import AgentSpec

    spec = AgentSpec.model_validate({
        "name": "CodingAgent",
        "system_message": "You generate code.",
    })

    assert spec.sandbox_shell is False


def test_agent_spec_unknown_field_still_rejected() -> None:
    from pydantic import ValidationError

    from mozaiksai.core.workflow.declarative.contracts import AgentSpec

    with pytest.raises(ValidationError):
        AgentSpec.model_validate({
            "name": "CodingAgent",
            "system_message": "You generate code.",
            "unknown_field": True,
        })


# ---------------------------------------------------------------------------
# YAML declarations — AppGenerator
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent_name", [
    "ServiceAgent",
    "ModelAgent",
    "ConfigMiddlewareAgent",
    "DatabaseAgent",
    "FrontendStubAgent",
    "ControllerAgent",
])
def test_appgenerator_coding_agents_declare_sandbox_shell(agent_name: str) -> None:
    agents = _load_agents_yaml("AppGenerator")
    agent = _agent_by_name(agents, agent_name)
    assert agent.get("sandbox_shell") is True, f"{agent_name} must declare sandbox_shell: true"


@pytest.mark.parametrize("agent_name", [
    "AppSchemaAgent",
    "AppUIQualityAgent",
    "ModuleContractQualityAgent",
    "ModuleRuntimeQualityAgent",
    "AppPlanAgent",
    "AdminRegistryAgent",
    "AssemblyAgent",
    "IntegrationReadinessAgent",
    "DownloadAgent",
    "AppValidationAgent",
])
def test_appgenerator_auto_tool_call_agents_have_no_sandbox_shell(agent_name: str) -> None:
    """auto_tool_call agents must not be marked sandbox_shell — the factory skips them."""
    agents = _load_agents_yaml("AppGenerator")
    agent = _agent_by_name(agents, agent_name)
    assert not agent.get("sandbox_shell"), f"{agent_name} must not declare sandbox_shell"


# ---------------------------------------------------------------------------
# YAML declarations — AgentGenerator
# ---------------------------------------------------------------------------

def test_agentgenerator_workflow_bundle_builder_agent_has_no_sandbox_shell() -> None:
    agents = _load_agents_yaml("AgentGenerator")
    agent = _agent_by_name(agents, "WorkflowBundleBuilderAgent")

    assert not agent.get("sandbox_shell")


def test_agentgenerator_workflow_bundle_builder_agent_defaults_sandbox_shell_false() -> None:
    from mozaiksai.core.workflow.declarative.contracts import AgentSpec

    agents = _load_agents_yaml("AgentGenerator")
    agent = _agent_by_name(agents, "WorkflowBundleBuilderAgent")

    spec = AgentSpec.model_validate(agent)
    assert spec.sandbox_shell is False


# ---------------------------------------------------------------------------
# Factory source: LocalShellTool injection guard
# ---------------------------------------------------------------------------

def test_factory_shell_injection_gated_on_auto_tool_call() -> None:
    """The factory must not inject LocalShellTool for auto_tool_call agents."""
    src = (WORKSPACE / "mozaiksai" / "core" / "workflow" / "agents" / "factory.py").read_text(encoding="utf-8")

    # The guard must be: only inject when NOT auto_tool_call
    assert "if not auto_tool_call_enabled and agent_config.get(\"sandbox_shell\")" in src


def test_factory_shell_tools_appended_after_wrapped_tools() -> None:
    """Wrapped context-injected tools and shell tools must be concatenated, not replacing."""
    src = (WORKSPACE / "mozaiksai" / "core" / "workflow" / "agents" / "factory.py").read_text(encoding="utf-8")

    # shell_tools must be appended to the wrapped list
    assert "] + shell_tools" in src
