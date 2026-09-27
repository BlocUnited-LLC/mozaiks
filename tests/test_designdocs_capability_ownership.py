"""Normalize determined ownership and reject ambiguous app-state rewrites."""

from __future__ import annotations

from copy import deepcopy
from types import MappingProxyType

import pytest
import yaml

from mozaiksai.core.session.build_context import (
    discover_pack_descriptors,
    load_contract_descriptors,
)
from mozaiksai.core.workflow.context.frozen import detach
from tests import test_designdocs_monetization_inventory as inventory

persistence = inventory.persistence


def _context(*, managed: bool):
    context = inventory._context(monetized=managed, subscription=managed)
    if managed:
        pack_root = inventory.ROOT / "factory_app/build_context/mozaikspay"
        config = yaml.safe_load((pack_root / "context.yaml").read_text(encoding="utf-8"))
        descriptor = discover_pack_descriptors(pack_root, config)[0]
        context.set("capability_packs", [descriptor])
        context.set("operator_capabilities", ["mozaikspay"])
        assert isinstance(context.get("capability_packs")[0], MappingProxyType)
    return context


def _collection(surface_id: str, name: str) -> dict:
    return {
        "name": name, "scope": "app", "entity": name, "tenancy": "app_wide", "owner_field": None,
        "ownership": {"surface_id": surface_id, "surface_kind": "module"},
        "fields": [{
            "name": "status", "type": "string", "required": True,
            "default": None, "enum": None, "nullable": False,
        }],
        "indexes": [], "search_by": None,
        "lifecycle": {"write_mode": "module_action", "migration_policy": "additive_only"},
    }


def _add_surface(
    bundle: dict, *, surface_id: str, name: str, route: str,
    entities: list[str], actions: list[str], collection: str | None = None,
    owner: str = "app",
) -> None:
    bundle["experience_spec"]["pages"].append({
        "name": name, "route": route, "layout": "full-width",
        "intent": f"Use {surface_id} for {', '.join(actions)}.",
        "sections": [{
            "id": surface_id.replace("_", "-"), "primitive": "SurfaceCard",
            "intent": f"Use {surface_id} for {', '.join(actions)}.",
        }],
    })
    bundle["surface_map"]["surfaces"].append({
        "surface_id": surface_id, "label": name, "surface_kind": "module", "owner": owner,
        "primary_entities": entities, "owned_pages": [name], "owned_mutations": actions,
        "source_capability_packs": ["mozaikspay"] if surface_id == "billing_portal" else [],
        "notes": None,
    })
    bundle["data_contract"]["surfaces"].append({
        "surface_id": surface_id, "surface_kind": "module",
        "collections": [{**_collection(surface_id, collection), "entity": entities[0] if entities else collection}] if collection else [],
    })


def _assert_refused(context, result: dict, store_factory, *, owner: str) -> None:
    assert result["outcome"] == "revise", result
    assert result.get("outcome_error") is None
    assert owner.lower() in result["error"].lower()
    assert context.get("design_docs_save_feedback") == result["error"]
    assert context.get("design_docs_save_attempts") == 1
    assert context.get("design_docs_save_outcome") == "revise"
    assert context.get("experience_spec") is None
    assert context.get("design_surface_map") is None
    assert context.get("data_contract") is None
    store_factory.assert_not_called()


def test_subscription_state_module_normalized_before_storage(persistence):
    context = _context(managed=True)
    bundle = inventory._bundle(pricing=False)
    _add_surface(
        bundle, surface_id="subscription_management", name="Subscription Management",
        route="/subscription", entities=["Subscription"], actions=["update_subscription"],
        collection="subscriptions",
    )

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    surfaces = detach(context.get("design_surface_map"))["surfaces"]
    assert not any(surface["surface_id"] == "subscription_management" for surface in surfaces)
    facade = next(surface for surface in surfaces if surface["surface_id"] == "billing_portal")
    assert "Subscription Management" in facade["owned_pages"]
    assert facade["primary_entities"] == []
    assert "update_subscription" not in facade["owned_mutations"]


@pytest.mark.parametrize("selection", ["explicit_disabled_monetization", "subscription_default"])
def test_managed_state_owner_follows_selected_or_default_pack(persistence, selection):
    if selection == "explicit_disabled_monetization":
        context = _context(managed=True)
        context.set("monetization_enabled", False)
    else:
        context = inventory._context(monetized=True, subscription=True)
        assert context.get("capability_packs") is None
    bundle = inventory._bundle(pricing=False)
    _add_surface(
        bundle, surface_id="subscription_management", name="Subscription Management",
        route="/subscription", entities=["Subscription"], actions=["update_subscription"],
        collection="subscriptions",
    )
    submitted = deepcopy(bundle)

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert not any(
        collection["name"] == "subscriptions"
        for surface in context.get("data_contract")["surfaces"] for collection in surface["collections"]
    )
    assert bundle == submitted, "Normalization must detach the submitted design before editing"


@pytest.mark.parametrize("surface_id,name,route,entity,action", [
    ("user_authentication", "User Authentication", "/auth", "UserCredential", "authenticate_user"),
    ("session_management", "Session Management", "/sessions", "Session", "create_session"),
])
def test_platform_capability_module_normalized_before_storage(
    persistence, surface_id, name, route, entity, action,
):
    context = _context(managed=False)
    bundle = inventory._bundle(pricing=False)
    _add_surface(
        bundle, surface_id=surface_id, name=name, route=route,
        entities=[entity], actions=[action],
    )

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    surface = next(s for s in detach(context.get("design_surface_map"))["surfaces"] if s["surface_id"] == surface_id)
    assert surface["owner"] == "platform"
    assert surface["primary_entities"] == []
    assert surface["owned_mutations"] == []
    assert surface["owned_pages"] == [name]


def test_concept_platform_owner_hint_normalizes_empty_app_module_alias(persistence):
    context = _context(managed=False)
    blueprint = detach(context.get("concept_blueprint"))
    blueprint["surface_candidate_hints"] = [{
        "surface_id": "member_access", "owner_hint": "platform",
    }]
    context.set("concept_blueprint", blueprint)
    bundle = inventory._bundle(pricing=False)
    _add_surface(
        bundle, surface_id="member_access", name="Member Access", route="/access",
        entities=[], actions=[],
    )

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    surface = next(s for s in context.get("design_surface_map")["surfaces"] if s["surface_id"] == "member_access")
    assert surface["owner"] == "platform"


def test_subscription_ui_owned_by_billing_facade_preserves_approved_route(persistence):
    store, _, summary = persistence
    context = _context(managed=True)
    bundle = inventory._bundle(pricing=False)
    _add_surface(
        bundle, surface_id="billing_portal", name="Subscription", route="/subscription",
        entities=[], actions=[
            "list_plans", "get_subscription_status", "start_subscription_checkout", "open_billing_portal",
        ],
    )
    subscription_page = deepcopy(bundle["experience_spec"]["pages"][-1])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    experience = detach(context.get("experience_spec"))
    assert [page for page in experience["pages"] if page["route"] == "/subscription"] == [subscription_page]
    assert {page["route"] for page in experience["pages"]} == {
        "/reports", "/subscription", "/pricing", "/billing", "/usage",
    }
    surfaces = detach(context.get("design_surface_map"))["surfaces"]
    assert [surface["surface_id"] for surface in surfaces if "Subscription" in surface["owned_pages"]] == [
        "billing_portal",
    ]
    data_contract = detach(context.get("data_contract"))
    facade_data = next(surface for surface in data_contract["surfaces"] if surface["surface_id"] == "billing_portal")
    assert facade_data["collections"] == []
    assert store.save_data_contract.await_args.kwargs["data_contract"] == data_contract
    assert summary.await_args.kwargs["summary_payload"]["experience_spec"] == experience


def test_facade_local_subscription_collection_is_removed(persistence):
    context = _context(managed=True)
    bundle = inventory._bundle(pricing=False)
    _add_surface(
        bundle, surface_id="billing_portal", name="Subscription", route="/subscription",
        entities=[], actions=["get_subscription_status"], collection="subscriptions",
    )

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    facade = next(s for s in context.get("data_contract")["surfaces"] if s["surface_id"] == "billing_portal")
    assert not facade["collections"]


@pytest.mark.parametrize("placement", ["shared", "group_facade", "owner_facade"])
def test_collection_ownership_cannot_bypass_managed_state_boundary(persistence, placement):
    _, store_factory, _ = persistence
    context = _context(managed=True)
    bundle = inventory._bundle(pricing=False)
    _add_surface(
        bundle, surface_id="billing_portal", name="Subscription", route="/subscription",
        entities=[], actions=["get_subscription_status"],
    )
    if placement == "shared":
        collection = _collection("reports", "subscriptions")
        bundle["data_contract"]["shared_collections"] = [collection]
    elif placement == "group_facade":
        collection = _collection("reports", "billing_cache")
        bundle["data_contract"]["surfaces"][-1]["collections"] = [collection]
    else:
        collection = _collection("billing_portal", "billing_cache")
        bundle["data_contract"]["surfaces"][0]["collections"] = [collection]

    result = inventory._save(context, bundle)

    if placement == "shared":
        assert result["outcome"] == "saved", result
        assert not context.get("data_contract")["shared_collections"]
        reports = next(s for s in context.get("design_surface_map")["surfaces"] if s["surface_id"] == "reports")
        assert reports["owner"] == "app"
        assert detach(reports["primary_entities"]) == ["Report"]
    else:
        _assert_refused(context, result, store_factory, owner="MozaiksPay")
        assert collection["name"] in result["error"]


def test_platform_owned_auth_reference_is_not_a_generated_module(persistence):
    context = _context(managed=False)
    bundle = inventory._bundle(pricing=False)
    _add_surface(
        bundle, surface_id="user_authentication", name="User Authentication", route="/auth",
        entities=[], actions=[], owner="platform",
    )

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    surface = detach(context.get("design_surface_map"))["surfaces"][-1]
    assert surface["owner"] == "platform"
    assert surface["primary_entities"] == []


def test_unmonetized_domain_app_preserves_ordinary_module(persistence):
    context = _context(managed=False)
    bundle = inventory._bundle(pricing=False)
    expected = deepcopy(bundle)

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert detach(context.get("experience_spec")) == expected["experience_spec"]
    assert detach(context.get("design_surface_map")) == expected["surface_map"]
    assert {page["route"] for page in context.get("experience_spec")["pages"]} == {"/reports"}


def test_facade_completion_removes_hosted_state_before_app_ownership(persistence):
    context = _context(managed=True)
    bundle = inventory._bundle(pricing=False)
    _add_surface(
        bundle, surface_id="billing_portal", name="Subscription", route="/subscription",
        entities=["Subscription"], actions=["update_subscription"], owner="hosted",
    )

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    facade = next(s for s in detach(context.get("design_surface_map"))["surfaces"] if s["surface_id"] == "billing_portal")
    assert facade["owner"] == "app"
    assert facade["primary_entities"] == []
    assert "update_subscription" not in facade["owned_mutations"]


@pytest.mark.parametrize("managed", [False, True])
def test_newsletter_subscriptions_remain_app_owned(persistence, managed):
    context = _context(managed=managed)
    bundle = inventory._bundle(pricing=False)
    _add_surface(
        bundle, surface_id="newsletter_subscriptions", name="Newsletter Subscriptions",
        route="/newsletter", entities=["NewsletterSubscription"], actions=["update_newsletter_subscription"],
        collection="newsletter_subscriptions",
    )
    newsletter_surface = deepcopy(bundle["surface_map"]["surfaces"][-1])
    newsletter_data = deepcopy(bundle["data_contract"]["surfaces"][-1])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert newsletter_surface in detach(context.get("design_surface_map"))["surfaces"]
    assert newsletter_data in detach(context.get("data_contract"))["surfaces"]


def test_monetized_training_plans_can_use_module_scoped_list_plans_action(persistence):
    context = _context(managed=True)
    bundle = inventory._bundle(pricing=False)
    _add_surface(
        bundle, surface_id="training_plans", name="Training Plans", route="/training",
        entities=["TrainingPlan"], actions=["list_plans"], collection="training_plans",
    )
    training_surface = deepcopy(bundle["surface_map"]["surfaces"][-1])
    training_data = deepcopy(bundle["data_contract"]["surfaces"][-1])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert training_surface in detach(context.get("design_surface_map"))["surfaces"]
    assert training_data in detach(context.get("data_contract"))["surfaces"]


def _search_pack(tmp_path) -> tuple[dict, list[dict]]:
    """Exercise declared contract assets through the normal pack discovery path."""
    pack_root = tmp_path / "managed_search"
    pack_root.mkdir()
    config = {
        "context_id": "managed_search", "applies_to_workflows": ["DesignDocs", "AppGenerator"],
        "assets": [{"path": "contract.yaml", "kind": "contract"}],
        "pack": {"id": "managed_search", "version": "1.0.0", "status": "active", "capability_source": "managed_capability"},
    }
    contract = {
        "contract_id": "managed_search", "contract_type": "build_pack_instructions",
        "surface_ownership": [{
            "owner": "ManagedSearch index service", "facade_module": "search_portal",
            "surface_ids": ["index_management"], "entity_names": ["SearchIndex"],
            "action_ids": ["create_search_index"], "collection_names": ["search_indexes"],
        }],
        "facades": [{
            "module_id": "search_portal", "provider_module": "managed_search",
            "pages": [{"name": "Search", "route": "/search", "primary_actions": ["query_index"]}],
        }],
    }
    (pack_root / "context.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (pack_root / "contract.yaml").write_text(yaml.safe_dump(contract), encoding="utf-8")
    return discover_pack_descriptors(pack_root, config)[0], load_contract_descriptors(pack_root, config)


@pytest.mark.parametrize("selected", [False, True])
def test_custom_pack_ownership_only_applies_when_selected(persistence, tmp_path, selected):
    _, store_factory, _ = persistence
    context = _context(managed=False)
    descriptor, contracts = _search_pack(tmp_path)
    context.set("operator_contracts", contracts)
    context.set("capability_packs", [descriptor] if selected else [])
    bundle = inventory._bundle(pricing=False)
    _add_surface(
        bundle, surface_id="index_management", name="Search Index Management", route="/search",
        entities=["SearchIndex"], actions=["create_search_index"], collection="search_indexes",
    )

    result = inventory._save(context, bundle)

    if selected:
        _assert_refused(context, result, store_factory, owner="ManagedSearch")
        assert "search_portal" in result["error"]
        assert "query_index" in result["error"]
    else:
        assert result["outcome"] == "saved", result
        assert detach(context.get("design_surface_map")) == bundle["surface_map"]


def test_custom_pack_facade_with_declared_action_saves_through_real_bridge(persistence, tmp_path):
    context = _context(managed=False)
    descriptor, _ = _search_pack(tmp_path)
    context.set("capability_packs", [descriptor])
    bundle = inventory._bundle(pricing=False)
    _add_surface(
        bundle, surface_id="search_portal", name="Search", route="/search",
        entities=[], actions=["query_index"],
    )
    bundle["surface_map"]["surfaces"][-1]["source_capability_packs"] = ["managed_search"]

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert detach(context.get("design_surface_map")) == bundle["surface_map"]
    assert detach(context.get("experience_spec")) == bundle["experience_spec"]


def test_malformed_selected_ownership_contract_is_not_silently_ignored(persistence, tmp_path):
    _, store_factory, _ = persistence
    context = _context(managed=False)
    descriptor, contracts = _search_pack(tmp_path)
    contract = contracts[0]
    contract["surface_ownership"] = None
    (tmp_path / "managed_search/contract.yaml").write_text(yaml.safe_dump(contract), encoding="utf-8")
    context.set("capability_packs", [descriptor])

    result = inventory._save(context, inventory._bundle(pricing=False))

    _assert_refused(context, result, store_factory, owner="managed_search")
    assert "surface_ownership must be a list" in result["error"]
