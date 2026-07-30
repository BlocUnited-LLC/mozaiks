"""Contract tests for brownfield adoption context wiring in generation workflows.

Verifies that:
- AgentGenerator, AppGenerator, and DesignDocs declare the canonical brownfield
  context variables (brownfield_build_path, adoption_plan, ownership_boundary,
  brownfield_registration) in their context_variables.yaml definitions.
- The planning agents in each workflow expose the brownfield context variables.
- All three workflows wire inject_brownfield_adoption_context middleware for
  their planning agents.
- The inject_brownfield_adoption_context hook returns empty for greenfield builds
  and a labelled context block for brownfield builds.
- The brownfield overlay and module generation sequences are correctly declared
  in the extension registry.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

AGENT_GENERATOR_CV = ROOT / "factory_app/workflows/AgentGenerator/context_variables.yaml"
APP_GENERATOR_CV = ROOT / "factory_app/workflows/AppGenerator/context_variables.yaml"
DESIGN_DOCS_CV = ROOT / "factory_app/workflows/DesignDocs/context_variables.yaml"
AGENT_GENERATOR_MW = ROOT / "factory_app/workflows/AgentGenerator/middleware.yaml"
APP_GENERATOR_MW = ROOT / "factory_app/workflows/AppGenerator/middleware.yaml"
DESIGN_DOCS_MW = ROOT / "factory_app/workflows/DesignDocs/middleware.yaml"
REGISTRY_PATH = ROOT / "factory_app/workflows/extended_orchestration/extension_registry.json"
HOOK_PATH = ROOT / "factory_app/workflows/_shared/brownfield_adoption_context.py"

BROWNFIELD_CONTEXT_VARS = (
    "brownfield_build_path",
    "adoption_plan",
    "ownership_boundary",
    "brownfield_registration",
)

# AgentGenerator planning agents that need brownfield context
AGENT_GENERATOR_PLANNING_AGENTS = {"PatternAgent", "WorkflowBundleBuilderAgent"}

# AppGenerator planning agents that need brownfield context
APP_GENERATOR_PLANNING_AGENTS = {"InterviewAgent", "AppPlanAgent"}

BROWNFIELD_HOOK_FUNCTION = "inject_brownfield_adoption_context"
BROWNFIELD_HOOK_FILE = "../_shared/brownfield_adoption_context.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_cv(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _load_mw(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _load_registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def _definitions(cv: dict) -> dict:
    return cv.get("definitions") or {}


def _agent_vars(cv: dict, agent_name: str) -> set[str]:
    agents = cv.get("agents") or {}
    agent = agents.get(agent_name) or {}
    return set(agent.get("variables") or [])


def _mw_entries(mw: dict) -> list[dict]:
    return mw.get("prompt_middleware") or []


def _wired_agents_for_function(mw: dict, function_name: str) -> set[str]:
    return {
        str(entry.get("agent") or "")
        for entry in _mw_entries(mw)
        if entry.get("function") == function_name
    }


# ---------------------------------------------------------------------------
# Context variable declarations
# ---------------------------------------------------------------------------

def test_agent_generator_declares_brownfield_context_vars() -> None:
    defs = _definitions(_load_cv(AGENT_GENERATOR_CV))
    for var in BROWNFIELD_CONTEXT_VARS:
        assert var in defs, f"AgentGenerator context_variables.yaml missing definition: {var}"


def test_app_generator_declares_brownfield_context_vars() -> None:
    defs = _definitions(_load_cv(APP_GENERATOR_CV))
    for var in BROWNFIELD_CONTEXT_VARS:
        assert var in defs, f"AppGenerator context_variables.yaml missing definition: {var}"


def test_agent_generator_brownfield_vars_have_null_default() -> None:
    defs = _definitions(_load_cv(AGENT_GENERATOR_CV))
    for var in BROWNFIELD_CONTEXT_VARS:
        source = (defs.get(var) or {}).get("source") or {}
        assert source.get("default") is None, (
            f"AgentGenerator {var} must default to null — it is only present for brownfield builds"
        )


def test_app_generator_brownfield_vars_have_null_default() -> None:
    defs = _definitions(_load_cv(APP_GENERATOR_CV))
    for var in BROWNFIELD_CONTEXT_VARS:
        source = (defs.get(var) or {}).get("source") or {}
        assert source.get("default") is None, (
            f"AppGenerator {var} must default to null — it is only present for brownfield builds"
        )


# ---------------------------------------------------------------------------
# Planning agent variable exposure
# ---------------------------------------------------------------------------

def test_agent_generator_planning_agents_expose_brownfield_vars() -> None:
    cv = _load_cv(AGENT_GENERATOR_CV)
    for agent in sorted(AGENT_GENERATOR_PLANNING_AGENTS):
        agent_vars = _agent_vars(cv, agent)
        for var in BROWNFIELD_CONTEXT_VARS:
            assert var in agent_vars, (
                f"AgentGenerator {agent} missing brownfield context var: {var}"
            )


def test_app_generator_planning_agents_expose_brownfield_vars() -> None:
    cv = _load_cv(APP_GENERATOR_CV)
    for agent in sorted(APP_GENERATOR_PLANNING_AGENTS):
        agent_vars = _agent_vars(cv, agent)
        for var in BROWNFIELD_CONTEXT_VARS:
            assert var in agent_vars, (
                f"AppGenerator {agent} missing brownfield context var: {var}"
            )


# ---------------------------------------------------------------------------
# Middleware wiring
# ---------------------------------------------------------------------------

def test_agent_generator_wires_brownfield_hook_for_planning_agents() -> None:
    mw = _load_mw(AGENT_GENERATOR_MW)
    wired = _wired_agents_for_function(mw, BROWNFIELD_HOOK_FUNCTION)
    for agent in sorted(AGENT_GENERATOR_PLANNING_AGENTS):
        assert agent in wired, (
            f"AgentGenerator middleware.yaml does not wire {BROWNFIELD_HOOK_FUNCTION} for {agent}"
        )


def test_app_generator_wires_brownfield_hook_for_planning_agents() -> None:
    mw = _load_mw(APP_GENERATOR_MW)
    wired = _wired_agents_for_function(mw, BROWNFIELD_HOOK_FUNCTION)
    for agent in sorted(APP_GENERATOR_PLANNING_AGENTS):
        assert agent in wired, (
            f"AppGenerator middleware.yaml does not wire {BROWNFIELD_HOOK_FUNCTION} for {agent}"
        )


def test_agent_generator_brownfield_hook_references_shared_file() -> None:
    mw = _load_mw(AGENT_GENERATOR_MW)
    entries = [
        e for e in _mw_entries(mw)
        if e.get("function") == BROWNFIELD_HOOK_FUNCTION
    ]
    assert entries, "No brownfield hook entries found in AgentGenerator middleware.yaml"
    for entry in entries:
        assert entry.get("filename") == BROWNFIELD_HOOK_FILE, (
            f"Expected filename '{BROWNFIELD_HOOK_FILE}', got '{entry.get('filename')}'"
        )


def test_app_generator_brownfield_hook_references_shared_file() -> None:
    mw = _load_mw(APP_GENERATOR_MW)
    entries = [
        e for e in _mw_entries(mw)
        if e.get("function") == BROWNFIELD_HOOK_FUNCTION
    ]
    assert entries, "No brownfield hook entries found in AppGenerator middleware.yaml"
    for entry in entries:
        assert entry.get("filename") == BROWNFIELD_HOOK_FILE, (
            f"Expected filename '{BROWNFIELD_HOOK_FILE}', got '{entry.get('filename')}'"
        )


# ---------------------------------------------------------------------------
# Shared hook implementation
# ---------------------------------------------------------------------------

def test_brownfield_adoption_context_hook_file_exists() -> None:
    assert HOOK_PATH.exists(), f"Missing shared hook: {HOOK_PATH}"


def test_brownfield_hook_returns_empty_for_greenfield() -> None:
    from factory_app.workflows._shared.brownfield_adoption_context import (
        inject_brownfield_adoption_context,
    )

    result = inject_brownfield_adoption_context(
        agent_name="PatternAgent",
        context_variables={"brownfield_build_path": None},
    )
    assert result == ""


def test_brownfield_hook_returns_empty_when_no_context() -> None:
    from factory_app.workflows._shared.brownfield_adoption_context import (
        inject_brownfield_adoption_context,
    )

    result = inject_brownfield_adoption_context(
        agent_name="AppPlanAgent",
        context_variables={},
    )
    assert result == ""


def test_brownfield_hook_returns_block_for_light_integration() -> None:
    from factory_app.workflows._shared.brownfield_adoption_context import (
        inject_brownfield_adoption_context,
    )

    ctx = {
        "brownfield_build_path": "light_integration",
        "adoption_plan": {
            "recommended_path": "overlay",
            "candidate_overlays": ["workflow:ChatSupport", "workflow:OnboardingFlow"],
            "candidate_adapters": ["adapter:stripe"],
            "candidate_migrations": [],
            "human_decisions_required": ["Approve which surfaces may be augmented."],
            "not_in_scope": ["Automatic takeover of existing source."],
        },
        "ownership_boundary": {
            "app_id": "my_existing_app",
            "ownership_boundaries": [
                {
                    "path_or_artifact": "/api/users",
                    "ownership": "read_only_discovered",
                    "allowed_operations": ["inspect", "explain"],
                    "requires_review": True,
                },
                {
                    "path_or_artifact": "workflow:ChatSupport",
                    "ownership": "generated_overlay",
                    "allowed_operations": ["generate_overlay"],
                    "requires_review": True,
                },
            ],
        },
        "brownfield_registration": {
            "registration_id": "reg_123",
            "app_id": "my_existing_app",
            "status": "pending",
        },
    }

    result = inject_brownfield_adoption_context(
        agent_name="PatternAgent",
        context_variables=ctx,
    )

    assert "[BROWNFIELD ADOPTION CONTEXT]" in result
    assert "light_integration" in result
    assert "my_existing_app" in result
    assert "overlay" in result
    assert "ChatSupport" in result
    assert "read_only_discovered" in result
    assert "generated_overlay" in result
    assert "RULE" in result
    assert "read_only_discovered" in result


def test_brownfield_hook_returns_block_for_full_migration() -> None:
    from factory_app.workflows._shared.brownfield_adoption_context import (
        inject_brownfield_adoption_context,
    )

    ctx = {
        "brownfield_build_path": "full_migration",
        "adoption_plan": {
            "recommended_path": "gradual_modernization",
            "candidate_overlays": [],
            "candidate_adapters": [],
            "candidate_migrations": ["module:billing", "module:auth"],
            "human_decisions_required": ["Approve migration scope."],
        },
        "ownership_boundary": {
            "app_id": "legacy_app",
            "ownership_boundaries": [],
        },
        "brownfield_registration": {
            "app_id": "legacy_app",
            "status": "pending",
        },
    }

    result = inject_brownfield_adoption_context(
        agent_name="AppPlanAgent",
        context_variables=ctx,
    )

    assert "[BROWNFIELD ADOPTION CONTEXT]" in result
    assert "full_migration" in result
    assert "gradual_modernization" in result


# ---------------------------------------------------------------------------
# Extension registry — brownfield sequences
# ---------------------------------------------------------------------------

def test_registry_declares_brownfield_overlay_generation_sequence() -> None:
    registry = _load_registry()
    sequences = {s["id"]: s for s in registry.get("workflow_sequences", [])}
    assert "brownfield_overlay_generation" in sequences
    seq = sequences["brownfield_overlay_generation"]
    workflows = [
        w for step in seq.get("steps", []) for w in step.get("workflows", [])
    ]
    assert "AgentGenerator" in workflows
    assert "AppGenerator" in workflows
    assert "DesignDocs" not in workflows


def test_registry_declares_brownfield_module_generation_sequence() -> None:
    registry = _load_registry()
    sequences = {s["id"]: s for s in registry.get("workflow_sequences", [])}
    assert "brownfield_module_generation" in sequences
    seq = sequences["brownfield_module_generation"]
    workflows = [
        w for step in seq.get("steps", []) for w in step.get("workflows", [])
    ]
    assert "DesignDocs" in workflows
    assert "AgentGenerator" in workflows
    assert "AppGenerator" in workflows


def test_registry_brownfield_sequences_affect_canonical_families() -> None:
    registry = _load_registry()
    sequences = {s["id"]: s for s in registry.get("workflow_sequences", [])}

    overlay_families = set(
        sequences["brownfield_overlay_generation"].get("affected_declarative_families") or []
    )
    assert "workflow_bundle" in overlay_families
    assert "app_bundle" in overlay_families

    module_families = set(
        sequences["brownfield_module_generation"].get("affected_declarative_families") or []
    )
    assert "design_docs" in module_families
    assert "workflow_bundle" in module_families
    assert "app_bundle" in module_families


def test_registry_brownfield_path_selector_routes_correctly() -> None:
    registry = _load_registry()
    transitions = {t["id"]: t for t in registry.get("transitions", [])}

    assert "brownfield_path_selector" in transitions
    selector = transitions["brownfield_path_selector"]
    options = {o["id"]: o for o in selector.get("options", [])}

    assert "light_integration" in options
    assert options["light_integration"]["route_to"] == "AgentGenerator"
    assert options["light_integration"]["sequence"] == "brownfield_overlay_generation"
    assert options["light_integration"]["context_variables"]["brownfield_build_path"] == "light_integration"

    assert "full_migration" in options
    assert options["full_migration"]["route_to"] == "DesignDocs"
    assert options["full_migration"]["sequence"] == "brownfield_module_generation"
    assert options["full_migration"]["context_variables"]["brownfield_build_path"] == "full_migration"


def test_brownfield_context_vars_not_declared_in_greenfield_only_artifacts() -> None:
    """Brownfield vars should default to null — they must not pollute greenfield builds."""
    cv = _load_cv(APP_GENERATOR_CV)
    defs = _definitions(cv)
    for var in BROWNFIELD_CONTEXT_VARS:
        source = (defs.get(var) or {}).get("source") or {}
        # Must be state-backed (session-carried), not config or data_reference
        assert source.get("type") == "state", (
            f"{var} must be type: state so it only appears in brownfield sessions"
        )


# ---------------------------------------------------------------------------
# DesignDocs — brownfield_module_generation path
# ---------------------------------------------------------------------------

def test_design_docs_declares_brownfield_context_vars() -> None:
    defs = _definitions(_load_cv(DESIGN_DOCS_CV))
    for var in BROWNFIELD_CONTEXT_VARS:
        assert var in defs, f"DesignDocs context_variables.yaml missing definition: {var}"


def test_design_docs_brownfield_vars_have_null_default() -> None:
    defs = _definitions(_load_cv(DESIGN_DOCS_CV))
    for var in BROWNFIELD_CONTEXT_VARS:
        source = (defs.get(var) or {}).get("source") or {}
        assert source.get("default") is None, (
            f"DesignDocs {var} must default to null — only present for brownfield builds"
        )


def test_design_docs_agent_exposes_brownfield_vars() -> None:
    cv = _load_cv(DESIGN_DOCS_CV)
    agent_vars = _agent_vars(cv, "DesignDocsAgent")
    for var in BROWNFIELD_CONTEXT_VARS:
        assert var in agent_vars, (
            f"DesignDocsAgent missing brownfield context var: {var}"
        )


def test_design_docs_wires_brownfield_hook_for_design_docs_agent() -> None:
    mw = _load_mw(DESIGN_DOCS_MW)
    wired = _wired_agents_for_function(mw, BROWNFIELD_HOOK_FUNCTION)
    assert "DesignDocsAgent" in wired, (
        "DesignDocs middleware.yaml does not wire inject_brownfield_adoption_context for DesignDocsAgent"
    )


def test_design_docs_brownfield_hook_references_shared_file() -> None:
    mw = _load_mw(DESIGN_DOCS_MW)
    entries = [
        e for e in _mw_entries(mw)
        if e.get("function") == BROWNFIELD_HOOK_FUNCTION
    ]
    assert entries, "No brownfield hook entries found in DesignDocs middleware.yaml"
    for entry in entries:
        assert entry.get("filename") == BROWNFIELD_HOOK_FILE, (
            f"Expected filename '{BROWNFIELD_HOOK_FILE}', got '{entry.get('filename')}'"
        )


def test_design_docs_brownfield_context_vars_are_state_type() -> None:
    defs = _definitions(_load_cv(DESIGN_DOCS_CV))
    for var in BROWNFIELD_CONTEXT_VARS:
        source = (defs.get(var) or {}).get("source") or {}
        assert source.get("type") == "state", (
            f"DesignDocs {var} must be type: state"
        )
