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
                "path": "data/contract.json",
                "kind": "data_contract_json",
                "purpose": "Data contract artifact.",
                "entity_refs": ["project"],
                "content": "{\"collections\":[]}\n",
            }
        ],
        "code_files": [
            {
                "filename": "data/contract.json",
                "content": "BROKEN",
            }
        ],
    }

    file_map = extract_code_file_map_from_payload(payload)

    assert file_map["data/contract.json"] == "{\"collections\":[]}\n"


def test_extract_code_file_map_unwraps_provider_output_envelope() -> None:
    payload = {
        "DatabaseOutput": {
            "database_files": [
                {
                    "path": "data/contract.json",
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

    assert file_map == {"data/contract.json": "{\"surfaces\":[]}\n"}


def test_extract_code_file_map_canonicalizes_module_contract_paths() -> None:
    payload = {
        "code_files": [
            {"filename": "modules/tickets/events.yaml", "content": "events: []\n"},
            {"filename": "modules/tickets/admin.yaml", "content": "panels: []\n"},
            {"filename": "modules/tickets/policy_hooks.yaml", "content": "hooks: []\n"},
            {"filename": "modules/tickets/module.yaml", "content": "id: tickets\n"},
        ]
    }

    file_map = extract_code_file_map_from_payload(payload)

    assert file_map == {
        "modules/tickets/contracts/admin.yaml": "panels: []\n",
        "modules/tickets/contracts/events.yaml": "events: []\n",
        "modules/tickets/contracts/policy_hooks.yaml": "hooks: []\n",
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


def test_extract_code_file_map_materializes_module_contract_with_profile_yaml() -> None:
    payload = {
        "module_contract": {
            "module_id": "wallet",
            "module_yaml": {
                "schema_version": "mozaiks.module.v1",
                "id": "wallet",
                "actions": [{"id": "get_wallet_summary", "name": "Get Wallet Summary", "description": "Returns balance."}],
            },
            "profile_yaml": {
                "schema_version": "mozaiks.profile.v1",
                "panels": [
                    {
                        "id": "wallet-balance",
                        "title": "Wallet",
                        "kind": "metrics",
                        "action": "get_wallet_summary",
                        "fields": [
                            {"id": "balance", "label": "Balance", "type": "currency"},
                        ],
                    }
                ],
            },
        }
    }

    file_map = extract_code_file_map_from_payload(payload)

    assert "modules/wallet/contracts/profile.yaml" in file_map
    parsed = yaml.safe_load(file_map["modules/wallet/contracts/profile.yaml"])
    assert parsed["schema_version"] == "mozaiks.profile.v1"
    assert parsed["panels"][0]["id"] == "wallet-balance"
    assert parsed["panels"][0]["kind"] == "metrics"
    assert parsed["panels"][0]["fields"][0]["type"] == "currency"


def test_extract_code_file_map_omits_profile_yaml_when_null() -> None:
    payload = {
        "module_contract": {
            "module_id": "projects",
            "module_yaml": {"schema_version": "mozaiks.module.v1", "id": "projects", "actions": []},
            "profile_yaml": None,
        }
    }

    file_map = extract_code_file_map_from_payload(payload)

    assert "modules/projects/contracts/profile.yaml" not in file_map


def test_extract_code_file_map_materializes_module_contract_with_relationships_yaml() -> None:
    payload = {
        "module_contract": {
            "module_id": "projects",
            "module_yaml": {"schema_version": "mozaiks.module.v1", "id": "projects", "actions": []},
            "relationships_yaml": {
                "schema_version": "mozaiks.relationships.v1",
                "providers": [
                    {
                        "id": "owned-projects",
                        "label": "Owned Projects",
                        "action": "list_user_project_relationships",
                        "resource_types": ["project"],
                        "relationship_types": ["owner"],
                    }
                ],
            },
        }
    }

    file_map = extract_code_file_map_from_payload(payload)

    assert "modules/projects/contracts/relationships.yaml" in file_map
    parsed = yaml.safe_load(file_map["modules/projects/contracts/relationships.yaml"])
    assert parsed["schema_version"] == "mozaiks.relationships.v1"
    assert parsed["providers"][0]["id"] == "owned-projects"
    assert parsed["providers"][0]["resource_types"] == ["project"]


def test_extract_code_file_map_materializes_module_contract_with_policy_hooks_yaml() -> None:
    payload = {
        "module_contract": {
            "module_id": "project_membership",
            "module_yaml": {"schema_version": "mozaiks.module.v1", "id": "project_membership", "actions": []},
            "policy_hooks_yaml": {
                "schema_version": "mozaiks.policy_hooks.v1",
                "hooks": [
                    {
                        "id": "project-participation",
                        "label": "Project Participation",
                        "hook_type": "decision_input",
                        "action": "evaluate_project_participation",
                        "resource_types": ["project"],
                        "deterministic": True,
                    }
                ],
            },
        }
    }

    file_map = extract_code_file_map_from_payload(payload)

    assert "modules/project_membership/contracts/policy_hooks.yaml" in file_map
    parsed = yaml.safe_load(file_map["modules/project_membership/contracts/policy_hooks.yaml"])
    assert parsed["schema_version"] == "mozaiks.policy_hooks.v1"
    assert parsed["hooks"][0]["id"] == "project-participation"
    assert parsed["hooks"][0]["resource_types"] == ["project"]


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
        "provenance.yaml",
        "brand/theme_config.json",
        "ui/pages/tickets.yaml",
    }
    assert '"appName": "Support Operations"' in file_map["app.json"]
    provenance = yaml.safe_load(file_map["provenance.yaml"])
    assert provenance["schema_version"] == "mozaiks.provenance.v1"
    assert provenance["app_kind"] == "generated"
    assert provenance["created_with"]["workflow"] == "AppGenerator"
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


def test_appgenerator_extract_code_file_map_materializes_typed_refinement_harness() -> None:
    payload = {
        "refinement_harness": {
            "harness_yaml": {
                "schema_version": "mozaiks.refinement_harness.v1",
                "extends": "mozaiks.default_refinement_harness",
                "overrides": {
                    "routing": {
                        "artifacts": [
                            {
                                "artifact_kind": "app_bundle",
                                "label": "custom app bundle",
                            }
                        ],
                    },
                    "checkpoints": [
                        {
                            "event": "coding_requested",
                            "append_tool_ids": ["app_local_context"],
                        }
                    ],
                },
            },
            "tools_yaml": {
                "schema_version": "mozaiks.refinement_harness.tools.v1",
                "tools": [
                    {
                        "id": "app_local_context",
                        "kind": "context_tool",
                        "description": "Load app-local refinement context.",
                        "entrypoint": "app.refinement_tools:app_local_context",
                        "available_to": ["coding_requested"],
                    }
                ],
            },
            "policies_yaml": {
                "schema_version": "mozaiks.refinement_harness.policies.v1",
                "scope": {
                    "max_selected_paths": 3,
                    "auto_apply_max_paths": 1,
                    "overflow_behavior": "clarify",
                },
            },
            "prompt_files": [
                {
                    "id": "coding_refinement_system",
                    "filename": "",
                    "content": "Use app-local coding guidance.",
                }
            ],
        },
        "code_files": [
            {
                "filename": "refinement_harness/config/harness.yaml",
                "content": "BROKEN",
            }
        ],
    }

    file_map = extract_appgenerator_code_file_map(payload)

    assert set(file_map) == {
        "app/config/refinement_policy.yaml",
        "refinement_harness/config/harness.yaml",
        "refinement_harness/config/tools.yaml",
        "refinement_harness/config/policies.yaml",
        "refinement_harness/prompts/coding_refinement_system.yaml",
    }
    harness_yaml = yaml.safe_load(file_map["refinement_harness/config/harness.yaml"])
    assert harness_yaml["extends"] == "mozaiks.default_refinement_harness"
    assert harness_yaml["overrides"]["routing"]["artifacts"][0]["label"] == "custom app bundle"
    assert harness_yaml["overrides"]["checkpoints"][0]["append_tool_ids"] == ["app_local_context"]
    assert harness_yaml["overrides"]["prompts"] == {
        "coding_refinement_system": "refinement_harness/prompts/coding_refinement_system.yaml"
    }
    assert yaml.safe_load(file_map["refinement_harness/config/tools.yaml"]) == {
        "schema_version": "mozaiks.refinement_harness.tools.v1",
        "tools": [
            {
                "id": "app_local_context",
                "kind": "context_tool",
                "description": "Load app-local refinement context.",
                "entrypoint": "app.refinement_tools:app_local_context",
                "available_to": ["coding_requested"],
            }
        ],
    }
    assert yaml.safe_load(file_map["refinement_harness/prompts/coding_refinement_system.yaml"]) == {
        "id": "coding_refinement_system",
        "content": "Use app-local coding guidance.",
    }


def test_appgenerator_refinement_harness_rejects_prompt_paths_outside_pack() -> None:
    payload = {
        "refinement_harness": {
            "harness_yaml": {
                "schema_version": "mozaiks.refinement_harness.v1",
                "extends": "mozaiks.default_refinement_harness",
                "overrides": None,
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

    with pytest.raises(ValueError, match="refinement harness prompt files"):
        extract_appgenerator_code_file_map(payload)


def test_appgenerator_refinement_harness_rejects_missing_extends() -> None:
    payload = {
        "refinement_harness": {
            "harness_yaml": {
                "schema_version": "mozaiks.refinement_harness.v1",
                "extends": "app.local_refinement_harness",
                "overrides": {},
            },
        }
    }

    with pytest.raises(ValueError, match="must extend"):
        extract_appgenerator_code_file_map(payload)


def test_appgenerator_refinement_harness_rejects_full_pack_manifest() -> None:
    payload = {
        "refinement_harness": {
            "harness_yaml": {
                "schema_version": "mozaiks.refinement_harness.v1",
                "routing": {
                    "default_artifact_kind": "app_bundle",
                    "artifacts": [
                        {
                            "artifact_kind": "app_bundle",
                            "label": "app bundle",
                            "routes": {
                                "patch": {
                                    "workflow_sequence": "app_revision",
                                    # Extra field that must be rejected
                                    "affected_workflows": ["AppGenerator"],
                                },
                                "design": {"workflow_sequence": "app_surface_revision"},
                                "feature": {"workflow_sequence": "app_revision"},
                                "core": {"workflow_sequence": "full_rebuild"},
                            },
                        }
                    ],
                },
                "checkpoints": [],
            },
        }
    }

    with pytest.raises(ValueError, match="extends overlay"):
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
                        "path": "data/contract.json",
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

    assert file_map["data/contract.json"] == "{\"collections\":[]}\n"
    assert file_map["modules/projects/backend/schemas.py"] == "class ProjectRecord(TypedDict):\n    project_id: str\n"
    assert file_map["backend/config.py"] == "SETTINGS = {}\n"

