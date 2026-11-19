"""Plan-selected packs use the same verified sources as template assembly."""
from __future__ import annotations

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_build_plan import (
    _apply_selected_pack_files,
    _normalize_managed_capability_page_bindings,
    _resolve_monetization_provider,
)
from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    PackIntegrityError,
    compute_pack_digest,
    resolve_managed_capability_templates,
    verified_pack_selection,
)
from mozaiksai.core.session.build_context import load_build_context, normalize_pack_descriptor
from mozaiksai.resources import resolve_factory_app_root


def _normalize(packs, context=None):
    return _apply_selected_pack_files(
        capability_packs=packs, pages=[], build_tasks=[], context_variables=context,
    )


@pytest.mark.parametrize("context", [None, {}, {"capability_packs": []}])
def test_late_provider_choice_cannot_insert_an_unreviewed_pack(context):
    with pytest.raises(ValueError, match="owner-reviewed in DesignDocs"):
        _resolve_monetization_provider(
            [], contract_required=True, monetization_provider="mozaiks_pay", context_variables=context,
        )


def test_explicit_standalone_pack_still_uses_the_canonical_physical_composer():
    row = {"capability_pack_id": "mozaikspay", "capability_source": "managed_capability"}
    packs, provider = _resolve_monetization_provider(
        [row], contract_required=True, monetization_provider="mozaiks_pay", context_variables=None,
    )
    normalized, pages, tasks = _normalize(packs)
    assert provider == "mozaiks_pay"
    facade = next(pack for pack in normalized if pack["capability_pack_id"] == "billing_portal")
    assert facade["capability_source"] == "generated_module"
    assert {page["route"] for page in pages} == {"/billing", "/usage"}
    assert tasks == []
    filenames = {file["filename"] for file in resolve_managed_capability_templates(normalized)}
    assert {"modules/billing_portal/module.yaml", "modules/billing_portal/backend/handler.py",
            "ui/pages/billing.yaml", "services/integrations/mozaikspay_client.py"} <= filenames
    assert not any(path.startswith("modules/mozaikspay/") for path in filenames)


def test_unselected_catalog_does_not_add_paid_surfaces():
    context = {"capability_registry": {"mozaikspay": {"capability_pack_id": "mozaikspay"}}}
    packs, provider = _resolve_monetization_provider(
        [], contract_required=False, monetization_provider=None, context_variables=context,
    )
    assert provider is None
    assert _normalize(packs, context) == ([], [], [])
    assert resolve_managed_capability_templates(packs, context_variables=context) == []


def test_selected_id_reads_verified_contract_instead_of_inline_facades():
    packs, pages, _ = _normalize([{
        "capability_pack_id": "mozaikspay", "capability_source": "managed_capability",
        "facades": [{"module_id": "invented", "pages": [{"route": "/invented"}]}],
    }])
    assert "billing_portal" in {pack["capability_pack_id"] for pack in packs}
    assert "/invented" not in {page["route"] for page in pages}


def test_reviewed_catalog_plan_keeps_its_location_when_enriching_managed_facades(monkeypatch, tmp_path):
    from factory_app.workflows._shared.pack_selection import (
        record_reviewed_pack_rows,
        selected_pack_rows,
    )
    from tests.reviewed_pack_fixture import reviewed_pack_fixture
    from tests.test_factory_pack_selection import _enable_managed

    _enable_managed(tmp_path, monkeypatch)
    context = reviewed_pack_fixture({}, ["mozaikspay"])
    reviewed = selected_pack_rows(context)
    plan_rows = record_reviewed_pack_rows(reviewed, reviewed)
    _, pages, _ = _normalize(plan_rows, context)
    assert {page["route"] for page in pages} == {"/billing", "/usage"}


@pytest.mark.parametrize("pin", [{"version": "0.0.0"}, {"digest": "sha256:incorrect"}])
@pytest.mark.parametrize("projected", [False, True])
def test_selected_pack_pin_mismatch_fails_before_facade_normalization(pin, projected):
    root = resolve_factory_app_root() / "build_context" / "mozaikspay"
    descriptor = normalize_pack_descriptor(load_build_context(root / "context.yaml"), context_root=root)
    with pytest.raises(PackIntegrityError, match="does not match freshly verified source"):
        _normalize([{
            "capability_pack_id": "mozaikspay", "capability_source": "managed_capability", **pin,
        }], {"capability_packs": [descriptor]} if projected else None)


def test_missing_selected_source_fails_before_facade_normalization(tmp_path):
    with pytest.raises(PackIntegrityError):
        _normalize([{
            "capability_pack_id": "missing_managed_pack", "capability_source": "managed_capability",
            "pack_source_path": str(tmp_path / "missing"),
        }])


def _inventory_pack(root, facade_id):
    root.mkdir()
    context = {
        "context_id": "inventory_provider", "applies_to_workflows": ["AppGenerator"],
        "assets": [{"path": "contract.yaml", "kind": "contract"}],
        "pack": {
            "id": "inventory_provider", "version": "1.0.0", "status": "active",
            "capability_source": "managed_capability",
        },
    }
    contract = {
        "contract_id": "inventory_provider", "contract_type": "build_pack_instructions",
        "facades": [{
            "module_id": facade_id, "provider_module": "inventory_provider",
            "pages": [{"name": "Inventory", "route": "/inventory", "primary_actions": ["list_items"]}],
        }],
    }
    context["facades"] = contract["facades"]
    (root / "context.yaml").write_text(yaml.safe_dump(context), encoding="utf-8")
    (root / "contract.yaml").write_text(yaml.safe_dump(contract), encoding="utf-8")
    return {
        "capability_pack_id": "inventory_provider", "capability_source": "managed_capability",
        "pack_source_path": str(root), "version": "1.0.0",
        "digest": compute_pack_digest(root, "inventory_provider"),
    }


def test_external_managed_pack_uses_its_own_verified_facade(tmp_path):
    packs, pages, _ = _normalize([_inventory_pack(tmp_path / "provider", "inventory_portal")])
    facade = next(pack for pack in packs if pack["capability_pack_id"] == "inventory_portal")
    assert facade["operations"] == ["list_items"]
    assert {page["route"] for page in pages} == {"/inventory"}


def test_explicit_selected_source_matches_assembly_despite_other_projected_source(tmp_path):
    projected = _inventory_pack(tmp_path / "source_a", "portal_a")
    selected = _inventory_pack(tmp_path / "source_b", "portal_b")
    context = {"capability_packs": [projected]}
    resolved = _normalize([selected], context)
    packs = resolved[0]
    assert {pack["capability_pack_id"] for pack in packs} == {"inventory_provider", "portal_b"}
    assert resolved == _normalize([selected])
    assert verified_pack_selection(selected, context_variables=context)["source_path"] == str(tmp_path / "source_b")


@pytest.mark.parametrize("pack_id", ["mozaikspay", "mozaiks_cloud"])
def test_plan_selection_matches_canonical_projected_descriptor(pack_id):
    root = resolve_factory_app_root() / "build_context" / pack_id
    descriptor = normalize_pack_descriptor(load_build_context(root / "context.yaml"), context_root=root)
    selected = [{"capability_pack_id": pack_id, "capability_source": "managed_capability"}]
    projected = _normalize(selected, {"capability_packs": [descriptor]})
    resolved = _normalize(selected)
    assert resolved == projected
    assert {page["route"] for page in resolved[1]} == {
        page["route"] for facade in descriptor["facades"] for page in facade.get("pages", [])
    }
    assert {pack["capability_pack_id"] for pack in resolved[0]} == {
        pack_id, *(facade["module_id"] for facade in descriptor["facades"]),
    }
    binding_pages = [{"data_source": {
        "module_id": facade.get("provider_module", pack_id), "action_id": action,
    }} for facade in descriptor["facades"] for action in facade.get("provider_actions", [])]
    projected_bindings = _normalize_managed_capability_page_bindings(
        binding_pages, capability_packs=projected[0], context_variables={"capability_packs": [descriptor]},
    )
    resolved_bindings = _normalize_managed_capability_page_bindings(
        binding_pages, capability_packs=resolved[0], context_variables=None,
    )
    assert resolved_bindings == projected_bindings
    assert all(page["data_source"]["module_id"] != pack_id for page in resolved_bindings)


def test_inline_facade_cannot_redirect_final_page_binding():
    selected = [{
        "capability_pack_id": "mozaikspay", "capability_source": "managed_capability",
        "facades": [{
            "module_id": "invented", "provider_module": "mozaikspay",
            "provider_actions": ["get_subscription_status"],
        }],
    }]
    packs, _, _ = _normalize(selected)
    pages = _normalize_managed_capability_page_bindings(
        [{"data_source": {"module_id": "mozaikspay", "action_id": "get_subscription_status"}}],
        capability_packs=packs, context_variables=None,
    )
    assert pages == [{"data_source": {"module_id": "billing_portal", "action_id": "get_subscription_status"}}]


@pytest.mark.parametrize("reverse", [False, True])
def test_verified_selected_provider_repairs_misclassified_facade_in_either_order(reverse):
    selected = [
        {"capability_pack_id": "mozaikspay", "capability_source": "managed_capability"},
        {"capability_pack_id": "billing_portal", "capability_source": "managed_capability"},
    ]
    packs, pages, _ = _normalize(selected[::-1] if reverse else selected)
    facade = next(pack for pack in packs if pack["capability_pack_id"] == "billing_portal")
    assert facade["capability_source"] == "generated_module"
    assert {page["route"] for page in pages} == {"/billing", "/usage"}


def test_unselected_projected_provider_does_not_authorize_unknown_facade():
    root = resolve_factory_app_root() / "build_context" / "mozaikspay"
    descriptor = normalize_pack_descriptor(load_build_context(root / "context.yaml"), context_root=root)
    with pytest.raises(PackIntegrityError, match="billing_portal.*cannot be resolved"):
        _normalize(
            [{"capability_pack_id": "billing_portal", "capability_source": "managed_capability"}],
            {"available_managed_capabilities": [descriptor]},
        )


@pytest.mark.parametrize("pin", [{"version": "0.0.0"}, {"digest": "sha256:incorrect"}, {"pack_source_path": "missing"}])
def test_facade_identity_does_not_exempt_explicit_invalid_selection(pin):
    with pytest.raises(PackIntegrityError):
        _normalize([
            {"capability_pack_id": "mozaikspay", "capability_source": "managed_capability"},
            {"capability_pack_id": "billing_portal", "capability_source": "managed_capability", **pin},
        ])
