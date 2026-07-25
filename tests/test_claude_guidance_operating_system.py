from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_new_rule_files_cover_requested_contributor_layers() -> None:
    architecture_boundaries = _read(".claude/rules/architecture-boundaries.md")
    build_rule = _read(".claude/rules/factory-build-workflows.md")
    control_plane_rule = _read(".claude/rules/control-plane-refinement.md")
    modules_rule = _read(".claude/rules/modules.md")
    frontend_rule = _read(".claude/rules/frontend.md")
    persistence_rule = _read(".claude/rules/persistence.md")
    managed_rule = _read(".claude/rules/managed-capabilities.md")
    testing_rule = _read(".claude/rules/testing.md")

    assert "universal substrate" in architecture_boundaries
    assert "framework capability" in architecture_boundaries
    assert "hosted product capability" in architecture_boundaries
    assert "first-party builder/reference app workspace" in architecture_boundaries

    assert "workflow_sequence" in build_rule
    assert "AppGenerator" in build_rule
    assert "not the whole build system" in build_rule
    assert "ValueEngine" in build_rule
    assert "ThemeCapture" in build_rule
    assert "DesignDocs" in build_rule
    assert "AgentGenerator" in build_rule
    assert "ExistingAppDiscovery" in build_rule
    assert "transition_graph.yaml" in build_rule
    assert "transitions[]" in build_rule
    assert "entrypoints[]" in build_rule

    assert "checkpoint/control-plane re-entry" in control_plane_rule
    assert "RefinementWorkflow" in control_plane_rule
    assert "app/config/refinement_policy.yaml" in control_plane_rule
    assert "refinement_harness/config/harness.yaml" in control_plane_rule
    assert "patch" in control_plane_rule
    assert "design" in control_plane_rule
    assert "feature" in control_plane_rule
    assert "core" in control_plane_rule
    assert "workflow_sequence" in control_plane_rule

    assert "contracts/reactions.yaml" in modules_rule
    assert "backend/schemas.py" in modules_rule
    assert "backend/models.py" in modules_rule
    assert "ctx.persistence.collection(module_id, entity_name)" in modules_rule

    assert "Admin UI Two-Tier Model" in frontend_rule
    assert "app/brand/theme_config.json" in frontend_rule
    assert "admin/admin_registry.yaml" in frontend_rule
    assert "route_manifest.json" in frontend_rule
    assert "admin/index.js" in frontend_rule

    assert "data/contract.json" in persistence_rule
    assert "data/migrations" in persistence_rule
    assert "ModuleContext.persistence" in persistence_rule
    assert "ctx.db" in persistence_rule

    assert "managed_capability" in managed_rule
    assert "external_adapter" in managed_rule
    assert "facade" in managed_rule
    assert "provider-neutral" in managed_rule
    assert "Do not copy managed" in managed_rule

    assert "OSS Change Impact" in testing_rule
    assert "Build Workflow Sequence Impact" in testing_rule
    assert "Control-Plane / Refinement Impact" in testing_rule
    assert "Module Contract Impact" in testing_rule
    assert "Managed Capability Boundary Check" in testing_rule
    assert "Tests run" in testing_rule
    assert "Compatibility risk" in testing_rule


def test_skills_readme_maps_requested_task_types() -> None:
    readme = _read(".claude/skills/README.md")

    assert "runtime-architecture-review" in readme
    assert "build-sequence-change" in readme
    assert "appgenerator-change" in readme
    assert "agentgenerator-change" in readme
    assert "existing-app-discovery-change" in readme
    assert "control-plane-refinement-change" in readme
    assert "add-module" in readme
    assert "add-page" in readme
    assert "create-workflow" in readme
    assert "docs-maintenance" in readme
    assert "persistence-change" in readme
    assert "release-notes" in readme
    assert "managed-capability-change" in readme
    assert "oss-contribution-review" in readme
    assert "planned" in readme
    assert "AppGenerator is one workflow inside the build sequence" in readme


def test_high_value_skill_stubs_anchor_current_truth() -> None:
    oss_review = _read(".claude/skills/oss-contribution-review/SKILL.md")
    build_skill = _read(".claude/skills/build-sequence-change/SKILL.md")
    control_plane_skill = _read(".claude/skills/control-plane-refinement-change/SKILL.md")
    persistence_skill = _read(".claude/skills/persistence-change/SKILL.md")

    assert "universal substrate" in oss_review
    assert "framework capability" in oss_review
    assert "hosted product capability" in oss_review

    assert "workflow_sequence" in build_skill
    assert "AppGenerator" in build_skill
    assert "ExistingAppDiscovery" in build_skill
    assert "transitions[]" in build_skill
    assert "transition_graph.yaml" in build_skill
    assert "entrypoints[]" in build_skill

    assert "app/config/ai.json" in control_plane_skill
    assert "refinement_policy.yaml" in control_plane_skill
    assert "refinement_harness/config/harness.yaml" in control_plane_skill
    assert "RefinementWorkflow" in control_plane_skill
    assert "factory_control_plane" in control_plane_skill

    assert "data_contract" in persistence_skill
    assert "data/contract.json" in persistence_skill
    assert "ModuleContext.persistence" in persistence_skill
    assert "ctx.db" in persistence_skill
    assert "backend/models.py" in persistence_skill


def test_no_private_repo_or_app_zero_language_in_new_guidance_surfaces() -> None:
    for relative_path in [
        ".claude/rules/architecture-boundaries.md",
        ".claude/rules/factory-build-workflows.md",
        ".claude/rules/control-plane-refinement.md",
        ".claude/rules/persistence.md",
        ".claude/rules/managed-capabilities.md",
        ".claude/rules/testing.md",
        ".claude/skills/README.md",
        ".claude/skills/oss-contribution-review/SKILL.md",
        ".claude/skills/build-sequence-change/SKILL.md",
        ".claude/skills/control-plane-refinement-change/SKILL.md",
        ".claude/skills/persistence-change/SKILL.md",
        "factory_app/refinement_harness/config/harness.yaml",
        "factory_app/refinement_harness/__init__.py",
    ]:
        text = _read(relative_path)
        assert "App Zero" not in text, relative_path
        assert "App-zero" not in text, relative_path
        assert "mozaiks-app" not in text, relative_path


