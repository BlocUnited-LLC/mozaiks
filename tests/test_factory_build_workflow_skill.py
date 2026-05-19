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

    assert "`workflow_sequence`, `transitions[]`, `entrypoints[]`, and workflow-local `handoffs.yaml` are different mechanisms" in skill
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


def test_factory_build_workflow_skill_is_routed_from_index_and_quickstart() -> None:
    skills = _read(".claude/skills/README.md")
    quickstart = _read("CONTRIBUTING.md")

    assert "| Build sequence / factory workflow changes | `factory-build-workflow-change` |" in skills
    assert "AppGenerator-specific change | `factory-build-workflow-change`" in skills
    assert "AgentGenerator-specific change | `factory-build-workflow-change`" in skills
    assert "ExistingAppDiscovery change | `factory-build-workflow-change`" in skills
    assert "Build workflow sequence change: use `factory-build-workflow-change`" in quickstart
    assert "AppGenerator-specific change: use `factory-build-workflow-change`" in quickstart
    assert "AgentGenerator-specific change: use `factory-build-workflow-change`" in quickstart
    assert "ExistingAppDiscovery or brownfield change: use `factory-build-workflow-change`" in quickstart


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
    for forbidden in ["Stripe", "AWS", "Azure", "wallet", "investor"]:
        assert forbidden not in skill, forbidden