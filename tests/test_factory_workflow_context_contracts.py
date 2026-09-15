from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

from mozaiksai.core.workflow.context.authority import (
    AGENT_TEXT_WRITER,
    SENTINEL_TEXT_TRIGGER_WRITER,
    build_context_authority_policy,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_ROOT = ROOT / "factory_app" / "workflows"

BUILD_SEQUENCE_WORKFLOWS = (
    "ValueEngine",
    "ThemeCapture",
    "DesignDocs",
    "SubscriptionContractDesigner",
    "AgentGenerator",
    "AppGenerator",
)

REFINEMENT_SEED_KEYS = (
    "build_mode",
    "workflow_sequence",
    "revision_scope",
    "change_request_id",
    "revision_id",
    "artifact_kind",
    "artifact_version_id",
    "sequence_status",
    "revision_origin_workflow",
    "refinement_request",
    "refinement_request_meta",
    "screen",
    "change_intent",
    "impact_set",
)

APP_CONTEXT_KEYS = (
    "app_context_required",
    "current_app_context_version_id",
    "app_context_summary",
)

APP_INTELLIGENCE_KEYS = (
    "app_type",
    "app_intelligence_ready",
    "app_intelligence_status",
    "app_intelligence_summary",
    "app_intelligence_health",
    "app_intelligence_catalog",
    "source_context_catalog",
)

BROWNFIELD_KEYS = (
    "brownfield_build_path",
    "adoption_plan",
    "ownership_boundary",
    "brownfield_registration",
)

PLANNING_AGENT_CONTEXT = {
    "ValueEngine": ("ValueInterviewAgent", "ResearchAgent", "GapAnalysisAgent"),
    "ThemeCapture": ("ThemeInterviewAgent", "ThemeAnalysisAgent"),
    "DesignDocs": ("DesignDocsAgent",),
    "SubscriptionContractDesigner": ("ContractDesignerAgent",),
    "AgentGenerator": ("PatternAgent", "WorkflowBundleBuilderAgent"),
    "AppGenerator": ("InterviewAgent", "AppPlanAgent"),
}

EXISTING_APP_DISCOVERY_OUTPUT_KEYS = (
    "application_inventory",
    "integration_inventory",
    "risk_report",
    "adoption_plan",
    "ownership_boundary",
    "brownfield_registration",
    "brownfield_app_context_artifacts",
    "brownfield_app_context_artifact_version_refs",
    "brownfield_app_context_artifact_persistence_error",
    "app_context_version",
)


def _load_context_variables(workflow_id: str) -> dict:
    path = WORKFLOWS_ROOT / workflow_id / "context_variables.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _definitions(workflow_id: str) -> dict:
    return _load_context_variables(workflow_id).get("definitions") or {}


def test_agentgenerator_attachments_use_execution_session_scope():
    from mozaiksai.core.workflow.context.variables import _materialize_query_template

    source = _definitions("AgentGenerator")["chat_attachments"]["source"]
    assert source["collection"] == "ChatSessions"
    query = _materialize_query_template(source["query_template"], {
        "chat_id": "execution-chat", "run_build_binding": {"target_app_id": "generated-app"},
    }, app_id="factory-host")
    assert query == {"app_id": "factory-host", "_id": "execution-chat"}


def _agent_variables(workflow_id: str, agent_name: str) -> set[str]:
    agents = _load_context_variables(workflow_id).get("agents") or {}
    return set((agents.get(agent_name) or {}).get("variables") or [])


def test_appgenerator_tool_state_writes_are_declared_and_authorized():
    definitions = _definitions("AppGenerator")
    policy = build_context_authority_policy(workflow_name="AppGenerator", definitions=definitions)
    for path in (WORKFLOWS_ROOT / "AppGenerator" / "tools").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "_context_set" and len(node.args) >= 3
                    and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)):
                continue
            key = node.args[1].value
            assert key in definitions, f"{path.name} writes undeclared state {key}"
            policy.require_can_write(key, writer_id="deterministic_tool")


def test_app_acceptance_evidence_cannot_be_written_by_agent_text():
    policy = build_context_authority_policy(workflow_name="AppGenerator", definitions=_definitions("AppGenerator"))
    for key in ("app_bundle_acceptance_status", "app_bundle_acceptance_result", "app_bundle_validation_evidence"):
        assert not policy.can_write(key, writer_id=AGENT_TEXT_WRITER)


def test_agentgenerator_interview_readiness_is_not_writable_by_agent_text() -> None:
    """Readiness routes the build, so model prose must not be able to set it.

    This used to be an exact-match `NEXT` sentinel, which held the boundary but
    required the model to emit a bare token with no preamble — it did not, and
    the workflow hung (#591). Readiness is now a validated structured-output
    field written by a deterministic tool. The boundary is the same; only the
    mechanism changed, so the agent-text writer must still be refused.
    """
    definitions = _definitions("AgentGenerator")
    readiness = definitions["interview_outcome"]

    assert readiness["writer_ids"] == ["deterministic_tool"]
    assert not readiness["source"].get("triggers"), (
        "readiness must not be inferred from chat text"
    )

    policy = build_context_authority_policy(
        workflow_name="AgentGenerator", definitions=definitions,
    )
    policy.require_can_write("interview_outcome", writer_id="deterministic_tool")
    assert not policy.can_write("interview_outcome", writer_id=AGENT_TEXT_WRITER)
    assert not policy.can_write("interview_outcome", writer_id=SENTINEL_TEXT_TRIGGER_WRITER)


@pytest.mark.parametrize("workflow_id", BUILD_SEQUENCE_WORKFLOWS)
def test_build_sequence_workflows_declare_refinement_seed_keys(workflow_id: str) -> None:
    defs = _definitions(workflow_id)

    for key in REFINEMENT_SEED_KEYS:
        assert key in defs, f"{workflow_id} must declare refinement launch key {key}"


@pytest.mark.parametrize("workflow_id", BUILD_SEQUENCE_WORKFLOWS)
def test_build_sequence_workflows_declare_app_intelligence_handoff_keys(workflow_id: str) -> None:
    defs = _definitions(workflow_id)

    for key in (*APP_CONTEXT_KEYS, *APP_INTELLIGENCE_KEYS, *BROWNFIELD_KEYS):
        assert key in defs, f"{workflow_id} must declare app-intelligence handoff key {key}"


@pytest.mark.parametrize("workflow_id", BUILD_SEQUENCE_WORKFLOWS)
def test_app_intelligence_handoff_keys_are_state_backed(workflow_id: str) -> None:
    defs = _definitions(workflow_id)

    for key in (*APP_CONTEXT_KEYS, *APP_INTELLIGENCE_KEYS, *BROWNFIELD_KEYS):
        source = (defs.get(key) or {}).get("source") or {}
        assert source.get("type") == "state", f"{workflow_id}.{key} must be state-backed"


@pytest.mark.parametrize(
    ("workflow_id", "agent_name"),
    [
        (workflow_id, agent_name)
        for workflow_id, agent_names in PLANNING_AGENT_CONTEXT.items()
        for agent_name in agent_names
    ],
)
def test_planning_agents_receive_compact_app_intelligence_context(
    workflow_id: str,
    agent_name: str,
) -> None:
    agent_vars = _agent_variables(workflow_id, agent_name)

    for key in (*APP_CONTEXT_KEYS, *APP_INTELLIGENCE_KEYS, *BROWNFIELD_KEYS):
        assert key in agent_vars, f"{workflow_id}.{agent_name} missing context var {key}"


def test_existing_app_discovery_declares_canonical_app_context_outputs() -> None:
    defs = _definitions("ExistingAppDiscovery")

    for key in EXISTING_APP_DISCOVERY_OUTPUT_KEYS:
        assert key in defs, f"ExistingAppDiscovery must declare generated app-context key {key}"
