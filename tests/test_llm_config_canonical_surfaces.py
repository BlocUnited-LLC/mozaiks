import inspect

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

    assert callable(module.get_secret)
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
    monkeypatch.setattr(module, "get_secret", lambda _name: (_ for _ in ()).throw(KeyError(_name)))

    providers = await module._load_raw_config_list(force=True)

    assert mongo_called is False
    assert providers == [{"model": "gpt-4o-mini", "api_key": "test-openai-key"}]
