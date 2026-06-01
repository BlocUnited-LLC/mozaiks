from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_contributing_quickstart_links_to_guidance_system() -> None:
    quickstart = _read("CONTRIBUTING.md")

    assert "[.claude/skills/README.md](.claude/skills/README.md)" in quickstart
    assert "[AGENTS.md](AGENTS.md)" in quickstart
    assert "[CLAUDE.md](CLAUDE.md)" in quickstart
    assert "[.claude/rules/testing.md](.claude/rules/testing.md)" in quickstart


def test_contributing_quickstart_states_repo_and_build_truth() -> None:
    quickstart = _read("CONTRIBUTING.md")

    assert "OSS runtime, platform, Studio, and factory framework repo" in quickstart
    assert "first-party builder/reference app workspace" in quickstart
    assert "workflow_sequence" in quickstart
    assert "AppGenerator` is one workflow inside that build system, not the whole build" in quickstart
    assert "checkpoint and control-plane re-entry" in quickstart
    assert "RefinementWorkflow" in quickstart


def test_contributing_quickstart_contains_common_task_map() -> None:
    quickstart = _read("CONTRIBUTING.md")

    assert "Runtime or platform change" in quickstart
    assert "Auth change" in quickstart
    assert "Build workflow sequence change" in quickstart
    assert "AppGenerator-specific change" in quickstart
    assert "AgentGenerator-specific change" in quickstart
    assert "ExistingAppDiscovery or brownfield change" in quickstart
    assert "Control-plane or refinement change" in quickstart
    assert "Module contract change" in quickstart
    assert "Page or frontend change" in quickstart
    assert "Admin UI change" in quickstart
    assert "Persistence change" in quickstart
    assert "Docs-only change" in quickstart
    assert "Test-only change" in quickstart
    assert "CLI change" in quickstart
    assert "Release/changelog change" in quickstart
    assert "Hosted-pack support change" in quickstart
    assert "Unsure" in quickstart


def test_contributing_quickstart_mentions_final_report_sections() -> None:
    quickstart = _read("CONTRIBUTING.md")
    testing_rule = _read(".claude/rules/testing.md")

    assert "OSS Change Impact" in quickstart
    assert "Build Workflow Sequence Impact" in quickstart
    assert "Control-Plane / Refinement Impact" in quickstart
    assert "Module Contract Impact" in quickstart
    assert "Hosted Pack Boundary Check" in quickstart
    assert "Hosted Pack Boundary Check" in testing_rule


def test_contributing_quickstart_includes_focused_guidance_validation_command() -> None:
    quickstart = _read("CONTRIBUTING.md")

    assert "python -m pytest" in quickstart
    assert "tests/test_contributor_guidance_framing.py" in quickstart
    assert "tests/test_module_reactions_docs_contract.py" in quickstart
    assert "tests/test_admin_ui_two_tier_contract.py" in quickstart
    assert "tests/test_claude_guidance_operating_system.py" in quickstart
    assert "tests/test_contributor_quickstart.py" in quickstart
    assert "tests/test_runtime_change_skill.py" in quickstart
    assert "tests/test_factory_build_workflow_skill.py" in quickstart
    assert "tests/test_control_plane_refinement_skill.py" in quickstart
    assert "tests/test_contributor_skill_routing_map.py" in quickstart


def test_contributing_quickstart_avoids_private_or_stale_public_framing() -> None:
    quickstart = _read("CONTRIBUTING.md")

    assert "mozaiks-app" not in quickstart
    assert "App Zero" not in quickstart
    assert "App-zero" not in quickstart
    assert "backend/models.py" in quickstart
    assert "Do not reintroduce `backend/models.py` as canonical" in quickstart
    assert "contracts/subscriptions.yaml" in quickstart
    assert "Do not author `contracts/subscriptions.yaml`; use `contracts/reactions.yaml`." in quickstart
    assert "app/capability_packs" in quickstart
    assert "transport.py" in quickstart
    assert "direct hosted internals" in quickstart

    for forbidden in ["Stripe", "AWS", "Azure", "wallet", "investor"]:
        assert forbidden not in quickstart, forbidden


def test_skill_index_still_maps_common_tasks_for_quickstart_follow_through() -> None:
    skills = _read(".claude/skills/README.md")

    assert "Runtime/platform change" in skills
    assert "Auth change" in skills
    assert "Build sequence / extension registry / journey composition" in skills
    assert "AppGenerator-specific change" in skills
    assert "AgentGenerator-specific change" in skills
    assert "ExistingAppDiscovery change" in skills
    assert "Control-plane / refinement / harness routing" in skills
    assert "Module contract change" in skills
    assert "Admin UI change" in skills
    assert "Persistence / data contract / repo contract" in skills
    assert "Docs-only change" in skills
    assert "Test-only change" in skills
    assert "CLI change" in skills
    assert "Release/changelog change" in skills
    assert "Hosted-pack support" in skills
    assert "If unsure or scope spans layers" in skills
