import inspect
from unittest.mock import Mock

import pytest

from tests.import_utils import import_module_directly


def test_validation_package_re_exports_canonical_llm_config() -> None:
    canonical = import_module_directly("mozaiksai.core.workflow.llm_config")
    validation_pkg = import_module_directly("mozaiksai.core.workflow.validation")

    assert validation_pkg.get_llm_config is canonical.get_llm_config
    assert validation_pkg.clear_llm_caches is canonical.clear_llm_caches
    assert validation_pkg.PRICE_MAP is canonical.PRICE_MAP


def test_core_config_imports_without_validation_llm_config_module() -> None:
    module = import_module_directly("mozaiksai.core.core_config")

    assert not hasattr(module, "get_secret")
    assert callable(module.get_mongo_client)


@pytest.mark.asyncio
async def test_canonical_llm_config_has_no_stream_kwarg(monkeypatch: pytest.MonkeyPatch) -> None:
    module = import_module_directly("mozaiksai.core.workflow.llm_config")

    async def fake_load_raw_config_list():
        return [{"model": "gpt-5-nano", "api_key": "test-key"}]

    monkeypatch.setattr(module, "_load_raw_config_list", fake_load_raw_config_list)

    _, llm_config = await module.get_llm_config(cache=False)

    assert "stream" not in inspect.signature(module.get_llm_config).parameters
    assert "stream" not in llm_config


@pytest.mark.asyncio
async def test_llm_config_skip_mongo_uses_env_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    module = import_module_directly("mozaiksai.core.workflow.llm_config")
    module.clear_llm_caches()
    mongo_called = False

    def fail_if_mongo_called():
        nonlocal mongo_called
        mongo_called = True
        raise AssertionError("Mongo should be skipped")

    monkeypatch.setenv("MOZAIKS_LLM_CONFIG_SKIP_MONGO", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("DEFAULT_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.delenv("OPENAI_MODEL_FALLBACK", raising=False)
    monkeypatch.setattr(module, "get_mongo_client", fail_if_mongo_called)
    monkeypatch.delenv("LLM_PRIMARY_API_KEY", raising=False)

    providers = await module._load_raw_config_list(force=True)

    assert mongo_called is False
    assert providers == [{"model": "gpt-4o-mini", "api_key": "test-openai-key"}]


@pytest.mark.asyncio
async def test_openai_config_consumes_declared_vault_reference(monkeypatch, tmp_path) -> None:
    from mozaiksai.core.secrets import app_secrets

    module = import_module_directly("mozaiksai.core.workflow.llm_config")
    module.clear_llm_caches()
    policy = tmp_path / "secrets.yaml"
    policy.write_text(
        "version: 1\nprovider:\n  type: azure_key_vault\n  azure_key_vault:\n"
        "    vault_url: https://example.vault.azure.net\nsecrets:\n"
        "  - env: OPENAI_API_KEY\n    azure_key_vault: {secret_name: app-openai-key}\n"
    )
    monkeypatch.setenv("MOZAIKS_SECRETS_CONFIG_PATH", str(policy))
    monkeypatch.setenv("MOZAIKS_LLM_CONFIG_SKIP_MONGO", "true")
    monkeypatch.setenv("DEFAULT_LLM_MODEL", "gpt-4o-mini")
    for name in ("OPENAI_API_KEY", "LLM_PRIMARY_API_KEY", "OPENAI_MODEL_FALLBACK",
                 "OPENAI_API_KEY_SECRET_NAME", "LLM_PRIMARY_API_KEY_SECRET_NAME"):
        monkeypatch.delenv(name, raising=False)
    client = Mock()
    client.get_secret.return_value.value = "local-openai-placeholder"
    monkeypatch.setattr(app_secrets, "build_secret_client", Mock(return_value=client))
    try:
        assert await module._load_raw_config_list(force=True) == [
            {"model": "gpt-4o-mini", "api_key": "local-openai-placeholder"},
        ]
        client.get_secret.assert_called_once_with("app-openai-key")
    finally:
        module.clear_llm_caches()
