from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_appgenerator_change_skill_exists_and_states_current_truth() -> None:
    skill = _read(".claude/skills/appgenerator-change/SKILL.md")

    assert "AppGenerator is the final app-bundle generation workflow inside the broader factory `workflow_sequence`." in skill
    assert "It emits canonical app workspace artifacts." in skill
    assert "It does not own product strategy, concept formation, brand discovery, or workflow generation." in skill


def test_appgenerator_change_skill_references_key_contract_anchors() -> None:
    skill = _read(".claude/skills/appgenerator-change/SKILL.md")

    assert "tools/file_contracts.yaml" in skill
    assert "tools/app_build_plan.py" in skill
    assert "generated_ui_contract.py" in skill
    assert "tools/domain_catalogs.yaml" in skill
    assert "tools/module_archetypes.yaml" in skill


def test_appgenerator_change_skill_forbids_drift_paths_and_runtime_leaks() -> None:
    skill = _read(".claude/skills/appgenerator-change/SKILL.md")

    assert "Do not generate `backend/models.py` or `backend/models/*.py`." in skill
    assert "Do not generate `contracts/subscriptions.yaml` as the canonical reaction contract." in skill
    assert "Do not generate `app/capability_packs/`." in skill
    assert "Do not bind pages directly to hosted-pack internals; use the app-owned facade module pattern." in skill
    assert "Do not assume `ctx.db`" in skill


def test_appgenerator_change_skill_requires_workflow_impact_reporting() -> None:
    skill = _read(".claude/skills/appgenerator-change/SKILL.md")

    assert "## AppGenerator Workflow Impact" in skill
    assert "AppGenerator component changed" in skill
    assert "upstream build sequence assumptions" in skill
    assert "generated artifacts affected" in skill
    assert "runtime/platform contracts affected" in skill
    assert "tests run" in skill
    assert "contract drift risk" in skill
    assert "OSS Change Impact" in skill


def test_appgenerator_change_skill_is_routed_from_index_and_quickstart() -> None:
    skills = _read(".claude/skills/README.md")
    quickstart = _read("CONTRIBUTING.md")

    assert "AppGenerator-specific change | `appgenerator-change`" in skills
    assert "AppGenerator-specific change: use `appgenerator-change`" in quickstart
    assert "Use `appgenerator-change` for AppGenerator-local changes." in skills
    assert "Add `factory-build-workflow-change` only when the change also affects `workflow_sequence`" in skills


def test_appgenerator_guidance_stays_public_safe() -> None:
    for relative_path in [
        ".claude/skills/appgenerator-change/SKILL.md",
        ".claude/skills/README.md",
        "CONTRIBUTING.md",
    ]:
        text = _read(relative_path)
        assert "App Zero" not in text, relative_path
        assert "App-zero" not in text, relative_path
        assert "mozaiks-app" not in text, relative_path

    skill = _read(".claude/skills/appgenerator-change/SKILL.md")
    for forbidden in ["Stripe", "AWS", "Azure", "wallet", "investor"]:
        assert forbidden not in skill, forbidden
