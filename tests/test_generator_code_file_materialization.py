from __future__ import annotations

from factory_app.workflows.AppGenerator.tools.assembly_phase import _merge_code_files
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
                "path": "backend/database/schema.json",
                "kind": "schema_json",
                "purpose": "Schema artifact.",
                "entity_refs": ["user"],
                "content": "{\"collections\":[]}\n",
            }
        ],
        "code_files": [
            {
                "filename": "backend/database/schema.json",
                "content": "BROKEN",
            }
        ],
    }

    file_map = extract_code_file_map_from_payload(payload)

    assert file_map["backend/database/schema.json"] == "{\"collections\":[]}\n"


def test_extract_code_file_map_materializes_typed_model_output() -> None:
    payload = {
        "model_files": [
            {
                "path": "backend/models/user.py",
                "entity_name": "user",
                "purpose": "User model.",
                "content": "class User:\n    pass\n",
            }
        ],
        "code_files": [
            {
                "filename": "backend/models/user.py",
                "content": "BROKEN",
            }
        ],
    }

    file_map = extract_code_file_map_from_payload(payload)

    assert file_map["backend/models/user.py"] == "class User:\n    pass\n"


def test_extract_code_file_map_materializes_typed_backend_foundation_output() -> None:
    payload = {
        "backend_foundation_bundle": {
            "files": [
                {
                    "path": "backend/config.py",
                    "kind": "config",
                    "purpose": "Config loader.",
                    "contract_refs": ["build_tasks[backend_foundation].owned_paths"],
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
                "registry_key": "campaigns.metrics",
                "purpose": "Campaign metrics custom panel.",
                "contract_refs": ["admin_yaml.panels[campaigns.metrics].component"],
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
                        "registry_key": "campaigns.metrics",
                        "purpose": "Campaign metrics custom panel.",
                        "contract_refs": ["admin_yaml.panels[campaigns.metrics].component"],
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


def test_assembly_phase_merges_typed_database_model_and_backend_foundation_outputs() -> None:
    merged = _merge_code_files(
        [
            {
                "database_files": [
                    {
                        "path": "backend/database/schema.json",
                        "kind": "schema_json",
                        "purpose": "Schema artifact.",
                        "entity_refs": ["user"],
                        "content": "{\"collections\":[]}\n",
                    }
                ],
                "code_files": [],
            },
            {
                "model_files": [
                    {
                        "path": "backend/models/user.py",
                        "entity_name": "user",
                        "purpose": "User model.",
                        "content": "class User:\n    pass\n",
                    }
                ],
                "code_files": [],
            },
            {
                "backend_foundation_bundle": {
                    "files": [
                        {
                            "path": "backend/config.py",
                            "kind": "config",
                            "purpose": "Config loader.",
                            "contract_refs": ["build_tasks[backend_foundation].owned_paths"],
                            "content": "SETTINGS = {}\n",
                        }
                    ]
                },
                "code_files": [],
            },
        ]
    )

    file_map = {entry["filename"]: entry["content"] for entry in merged}

    assert file_map["backend/database/schema.json"] == "{\"collections\":[]}\n"
    assert file_map["backend/models/user.py"] == "class User:\n    pass\n"
    assert file_map["backend/config.py"] == "SETTINGS = {}\n"
