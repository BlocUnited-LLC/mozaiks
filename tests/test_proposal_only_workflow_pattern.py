"""
Tests for the proposal-only workflow archetype pattern.

Verifies that the OSS guidance for proposal-only / HITL workflows is complete,
internally consistent, and free of proprietary or domain-specific content.

Test requirements:
1.  OSS docs/guidance mention the proposal_only workflow pattern.
2.  proposal_only requires human_in_the_loop: true.
3.  proposal_only requires human_review_required: true.
4.  proposal_only forbids execute_action.
5.  proposal_only allows save_* artifact/proposal tools.
6.  proposal_only recommends OutputAgent invariant enforcement.
7.  blocked/deferred phase pattern is documented.
8.  AppGenerator guidance routes high-stakes planning workflows to proposal_only.
9.  No proprietary workflow names appear in the OSS guidance.
10. No MozaiksPay/Stripe/billing/investor/domain-registry examples in the generic pattern.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

# ── Paths ─────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = REPO_ROOT / "factory_app" / "workflows" / "AppGenerator" / "tools"
APPGEN_CATALOG_DIR = REPO_ROOT / "factory_app" / "build_context" / "AppGenerator"
AGENTS_YAML = REPO_ROOT / "factory_app" / "workflows" / "AppGenerator" / "agents.yaml"
WORKFLOW_ARCHETYPES_YAML = APPGEN_CATALOG_DIR / "workflow_archetypes.yaml"
HOOK_FILE = TOOLS_DIR / "hook_file_contract_context.py"
PATTERN_DOC = REPO_ROOT / "docs" / "architecture" / "workflows" / "proposal-only-workflow-pattern.md"
WORKFLOWS_INDEX = REPO_ROOT / "docs" / "architecture" / "workflows" / "index.md"


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _workflow_archetypes() -> dict:
    return _load_yaml(WORKFLOW_ARCHETYPES_YAML)


def _proposal_only_archetype() -> dict:
    return _workflow_archetypes().get("archetypes", {}).get("proposal_only", {})


# ── Proprietary content that must NOT appear in OSS guidance ─────────────────

PROPRIETARY_WORKFLOW_NAMES = [
    "AssuranceReviewWorkflow",
    "InfrastructureRemediationWorkflow",
    "DomainMigrationWorkflow",
    "MigrationPlannerAgent",
    "RemediationPlannerAgent",
    "AssuranceAnalysisAgent",
]

PROPRIETARY_EXAMPLES = [
    "MozaiksPay",
    "mozaikspay",
    "Stripe",
    "stripe",
    "GoDaddy",
    "godaddy",
    "OpenSRS",
    "opensrs",
    "Cloudflare",
    "cloudflare",
    "Azure DNS",
    "azure_dns",
    "investor",
    "wallet",
    "payout",
    "billing_pack",
]


# ══════════════════════════════════════════════════════════════════════════════
# 1. OSS docs and guidance mention proposal_only
# ══════════════════════════════════════════════════════════════════════════════

class TestProposalOnlyDocumentationExists:

    def test_workflow_archetypes_yaml_exists(self):
        assert WORKFLOW_ARCHETYPES_YAML.exists(), (
            "workflow_archetypes.yaml must exist in factory_app/build_context/AppGenerator/"
        )

    def test_proposal_only_archetype_declared(self):
        archetypes = _workflow_archetypes().get("archetypes", {})
        assert "proposal_only" in archetypes, (
            "workflow_archetypes.yaml must declare a 'proposal_only' archetype"
        )

    def test_pattern_doc_exists(self):
        assert PATTERN_DOC.exists(), (
            "docs/architecture/workflows/proposal-only-workflow-pattern.md must exist"
        )

    def test_pattern_doc_is_non_empty(self):
        content = _read(PATTERN_DOC)
        assert len(content) > 500, "proposal-only-workflow-pattern.md is too short"

    def test_pattern_doc_indexed(self):
        index = _read(WORKFLOWS_INDEX)
        assert "proposal-only-workflow-pattern" in index, (
            "workflows/index.md must link to proposal-only-workflow-pattern.md"
        )

    def test_agents_yaml_references_workflow_archetype_context(self):
        text = _read(AGENTS_YAML)
        assert "WORKFLOW ARCHETYPE CONTEXT" in text, (
            "AppPlanAgent in agents.yaml must reference [WORKFLOW ARCHETYPE CONTEXT]"
        )

    def test_agents_yaml_references_proposal_only(self):
        text = _read(AGENTS_YAML)
        assert "proposal_only" in text, (
            "AppPlanAgent in agents.yaml must mention the proposal_only archetype"
        )

    def test_hook_injects_workflow_archetypes(self):
        text = _read(HOOK_FILE)
        assert "_build_workflow_archetypes_body" in text or "workflow_archetypes" in text, (
            "hook_file_contract_context.py must reference workflow_archetypes"
        )
        assert "_WORKFLOW_ARCHETYPES_PATH" in text or "workflow_archetypes.yaml" in text, (
            "hook must declare path to workflow_archetypes.yaml"
        )

    def test_workflow_archetypes_yaml_parses(self):
        try:
            data = _load_yaml(WORKFLOW_ARCHETYPES_YAML)
            assert data is not None
        except yaml.YAMLError as e:
            pytest.fail(f"workflow_archetypes.yaml is invalid YAML: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# 2. proposal_only requires human_in_the_loop: true
# ══════════════════════════════════════════════════════════════════════════════

class TestProposalOnlyHumanInTheLoop:

    def test_orchestrator_defaults_declare_human_in_the_loop(self):
        archetype = _proposal_only_archetype()
        defaults = archetype.get("orchestrator_defaults") or {}
        assert defaults.get("human_in_the_loop") is True, (
            "proposal_only.orchestrator_defaults.human_in_the_loop must be true"
        )

    def test_pattern_doc_states_human_in_the_loop_required(self):
        doc = _read(PATTERN_DOC)
        assert re.search(r"human_in_the_loop.*true", doc), (
            "pattern doc must state human_in_the_loop: true"
        )

    def test_pattern_doc_states_hitl_non_negotiable(self):
        doc = _read(PATTERN_DOC)
        assert re.search(
            r"human_in_the_loop.*true.*required|required.*human_in_the_loop.*true",
            doc, re.DOTALL | re.IGNORECASE
        ), "pattern doc must state human_in_the_loop: true is required"

    def test_agents_yaml_guidance_states_human_in_the_loop(self):
        text = _read(AGENTS_YAML)
        assert "human_in_the_loop: true" in text, (
            "AppPlanAgent guidance must mention human_in_the_loop: true"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 3. proposal_only requires human_review_required: true
# ══════════════════════════════════════════════════════════════════════════════

class TestProposalOnlyHumanReviewRequired:

    def test_structured_output_defaults_include_human_review_required(self):
        archetype = _proposal_only_archetype()
        so = archetype.get("structured_output_defaults") or {}
        required_fields = so.get("required_fields") or {}
        assert "human_review_required" in required_fields, (
            "proposal_only structured_output_defaults must include human_review_required"
        )

    def test_output_invariants_enforce_human_review_required(self):
        archetype = _proposal_only_archetype()
        invariants_block = archetype.get("output_invariants") or {}
        invariants = invariants_block.get("invariants") or []
        hrr_invariants = [
            i for i in invariants
            if isinstance(i, dict) and i.get("field") == "human_review_required"
        ]
        assert hrr_invariants, (
            "proposal_only.output_invariants must include human_review_required invariant"
        )
        assert hrr_invariants[0].get("enforced_value") is True, (
            "human_review_required invariant must enforce value=true"
        )

    def test_pattern_doc_states_human_review_required_always_true(self):
        doc = _read(PATTERN_DOC)
        assert re.search(r"human_review_required.*true.*always|always.*human_review_required.*true", doc, re.DOTALL | re.IGNORECASE), (
            "pattern doc must state human_review_required is always true"
        )

    def test_pattern_doc_output_invariants_section_exists(self):
        doc = _read(PATTERN_DOC)
        assert "output invariant" in doc.lower(), (
            "pattern doc must have an output invariants section"
        )

    def test_todo_notes_future_schema_level_invariants(self):
        """Pattern doc must acknowledge that schema-level invariants are a future enhancement."""
        doc = _read(PATTERN_DOC)
        assert re.search(r"TODO.*schema.level|schema.level.*invariant.*future", doc, re.DOTALL | re.IGNORECASE), (
            "pattern doc must note that schema-level invariant enforcement is a future enhancement"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 4. proposal_only forbids execute_action
# ══════════════════════════════════════════════════════════════════════════════

class TestProposalOnlyForbidsExecuteAction:

    def test_tool_constraints_forbid_execute_action(self):
        archetype = _proposal_only_archetype()
        constraints = archetype.get("tool_constraints") or {}
        forbidden = constraints.get("forbidden") or []
        forbidden_text = " ".join(str(f) for f in forbidden)
        assert "execute_action" in forbidden_text, (
            "proposal_only.tool_constraints.forbidden must list execute_action"
        )

    def test_hard_constraints_forbid_execute_action(self):
        archetype = _proposal_only_archetype()
        hard = archetype.get("hard_constraints") or []
        hard_text = " ".join(str(h) for h in hard)
        assert "execute_action" in hard_text, (
            "proposal_only.hard_constraints must forbid execute_action"
        )

    def test_pattern_doc_forbids_execute_action(self):
        doc = _read(PATTERN_DOC)
        assert "execute_action" in doc, (
            "pattern doc must mention execute_action in its forbidden list"
        )
        assert re.search(r"(must not|never|forbidden|do not).*execute_action", doc, re.IGNORECASE), (
            "pattern doc must explicitly forbid execute_action"
        )

    def test_agents_yaml_guidance_forbids_execute_action(self):
        text = _read(AGENTS_YAML)
        # The proposal_only guidance in agents.yaml should mention forbidding execute_action
        assert re.search(r"proposal_only.*execute_action|execute_action.*proposal_only|forbid.*execute_action", text, re.DOTALL | re.IGNORECASE), (
            "AppPlanAgent guidance must state that proposal_only forbids execute_action"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 5. proposal_only allows save_* artifact/proposal tools
# ══════════════════════════════════════════════════════════════════════════════

class TestProposalOnlyAllowsSaveTools:

    def test_tool_constraints_allow_save(self):
        archetype = _proposal_only_archetype()
        constraints = archetype.get("tool_constraints") or {}
        allowed = constraints.get("allowed") or []
        allowed_text = " ".join(str(a) for a in allowed)
        assert "save_" in allowed_text or "save_*" in allowed_text, (
            "proposal_only.tool_constraints.allowed must include save_* tools"
        )

    def test_pattern_doc_describes_save_tool_auto_tool_call(self):
        doc = _read(PATTERN_DOC)
        assert "auto_tool_call: true" in doc, (
            "pattern doc must state that save_* tool uses auto_tool_call: true"
        )

    def test_pattern_doc_save_tool_on_output_agent(self):
        doc = _read(PATTERN_DOC)
        assert re.search(r"save_.*OutputAgent|OutputAgent.*save_", doc, re.DOTALL), (
            "pattern doc must associate save_* tool with OutputAgent"
        )

    def test_allowed_tool_prefixes_exclude_write(self):
        archetype = _proposal_only_archetype()
        constraints = archetype.get("tool_constraints") or {}
        allowed = constraints.get("allowed") or []
        allowed_text = " ".join(str(a) for a in allowed).lower()
        for write_prefix in ("create_", "update_", "delete_", "execute_", "approve_"):
            assert write_prefix not in allowed_text, (
                f"proposal_only.tool_constraints.allowed must not permit {write_prefix}* tools"
            )


# ══════════════════════════════════════════════════════════════════════════════
# 6. proposal_only recommends OutputAgent invariant enforcement
# ══════════════════════════════════════════════════════════════════════════════

class TestProposalOnlyOutputAgentInvariants:

    def test_canonical_agent_sequence_includes_output_agent(self):
        archetype = _proposal_only_archetype()
        agents = archetype.get("canonical_agent_sequence") or []
        agent_names = []
        for item in agents:
            if isinstance(item, dict):
                agent_names.extend(item.keys())
            elif isinstance(item, str):
                agent_names.append(item)
        assert any("Output" in name for name in agent_names), (
            "proposal_only.canonical_agent_sequence must include an OutputAgent"
        )

    def test_output_agent_requires_structured_outputs(self):
        archetype = _proposal_only_archetype()
        agents = archetype.get("canonical_agent_sequence") or []
        for item in agents:
            if isinstance(item, dict):
                for name, config in item.items():
                    if "Output" in name and isinstance(config, dict):
                        assert config.get("structured_outputs_required") is True, (
                            "OutputAgent in proposal_only must have structured_outputs_required: true"
                        )

    def test_hard_constraints_mention_output_agent_invariants(self):
        archetype = _proposal_only_archetype()
        hard = archetype.get("hard_constraints") or []
        hard_text = " ".join(str(h) for h in hard).lower()
        assert "outputagent" in hard_text or "output format" in hard_text or "invariant" in hard_text, (
            "proposal_only.hard_constraints must reference OutputAgent invariant enforcement"
        )

    def test_pattern_doc_output_agent_section_enforces_invariants(self):
        doc = _read(PATTERN_DOC)
        assert re.search(
            r"OutputAgent.*enforce.*invariant|enforce.*invariant.*OutputAgent",
            doc, re.DOTALL | re.IGNORECASE
        ), "pattern doc must state that OutputAgent enforces invariants"

    def test_pattern_doc_has_outputagent_output_format_example(self):
        doc = _read(PATTERN_DOC)
        assert "[OUTPUT FORMAT]" in doc, (
            "pattern doc must show an example [OUTPUT FORMAT] section for OutputAgent"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 7. blocked/deferred phase pattern is documented
# ══════════════════════════════════════════════════════════════════════════════

class TestBlockedDeferredPhasePattern:

    def test_workflow_archetypes_declares_blocked_phase_pattern(self):
        archetype = _proposal_only_archetype()
        assert "blocked_phase_pattern" in archetype, (
            "proposal_only must declare a blocked_phase_pattern section"
        )

    def test_blocked_phase_pattern_has_status_blocked(self):
        archetype = _proposal_only_archetype()
        pattern = archetype.get("blocked_phase_pattern") or {}
        fields = pattern.get("fields") or {}
        status_field = fields.get("status") or {}
        if isinstance(status_field, dict):
            assert status_field.get("value") == "blocked"
        else:
            # may be a string description
            assert "blocked" in str(status_field).lower()

    def test_blocked_phase_pattern_has_blocked_reason(self):
        archetype = _proposal_only_archetype()
        pattern = archetype.get("blocked_phase_pattern") or {}
        fields = pattern.get("fields") or {}
        assert "blocked_reason" in fields, (
            "blocked_phase_pattern must define a blocked_reason field"
        )

    def test_blocked_phase_pattern_has_deferred_to(self):
        archetype = _proposal_only_archetype()
        pattern = archetype.get("blocked_phase_pattern") or {}
        fields = pattern.get("fields") or {}
        assert "deferred_to" in fields, (
            "blocked_phase_pattern must define a deferred_to field"
        )

    def test_blocked_phase_rule_states_never_omit(self):
        archetype = _proposal_only_archetype()
        pattern = archetype.get("blocked_phase_pattern") or {}
        rule = str(pattern.get("rule", "")).lower()
        assert rule, "blocked_phase_pattern must have a rule"
        assert re.search(r"not.*omit|never.*omit|not.*skip|never.*skip|must.*appear", rule), (
            "blocked_phase_pattern rule must state that blocked phases must appear in output"
        )

    def test_pattern_doc_documents_blocked_phase_pattern(self):
        doc = _read(PATTERN_DOC)
        assert "blocked" in doc.lower(), "pattern doc must discuss blocked phases"
        assert "deferred_to" in doc, "pattern doc must document deferred_to field"
        assert "blocked_reason" in doc, "pattern doc must document blocked_reason field"

    def test_pattern_doc_explains_why_blocked_phases_appear(self):
        doc = _read(PATTERN_DOC)
        assert re.search(r"blocked phase.{0,100}appear|operator.{0,100}blocked.{0,100}see", doc, re.DOTALL | re.IGNORECASE), (
            "pattern doc must explain why blocked phases must appear in the output"
        )

    def test_pattern_doc_blocked_phase_yaml_example(self):
        doc = _read(PATTERN_DOC)
        assert "status: blocked" in doc, "pattern doc must include a status: blocked example"


# ══════════════════════════════════════════════════════════════════════════════
# 8. AppGenerator guidance routes high-stakes planning to proposal_only
# ══════════════════════════════════════════════════════════════════════════════

class TestAppGeneratorRoutesToProposalOnly:

    def test_agents_yaml_routes_planning_workflows_to_proposal_only(self):
        text = _read(AGENTS_YAML)
        # AppPlanAgent must guide proposal_only selection for planning workflows
        assert re.search(
            r"proposal_only.{0,200}(plan|assess|recommend|compliance|migration|remediation)",
            text, re.DOTALL | re.IGNORECASE
        ), "AppPlanAgent must route planning/assessment workflows to proposal_only"

    def test_proposal_only_select_when_includes_planning_examples(self):
        archetype = _proposal_only_archetype()
        select_when = archetype.get("select_when") or []
        combined = " ".join(str(sw) for sw in select_when).lower()
        planning_keywords = ["plan", "assess", "recommend", "compliance", "migration",
                             "remediation", "review", "proposal"]
        matches = [kw for kw in planning_keywords if kw in combined]
        assert len(matches) >= 3, (
            f"proposal_only.select_when should include planning/assessment examples. "
            f"Found: {matches}"
        )

    def test_proposal_only_select_when_neutral_examples_only(self):
        archetype = _proposal_only_archetype()
        select_when = archetype.get("select_when") or []
        combined = " ".join(str(sw) for sw in select_when)
        # Must use neutral examples, not proprietary domain-specific ones
        for proprietary in PROPRIETARY_WORKFLOW_NAMES + PROPRIETARY_EXAMPLES:
            assert proprietary not in combined, (
                f"proposal_only.select_when must not mention proprietary example: {proprietary}"
            )

    def test_workflow_archetypes_description_explains_routing(self):
        data = _workflow_archetypes()
        desc = str(data.get("description", ""))
        assert "workflow" in desc.lower() and "archetype" in desc.lower(), (
            "workflow_archetypes.yaml description must explain its routing purpose"
        )

    def test_pattern_doc_describes_appgenerator_routing(self):
        doc = _read(PATTERN_DOC)
        assert "AppGenerator" in doc or "AppPlanAgent" in doc, (
            "pattern doc should mention how AppGenerator routes to proposal_only"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 9. No proprietary workflow names in OSS guidance
# ══════════════════════════════════════════════════════════════════════════════

class TestNoProprietaryWorkflowNames:

    @pytest.mark.parametrize("name", PROPRIETARY_WORKFLOW_NAMES)
    def test_workflow_archetypes_yaml_no_proprietary_workflow_name(self, name):
        text = _read(WORKFLOW_ARCHETYPES_YAML)
        assert name not in text, (
            f"workflow_archetypes.yaml must not contain proprietary workflow name: {name}"
        )

    @pytest.mark.parametrize("name", PROPRIETARY_WORKFLOW_NAMES)
    def test_pattern_doc_no_proprietary_workflow_name(self, name):
        text = _read(PATTERN_DOC)
        assert name not in text, (
            f"proposal-only-workflow-pattern.md must not contain proprietary name: {name}"
        )

    def test_examples_in_archetypes_are_neutral(self):
        text = _read(WORKFLOW_ARCHETYPES_YAML)
        for name in PROPRIETARY_WORKFLOW_NAMES:
            assert name not in text, f"Proprietary name found in workflow_archetypes.yaml: {name}"

    def test_hook_no_proprietary_workflow_names(self):
        text = _read(HOOK_FILE)
        for name in PROPRIETARY_WORKFLOW_NAMES:
            assert name not in text, (
                f"hook_file_contract_context.py must not reference proprietary workflow: {name}"
            )


# ══════════════════════════════════════════════════════════════════════════════
# 10. No proprietary product examples in OSS guidance
# ══════════════════════════════════════════════════════════════════════════════

class TestNoProprietaryProductExamples:

    @pytest.mark.parametrize("example", PROPRIETARY_EXAMPLES)
    def test_workflow_archetypes_yaml_no_proprietary_example(self, example):
        text = _read(WORKFLOW_ARCHETYPES_YAML)
        assert example not in text, (
            f"workflow_archetypes.yaml must not contain proprietary example: {example!r}"
        )

    @pytest.mark.parametrize("example", PROPRIETARY_EXAMPLES)
    def test_pattern_doc_no_proprietary_example(self, example):
        text = _read(PATTERN_DOC)
        assert example not in text, (
            f"proposal-only-workflow-pattern.md must not contain proprietary example: {example!r}"
        )

    def test_workflow_archetypes_uses_neutral_examples(self):
        text = _read(WORKFLOW_ARCHETYPES_YAML)
        # Should contain only neutral examples
        neutral_examples = [
            "compliance", "migration", "deployment", "content", "change proposal", "data cleanup",
        ]
        found = [ex for ex in neutral_examples if ex in text.lower()]
        assert found, (
            "workflow_archetypes.yaml should use neutral examples like compliance, migration, deployment"
        )

    def test_pattern_doc_uses_neutral_examples(self):
        text = _read(PATTERN_DOC)
        neutral_examples = [
            "compliance", "migration", "deployment", "content", "change proposal", "data cleanup",
        ]
        found = [ex for ex in neutral_examples if ex in text.lower()]
        assert len(found) >= 3, (
            "pattern doc should use multiple neutral domain examples"
        )

    def test_no_dns_registrar_provider_specifics_in_oss(self):
        """The OSS layer must not encode domain registry / DNS provider specifics."""
        oss_files = [WORKFLOW_ARCHETYPES_YAML, PATTERN_DOC, HOOK_FILE]
        for path in oss_files:
            if not path.exists():
                continue
            text = _read(path)
            for term in ["CLOUDFLARE_API_TOKEN", "GODADDY_API_SECRET", "OPENSRS_USERNAME",
                         "AZURE_CLIENT_SECRET", "sso-key", "X-Auth-Key"]:
                assert term not in text, (
                    f"{path.name} must not contain provider credential term: {term}"
                )

