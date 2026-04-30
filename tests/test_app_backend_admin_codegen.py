from __future__ import annotations

import importlib
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from factory_app.app.workflows.AppGenerator.tools.assembly_phase import _merge_code_files
from mozaiksai.core.admin import build_app_backend_admin_code_files
from mozaiksai.core.workflow.generator_support.code_files import (
    extract_code_file_map_from_payload,
)


def _valid_admin_config() -> dict:
    return {
        "schema_version": "mozaiks.admin.app_backend.v1",
        "panels": [
            {
                "id": "app.users",
                "label": "Users",
                "section": "users",
                "order": 10,
                "renderer": "builtin",
                "builtin_panel": "users",
                "permissions": ["admin.users.read"],
            },
            {
                "id": "billing.summary",
                "label": "Billing",
                "section": "billing",
                "order": 20,
                "renderer": "schema",
                "layout": "full-width",
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


def _write_package_file(root: Path, relative_path: str, content: str) -> None:
    target = root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_build_app_backend_admin_code_files_produces_importable_surface(tmp_path: Path, monkeypatch) -> None:
    code_files = build_app_backend_admin_code_files(_valid_admin_config())
    file_map = {entry["filename"]: entry["content"] for entry in code_files}

    assert set(file_map) == {"backend/admin_config.py", "backend/routes/admin.py"}

    _write_package_file(tmp_path, "backend/__init__.py", "")
    _write_package_file(tmp_path, "backend/routes/__init__.py", "")
    for filename, content in file_map.items():
        _write_package_file(tmp_path, filename, content)

    monkeypatch.syspath_prepend(str(tmp_path))
    for module_name in [
        "backend",
        "backend.admin_config",
        "backend.routes",
        "backend.routes.admin",
    ]:
        sys.modules.pop(module_name, None)

    admin_config_module = importlib.import_module("backend.admin_config")
    admin_config = admin_config_module.get_admin_config()
    assert admin_config.schema_version == "mozaiks.admin.app_backend.v1"
    assert admin_config.panels[0].builtin_panel == "users"

    route_module = importlib.import_module("backend.routes.admin")
    app = FastAPI()
    app.include_router(route_module.router)

    client = TestClient(app)
    response = client.get("/api/admin/config")

    assert response.status_code == 200
    assert response.json()["schema_version"] == "mozaiks.admin.app_backend.v1"


def test_extract_code_file_map_prefers_typed_app_backend_admin_config() -> None:
    payload = {
        "mode": "app_backend_admin_surface",
        "app_backend_admin_config": _valid_admin_config(),
        "code_files": [
            {
                "filename": "backend/admin_config.py",
                "content": "BROKEN",
            }
        ],
    }

    file_map = extract_code_file_map_from_payload(payload)

    assert "BROKEN" not in file_map["backend/admin_config.py"]
    assert "validate_app_backend_admin_config" in file_map["backend/admin_config.py"]
    assert "build_app_backend_admin_router" in file_map["backend/routes/admin.py"]


def test_assembly_phase_materializes_split_admin_surface_from_typed_payload() -> None:
    merged = _merge_code_files(
        [
            {
                "mode": "app_backend_admin_surface",
                "app_backend_admin_config": _valid_admin_config(),
                "code_files": [],
            }
        ]
    )

    file_map = {entry["filename"]: entry["content"] for entry in merged}

    assert set(file_map) == {"backend/admin_config.py", "backend/routes/admin.py"}
    assert "mozaiks.admin.app_backend.v1" in file_map["backend/admin_config.py"]
    assert "build_app_backend_admin_router" in file_map["backend/routes/admin.py"]
