from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_auth_docs_release_and_cli_routes_are_explicit() -> None:
    skills = _read(".claude/skills/README.md")
    quickstart = _read("CONTRIBUTING.md")

    assert "Auth change | `runtime-change`" in skills
    assert "Auth change: use `runtime-change`" in quickstart

    assert "Docs-only change | `docs-maintenance`" in skills
    assert "Docs-only change: use `docs-maintenance`." in quickstart

    assert "Release/changelog change | `release-notes`" in skills
    assert "Release/changelog change: use `release-notes`." in quickstart

    assert "CLI change | `oss-contribution-review`" in skills
    assert "CLI change: use `oss-contribution-review` for now." in quickstart


def test_test_only_change_routes_to_the_owner_or_review() -> None:
    skills = _read(".claude/skills/README.md")
    quickstart = _read("CONTRIBUTING.md")

    assert "Test-only change | owning surface skill |" in skills
    assert "If unclear, start with `oss-contribution-review`." in skills

    assert "Test-only change: use the owning surface skill when obvious." in quickstart
    assert "Runtime tests go to `runtime-change`" in quickstart
    assert "AppGenerator tests go to `appgenerator-change`" in quickstart
    assert "workflow sequence tests go to `factory-build-workflow-change`" in quickstart
    assert "If the owner is unclear, use `oss-contribution-review`." in quickstart


def test_admin_ui_and_module_contract_routes_are_explicit() -> None:
    skills = _read(".claude/skills/README.md")
    quickstart = _read("CONTRIBUTING.md")
    frontend_rule = _read(".claude/rules/frontend.md")

    assert "Admin UI change | `add-page` |" in skills
    assert "AdminPortal schema panels" in skills
    assert "custom operator/admin React pages" in skills
    assert "Admin UI change: use `add-page` plus the frontend rule" in quickstart
    assert "AdminPortal schema panels" in quickstart
    assert "custom operator React routes" in quickstart
    assert "Admin UI Two-Tier Model" in frontend_rule

    assert "Module contract change | `add-module` |" in skills
    assert "Use `runtime-change` when module loader/executor/runtime behavior changes." in skills
    assert "Use `appgenerator-change` when generated module output changes." in skills
    assert "Module contract change: use `add-module`" in quickstart
    assert "Use `runtime-change` if module loader, executor, or runtime behavior changes." in quickstart
    assert "Use `appgenerator-change` if generated module output changes." in quickstart


def test_hosted_pack_support_route_is_explicit_without_new_skill() -> None:
    skills = _read(".claude/skills/README.md")
    quickstart = _read("CONTRIBUTING.md")

    assert "Hosted-pack support | `oss-contribution-review` |" in skills
    assert "No dedicated `hosted-pack-change` skill exists yet." in skills
    assert "`hosted-pack-change` remains planned." in skills
    assert "Hosted-pack support change: use `oss-contribution-review` plus the hosted-packs rule for now; no dedicated `hosted-pack-change` skill exists yet." in quickstart
    assert "- `hosted-pack-change`" in skills


def test_local_workflow_skills_stay_primary_and_factory_build_is_companion() -> None:
    skills = _read(".claude/skills/README.md")
    quickstart = _read("CONTRIBUTING.md")

    assert "Use `appgenerator-change` for AppGenerator-local changes." in skills
    assert "Use `agentgenerator-change` for AgentGenerator-local changes." in skills
    assert "Use `existing-app-discovery-change` for ExistingAppDiscovery-local changes." in skills
    assert "Add `factory-build-workflow-change` only when the change also affects `workflow_sequence`" in skills
    assert "Add `factory-build-workflow-change` only when the change also affects `extension_registry.json`, `workflow_sequence`, `transitions[]`, `entrypoints[]`, or cross-workflow sequence composition." in skills

    assert "AppGenerator-specific change: use `appgenerator-change`" in quickstart
    assert "AgentGenerator-specific change: use `agentgenerator-change`" in quickstart
    assert "ExistingAppDiscovery or brownfield change: use `existing-app-discovery-change`" in quickstart
    assert "Add `factory-build-workflow-change` as a companion skill only when the change widens into `extension_registry.json`, sequence design, transitions, entrypoints, or cross-workflow factory composition." in quickstart


def test_control_plane_and_persistence_companion_notes_are_explicit() -> None:
    control_plane_skill = _read(".claude/skills/control-plane-refinement-change/SKILL.md")
    persistence_skill = _read(".claude/skills/persistence-change/SKILL.md")

    assert "Add `factory-build-workflow-change` as a companion skill when the change also alters `factory_app/workflows/extended_orchestration/extension_registry.json`" in control_plane_skill
    assert "Add `runtime-change` when `ModuleContext.persistence`, runtime persistence injection, or runtime persistence behavior changes." in persistence_skill
    assert "Add `appgenerator-change` when generated `data/contract.json`, `data/migrations/{migration_id}.json`, or generated module persistence output changes." in persistence_skill

