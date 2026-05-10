import asyncio
import importlib.util
from pathlib import Path


def _load_console_summary_module():
    workspace = Path(__file__).resolve().parents[1]
    file_path = workspace / "mozaiksai/core/runtime/app/console_summary.py"
    spec = importlib.util.spec_from_file_location("tests.console_summary_module", file_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_integrations_summary_always_includes_connector_vault(monkeypatch) -> None:
    console_summary = _load_console_summary_module()

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
    monkeypatch.setattr(console_summary, "get_connector_backend_summary", fake_connector_backend_summary)

    summary = asyncio.run(console_summary.build_integrations_summary())

    assert "connector_vault" in summary["integrations"]
    assert summary["integrations"]["connector_vault"]["kind"] == "vault"
    assert summary["integrations"]["connector_vault"]["mode"] == "auto"
    assert summary["runtime_integrations"]["connector_vault"]["provider"] == "disabled"
