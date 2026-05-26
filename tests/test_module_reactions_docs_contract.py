from __future__ import annotations

from pathlib import Path


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def test_module_reactions_docs_state_canonical_contract() -> None:
    module_system = _read("docs/architecture/modules-systems/module-system.md")
    event_system = _read("docs/architecture/foundations/events-and-data/event-system.md")
    event_contracts = _read("docs/architecture/foundations/events-and-data/event-contracts.md")
    app_bundle = _read("docs/architecture/app/app-bundle-declaratives.md")
    builder_contract = _read("docs/architecture/builder/appgenerator-output-assembly-contract.md")
    add_module_skill = _read(".claude/skills/add-module/SKILL.md")
    module_rules = _read(".claude/rules/modules.md")

    combined = "\n".join(
        [
            module_system,
            event_system,
            event_contracts,
            app_bundle,
            builder_contract,
            add_module_skill,
            module_rules,
        ]
    )

    assert "contracts/reactions.yaml" in module_system
    assert "schema_version: mozaiks.reactions.v1" in event_system
    assert "Runtime rejects it" in combined
    assert "Do not author modules with `contracts/subscriptions.yaml`." in event_contracts
    assert "`contracts/subscriptions.yaml` is the canonical" not in combined
    assert "event_type:" in combined
    assert "target:\n      kind: handler" in combined
    assert "domain.tasks.task.completed" in combined
    assert "update_project_progress" in combined
    assert "contracts/reactions.yaml" in add_module_skill
    assert "backend/schemas.py" in app_bundle
    assert "backend/models.py" in builder_contract
