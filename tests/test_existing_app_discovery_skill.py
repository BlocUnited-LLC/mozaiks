from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_existing_app_discovery_skill_exists_and_states_brownfield_truth() -> None:
    skill = _read(".claude/skills/existing-app-discovery-change/SKILL.md")

    assert "`ExistingAppDiscovery` belongs to the brownfield or existing-app adoption flow." in skill
    assert "It is not the default greenfield build path." in skill
    assert "It is not `AppGenerator`." in skill
    assert "Code truth wins" in skill


def test_existing_app_discovery_skill_covers_adoption_patterns_and_detection_surfaces() -> None:
    skill = _read(".claude/skills/existing-app-discovery-change/SKILL.md")

    assert "`embed`, `bridge`, `ecosystem`, and `native_migration`" in skill
    assert "storage, connector, auth, or security detection" in skill
    assert "Do not copy legacy code directly into generated apps" in skill
    assert "tests/test_existing_app_discovery_contracts.py" in skill
    assert "tests/test_existing_app_discovery_native_migration.py" in skill


def test_existing_app_discovery_skill_requires_brownfield_discovery_impact_report() -> None:
    skill = _read(".claude/skills/existing-app-discovery-change/SKILL.md")

    assert "## Brownfield Discovery Impact" in skill
    assert "Existing workflow affected" in skill
    assert "Adoption levels affected" in skill
    assert "Detectors affected" in skill
    assert "Artifacts affected" in skill
    assert "Tests run" in skill
    assert "Compatibility risk" in skill
    assert "OSS Change Impact" in skill
    assert "Build Workflow Sequence Impact" in skill


def test_existing_app_discovery_skill_is_routed_from_index_and_quickstart() -> None:
    skills = _read(".claude/skills/README.md")
    quickstart = _read("CONTRIBUTING.md")

    assert "ExistingAppDiscovery change | `existing-app-discovery-change`" in skills
    assert "Also use for brownfield or `native_migration` discovery changes." in skills
    assert "ExistingAppDiscovery or brownfield change: use `existing-app-discovery-change`" in quickstart


def test_existing_app_discovery_guidance_stays_public_safe() -> None:
    for relative_path in [
        ".claude/skills/existing-app-discovery-change/SKILL.md",
        ".claude/skills/README.md",
        "CONTRIBUTING.md",
    ]:
        text = _read(relative_path)
        assert "App Zero" not in text, relative_path
        assert "App-zero" not in text, relative_path
        assert "mozaiks-app" not in text, relative_path
