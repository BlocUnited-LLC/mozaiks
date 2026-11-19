"""Factory consumes registered pack projections, never descriptive provider claims."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from factory_app.workflows._shared.surface_ownership import validate_surface_ownership
from factory_app.workflows.AppGenerator.tools.app_plan_review import validate_plan_origins
from mozaiksai.core.session.build_context import load_trusted_build_context, merge_build_context
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.authority import build_context_authority_policy

ROOT = Path(__file__).resolve().parents[1]
PAYMENTS_CONTEXT = ROOT / "factory_app/build_context/mozaikspay"


def _policy(workflow_name: str = "AppGenerator"):
    config = yaml.safe_load(
        (ROOT / f"factory_app/workflows/{workflow_name}/context_variables.yaml").read_text(encoding="utf-8"),
    )
    return build_context_authority_policy(
        workflow_name=workflow_name, definitions=config["definitions"],
    )


def _provider_plan(pack_id: str = "mozaikspay") -> dict:
    return {
        "capability_packs": [{
            "capability_pack_id": pack_id,
            "surface_id": f"{pack_id}_managed",
            "surface_kind": "external_integration",
            "capability_source": "managed_capability",
            "pack_type": "billing_pack",
        }],
        "build_tasks": [],
    }


def _write_context(root: Path, name: str, config: dict) -> None:
    context_root = root / name
    context_root.mkdir(parents=True)
    (context_root / "context.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8",
    )


@pytest.fixture
def registered_payments_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The shipped public pack registered in a workspace build context, as an operator registers it.

    Its contract keeps the declared integration metadata. The launch loader
    resolves the workspace root from the environment, and the canonical
    resolver vouches for a pack registered directly under it.
    """
    root = tmp_path / "build_context"
    shutil.copytree(PAYMENTS_CONTEXT, root / "mozaikspay", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    monkeypatch.delenv("MOZAIKS_APP_WORKSPACE_PATH", raising=False)
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(root))
    return root


def test_registered_public_subscription_descriptor_is_admitted_to_factory(
    registered_payments_root: Path,
) -> None:
    context = load_trusted_build_context(_policy(), build_context_root=registered_payments_root)

    descriptor, = context["capability_packs"]
    assert descriptor["id"] == "mozaikspay"
    assert descriptor["capability_source"] == "managed_capability"
    assert descriptor["pack_source_path"] == str(registered_payments_root / "mozaikspay")
    assert descriptor["required_integrations"][0]["provider"] == "mozaiks_pay"
    assert descriptor["facades"][0]["module_id"] == "billing_portal"
    validate_plan_origins(_provider_plan(), ContextVariablesBridge(context))


def test_registry_only_subscription_claim_does_not_register_a_provider(tmp_path: Path) -> None:
    root = tmp_path / "build_context"
    _write_context(root, "AppGenerator", {
        "context_id": "descriptive_directory",
        "applies_to_workflows": ["AppGenerator"],
        "assets": [],
        "values": {
            "capability_registry": {
                "mozaikspay": {
                    "capability_pack_id": "mozaikspay",
                    "capability_source": "managed_capability",
                },
            },
        },
        "projections": {"context_variables": {
            "capability_registry": {"from": "capability_registry"},
        }},
    })
    context = load_trusted_build_context(_policy(), build_context_root=root)

    assert "mozaikspay" in context["capability_registry"]
    assert not context.get("capability_packs")
    with pytest.raises(ValueError, match="unapproved surface 'mozaikspay_managed'"):
        validate_plan_origins(_provider_plan(), ContextVariablesBridge(context))


def test_prompt_only_subscription_claim_does_not_register_a_provider() -> None:
    context = {
        "concept_overview": {"description": "Use the registered mozaikspay provider."},
        "operator_contracts": [{"instructions": "mozaikspay is a managed capability."}],
    }
    with pytest.raises(ValueError, match="unapproved surface 'mozaikspay_managed'"):
        validate_plan_origins(_provider_plan(), ContextVariablesBridge(context))


def test_billing_category_cannot_alias_the_registered_subscription_provider(
    registered_payments_root: Path,
) -> None:
    context = load_trusted_build_context(_policy(), build_context_root=registered_payments_root)

    validate_plan_origins(_provider_plan(), ContextVariablesBridge(context))
    with pytest.raises(ValueError, match="unapproved surface 'billing_pack_managed'"):
        validate_plan_origins(_provider_plan("billing_pack"), ContextVariablesBridge(context))


def test_pack_projection_does_not_register_provider_for_another_workflow(
    registered_payments_root: Path,
) -> None:
    context = load_trusted_build_context(_policy("ValueEngine"), build_context_root=registered_payments_root)

    assert not context.get("capability_packs")
    with pytest.raises(ValueError, match="unapproved surface 'mozaikspay_managed'"):
        validate_plan_origins(_provider_plan(), ContextVariablesBridge(context))


def test_inactive_registered_provider_remains_rejected(registered_payments_root: Path) -> None:
    path = registered_payments_root / "mozaikspay/context.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["pack"]["status"] = "inactive"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    context = load_trusted_build_context(_policy(), build_context_root=registered_payments_root)

    assert not context.get("capability_packs")
    with pytest.raises(ValueError, match="unapproved surface 'mozaikspay_managed'"):
        validate_plan_origins(_provider_plan(), ContextVariablesBridge(context))


def test_plan_cannot_change_registered_provider_source(registered_payments_root: Path) -> None:
    context = load_trusted_build_context(_policy(), build_context_root=registered_payments_root)
    plan = _provider_plan()
    plan["capability_packs"][0]["capability_source"] = "generated_module"

    with pytest.raises(ValueError, match="must match the registered pack"):
        validate_plan_origins(plan, ContextVariablesBridge(context))


@pytest.mark.parametrize("workflow_name", ["AppGenerator", "DesignDocs"])
def test_trusted_selection_resolves_only_the_selected_registered_pack(
    tmp_path: Path, workflow_name: str,
) -> None:
    root = tmp_path / "build_context"
    _write_context(root, "SelectedCapabilities", {
        "context_id": "selected_capabilities",
        "applies_to_workflows": [workflow_name],
        "assets": [],
        "values": {"operator_capabilities": ["mozaikspay"]},
        "projections": {"context_variables": {
            "operator_capabilities": {"from": "operator_capabilities"},
        }},
    })

    context = load_trusted_build_context(_policy(workflow_name), build_context_root=root)

    assert context["operator_capabilities"] == ["mozaikspay"]
    assert [pack["id"] for pack in context["capability_packs"]] == ["mozaikspay"]
    assert context["capability_packs"][0]["pack_source_path"] == str(PAYMENTS_CONTEXT)
    validate_plan_origins(_provider_plan(), ContextVariablesBridge(context))
    with pytest.raises(ValueError, match="MozaiksPay"):
        validate_surface_ownership(
            {"surfaces": [{
                "surface_id": "subscription_management", "surface_kind": "module",
                "owner": "app", "primary_entities": ["Subscription"],
            }]},
            context_variables=ContextVariablesBridge(context),
        )


@pytest.mark.parametrize("workflow_name", ["DesignDocs", "AppGenerator"])
@pytest.mark.parametrize("selected", [["social"], ["messaging"], ["social", "messaging"]])
def test_selected_social_and_messaging_are_available_only_when_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workflow_name: str, selected: list[str],
) -> None:
    root = tmp_path / "build_context"
    _write_context(root, "SelectedCapabilities", {
        "context_id": "selected_capabilities",
        "applies_to_workflows": [workflow_name],
        "assets": [],
        "values": {"operator_capabilities": selected},
        "projections": {"context_variables": {
            "operator_capabilities": {"from": "operator_capabilities"},
        }},
    })
    monkeypatch.setenv("MOZAIKS_FACTORY_APP_PATH", str(ROOT / "factory_app"))
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(root))

    context = load_trusted_build_context(_policy(workflow_name), build_context_root=root)

    assert sorted(pack["id"] for pack in context["capability_packs"]) == sorted(selected)
    assert all(pack["capability_source"] == "generated_module" for pack in context["capability_packs"])


@pytest.mark.parametrize("selected", [[], ["social"], ["messaging"], ["social", "messaging"], ["content"]])
@pytest.mark.parametrize("workflow_name", ["DesignDocs", "AppGenerator"])
def test_shipped_catalog_root_only_admits_operator_selected_packs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selected: list[str], workflow_name: str,
) -> None:
    factory = tmp_path / "factory_app"
    root = factory / "build_context"
    for name in ("social", "messaging", "content"):
        shutil.copytree(ROOT / "factory_app/build_context" / name, root / name)
    _write_context(root, "SelectedCapabilities", {
        "context_id": "selected_capabilities",
        "applies_to_workflows": [workflow_name],
        "assets": [],
        "values": {"operator_capabilities": selected},
        "projections": {"context_variables": {
            "operator_capabilities": {"from": "operator_capabilities"},
        }},
    })
    monkeypatch.setenv("MOZAIKS_FACTORY_APP_PATH", str(factory))
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(root))
    monkeypatch.setattr(
        "mozaiksai.core.data.persistence.persistence_manager._context_authority_policy_for_workflow",
        _policy,
    )

    context = load_trusted_build_context(_policy(workflow_name), build_context_root=root)
    launched = merge_build_context(workflow_id=workflow_name, build_context_root=root)

    assert context["operator_capabilities"] == selected
    assert sorted(pack["id"] for pack in context.get("capability_packs", [])) == sorted(selected)
    assert launched.get("capability_packs", []) == context.get("capability_packs", [])
    assert launched.get("operator_contracts", []) == context.get("operator_contracts", [])
    assert any(
        Path(contract["source_path"]).parent.name == "content"
        for contract in context.get("operator_contracts", [])
    ) == ("content" in selected)


def test_shipped_root_without_operator_selection_does_not_select_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = ROOT / "factory_app/build_context"
    monkeypatch.setenv("MOZAIKS_FACTORY_APP_PATH", str(ROOT / "factory_app"))
    context = load_trusted_build_context(_policy("DesignDocs"), build_context_root=root)
    assert not context.get("operator_capabilities")
    assert not context.get("capability_packs")
    assert not context.get("operator_contracts")
    assert (root / "social/context.yaml").is_file()
    assert (root / "messaging/context.yaml").is_file()


@pytest.mark.parametrize("pack_id,old_version,new_version", [
    ("social", "0.1.17", "0.1.18"), ("messaging", "0.1.16", "0.1.17"),
])
def test_pack_registration_upgrade_rejects_previous_pins(
    tmp_path: Path, pack_id: str, old_version: str, new_version: str,
) -> None:
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        PackIntegrityError,
        verified_pack_selection,
        verify_pack_integrity,
    )

    current = ROOT / "factory_app/build_context" / pack_id
    old = tmp_path / pack_id
    shutil.copytree(current, old)
    manifest = old / "context.yaml"
    previous = manifest.read_text(encoding="utf-8").replace("  - DesignDocs\n", "").replace(
        f'version: "{new_version}"', f'version: "{old_version}"',
    )
    manifest.write_text(previous, encoding="utf-8", newline="\n")
    before = verify_pack_integrity(old, pack_id)
    after = verify_pack_integrity(current, pack_id)
    assert before["version"] == old_version and after["version"] == new_version
    assert before["digest"] != after["digest"]
    selection = {
        "id": pack_id, "pack_source_path": str(current), "capability_source": "generated_module",
        "version": old_version, "digest": before["digest"],
    }
    with pytest.raises(PackIntegrityError, match="selected version"):
        verified_pack_selection(selection, context_variables={})


def test_trusted_selection_does_not_register_unknown_provider_alias(tmp_path: Path) -> None:
    root = tmp_path / "build_context"
    _write_context(root, "SelectedCapabilities", {
        "context_id": "selected_capabilities",
        "applies_to_workflows": ["AppGenerator"],
        "assets": [],
        "values": {"operator_capabilities": ["billing_pack"]},
        "projections": {"context_variables": {
            "operator_capabilities": {"from": "operator_capabilities"},
        }},
    })

    context = load_trusted_build_context(_policy(), build_context_root=root)

    assert not context.get("capability_packs")
    with pytest.raises(ValueError, match="unapproved surface 'billing_pack_managed'"):
        validate_plan_origins(_provider_plan("billing_pack"), ContextVariablesBridge(context))
