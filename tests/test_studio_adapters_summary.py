import asyncio

from mozaiksai.core.runtime.app import studio_home


def test_build_studio_adapters_summary_always_includes_connector_vault(monkeypatch) -> None:
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
    monkeypatch.setattr(studio_home, "get_connector_backend_summary", fake_connector_backend_summary)

    summary = asyncio.run(studio_home.build_studio_adapters_summary())

    assert "connector_vault" in summary["adapters"]
    assert summary["adapters"]["connector_vault"]["kind"] == "vault"
    assert summary["adapters"]["connector_vault"]["mode"] == "auto"
    assert summary["runtime_adapters"]["connector_vault"]["provider"] == "disabled"