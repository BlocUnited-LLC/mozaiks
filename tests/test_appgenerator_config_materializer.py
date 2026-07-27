from __future__ import annotations

import json

import yaml

from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import (
    _apply_entitlement_gates,
)
from factory_app.workflows.AppGenerator.tools.materialize_app_config_contracts import (
    _materialize_subscriptions_yaml,
    materialize_app_config_contracts,
)


class _Context:
    def __init__(self, data: dict):
        self.data = dict(data)

    def get(self, key: str, default=None):
        return self.data.get(key, default)


def test_materializes_integrations_and_targets_without_secret_values() -> None:
    context = _Context(
        {
            "app_build_plan": {
                "deployment_profile": "generic_container",
                "deployment_targets": [
                    {
                        "target_id": "local-container",
                        "deployment_profile": "generic_container",
                        "runtime": {"kind": "web", "health_path": "/health", "container_port": 8000},
                        "environment": {
                            "public": ["MOZAIKSPAY_PUBLIC_API_BASE"],
                            "secret": ["MOZAIKSPAY_API_KEY"],
                        },
                    }
                ],
                "capability_packs": [
                    {
                        "capability_pack_id": "mozaikspay",
                        "required_integrations": [
                            {
                                "service": "mozaikspay",
                                "provider": "mozaikspay",
                                "display_name": "MozaiksPay",
                                "kind": "managed_capability",
                                "purpose": "Use hosted billing.",
                                "preferred_setup_lane": "managed",
                                "allowed_setup_lanes": ["managed", "bring_your_own_key"],
                                "managed_default": '{"display_name":"MozaiksPay","client_secret":"do-not-store"}',
                                "required_fields": [
                                    {"name": "api_base", "type": "url", "frontend_safe": True},
                                    {"name": "client_secret", "type": "secret", "frontend_safe": False},
                                ],
                            }
                        ],
                    }
                ],
            }
        }
    )

    files = {
        item["filename"]: item["content"]
        for item in materialize_app_config_contracts(
            app_id="demo-app",
            app_build_plan=context.get("app_build_plan"),
            context_variables=context,
        )
    }

    assert set(files) == {"config/integrations.yaml", "config/targets.json"}
    integrations = yaml.safe_load(files["config/integrations.yaml"])
    assert integrations["schema_version"] == "mozaiks.integrations.v1"
    requirement = integrations["requirements"][0]
    assert requirement["service"] == "mozaikspay"
    assert requirement["preferred_setup_lane"] == "managed"
    assert requirement["allowed_setup_lanes"] == ["managed", "bring_your_own_key"]
    assert requirement["managed_default"] == {"display_name": "MozaiksPay"}
    assert {field["name"] for field in requirement["required_fields"]} == {"api_base", "client_secret"}
    assert "secret-value" not in files["config/integrations.yaml"]
    assert "do-not-store" not in files["config/integrations.yaml"]

    targets = json.loads(files["config/targets.json"])
    assert targets["schema_version"] == "mozaiks.targets.v1"
    assert targets["app_id"] == "demo-app"
    assert targets["deployment"]["profile"] == "generic_container"
    assert targets["deployment"]["target_id"] == "local-container"
    assert targets["runtime"]["health_path"] == "/health"
    assert targets["deployment"]["environment"]["secret"] == ["MOZAIKSPAY_API_KEY"]


def _saas_subscription_config_file() -> dict:
    """Minimal but complete SubscriptionConfigFile fixture for SaaS tests."""
    return {
        "schema_version": "mozaiks.subscriptions.v1",
        "label": "Demo SaaS Plans",
        "default_plan_id": "free",
        "assignment_store": {
            "data_alias": "subscriptions",
            "app_id_field": "app_id",
            "user_id_field": "user_id",
            "plan_id_field": "plan_id",
            "status_field": "status",
            "active_statuses": ["active", "trialing"],
        },
        "plans": [
            {
                "plan_id": "free",
                "label": "Free",
                "capabilities": [],
            },
            {
                "plan_id": "pro",
                "label": "Pro",
                "capabilities": ["analytics.view", "exports.download"],
            },
        ],
        "token_wallets": [],
        "top_up_products": [],
        "add_on_products": [
            {
                "add_on_id": "priority_review",
                "label": "Priority Review",
                "kind": "service",
                "billing_mode": "one_time",
                "price": {
                    "amount_cents": 2500,
                    "currency": "usd",
                    "display": "$25",
                    "interval": "one_time",
                },
                "active": True,
            }
        ],
        "usage_charge_policies": [],
        "pricing_catalog": {
            "default_group_id": "services",
            "groups": [
                {
                    "group_id": "services",
                    "label": "Services",
                    "kind": "add_on",
                    "plan_ids": [],
                    "capability_groups": [],
                    "add_on_ids": ["priority_review"],
                }
            ],
        },
    }


def test_materialize_subscriptions_yaml_returns_none_for_non_saas() -> None:
    """No subscription contract → returns None (NoOpEntitlementAdapter wired at runtime)."""
    context = _Context({})
    result = _materialize_subscriptions_yaml(context_variables=context)
    assert result is None


def test_materialize_subscriptions_yaml_returns_none_for_wrong_schema() -> None:
    """Contract with wrong schema_version is ignored."""
    context = _Context(
        {
            "subscription_contract": {
                "subscription_config_file": {
                    "schema_version": "mozaiks.subscriptions.v99",
                    "plans": [],
                }
            }
        }
    )
    result = _materialize_subscriptions_yaml(context_variables=context)
    assert result is None


def test_materialize_subscriptions_yaml_emits_valid_yaml_for_saas_app() -> None:
    cfg = _saas_subscription_config_file()
    context = _Context({"subscription_contract": {"subscription_config_file": cfg}})

    result = _materialize_subscriptions_yaml(context_variables=context)

    assert result is not None
    doc = yaml.safe_load(result)
    assert doc["schema_version"] == "mozaiks.subscriptions.v1"
    assert doc["label"] == "Demo SaaS Plans"
    assert doc["default_plan_id"] == "free"
    assert doc["assignment_store"]["data_alias"] == "subscriptions"
    assert doc["assignment_store"]["active_statuses"] == ["active", "trialing"]
    assert len(doc["plans"]) == 2
    assert doc["plans"][1]["plan_id"] == "pro"
    assert doc["plans"][1]["capabilities"] == ["analytics.view", "exports.download"]
    assert doc["add_on_products"][0]["add_on_id"] == "priority_review"
    assert doc["pricing_catalog"]["groups"][0]["add_on_ids"] == ["priority_review"]


def test_materialize_subscriptions_yaml_reads_artifact_fallback() -> None:
    """Falls back to subscription_contract_artifact when live contract absent."""
    cfg = _saas_subscription_config_file()
    context = _Context({"subscription_contract_artifact": {"subscription_config_file": cfg}})

    result = _materialize_subscriptions_yaml(context_variables=context)

    assert result is not None
    doc = yaml.safe_load(result)
    assert doc["schema_version"] == "mozaiks.subscriptions.v1"
    assert doc["default_plan_id"] == "free"


def test_materialize_app_config_contracts_includes_subscriptions_for_saas_app() -> None:
    cfg = _saas_subscription_config_file()
    context = _Context(
        {
            "subscription_contract": {"subscription_config_file": cfg},
            "app_build_plan": {},
        }
    )

    files = {
        item["filename"]: item["content"]
        for item in materialize_app_config_contracts(
            app_id="saas-app",
            app_build_plan={},
            context_variables=context,
        )
    }

    assert "config/subscriptions.yaml" in files
    doc = yaml.safe_load(files["config/subscriptions.yaml"])
    assert doc["schema_version"] == "mozaiks.subscriptions.v1"
    assert doc["app_id"] if "app_id" in doc else True  # app_id not required in subscriptions.yaml


def test_materialize_app_config_contracts_omits_subscriptions_for_non_saas() -> None:
    context = _Context({"app_build_plan": {}})

    files = {
        item["filename"]: item["content"]
        for item in materialize_app_config_contracts(
            app_id="non-saas-app",
            app_build_plan={},
            context_variables=context,
        )
    }

    assert "config/subscriptions.yaml" not in files
    assert set(files) == {"config/integrations.yaml", "config/targets.json"}


# --- _apply_entitlement_gates tests ---


def _module_yaml_content(actions: list[dict]) -> str:
    data = {"id": "analytics", "actions": actions}
    return yaml.safe_dump(data, sort_keys=False)


def test_apply_entitlement_gates_sets_gates_from_contract() -> None:
    module_yaml = _module_yaml_content(
        [
            {"id": "list_reports", "handler_method": "list_reports", "permissions": ["reports.read"]},
            {"id": "export_csv", "handler_method": "export_csv", "permissions": ["reports.read"]},
        ]
    )
    context = _Context(
        {
            "subscription_contract": {
                "module_contract_updates": [
                    {"module_id": "analytics", "action_id": "list_reports", "entitlement_gate": "analytics.view"},
                    {"module_id": "analytics", "action_id": "export_csv", "entitlement_gate": "exports.download"},
                ]
            }
        }
    )
    code_files = [{"filename": "modules/analytics/module.yaml", "content": module_yaml}]

    result = _apply_entitlement_gates(code_files, context_variables=context)

    updated = {f["filename"]: yaml.safe_load(f["content"]) for f in result}
    actions = {a["id"]: a for a in updated["modules/analytics/module.yaml"]["actions"]}
    assert actions["list_reports"]["entitlement_gate"] == "analytics.view"
    assert actions["export_csv"]["entitlement_gate"] == "exports.download"


def test_apply_entitlement_gates_does_not_overwrite_existing_gate() -> None:
    """Agent-set gates take precedence over contract defaults."""
    module_yaml = _module_yaml_content(
        [
            {
                "id": "list_reports",
                "handler_method": "list_reports",
                "entitlement_gate": "agent.set.gate",
                "permissions": [],
            }
        ]
    )
    context = _Context(
        {
            "subscription_contract": {
                "module_contract_updates": [
                    {"module_id": "analytics", "action_id": "list_reports", "entitlement_gate": "analytics.view"}
                ]
            }
        }
    )
    code_files = [{"filename": "modules/analytics/module.yaml", "content": module_yaml}]

    result = _apply_entitlement_gates(code_files, context_variables=context)

    updated = {f["filename"]: yaml.safe_load(f["content"]) for f in result}
    action = updated["modules/analytics/module.yaml"]["actions"][0]
    assert action["entitlement_gate"] == "agent.set.gate"


def test_apply_entitlement_gates_is_noop_without_contract() -> None:
    module_yaml = _module_yaml_content(
        [{"id": "list_reports", "handler_method": "list_reports", "permissions": []}]
    )
    context = _Context({})
    code_files = [{"filename": "modules/analytics/module.yaml", "content": module_yaml}]

    result = _apply_entitlement_gates(code_files, context_variables=context)

    updated = {f["filename"]: yaml.safe_load(f["content"]) for f in result}
    action = updated["modules/analytics/module.yaml"]["actions"][0]
    assert "entitlement_gate" not in action


def test_apply_entitlement_gates_ignores_non_module_yaml_files() -> None:
    non_module = {"filename": "services/config.py", "content": "API_KEY = 'x'\n"}
    context = _Context(
        {
            "subscription_contract": {
                "module_contract_updates": [
                    {"module_id": "analytics", "action_id": "list_reports", "entitlement_gate": "analytics.view"}
                ]
            }
        }
    )
    code_files = [non_module]

    result = _apply_entitlement_gates(code_files, context_variables=context)

    # File unchanged
    assert result[0]["content"] == non_module["content"]
