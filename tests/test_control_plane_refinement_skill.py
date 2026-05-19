from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_control_plane_refinement_skill_exists_and_states_current_truth() -> None:
    skill = _read(".claude/skills/control-plane-refinement-change/SKILL.md")

    assert "checkpoint/control-plane re-entry" in skill
    assert "not a dedicated\n  `RefinementWorkflow`" in skill or "not a dedicated `RefinementWorkflow`" in skill
    assert "control_plane.yaml" in skill
    assert "app/config/ai.json" in skill
    assert "affected_declarative_families" in skill
    assert "factory_control_plane" in skill


def test_control_plane_refinement_skill_distinguishes_routing_layers() -> None:
    skill = _read(".claude/skills/control-plane-refinement-change/SKILL.md")

    assert "`workflow_sequence` / `workflow_sequences[]`" in skill
    assert "`transitions[]`" in skill
    assert "`entrypoints[]`" in skill
    assert "`handoffs.yaml`" in skill
    assert "`routing.artifacts[]`" in skill
    assert "`checkpoints[]`" in skill


def test_control_plane_refinement_skill_requires_focused_tests_and_reporting() -> None:
    skill = _read(".claude/skills/control-plane-refinement-change/SKILL.md")

    assert "tests/test_control_plane_loader.py" in skill
    assert "tests/test_refinement_router.py" in skill
    assert "tests/test_pack_config_paths.py" in skill
    assert "tests/test_build_lifecycle_hooks.py" in skill
    assert "Control-Plane / Refinement Impact" in skill
    assert "OSS Change Impact" in skill


def test_control_plane_refinement_skill_is_routed_and_public_safe() -> None:
    skills = _read(".claude/skills/README.md")
    quickstart = _read("CONTRIBUTING.md")
    skill = _read(".claude/skills/control-plane-refinement-change/SKILL.md")

    assert "| Control-plane / refinement / harness routing | `control-plane-refinement-change` |" in skills
    assert "Control-plane or refinement change: use `control-plane-refinement-change`" in quickstart

    for text, label in [
        (skills, ".claude/skills/README.md"),
        (quickstart, "CONTRIBUTING.md"),
        (skill, ".claude/skills/control-plane-refinement-change/SKILL.md"),
    ]:
        assert "App Zero" not in text, label
        assert "App-zero" not in text, label
        assert "mozaiks-app" not in text, label

    for forbidden in ["Stripe", "AWS", "Azure", "wallet", "investor"]:
        assert forbidden not in skill, forbidden