from tests.import_utils import import_module_directly
import pytest
import inspect


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
    monkeypatch.setattr(module, "_attach_autogen_cache", lambda cfg: None)

    _, llm_config = await module.get_llm_config(cache=False)

    assert "stream" not in inspect.signature(module.get_llm_config).parameters
    assert "stream" not in llm_config