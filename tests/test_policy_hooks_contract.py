from __future__ import annotations

from pathlib import Path

import pytest


def test_policy_hook_provider_discovery_returns_empty_for_no_modules(tmp_path: Path) -> None:
    from mozaiksai.core.policy_hooks.discovery import load_policy_hook_providers

    assert load_policy_hook_providers(tmp_path) == []
    (tmp_path / "modules").mkdir()
    assert load_policy_hook_providers(tmp_path) == []


def test_policy_hook_provider_discovery_loads_valid_provider(tmp_path: Path) -> None:
    from mozaiksai.core.policy_hooks.discovery import load_policy_hook_providers

    module_dir = tmp_path / "modules" / "project_membership"
    (module_dir / "contracts").mkdir(parents=True)
    (module_dir / "contracts" / "policy_hooks.yaml").write_text(
        """
schema_version: mozaiks.policy_hooks.v1
hooks:
  - id: project-participation
    label: Project Participation
    description: Evaluates current-user project participation inputs.
    order: 20
    hook_type: decision_input
    action: evaluate_project_participation
    resource_types: [project]
    input_schema:
      type: object
    output_schema:
      type: object
    deterministic: true
""".lstrip(),
        encoding="utf-8",
    )

    providers = load_policy_hook_providers(tmp_path)

    assert len(providers) == 1
    assert providers[0]["id"] == "project-participation"
    assert providers[0]["module_id"] == "project_membership"
    assert providers[0]["hook_type"] == "decision_input"
    assert providers[0]["action"] == "evaluate_project_participation"
    assert providers[0]["resource_types"] == ["project"]


def test_policy_hook_provider_discovery_sorts_by_order(tmp_path: Path) -> None:
    from mozaiksai.core.policy_hooks.discovery import load_policy_hook_providers

    for module_id, order in [("zeta", 50), ("alpha", 20)]:
        module_dir = tmp_path / "modules" / module_id
        (module_dir / "contracts").mkdir(parents=True)
        (module_dir / "contracts" / "policy_hooks.yaml").write_text(
            f"""
schema_version: mozaiks.policy_hooks.v1
hooks:
  - id: {module_id}-policy
    label: {module_id.title()} Policy
    order: {order}
    hook_type: access
    action: evaluate_access
    resource_types: [thing]
""".lstrip(),
            encoding="utf-8",
        )

    providers = load_policy_hook_providers(tmp_path)

    assert [provider["module_id"] for provider in providers] == ["alpha", "zeta"]


def test_policy_hooks_manifest_rejects_duplicate_hook_ids() -> None:
    from pydantic import ValidationError

    from mozaiksai.core.runtime.app.module_loader import ModulePolicyHooksManifest

    with pytest.raises(ValidationError, match="unique id"):
        ModulePolicyHooksManifest.model_validate(
            {
                "schema_version": "mozaiks.policy_hooks.v1",
                "hooks": [
                    {
                        "id": "dup",
                        "label": "One",
                        "hook_type": "access",
                        "action": "evaluate_one",
                        "resource_types": ["app"],
                    },
                    {
                        "id": "dup",
                        "label": "Two",
                        "hook_type": "classification",
                        "action": "evaluate_two",
                        "resource_types": ["app"],
                    },
                ],
            }
        )


def test_policy_hooks_manifest_requires_resource_types() -> None:
    from pydantic import ValidationError

    from mozaiksai.core.runtime.app.module_loader import ModulePolicyHooksManifest

    with pytest.raises(ValidationError, match="resource_type"):
        ModulePolicyHooksManifest.model_validate(
            {
                "schema_version": "mozaiks.policy_hooks.v1",
                "hooks": [
                    {
                        "id": "access",
                        "label": "Access",
                        "hook_type": "access",
                        "action": "evaluate_access",
                        "resource_types": [],
                    },
                ],
            }
        )


def test_policy_hooks_manifest_rejects_unknown_hook_type() -> None:
    from pydantic import ValidationError

    from mozaiksai.core.runtime.app.module_loader import ModulePolicyHooksManifest

    with pytest.raises(ValidationError, match="literal"):
        ModulePolicyHooksManifest.model_validate(
            {
                "schema_version": "mozaiks.policy_hooks.v1",
                "hooks": [
                    {
                        "id": "score",
                        "label": "Score",
                        "hook_type": "governance_revenue_split",
                        "action": "evaluate_score",
                        "resource_types": ["app"],
                    },
                ],
            }
        )


def test_module_loader_exposes_policy_hooks_manifest(tmp_path: Path) -> None:
    from mozaiksai.core.runtime.app.module_loader import ModuleLoader
    from tests.test_module_loader_contracts import _write_canonical_module

    module_dir = _write_canonical_module(tmp_path)
    module_dir.joinpath("contracts", "policy_hooks.yaml").write_text(
        """
schema_version: mozaiks.policy_hooks.v1
hooks:
  - id: task-access
    label: Task Access
    action: create
    hook_type: access
    resource_types: [task]
""".lstrip(),
        encoding="utf-8",
    )

    loaded = ModuleLoader(str(tmp_path)).load("tasks")

    assert loaded.manifests.policy_hooks is not None
    assert loaded.manifests.policy_hooks.hooks[0].id == "task-access"
