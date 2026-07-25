from __future__ import annotations

import json

import yaml

from factory_app.workflows.AppGenerator.tools.materialize_app_config_contracts import (
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
