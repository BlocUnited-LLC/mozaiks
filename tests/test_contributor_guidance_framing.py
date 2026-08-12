import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_public_contributor_surfaces_do_not_require_private_hosted_repo_language() -> None:
    for relative_path in [
        "ARCHITECTURE.md",
        ".env.example",
        "web_shell/README.md",
        ".claude/skills/setup/SKILL.md",
        ".claude/skills/add-feature/SKILL.md",
        ".claude/skills/release-notes/SKILL.md",
        "docs/architecture/builder/end-to-end-build-lifecycle.md",
    ]:
        text = _read(relative_path)
        assert "mozaiks-app" not in text, relative_path
        assert "App Zero" not in text, relative_path
        assert "App-zero" not in text, relative_path


def test_architecture_frames_factory_app_as_first_party_reference_workspace() -> None:
    architecture = _read("ARCHITECTURE.md")

    assert "first-party builder/reference app workspace" in architecture
    assert "stub `backend/handler.py` only" in architecture


def test_build_guidance_states_workflow_sequence_truth() -> None:
    architecture = _read("ARCHITECTURE.md")
    builder_execution = _read("docs/architecture/builder/builder-execution-model.md")

    assert "workflow_sequences" in architecture
    assert "individual workflows inside those sequences" in architecture
    assert "brownfield adoption sequence" in architecture
    assert "sequence-driven" in builder_execution


def test_top_level_guidance_documents_shared_workflow_ui_lane() -> None:
    architecture = _read("ARCHITECTURE.md")
    agents = _read("AGENTS.md")
    claude = _read("CLAUDE.md")

    for text in (architecture, agents, claude):
        assert "factory_app/workflows/_shared/" in text
        assert "reusable workflow UI components" in text

    assert "factory_app/workflows/_shared/ui/" in agents
    assert "each consuming workflow must re-export/register them from its own" in agents
    assert "Do not import UI from a sibling workflow folder" in agents


def test_agent_guidance_requires_architecture_first_review() -> None:
    agents = _read("AGENTS.md")
    quick_reference = _read("docs/architecture/ARCHITECTURE_QUICK_REFERENCE.md")

    for text in (agents, quick_reference):
        assert "Required Pre-Edit Architecture Check" in text
        assert "ARCHITECTURE_QUICK_REFERENCE.md" in text or "read this quick reference" in text
        assert "MOZAIKS_OSS_SOFTWARE_DESIGN.md" in text
        assert "current source" in text.lower()
        assert "final authority" in text.lower()
        assert "parallel subsystem" in text

    assert "architectural changes that contradict the frozen north star require an ADR" in agents


def test_refinement_guidance_states_checkpoint_reentry_truth() -> None:
    architecture = _read("ARCHITECTURE.md")
    refinement_harness = _read("docs/architecture/workflows/refinement-harness-architecture.md")

    assert "checkpoint-driven re-entry" in architecture
    assert "not a dedicated `RefinementWorkflow`" in architecture
    assert "Normal chat/workflow startup comes" in refinement_harness
    assert "app/config/refinement_policy.yaml" in refinement_harness
    assert "refinement_harness/config/harness.yaml" in refinement_harness
    assert "Do not document a dedicated `RefinementWorkflow`" in refinement_harness


def test_control_plane_guide_states_runtime_split_and_generation_ownership() -> None:
    guide = _read("docs/guides/extending-ai-functionality/01-overview.md")

    assert "app/config/ai.json" in guide
    assert "app/config/refinement_policy.yaml" in guide
    assert "refinement_harness/config/harness.yaml" in guide
    assert "`ValueEngine` may hint" in guide
    assert "`DesignDocs` decides whether `surface_kind = refinement`" in guide
    assert "`AppGenerator` materializes" in guide
    assert "`AgentGenerator` stays responsible for workflow bundles" in guide


def test_create_workflow_skill_uses_current_contract_fields() -> None:
    skill = _read(".claude/skills/create-workflow/SKILL.md")

    assert "workflow_startup_mode:" in skill
    assert not re.search(r"(?m)^startup_mode\s*:", skill)
    assert "registry:" in skill
    assert "models:" in skill
    assert not re.search(r"(?m)^structured_outputs\s*:", skill)


def test_create_workflow_skill_distinguishes_routing_layers() -> None:
    skill = _read(".claude/skills/create-workflow/SKILL.md")

    assert "`transition_graph.yaml`" in skill
    assert "`workflow_sequences[]`" in skill
    assert "`transitions[]`" in skill
    assert "`entrypoints[]`" in skill


def test_setup_and_chat_ui_guidance_use_web_shell_and_factory_app() -> None:
    setup_skill = _read(".claude/skills/setup/SKILL.md")
    add_feature_skill = _read(".claude/skills/add-feature/SKILL.md")
    web_shell_readme = _read("web_shell/README.md")

    assert "web_shell" in setup_skill
    assert "run-studio.ps1" in setup_skill
    assert "factory_app/app" in setup_skill
    assert "run-frontend.ps1" in add_feature_skill
    assert "factory_app/app" in add_feature_skill
    assert "run-studio.ps1" in web_shell_readme
    assert "factory_app/app" in web_shell_readme


def test_module_guidance_uses_canonical_reactions_contract() -> None:
    add_module_skill = _read(".claude/skills/add-module/SKILL.md")
    module_guide = _read("docs/guides/adding-modules/01-overview.md")

    for text in (add_module_skill, module_guide):
        assert "contracts/reactions.yaml" in text
        assert "Use `contracts/reactions.yaml` as the canonical event-reaction contract." in text
        assert "runtime rejects `contracts/subscriptions.yaml`" in text.lower()
        assert "Do not introduce both forms in the same change." not in text


def test_new_rules_capture_public_framing_and_current_vs_target_guardrails() -> None:
    oss_rule = _read(".claude/rules/oss-contributor-framing.md")
    current_vs_target_rule = _read(".claude/rules/current-vs-target-contracts.md")
    build_rule = _read(".claude/rules/build-refinement-truth.md")

    assert "Do not present `App Zero` as a required public concept." in oss_rule
    assert "Do not present `mozaiks-app` or any private hosted-product repo" in oss_rule
    assert "Do not present aspirational contracts as current" in current_vs_target_rule
    assert "`AppGenerator` and `AgentGenerator` are individual workflows inside the build" in build_rule
    assert "`ExistingAppDiscovery` is the brownfield/existing-app adoption workflow path" in build_rule
    assert "a dedicated `RefinementWorkflow`" in build_rule
    assert "app/config/refinement_policy.yaml" in build_rule


def test_release_notes_guidance_uses_generic_hosted_product_language() -> None:
    skill = _read(".claude/skills/release-notes/SKILL.md")
    rule = _read(".claude/rules/release-notes.md")

    assert "private hosted-product release notes" in skill
    assert "private hosted-product release notes" in rule
    assert "mozaiks-app" not in skill
    assert "mozaiks-app" not in rule
