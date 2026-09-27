from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
    _scan_subscription_assignment_storage,
    scan_generated_bundle,
)
from mozaiksai.core.runtime.persistence.app_data import collection_name_for_alias
from mozaiksai.core.runtime.persistence.intent_loader import (
    load_data_contract,
    validate_complete_data_contract_ownership,
)
from mozaiksai.core.workflow.generator_support.subscription_data_contract import (
    ensure_subscription_assignment_stores,
)


def _data_contract() -> dict:
    return {"version": "1", "surfaces": [], "shared_collections": []}


def _subscription_contract(alias: str = "billing.subscriptions") -> dict:
    return {
        "contract_required": True,
        "subscription_config_file": {
            "schema_version": "mozaiks.subscriptions.v1",
            "assignment_store": {"data_alias": alias, "user_id_field": "user_id"},
        },
    }


def test_assignment_store_is_a_loadable_app_policy_collection(tmp_path: Path) -> None:
    contract = ensure_subscription_assignment_stores(_data_contract(), _subscription_contract())
    validate_complete_data_contract_ownership(contract)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "contract.json").write_text(json.dumps(contract), encoding="utf-8")

    loaded = load_data_contract(tmp_path)
    assert collection_name_for_alias("billing.subscriptions", contract=loaded) == "billing_subscriptions"
    surface, = contract["surfaces"]
    assert surface["surface_kind"] == "app_policy"
    assert surface["surface_id"] == "subscription_assignments"
    collection, = surface["collections"]
    assert collection["scope"] == "app"
    assert collection["mongo_collection"] == "billing_subscriptions"
    assert collection["indexes"] == [{
        "name": "assignment_subject",
        "keys": [{"field": "app_id", "order": 1}, {"field": "user_id", "order": 1}, {"field": "tenant_id", "order": 1}],
        "unique": True,
    }]


def test_assignment_storage_compilation_is_pure_and_idempotent() -> None:
    data, subscriptions = _data_contract(), _subscription_contract()
    original_data, original_subscriptions = deepcopy(data), deepcopy(subscriptions)
    compiled = ensure_subscription_assignment_stores(data, subscriptions)

    assert data == original_data
    assert subscriptions == original_subscriptions
    assert ensure_subscription_assignment_stores(compiled, subscriptions) == compiled


def test_custom_assignment_fields_are_declared() -> None:
    subscriptions = _subscription_contract("paid.access")
    subscriptions["subscription_config_file"]["assignment_store"] = {
        "data_alias": "paid.access",
        "app_id_field": "application",
        "user_id_field": "customer",
        "tenant_id_field": None,
        "workspace_id_field": "organization",
        "plan_id_field": "tier",
        "status_field": "state",
        "starts_at_field": None,
        "expires_at_field": "ends_at",
        "capabilities_field": "grants",
        "plan_snapshot_field": "snapshot",
        "revision_field": None,
    }
    compiled = ensure_subscription_assignment_stores(_data_contract(), subscriptions)
    collection = compiled["surfaces"][0]["collections"][0]
    fields = {field["name"]: field for field in collection["fields"]}
    assert set(fields) == {"application", "customer", "organization", "tier", "state", "ends_at", "grants", "snapshot"}
    assert fields["tier"]["required"] is True
    assert fields["state"]["required"] is True
    assert fields["grants"]["type"] == "array"
    assert collection["indexes"][0]["keys"] == [
        {"field": name, "order": 1} for name in ("application", "customer", "organization")
    ]
    assert collection["indexes"][0]["unique"] is True


def test_explicit_assignment_alias_mapping_is_preserved() -> None:
    data = {**_data_contract(), "aliases": [{"alias": "billing.subscriptions", "collection": "existing_assignments"}]}
    compiled = ensure_subscription_assignment_stores(data, _subscription_contract())

    assert compiled["aliases"] == data["aliases"]
    assert compiled["surfaces"][0]["collections"][0]["name"] == "existing_assignments"
    assert collection_name_for_alias("billing.subscriptions", contract=compiled) == "existing_assignments"


def test_existing_assignment_collection_is_preserved() -> None:
    existing = ensure_subscription_assignment_stores(_data_contract(), _subscription_contract())
    existing["surfaces"][0]["collections"][0]["description"] = "Existing declared storage"
    compiled = ensure_subscription_assignment_stores(existing, _subscription_contract())

    assert compiled == existing


def test_all_product_assignment_stores_are_materialized() -> None:
    subscriptions = _subscription_contract()
    subscriptions["subscription_config_file"].update({
        "schema_version": "mozaiks.subscriptions.v2",
        "products": [
            {"product_id": "reports", "assignment_store": {"data_alias": "reports.assignments"}},
            {"product_id": "exports", "assignment_store": {"data_alias": "exports.assignments"}},
            {"product_id": "public", "assignment_store": None},
        ],
    })
    compiled = ensure_subscription_assignment_stores(_data_contract(), subscriptions)

    assert compiled["aliases"] == [
        {"alias": "billing.subscriptions", "collection": "billing_subscriptions"},
        {"alias": "reports.assignments", "collection": "reports_assignments"},
        {"alias": "exports.assignments", "collection": "exports_assignments"},
    ]
    assert len(compiled["surfaces"][0]["collections"]) == 3


@pytest.mark.parametrize("target", ["", None, "system.users", "$invalid", "contains\x00null"])
def test_invalid_explicit_assignment_target_is_rejected(target: str | None) -> None:
    data = {**_data_contract(), "aliases": [{"alias": "billing.subscriptions", "collection": target}]}
    with pytest.raises(ValueError, match="valid literal collection name"):
        ensure_subscription_assignment_stores(data, _subscription_contract())


def test_conflicting_alias_declarations_are_rejected() -> None:
    data = {**_data_contract(), "aliases": [
        {"alias": "billing.subscriptions", "collection": "first"},
        {"alias": "billing.subscriptions", "collection": "second"},
    ]}
    with pytest.raises(ValueError, match="conflicting collection declarations"):
        ensure_subscription_assignment_stores(data, _subscription_contract())


def test_normalized_alias_collision_is_rejected() -> None:
    data = {**_data_contract(), "aliases": [{"alias": "billing_subscriptions", "collection": "billing_subscriptions"}]}
    with pytest.raises(ValueError, match="conflicts with an existing alias"):
        ensure_subscription_assignment_stores(data, _subscription_contract())


def test_undeclared_alias_cannot_adopt_an_unrelated_collection() -> None:
    data = {**_data_contract(), "surfaces": [{
        "surface_id": "reporting", "surface_kind": "module",
        "collections": [{"name": "billing_subscriptions"}],
    }]}
    with pytest.raises(ValueError, match="conflicts with collection"):
        ensure_subscription_assignment_stores(data, _subscription_contract())


def test_products_sharing_an_assignment_alias_require_matching_field_mappings() -> None:
    subscriptions = _subscription_contract()
    subscriptions["subscription_config_file"]["products"] = [{
        "product_id": "reports",
        "assignment_store": {"data_alias": "billing.subscriptions", "user_id_field": "member"},
    }]
    with pytest.raises(ValueError, match="conflicting field mappings"):
        ensure_subscription_assignment_stores(_data_contract(), subscriptions)


def test_products_can_share_identical_assignment_field_mappings() -> None:
    subscriptions = _subscription_contract()
    subscriptions["subscription_config_file"]["products"] = [{
        "product_id": "reports",
        "assignment_store": {"data_alias": "billing.subscriptions", "user_id_field": "user_id"},
    }]
    compiled = ensure_subscription_assignment_stores(_data_contract(), subscriptions)
    assert len(compiled["aliases"]) == 1
    assert len(compiled["surfaces"][0]["collections"]) == 1


@pytest.mark.parametrize("subscriptions", [None, {"contract_required": False}, {"contract_required": True, "subscription_config_file": {}}])
def test_no_assignment_store_leaves_contract_unchanged(subscriptions: dict | None) -> None:
    data = _data_contract()
    assert ensure_subscription_assignment_stores(data, subscriptions) == data


@pytest.mark.parametrize("products", [False, True])
def test_public_bundle_scan_rejects_missing_assignment_storage_without_build_context(products: bool) -> None:
    config = {
        "schema_version": "mozaiks.subscriptions.v1", "label": "Plans", "default_plan_id": "free",
        "plans": [{"plan_id": "free", "label": "Free", "capabilities": []}],
        "assignment_store": {"data_alias": "billing.subscriptions", "user_id_field": "user_id"},
    }
    if products:
        config = {
            "schema_version": "mozaiks.subscriptions.v2", "label": "Plans", "default_product_id": "core",
            "products": [{"product_id": "core", "label": "Core", "default_plan_id": "free",
                          "plans": config["plans"], "assignment_store": config["assignment_store"]}],
        }
    files = {"config/subscriptions.yaml": yaml.safe_dump(config)}
    assert any("configured assignment_store requires" in error for error in scan_generated_bundle(files))
    files["data/contract.json"] = json.dumps(_data_contract())
    assert any("assignment_store aliases and collections" in error for error in scan_generated_bundle(files))

    compiled = ensure_subscription_assignment_stores(_data_contract(), {
        "contract_required": True, "subscription_config_file": config,
    })
    files["data/contract.json"] = json.dumps(compiled)
    assert _scan_subscription_assignment_storage(files) == []
