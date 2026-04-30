from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mozaiksai.core.admin import build_app_backend_admin_router


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
                            "columns": [
                                {"key": "plan", "label": "Plan"},
                                {"key": "status", "label": "Status", "type": "badge"},
                            ],
                        },
                    }
                ],
            },
        ],
    }


def test_build_app_backend_admin_router_returns_validated_config() -> None:
    app = FastAPI()
    app.include_router(build_app_backend_admin_router(_valid_admin_config))

    client = TestClient(app)
    response = client.get("/api/admin/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "mozaiks.admin.app_backend.v1"
    assert payload["panels"][0]["renderer"] == "builtin"
    assert payload["panels"][0]["builtin_panel"] == "users"
    assert payload["panels"][1]["renderer"] == "schema"
    assert payload["panels"][1]["layout"] == "full-width"


def test_build_app_backend_admin_router_supports_async_provider() -> None:
    async def provider() -> dict:
        return _valid_admin_config()

    app = FastAPI()
    app.include_router(build_app_backend_admin_router(provider))

    client = TestClient(app)
    response = client.get("/api/admin/config")

    assert response.status_code == 200
    assert response.json()["schema_version"] == "mozaiks.admin.app_backend.v1"


def test_build_app_backend_admin_router_rejects_invalid_payload() -> None:
    app = FastAPI()
    app.include_router(
        build_app_backend_admin_router(
            lambda: {
                "panels": [
                    {
                        "id": "legacy.stats",
                        "label": "Legacy Stats",
                        "section": "overview",
                        "renderer": "builtin",
                        "builtin_panel": "stats",
                    }
                ]
            }
        )
    )

    client = TestClient(app)
    response = client.get("/api/admin/config")

    assert response.status_code == 500
    assert "mozaiks.admin.app_backend.v1" in response.json()["detail"]
