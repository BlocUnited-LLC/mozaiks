import asyncio
import importlib.util
from pathlib import Path


def _load_studio_summary_module():
    workspace = Path(__file__).resolve().parents[1]
    file_path = workspace / "mozaiksai/core/runtime/app/studio_summary.py"
    spec = importlib.util.spec_from_file_location("tests.studio_summary_module", file_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_integrations_summary_always_includes_connector_vault(monkeypatch) -> None:
    studio_summary = _load_studio_summary_module()

    async def fake_connector_backend_summary():
        return {
            "provider": "disabled",
            "configured": False,
            "mode": "auto",
            "vault_name": None,
            "secret_prefix": "mozaiks-connector",
            "error": None,
        }

    monkeypatch.delenv("AZURE_KEY_VAULT_NAME", raising=False)
    monkeypatch.setattr(studio_summary, "get_connector_backend_summary", fake_connector_backend_summary)

    summary = asyncio.run(studio_summary.build_integrations_summary())

    assert "connector_vault" in summary["integrations"]
    assert summary["integrations"]["connector_vault"]["kind"] == "vault"
    assert summary["integrations"]["connector_vault"]["mode"] == "auto"
    assert summary["runtime_integrations"]["connector_vault"]["provider"] == "disabled"


def test_integrations_summary_includes_connector_health_counts(monkeypatch) -> None:
    studio_summary = _load_studio_summary_module()

    async def fake_connector_backend_summary():
        return {
            "provider": "disabled",
            "configured": False,
            "mode": "auto",
            "vault_name": None,
            "secret_prefix": "mozaiks-connector",
            "error": None,
        }

    async def fake_list_connectors(app_id: str):
        assert app_id == "app_1"
        return [
            {
                "service": "analytics_provider",
                "status": "active",
                "health": {
                    "status": "configured",
                    "missing_fields": [],
                    "frontend_safe": True,
                },
            },
            {
                "service": "email_provider",
                "status": "metadata_only",
                "health": {
                    "status": "not_configured",
                    "missing_fields": ["api_key"],
                    "frontend_safe": True,
                },
            },
        ]

    monkeypatch.setattr(studio_summary, "get_connector_backend_summary", fake_connector_backend_summary)
    monkeypatch.setattr(studio_summary, "list_connectors", fake_list_connectors)

    summary = asyncio.run(studio_summary.build_integrations_summary(app_id="app_1"))

    assert summary["connector_summary"]["configured"] == 1
    assert summary["connector_summary"]["not_configured"] == 1
    assert summary["app_connectors"][1]["health"]["missing_fields"] == ["api_key"]

