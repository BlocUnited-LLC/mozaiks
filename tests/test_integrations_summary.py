import asyncio
import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
import yaml

from mozaiksai.core.adapters.llm_fallback import MODEL_API_KEY_ENV_NAMES


@pytest.fixture(autouse=True)
def isolate_integration_secrets(monkeypatch, tmp_path):
    names = {name for handles in MODEL_API_KEY_ENV_NAMES.values() for name in handles}
    names.update({"MONGO_URI", "MONGODB_URI", "MONGO_URL", "LLM_PRIMARY_API_TYPE"})
    for name in tuple(os.environ):
        if name in names or name.endswith("_SECRET_NAME") or name.startswith("AZURE_KEY_VAULT_"):
            monkeypatch.delenv(name)
    policy = tmp_path / "secrets.yaml"
    policy.write_text(yaml.safe_dump({"version": 1, "provider": {"type": "env"}, "secrets": []}))
    monkeypatch.setenv("MOZAIKS_SECRETS_CONFIG_PATH", str(policy))
    return policy


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

    async def fake_list_connectors(*, scope: str, scope_id: str):
        assert scope == "app"
        assert scope_id == "app_1"
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


def _isolated_runtime_summary(monkeypatch):
    module = _load_studio_summary_module()
    monkeypatch.setattr(module, "get_connector_backend_summary", AsyncMock(return_value={}))
    return module


@pytest.mark.parametrize("provider,handle", [
    ("openai", "OPENAI_API_KEY"), ("openai", "LLM_PRIMARY_API_KEY"),
    ("google", "GEMINI_API_KEY"), ("google", "GOOGLE_API_KEY"),
    ("anthropic", "ANTHROPIC_API_KEY"),
])
def test_model_status_inspects_selected_provider_without_exposing_credentials(monkeypatch, provider, handle):
    module = _isolated_runtime_summary(monkeypatch)
    monkeypatch.setenv("LLM_PRIMARY_API_TYPE", provider)
    monkeypatch.setenv(handle, "private-model-value")
    monkeypatch.setenv("MONGO_URI", "mongodb://private-user:private-password@database.invalid")
    summary = asyncio.run(module.build_integrations_summary())["integrations"]
    assert summary["llm"]["configured"] and summary["llm"]["api_key_set"]
    assert summary["llm"]["provider"] == provider
    assert summary["llm"]["api_key_env"] == handle
    assert summary["llm"]["source"] == "env"
    assert summary["database"]["configured"] and summary["database"]["source"] == "env"
    assert "private-" not in json.dumps(summary)
    assert "api_key_masked" not in summary["llm"]
    assert "uri_masked" not in summary["database"]


def test_vault_status_uses_policy_metadata_without_fetching_secrets(monkeypatch, isolate_integration_secrets):
    from mozaiksai.core.secrets import app_secrets

    module = _isolated_runtime_summary(monkeypatch)
    isolate_integration_secrets.write_text(yaml.safe_dump({
        "version": 1,
        "provider": {"type": "azure_key_vault", "azure_key_vault": {"vault_name": "operator-vault"}},
        "secrets": [
            {"env": handle, "azure_key_vault": {"secret_name": handle.lower().replace("_", "-")}}
            for handle in ("OPENAI_API_KEY", "MONGO_URI")
        ],
    }))
    client = Mock(side_effect=AssertionError("Status must not retrieve vault values"))
    monkeypatch.setattr(app_secrets, "build_secret_client", client)
    summary = asyncio.run(module.build_integrations_summary())["integrations"]
    assert summary["llm"]["configured"] and summary["database"]["configured"]
    assert summary["llm"]["source"] == summary["database"]["source"] == "azure_key_vault"
    client.assert_not_called()


def test_ollama_needs_no_key_and_retired_mongo_aliases_are_not_configuration(monkeypatch):
    module = _isolated_runtime_summary(monkeypatch)
    monkeypatch.setenv("LLM_PRIMARY_API_TYPE", "ollama")
    monkeypatch.setenv("MONGODB_URI", "mongodb://retired.invalid")
    monkeypatch.setenv("MONGO_URL", "mongodb://retired.invalid")
    summary = asyncio.run(module.build_integrations_summary())["integrations"]
    assert summary["llm"]["configured"] and not summary["llm"]["api_key_set"]
    assert summary["llm"]["source"] == "not_required"
    assert not summary["database"]["configured"]
    assert summary["database"]["source"] == "missing"


def test_invalid_primary_reference_does_not_fall_through_to_another_key(monkeypatch, isolate_integration_secrets):
    module = _isolated_runtime_summary(monkeypatch)
    isolate_integration_secrets.write_text(yaml.safe_dump({
        "version": 1, "secrets": [
            {"env": "LLM_PRIMARY_API_KEY", "azure_key_vault": {"secret_name": "model"}},
        ],
    }))
    monkeypatch.setenv("OPENAI_API_KEY", "secondary-key")
    summary = asyncio.run(module.build_integrations_summary())["integrations"]
    assert not summary["llm"]["configured"]
    assert summary["llm"]["source"] == "invalid"
    assert summary["llm"]["api_key_env"] == "LLM_PRIMARY_API_KEY"


def test_existing_database_model_config_remains_a_reported_source(monkeypatch):
    from mozaiksai.core import core_config

    module = _isolated_runtime_summary(monkeypatch)
    monkeypatch.setenv("MONGO_URI", "mongodb://database.invalid")
    collection = Mock(find_one=AsyncMock(return_value={"model": "configured-model"}))
    database = Mock()
    database.__getitem__ = Mock(return_value=collection)
    client = Mock()
    client.__getitem__ = Mock(return_value=database)
    monkeypatch.setattr(core_config, "get_mongo_client", lambda: client)
    summary = asyncio.run(module.build_integrations_summary())["integrations"]
    assert summary["llm"]["configured"]
    assert summary["llm"]["source"] == "database"
    assert not summary["llm"]["api_key_set"]
    collection.find_one.assert_awaited_once()

