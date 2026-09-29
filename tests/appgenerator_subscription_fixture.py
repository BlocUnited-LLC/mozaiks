"""Persisted feature selections used by AppGenerator gate integration tests."""

from __future__ import annotations


def subscription_contract(
    module_id: str, *actions: str, free_actions: tuple[str, ...] = (),
) -> dict:
    selected = {action: f"module.{module_id}.{action}" for action in actions}
    return {
        "contract_required": True,
        "selected_features_by_plan": {
            "free": sorted(selected[action] for action in free_actions),
            "pro": sorted(selected.values()),
        },
        "subscription_config_file": {
            "schema_version": "mozaiks.subscriptions.v1",
            "default_plan_id": "free",
            "plans": [
                {"plan_id": "free", "label": "Free", "capabilities": sorted(
                    f"feature.{selected[action]}" for action in free_actions
                )},
                {"plan_id": "pro", "label": "Pro", "capabilities": sorted(
                    f"feature.{feature}" for feature in selected.values()
                )},
            ],
        },
        "module_contract_updates": [
            {"module_id": module_id, "action_id": action, "entitlement_gate": f"feature.{feature}"}
            for action, feature in selected.items()
        ],
    }
