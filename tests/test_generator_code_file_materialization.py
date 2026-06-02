from __future__ import annotations

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.assembly_phase import _merge_code_files
from factory_app.workflows.AppGenerator.tools.code_file_utils import (
    extract_code_file_map_from_payload as extract_appgenerator_code_file_map,
)
from mozaiksai.core.workflow.generator_support.code_files import (
    extract_code_file_entries_from_payload,
    extract_code_file_map_from_payload,
)


def test_extract_code_file_map_materializes_typed_service_output() -> None:
    payload = {
        "python_files": [
            {
                "path": "modules/task_manager/backend/handler.py",
                "kind": "handler",
                "purpose": "Implements module actions.",
                "contract_refs": ["module_yaml.actions[*].handler_method"],
                "content": "class TaskManagerModule:\n    pass\n",
            }
        ],
        "code_files": [
            {
                "filename": "modules/task_manager/backend/handler.py",
                "content": "BROKEN",
            }
        ],
    }

    file_map = extract_code_file_map_from_payload(payload)

    assert file_map["modules/task_manager/backend/handler.py"] == "class TaskManagerModule:\n    pass\n"


def test_extract_code_file_map_materializes_typed_database_output() -> None:
    payload = {
        "database_files": [
            {
                "path": "config/data.json",
                "kind": "data_contract_json",
                "purpose": "Data contract artifact.",
                "entity_refs": ["project"],
                "content": "{\"collections\":[]}\n",
            }
        ],
        "code_files": [
            {
                "filename": "config/data.json",
                "content": "BROKEN",
            }
        ],
    }

    file_map = extract_code_file_map_from_payload(payload)

    assert file_map["config/data.json"] == "{\"collections\":[]}\n"


def test_extract_code_file_map_unwraps_provider_output_envelope() -> None:
    payload = {
        "DatabaseOutput": {
            "database_files": [
                {
                    "path": "config/data.json",
                    "kind": "data_contract_json",
                    "purpose": "Data contract artifact.",
                    "entity_refs": ["ticket"],
                    "content": "{\"surfaces\":[]}\n",
                }
            ],
            "pending_schema_migration": None,
            "agent_message": "Staged data contract.",
        }
    }

    file_map = extract_code_file_map_from_payload(payload)

    assert file_map == {"config/data.json": "{\"surfaces\":[]}\n"}


def test_extract_code_file_map_canonicalizes_module_contract_paths() -> None:
    payload = {
        "code_files": [
            {"filename": "modules/tickets/events.yaml", "content": "events: []\n"},
            {"filename": "modules/tickets/admin.yaml", "content": "panels: []\n"},
            {"filename": "modules/tickets/module.yaml", "content": "id: tickets\n"},
        ]
    }

    file_map = extract_code_file_map_from_payload(payload)

    assert file_map == {
        "modules/tickets/contracts/admin.yaml": "panels: []\n",
        "modules/tickets/contracts/events.yaml": "events: []\n",
        "modules/tickets/module.yaml": "id: tickets\n",
    }


def test_extract_code_file_map_materializes_typed_module_contract_bundle() -> None:
    payload = {
        "module_contract": {
            "module_id": "tickets",
            "module_yaml": {
                "schema_version": "mozaiks.module.v1",
                "id": "tickets",
                "actions": [],
            },
            "events_yaml": {
                "schema_version": "mozaiks.events.v1",
                "events": [],
            },
            "settings_yaml": {
                "schema_version": "mozaiks.settings.v1",
                "settings": [],
            },
            "admin_yaml": {
                "schema_version": "mozaiks.admin.v2",
                "panels": [],
            },
            "reactions_yaml": {"schema_version": "mozaiks.reactions.v1", "reactions": []},
            "notifications_yaml": None,
            "runtime_extensions_yaml": None,
        }
    }

    file_map = extract_code_file_map_from_payload(payload)

    assert set(file_map) == {
        "modules/tickets/module.yaml",
        "modules/tickets/contracts/events.yaml",
        "modules/tickets/contracts/settings.yaml",
        "modules/tickets/contracts/admin.yaml",
        "modules/tickets/contracts/reactions.yaml",
    }
    assert yaml.safe_load(file_map["modules/tickets/module.yaml"])["id"] == "tickets"


def test_extract_code_file_map_materializes_app_schema_output() -> None:
    payload = {
        "AppSchemaOutput": {
            "agent_message": "Generated pages.",
            "manifest": {
                "app_name": "Support Operations",
                "default_route": "/tickets",
                "auth_strategy": "role-based",
            },
            "pages": [
                {
                    "name": "Tickets",
                    "route": "/tickets",
                    "sections": [],
                }
            ],
            "custom_route_bundle": None,
            "theme_config_patch": {"theme": {"appearance": "dark"}},
            "shell_config": None,
            "asset_manifest": None,
            "data_contract": None,
        }
    }

    file_map = extract_code_file_map_from_payload(payload)

    assert set(file_map) == {
        "app.json",
        "brand/theme_config.json",
        "ui/pages/tickets.yaml",
    }
    assert '"appName": "Support Operations"' in file_map["app.json"]
    assert "name: Tickets" in file_map["ui/pages/tickets.yaml"]


def test_extract_code_file_map_materializes_typed_model_output() -> None:
    payload = {
        "model_files": [
            {
                "path": "modules/projects/backend/schemas.py",
                "entity_name": "project",
                "purpose": "Project document shapes.",
                "content": "class ProjectRecord(TypedDict):\n    project_id: str\n",
            }
        ],
        "code_files": [
            {
                "filename": "modules/projects/backend/schemas.py",
                "content": "BROKEN",
            }
        ],
    }

    file_map = extract_code_file_map_from_payload(payload)

    assert file_map["modules/projects/backend/schemas.py"] == "class ProjectRecord(TypedDict):\n    project_id: str\n"


def test_extract_code_file_map_materializes_typed_service_foundation_output() -> None:
    payload = {
        "service_foundation_bundle": {
            "files": [
                {
                    "path": "backend/config.py",
                    "kind": "config",
                    "purpose": "Config loader.",
                    "contract_refs": ["build_tasks[service_foundation].owned_paths"],
                    "content": "SETTINGS = {}\n",
                }
            ]
        },
        "code_files": [
            {
                "filename": "backend/config.py",
                "content": "BROKEN",
            }
        ],
    }

    file_map = extract_code_file_map_from_payload(payload)

    assert file_map["backend/config.py"] == "SETTINGS = {}\n"


def test_extract_code_file_map_materializes_typed_frontend_stub_output() -> None:
    payload = {
        "js_files": [
            {
                "path": "ui/admin/CampaignMetricsPanel.jsx",
                "surface": "admin_component",
                "registry_key": "projects.metrics",
                "purpose": "Campaign metrics custom panel.",
                "contract_refs": ["admin_yaml.panels[projects.metrics].component"],
                "content": "export default function CampaignMetricsPanel() { return null; }\n",
            }
        ],
        "registration_barrel": "export function register() {}\n",
        "code_files": [],
    }

    file_map = extract_code_file_map_from_payload(payload)

    assert file_map["ui/admin/CampaignMetricsPanel.jsx"] == "export default function CampaignMetricsPanel() { return null; }\n"
    assert file_map["ui/index.js"] == "export function register() {}\n"


def test_extract_code_file_entries_sorts_typed_materialized_files() -> None:
    payload = {
        "js_files": [
            {
                "path": "ui/admin/ZetaPanel.jsx",
                "surface": "admin_component",
                "registry_key": "zeta.panel",
                "purpose": "Zeta panel.",
                "contract_refs": [],
                "content": "export default function ZetaPanel() { return null; }\n",
            }
        ],
        "registration_barrel": "export function register() {}\n",
        "python_files": [
            {
                "path": "modules/demo/backend/handler.py",
                "kind": "handler",
                "purpose": "Demo handler.",
                "contract_refs": [],
                "content": "class DemoModule:\n    pass\n",
            }
        ],
    }

    entries = extract_code_file_entries_from_payload(payload)
    filenames = [entry["filename"] for entry in entries]

    assert filenames == sorted(filenames)


def test_appgenerator_extract_code_file_map_materializes_typed_control_plane_pack() -> None:
    payload = {
        "control_plane_pack": {
            "control_plane_yaml": {
                "schema_version": "mozaiks.control_plane",
                "profile": {
                    "id": "app_refinement_harness",
                    "display_name": "App Refinement Harness",
                    "description": "App-local refinement harness.",
                },
                "harness": {
                    "implementation": "mozaiksai.control_plane.implementations.orchestration_control:OrchestrationControlHarness",
                    "supported_trigger_sources": ["refinement"],
                },
                "routing": {
                    "default_artifact_kind": "app_bundle",
                    "artifacts": [
                        {
                            "artifact_kind": "app_bundle",
                            "label": "app bundle",
                            "routes": {
                                "patch": {
                                    "workflow_sequence": "app_revision",
                                }
                            },
                        }
                    ],
                },
                "checkpoints": [],
            },
            "tools_yaml": {
                "schema_version": "mozaiks.control_plane.tools",
                "tools": [],
            },
            "policies_yaml": {
                "scope": {
                    "max_files": 12,
                }
            },
            "prompt_files": [
                {
                    "id": "change classifier system",
                    "filename": "",
                    "content": "Classify the refinement request.",
                }
            ],
        },
        "code_files": [
            {
                "filename": "control_plane/config/control_plane.yaml",
                "content": "BROKEN",
            }
        ],
    }

    file_map = extract_appgenerator_code_file_map(payload)

    assert set(file_map) == {
        "control_plane/config/runtime.yaml",
        "control_plane/config/control_plane.yaml",
        "control_plane/config/tools.yaml",
        "control_plane/config/policies.yaml",
        "control_plane/prompts/change_classifier_system.yaml",
    }
    assert yaml.safe_load(file_map["control_plane/config/control_plane.yaml"])["routing"]["artifacts"][0]["routes"]["patch"] == {
        "workflow_sequence": "app_revision",
    }
    assert yaml.safe_load(file_map["control_plane/config/tools.yaml"]) == {
        "schema_version": "mozaiks.control_plane.tools",
        "tools": [],
    }
    assert yaml.safe_load(file_map["control_plane/prompts/change_classifier_system.yaml"]) == {
        "id": "change classifier system",
        "content": "Classify the refinement request.",
    }


def test_appgenerator_control_plane_pack_rejects_prompt_paths_outside_pack() -> None:
    payload = {
        "control_plane_pack": {
            "control_plane_yaml": {
                "schema_version": "mozaiks.control_plane",
                "profile": {"id": "demo", "display_name": "Demo", "description": "Demo"},
                "harness": {
                    "implementation": "mozaiksai.control_plane.implementations.orchestration_control:OrchestrationControlHarness",
                    "supported_trigger_sources": ["refinement"],
                },
                "routing": {"default_artifact_kind": "app_bundle", "artifacts": []},
                "checkpoints": [],
            },
            "tools_yaml": {
                "schema_version": "mozaiks.control_plane.tools",
                "tools": [],
            },
            "prompt_files": [
                {
                    "id": "unsafe_prompt",
                    "filename": "prompts/unsafe_prompt.yaml",
                    "content": "No.",
                }
            ],
        }
    }

    with pytest.raises(ValueError, match="control-plane prompt files"):
        extract_appgenerator_code_file_map(payload)


def test_assembly_phase_merges_typed_service_and_frontend_outputs() -> None:
    merged = _merge_code_files(
        [
            {
                "python_files": [
                    {
                        "path": "modules/task_manager/backend/handler.py",
                        "kind": "handler",
                        "purpose": "Implements module actions.",
                        "contract_refs": ["module_yaml.actions[*].handler_method"],
                        "content": "class TaskManagerModule:\n    pass\n",
                    }
                ],
                "code_files": [],
            },
            {
                "js_files": [
                    {
                        "path": "ui/admin/CampaignMetricsPanel.jsx",
                        "surface": "admin_component",
                        "registry_key": "projects.metrics",
                        "purpose": "Campaign metrics custom panel.",
                        "contract_refs": ["admin_yaml.panels[projects.metrics].component"],
                        "content": "export default function CampaignMetricsPanel() { return null; }\n",
                    }
                ],
                "registration_barrel": "export function register() {}\n",
                "code_files": [],
            },
        ]
    )

    file_map = {entry["filename"]: entry["content"] for entry in merged}

    assert "modules/task_manager/backend/handler.py" in file_map
    assert "ui/admin/CampaignMetricsPanel.jsx" in file_map
    assert file_map["ui/index.js"] == "export function register() {}\n"


def test_assembly_phase_merges_typed_database_model_and_service_foundation_outputs() -> None:
    merged = _merge_code_files(
        [
            {
                "database_files": [
                    {
                        "path": "config/data.json",
                        "kind": "data_contract_json",
                        "purpose": "Data contract artifact.",
                        "entity_refs": ["project"],
                        "content": "{\"collections\":[]}\n",
                    }
                ],
                "code_files": [],
            },
            {
                "model_files": [
                    {
                        "path": "modules/projects/backend/schemas.py",
                        "entity_name": "project",
                        "purpose": "Project document shapes.",
                        "content": "class ProjectRecord(TypedDict):\n    project_id: str\n",
                    }
                ],
                "code_files": [],
            },
            {
                "service_foundation_bundle": {
                    "files": [
                        {
                            "path": "backend/config.py",
                            "kind": "config",
                            "purpose": "Config loader.",
                            "contract_refs": ["build_tasks[service_foundation].owned_paths"],
                            "content": "SETTINGS = {}\n",
                        }
                    ]
                },
                "code_files": [],
            },
        ]
    )

    file_map = {entry["filename"]: entry["content"] for entry in merged}

    assert file_map["config/data.json"] == "{\"collections\":[]}\n"
    assert file_map["modules/projects/backend/schemas.py"] == "class ProjectRecord(TypedDict):\n    project_id: str\n"
    assert file_map["backend/config.py"] == "SETTINGS = {}\n"
