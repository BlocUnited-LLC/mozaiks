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
                "primary_surface": "module",
                "actor": "student",
                "module_refs": ["marketplace_home"],
                "view_refs": ["listings_list"],
                "entity_refs": ["Listing"],
            },
            {
                "capability_id": "save_listing",
                "label": "Save listing",
                "primary_surface": "action",
                "actor": "student",
                "action_refs": ["save_favorite"],
                "entity_refs": ["Favorite"],
            },
            {
                "capability_id": "assistant_flow",
                "label": "Assistant flow",
                "primary_surface": "workflow",
                "actor": "student",
                "workflow_refs": ["Concierge"],
                "event_refs": ["listing.saved"],
                "automation_route_refs": ["route_saved_listing_followup"],
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
        "events": [
            {
                "event_type": "listing.saved",
                "producer": "mozaikscore",
                "description": "A listing was saved to favorites",
                "source_event": "listing_saved",
                "correlation_keys": ["listing_id", "user_id"],
                "post_commit_only": True,
            }
        ],
        "automation_routes": [
            {
                "route_id": "route_saved_listing_followup",
                "event_type": "listing.saved",
                "when": {"payload.priority": "high"},
                "effect": {
                    "kind": "workflow.run",
                    "workflow": "Concierge",
                    "surface": "background",
                    "message_template": "Saved listing follow-up for {tenant.user_id}",
                },
                "bindings": {
                    "app_id": "tenant.app_id",
                    "user_id": "tenant.user_id",
                },
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
            "manifest_paths": ["platform/app.json"],
            "shell_paths": [
                "platform/shell/navigation.json",
                "platform/shell/theme.json",
            ],
            "substrate_paths": ["platform/data/entities/listing.json"],
            "module_paths": ["platform/modules/marketplace_home/module.json"],
            "automation_paths": [
                "platform/automations/event_catalog.json",
                "platform/automations/routes.json",
            ],
            "workflow_paths": ["platform/workflows/Concierge/orchestrator.yaml"],
        },
    }


def test_build_decomposition_package_valid_payload() -> None:
    pkg = _contracts.build_decomposition_package(_valid_payload())
    assert pkg.app_spec.name == "CampusMarket"
    assert pkg.capabilities[0].primary_surface == _contracts.PrimarySurface.MODULE


def test_mode_requires_matching_refs() -> None:
    payload = _valid_payload()
    payload["capabilities"][0]["module_refs"] = []
    with pytest.raises(ValueError):
        _contracts.build_decomposition_package(payload)


def test_bundle_plan_rejects_cross_family_duplicate_paths() -> None:
    payload = _valid_payload()
    payload["bundle_plan"]["shell_paths"] = ["platform/app.json"]
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


def test_automation_route_requires_declared_event() -> None:
    payload = _valid_payload()
    payload["automation_routes"][0]["event_type"] = "missing.event"
    with pytest.raises(ValueError):
        _contracts.build_decomposition_package(payload)


def test_concept_blueprint_change_requires_change_summary() -> None:
    with pytest.raises(ValueError):
        _contracts.ConceptBlueprint.model_validate(
            {
                "intent_mode": "change",
                "app_name": "CampusMarket",
                "product_summary": "Student marketplace",
                "value_proposition": "Launch a marketplace quickly.",
                "primary_users": ["student"],
                "approved_scope": ["browse listings"],
                "core_outcomes": ["marketplace launch"],
            }
        )


def test_platform_provision_plan_rejects_unknown_dependency() -> None:
    with pytest.raises(ValueError):
        _contracts.PlatformProvisionPlan.model_validate(
            {
                "provisions": [
                    {
                        "provision_id": "shell-config",
                        "label": "Shell config",
                        "category": "shell",
                        "runtime_owner": "shared",
                        "mode": "core_configured",
                        "summary": "Use platform navigation and theme.",
                        "depends_on": ["missing-provision"],
                        "config_paths": ["platform/config/navigation_config.json"],
                    }
                ]
            }
        )


def test_platform_provision_app_stub_requires_stub_paths() -> None:
    with pytest.raises(ValueError):
        _contracts.PlatformProvisionSpec.model_validate(
            {
                "provision_id": "custom-crm-adapter",
                "label": "Custom CRM adapter",
                "category": "integration",
                "runtime_owner": "mozaikscore",
                "mode": "app_stub",
                "summary": "App-specific adapter written on top of the enterprise core.",
            }
        )


def test_impact_set_requires_at_least_one_affected_surface() -> None:
    with pytest.raises(ValueError):
        _contracts.ImpactSet.model_validate({"change_summary": "Change the shell layout."})
