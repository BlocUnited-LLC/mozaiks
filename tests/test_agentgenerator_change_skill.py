from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_agentgenerator_change_skill_exists_and_states_current_truth() -> None:
    skill = _read(".claude/skills/agentgenerator-change/SKILL.md")

    assert "AgentGenerator is one workflow inside the broader factory build `workflow_sequence`." in skill
    assert "It generates workflow and agent bundles, not app modules or persistent app pages." in skill
    assert "AppGenerator generates app bundle artifacts; AgentGenerator generates AI workflow artifacts." in skill


def test_agentgenerator_change_skill_references_key_contract_anchors() -> None:
    skill = _read(".claude/skills/agentgenerator-change/SKILL.md")

    assert "tools/hook_universal_prompts.py" in skill
    assert "structured_outputs.yaml" in skill
    assert "tools/tool_planning.py" in skill
    assert "docs/architecture/workflows/workflow-authoring-contracts.md" in skill


def test_agentgenerator_change_skill_forbids_stale_or_private_generation_patterns() -> None:
    skill = _read(".claude/skills/agentgenerator-change/SKILL.md")

    assert "Do not inject private hosted-product names, assumptions, or proprietary examples" in skill
    assert "Do not use stale workflow fields like `startup_mode` when the current contract uses `workflow_startup_mode`." in skill
    assert "Do not use `workflow_sequence` as a HITL substitute." in skill


def test_agentgenerator_change_skill_requires_workflow_impact_reporting() -> None:
    skill = _read(".claude/skills/agentgenerator-change/SKILL.md")

    assert "## AgentGenerator Workflow Impact" in skill
    assert "AgentGenerator component changed" in skill
    assert "generated workflow artifacts affected" in skill
    assert "workflow authoring contract affected" in skill
    assert "universal prompts or hooks affected" in skill
    assert "tests run" in skill
    assert "contract drift risk" in skill
    assert "OSS Change Impact" in skill


def test_agentgenerator_change_skill_is_routed_from_index_and_quickstart() -> None:
    skills = _read(".claude/skills/README.md")
    quickstart = _read("CONTRIBUTING.md")

    assert "AgentGenerator-specific change | `agentgenerator-change`" in skills
    assert "AgentGenerator-specific change: use `agentgenerator-change`" in quickstart
    assert "Use `agentgenerator-change` for AgentGenerator-local changes." in skills
    assert "Add `factory-build-workflow-change` only when the change also affects `workflow_sequence`" in skills


def test_agentgenerator_guidance_stays_public_safe() -> None:
    for relative_path in [
        ".claude/skills/agentgenerator-change/SKILL.md",
        ".claude/skills/README.md",
        "CONTRIBUTING.md",
    ]:
        text = _read(relative_path)
        assert "App Zero" not in text, relative_path
        assert "App-zero" not in text, relative_path
        assert "mozaiks-app" not in text, relative_path

    skill = _read(".claude/skills/agentgenerator-change/SKILL.md")
    for forbidden in ["Stripe", "AWS", "Azure", "wallet", "investor"]:
        assert forbidden not in skill, forbidden
