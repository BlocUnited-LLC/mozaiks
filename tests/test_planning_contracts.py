from __future__ import annotations

import pytest

from tests.import_utils import import_module_directly

_contracts = import_module_directly("mozaiksai.core.orchestration.planning_contracts")


def _valid_payload() -> dict:
    return {
        "app_spec": {
            "name": "CampusMarket",
            "summary": "Student marketplace",
            "user_personas": ["student", "seller", "admin"],
            "core_jobs": ["browse listings", "create listing", "message seller"],
            "constraints": ["mobile first"],
            "non_goals": ["crypto payments"],
        },
        "capabilities": [
            {
                "capability_id": "browse_listings",
                "label": "Browse listings",
                "mode": "module",
                "module_refs": ["marketplace_home"],
                "view_refs": ["listings_list"],
                "entity_refs": ["Listing"],
            },
            {
                "capability_id": "save_listing",
                "label": "Save listing",
                "mode": "action",
                "action_refs": ["save_favorite"],
                "entity_refs": ["Favorite"],
            },
            {
                "capability_id": "assistant_flow",
                "label": "Assistant flow",
                "mode": "workflow",
                "workflow_refs": ["Concierge"],
            },
        ],
        "entities": [
            {
                "name": "Listing",
                "purpose": "Sell items",
                "key_fields": [{"name": "title", "field_type": "string", "required": True}],
            },
            {
                "name": "Favorite",
                "purpose": "Saved listings",
                "key_fields": [{"name": "listing_id", "field_type": "string", "required": True}],
            },
        ],
        "views": [
            {
                "name": "listings_list",
                "view_type": "list",
                "entity": "Listing",
                "module": "marketplace_home",
                "fields": ["title"],
            }
        ],
        "actions": [
            {
                "name": "save_favorite",
                "action_type": "mutation",
                "summary": "Save listing to favorites",
                "reads": ["Listing"],
                "writes": ["Favorite"],
                "required_inputs": ["listing_id"],
            }
        ],
        "modules": [
            {
                "name": "marketplace_home",
                "purpose": "Main marketplace page",
                "route": "/marketplace",
                "primary_views": ["listings_list"],
            }
        ],
        "workflows": [
            {
                "name": "Concierge",
                "purpose": "Buyer assistance workflow",
                "entry_reason": "Needs conversational discovery",
                "outputs": ["recommendations"],
            }
        ],
        "policies": [
            {
                "name": "seller_only_create",
                "scope": "module",
                "rule": "Only sellers can create listings",
                "targets": ["marketplace_home"],
            }
        ],
        "bundle_plan": {
            "config_files": ["platform/config/ai.json"],
            "module_paths": ["platform/modules/marketplace_home/module.json"],
            "workflow_paths": ["platform/workflows/Concierge/orchestrator.yaml"],
            "data_model_paths": ["platform/entities/listing.json"],
        },
    }


def test_build_decomposition_package_valid_payload() -> None:
    pkg = _contracts.build_decomposition_package(_valid_payload())
    assert pkg.app_spec.name == "CampusMarket"
    assert pkg.capabilities[0].mode == _contracts.CapabilityExecutionMode.MODULE


def test_mode_requires_matching_refs() -> None:
    payload = _valid_payload()
    payload["capabilities"][0]["module_refs"] = []
    with pytest.raises(ValueError):
        _contracts.build_decomposition_package(payload)


def test_unknown_cross_reference_is_rejected() -> None:
    payload = _valid_payload()
    payload["capabilities"][1]["action_refs"] = ["missing_action"]
    with pytest.raises(ValueError):
        _contracts.build_decomposition_package(payload)


def test_module_route_must_be_absolute() -> None:
    payload = _valid_payload()
    payload["modules"][0]["route"] = "marketplace"
    with pytest.raises(ValueError):
        _contracts.build_decomposition_package(payload)
