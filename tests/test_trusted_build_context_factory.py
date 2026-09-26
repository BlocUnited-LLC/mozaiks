"""Factory consumes registered pack projections, never descriptive provider claims."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_plan_review import validate_plan_origins
from mozaiksai.core.session.build_context import load_trusted_build_context
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
def registered_payments_root(tmp_path: Path) -> Path:
    """Use the shipped public contract, including its declared integration metadata."""
    root = tmp_path / "build_context"
    config = yaml.safe_load((PAYMENTS_CONTEXT / "context.yaml").read_text(encoding="utf-8"))
    _write_context(root, "mozaikspay", config)
    for asset in config["assets"]:
        if asset["kind"] == "contract":
            relative = asset["path"]
            (root / "mozaikspay" / relative).write_text(
                (PAYMENTS_CONTEXT / relative).read_text(encoding="utf-8"), encoding="utf-8",
            )
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


def test_trusted_selection_resolves_only_the_selected_registered_pack(tmp_path: Path) -> None:
    root = tmp_path / "build_context"
    _write_context(root, "SelectedCapabilities", {
        "context_id": "selected_capabilities",
        "applies_to_workflows": ["AppGenerator"],
        "assets": [],
        "values": {"operator_capabilities": ["mozaikspay"]},
        "projections": {"context_variables": {
            "operator_capabilities": {"from": "operator_capabilities"},
        }},
    })

    context = load_trusted_build_context(_policy(), build_context_root=root)

    assert context["operator_capabilities"] == ["mozaikspay"]
    assert [pack["id"] for pack in context["capability_packs"]] == ["mozaikspay"]
    assert context["capability_packs"][0]["pack_source_path"] == str(PAYMENTS_CONTEXT)
    validate_plan_origins(_provider_plan(), ContextVariablesBridge(context))


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
