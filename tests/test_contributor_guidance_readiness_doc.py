from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_contributor_guidance_readiness_doc_exists_and_is_linked() -> None:
    doc = _read("docs/contributing/contributor-guidance-readiness.md")
    index = _read("docs/contributing/index.md")
    mkdocs = _read("mkdocs.yml")

    assert "# OSS Contributor Guidance Readiness" in doc
    assert "Contributor Guidance Readiness" in index
    assert "contributing/contributor-guidance-readiness.md" in mkdocs


def test_contributor_guidance_readiness_doc_mentions_main_focused_skills() -> None:
    doc = _read("docs/contributing/contributor-guidance-readiness.md")

    for skill_name in [
        "runtime-change",
        "factory-build-workflow-change",
        "control-plane-refinement-change",
        "existing-app-discovery-change",
        "appgenerator-change",
        "agentgenerator-change",
    ]:
        assert skill_name in doc, skill_name


def test_contributor_guidance_readiness_doc_lists_known_deferrals_and_entry_points() -> None:
    doc = _read("docs/contributing/contributor-guidance-readiness.md")

    assert "No dedicated `hosted-pack-change` skill exists yet." in doc
    assert "No dedicated CLI-change skill exists yet." in doc
    assert "CONTRIBUTING.md" in doc
    assert ".claude/skills/README.md" in doc
    assert "Private hosted product repos are not contributor dependencies for this repo." in doc


def test_contributor_guidance_readiness_doc_mentions_guidance_tests() -> None:
    doc = _read("docs/contributing/contributor-guidance-readiness.md")

    for test_name in [
        "test_contributor_quickstart.py",
        "test_claude_guidance_operating_system.py",
        "test_runtime_change_skill.py",
        "test_factory_build_workflow_skill.py",
        "test_control_plane_refinement_skill.py",
        "test_existing_app_discovery_skill.py",
        "test_appgenerator_change_skill.py",
        "test_agentgenerator_change_skill.py",
        "test_contributor_skill_routing_map.py",
    ]:
        assert test_name in doc, test_name


def test_contributor_guidance_readiness_doc_stays_public_safe() -> None:
    doc = _read("docs/contributing/contributor-guidance-readiness.md")

    assert "mozaiks-app" not in doc
    assert "App Zero" not in doc
    assert "App-zero" not in doc

    for forbidden in ["Stripe", "AWS", "Azure", "wallet", "investor"]:
        assert forbidden not in doc, forbidden