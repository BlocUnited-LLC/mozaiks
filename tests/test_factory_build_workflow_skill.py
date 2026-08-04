from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_factory_build_workflow_skill_exists_and_states_build_truth() -> None:
    skill = _read(".claude/skills/factory-build-workflow-change/SKILL.md")

    assert "build is `workflow_sequence`-driven" in skill
    assert "`AppGenerator` is one workflow in the build sequence, not the whole build system" in skill
    assert "`AgentGenerator` is one workflow in the build sequence, not the whole build system" in skill
    assert "`ExistingAppDiscovery` belongs to the brownfield or existing-app adoption sequence" in skill


def test_factory_build_workflow_skill_distinguishes_routing_mechanisms_and_human_review_boundary() -> None:
    skill = _read(".claude/skills/factory-build-workflow-change/SKILL.md")

    assert "`workflow_sequence`, `transitions[]`, `entrypoints[]`, and workflow-local `transition_graph.yaml` are different mechanisms" in skill
    assert "`workflow_sequence` auto-advance must not be used as a human review or HITL boundary" in skill
    assert "Do not add `workflow_sequence` steps for operator-review or HITL checkpoints" in skill


def test_factory_build_workflow_skill_covers_boundaries_reports_and_tests() -> None:
    skill = _read(".claude/skills/factory-build-workflow-change/SKILL.md")

    assert "Do not change runtime or platform behavior from this skill" in skill
    assert "Do not add private hosted-product logic" in skill
    assert "Build Workflow Sequence Impact" in skill
    assert "OSS Change Impact" in skill
    assert "tests/test_existing_app_discovery_contracts.py" in skill
    assert "tests/test_agentgenerator_workflow_converter.py" in skill
    assert "tests/test_appgenerator_canonical_generation.py" in skill
    assert "tests/test_control_plane_loader.py" in skill
    assert "tests/test_build_lifecycle_hooks.py" in skill


def test_factory_build_workflow_skill_documents_shared_workflow_ui_lane() -> None:
    skill = _read(".claude/skills/factory-build-workflow-change/SKILL.md")
    codex_skill = _read(".agents/skills/factory-build-workflow-change/SKILL.md")
    rule = _read(".claude/rules/factory-build-workflows.md")

    for content in (skill, codex_skill, rule):
        assert "factory_app/workflows/_shared/ui/" in content
        assert "Shared workflow UI is not auto-registered" in content
        assert "Do not import UI from a sibling workflow folder" in content

    assert "Changing shared workflow UI" in skill
    assert "tests/test_workflow_ui_tool_contracts.py" in skill


def test_factory_build_workflow_skill_is_routed_from_index_and_quickstart() -> None:
    skills = _read(".claude/skills/README.md")
    quickstart = _read("CONTRIBUTING.md")

    assert "| Build sequence / factory workflow changes | `factory-build-workflow-change` |" in skills
    assert "Build workflow sequence change: use `factory-build-workflow-change`" in quickstart


def test_factory_build_workflow_skill_stays_sequence_focused_when_appgenerator_has_its_own_skill() -> None:
    skills = _read(".claude/skills/README.md")
    quickstart = _read("CONTRIBUTING.md")

    assert "AppGenerator-specific change | `appgenerator-change`" in skills
    assert "Use `appgenerator-change` for AppGenerator-local changes." in skills
    assert "Add `factory-build-workflow-change` only when the change also affects `workflow_sequence`" in skills
    assert "AppGenerator-specific change: use `appgenerator-change`" in quickstart
    assert "Add `factory-build-workflow-change` as a companion skill only when" in quickstart


def test_factory_build_workflow_skill_stays_sequence_focused_when_agentgenerator_has_its_own_skill() -> None:
    skills = _read(".claude/skills/README.md")
    quickstart = _read("CONTRIBUTING.md")

    assert "AgentGenerator-specific change | `agentgenerator-change`" in skills
    assert "Use `agentgenerator-change` for AgentGenerator-local changes." in skills
    assert "Add `factory-build-workflow-change` only when the change also affects `workflow_sequence`" in skills
    assert "AgentGenerator-specific change: use `agentgenerator-change`" in quickstart
    assert "Add `factory-build-workflow-change` as a companion skill only when" in quickstart


def test_factory_build_workflow_skill_stays_sequence_focused_when_discovery_has_its_own_skill() -> None:
    skills = _read(".claude/skills/README.md")
    quickstart = _read("CONTRIBUTING.md")

    assert "ExistingAppDiscovery change | `existing-app-discovery-change`" in skills
    assert "Use `existing-app-discovery-change` for ExistingAppDiscovery-local changes." in skills
    assert "ExistingAppDiscovery or brownfield change: use `existing-app-discovery-change`" in quickstart
    assert "Add `factory-build-workflow-change` as a companion skill only when" in quickstart


def test_factory_build_workflow_guidance_stays_public_safe() -> None:
    for relative_path in [
        ".claude/skills/factory-build-workflow-change/SKILL.md",
        ".claude/skills/README.md",
        "CONTRIBUTING.md",
    ]:
        text = _read(relative_path)
        assert "App Zero" not in text, relative_path
        assert "App-zero" not in text, relative_path
        assert "mozaiks-app" not in text, relative_path

    skill = _read(".claude/skills/factory-build-workflow-change/SKILL.md")
    for forbidden in ["payment provider", "AWS", "Azure", "wallet", "investor"]:
        assert forbidden not in skill, forbidden

