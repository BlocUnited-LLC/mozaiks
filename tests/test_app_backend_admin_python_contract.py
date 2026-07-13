from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_backend_admin_contract import (
    validate_app_backend_admin_config,
)


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def test_python_app_backend_admin_contract_accepts_builtin_and_schema_panels() -> None:
    config = validate_app_backend_admin_config(
        {
            "schema_version": "mozaiks.admin.app_backend.v1",
            "panels": [
                {
                    "id": "app.access",
                    "label": "Access",
                    "section": "access",
                    "renderer": "builtin",
                    "builtin_panel": "users",
                },
                {
                    "id": "billing.summary",
                    "label": "Billing",
                    "section": "billing",
                    "renderer": "schema",
                    "sections": [
                        {
                            "id": "billing-table",
                            "primitive": "DataTable",
                            "config": {
                                "api_endpoint": "/api/admin/billing",
                                "columns": [{"key": "plan", "label": "Plan"}],
                            },
                        }
                    ],
                },
            ],
        }
    )

    assert config.schema_version == "mozaiks.admin.app_backend.v1"
    assert config.panels[0].renderer == "builtin"
    assert config.panels[0].builtin_panel == "users"
    assert config.panels[1].renderer == "schema"
    assert config.panels[1].layout == "full-width"


def test_python_app_backend_admin_contract_rejects_removed_nested_panels_shape() -> None:
    with pytest.raises(Exception, match="schema_version"):
        validate_app_backend_admin_config(
            {
                "panels": {
                    "app": [],
                    "modules": [],
                    "runtime": [],
                }
            }
        )


def test_python_app_backend_admin_contract_rejects_builtin_without_builtin_panel() -> None:
    with pytest.raises(Exception, match="builtin_panel"):
        validate_app_backend_admin_config(
            {
                "schema_version": "mozaiks.admin.app_backend.v1",
                "panels": [
                    {
                        "id": "app.access",
                        "label": "Access",
                        "section": "access",
                        "renderer": "builtin",
                    }
                ],
            }
        )


def test_appgenerator_structured_output_contract_matches_runtime_schema_version_and_section_ids() -> None:
    structured_outputs = yaml.safe_load(
        (_workspace() / "factory_app/workflows/AppGenerator/structured_outputs.yaml").read_text(encoding="utf-8")
    )
    models = structured_outputs["models"]

    assert models["AppBackendAdminConfig"]["fields"]["schema_version"]["values"] == [
        "mozaiks.admin.app_backend.v1"
    ]
    assert models["AppBackendAdminPanel"]["fields"]["section"]["values"] == [
        "overview",
        "access",
        "billing",
        "usage",
        "operations",
        "settings",
        "integrations",
        "support",
    ]
    assert models["AppBackendAdminPanel"]["fields"]["renderer"]["values"] == [
        "builtin",
        "schema",
        "custom_component",
    ]

