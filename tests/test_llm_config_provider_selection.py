"""Factory selection reaches AG2 without provider calls or real credentials."""
import logging

import pytest

from mozaiksai.core.adapters import llm_fallback
from mozaiksai.core.workflow import llm_config


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    for name in (
        "LLM_PRIMARY_API_TYPE", "LLM_PRIMARY_MODEL", "LLM_PRIMARY_BASE_URL",
        "LLM_FALLBACK_MODELS", "LLM_FALLBACK_BASE_URLS", "LLM_FALLBACK_ENABLED",
        "OPENAI_MODEL_FALLBACK", "DEFAULT_LLM_MODEL", "LLM_MAX_TOKENS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MOZAIKS_LLM_CONFIG_SKIP_MONGO", "true")
    monkeypatch.setattr(llm_fallback, "resolve_model_api_key", lambda provider, explicit=None: explicit or "test-only-" + provider)
    monkeypatch.setattr(llm_fallback, "_optional_secret", lambda name: "")
    llm_config.clear_llm_caches()
    yield
    llm_config.clear_llm_caches()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "anthropic", "google", "ollama"])
async def test_environment_preserves_selected_provider_model_and_key(provider, monkeypatch):
    monkeypatch.setenv("LLM_PRIMARY_API_TYPE", provider)
    monkeypatch.setenv("LLM_PRIMARY_MODEL", "selected-model")
    monkeypatch.setenv("OPENAI_MODEL_FALLBACK", "must-not-replace-primary")
    monkeypatch.setenv("LLM_FALLBACK_ENABLED", "false")
    _, config = await llm_config.get_llm_config()
    assert config["config_list"] == [{
        "model": "selected-model", "api_type": provider, "api_key": "test-only-" + provider,
    }]


@pytest.mark.asyncio
async def test_historical_openai_default_and_order_remain(monkeypatch):
    _, default = await llm_config.get_llm_config()
    assert default["config_list"][0]["model"] == "gpt-5-nano"
    llm_config.clear_llm_caches()
    monkeypatch.setenv("DEFAULT_LLM_MODEL", "unused-default")
    monkeypatch.setenv("OPENAI_MODEL_FALLBACK", "first, second")
    _, config = await llm_config.get_llm_config()
    assert [entry["model"] for entry in config["config_list"]] == ["first", "second"]
    assert {entry["api_type"] for entry in config["config_list"]} == {"openai"}


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["anthropic", "google", "ollama"])
async def test_non_openai_requires_explicit_model(provider, monkeypatch):
    monkeypatch.setenv("LLM_PRIMARY_API_TYPE", provider)
    with pytest.raises(ValueError, match="LLM_PRIMARY_MODEL"):
        await llm_config.get_llm_config()


@pytest.mark.asyncio
async def test_unknown_provider_does_not_fall_through_to_openai(monkeypatch):
    monkeypatch.setenv("LLM_PRIMARY_API_TYPE", "typo")
    monkeypatch.setenv("LLM_PRIMARY_MODEL", "test-model")
    with pytest.raises(ValueError, match="Unsupported LLM provider"):
        await llm_config.get_llm_config()


class StoredConfig:
    def __init__(self, providers):
        self.providers = providers

    def __getitem__(self, key):
        return self

    async def find_one(self):
        return {"providers": self.providers}


def stored_config(monkeypatch, providers):
    stored = StoredConfig(providers)
    monkeypatch.setenv("MOZAIKS_LLM_CONFIG_SKIP_MONGO", "false")
    monkeypatch.setattr(llm_config, "get_mongo_client", lambda: stored)
    return stored


@pytest.mark.asyncio
async def test_database_retains_provider_and_never_resolves_other_provider_key(monkeypatch, caplog):
    secret = "SENTINEL-PRIVATE-PROVIDER-KEY"
    stored_config(monkeypatch, [{"api_type": "anthropic", "model": "stored-model", "api_key": secret,
                                 "base_url": "https://provider.invalid", "price": [1, 2]}])
    monkeypatch.setenv("LLM_PRIMARY_API_TYPE", "google")
    with caplog.at_level(logging.DEBUG, logger=llm_config.__name__):
        _, config = await llm_config.get_llm_config(extra_config={"max_tokens": 32000})
        _, cached = await llm_config.get_llm_config(extra_config={"max_tokens": 32000})
    assert cached == config
    assert config["config_list"] == [{"api_type": "anthropic", "model": "stored-model", "api_key": secret,
                                      "base_url": "https://provider.invalid", "price": [1, 2]}]
    assert secret not in caplog.text
    assert "SENT" not in caplog.text


@pytest.mark.asyncio
async def test_database_without_type_retains_openai_default(monkeypatch):
    stored_config(monkeypatch, [{"Model": "stored-model", "OPENAI_API_KEY": "legacy-test-key"}])
    monkeypatch.setenv("LLM_PRIMARY_API_TYPE", "anthropic")
    _, config = await llm_config.get_llm_config()
    assert config["config_list"] == [{"api_type": "openai", "model": "stored-model", "api_key": "legacy-test-key"}]


@pytest.mark.asyncio
async def test_database_anthropic_does_not_consume_openai_named_key(monkeypatch):
    stored_config(monkeypatch, [{"api_type": "anthropic", "model": "stored-model", "OPENAI_API_KEY": "wrong-provider-key"}])
    _, config = await llm_config.get_llm_config()
    assert config["config_list"][0]["api_key"] == "test-only-anthropic"


@pytest.mark.asyncio
async def test_complete_stored_provider_does_not_consume_unused_env_defaults(monkeypatch):
    stored_config(monkeypatch, [{"api_type": "anthropic", "model": "stored-model", "api_key": "stored-test-key"}])
    monkeypatch.setenv("LLM_PRIMARY_BASE_URL", "https://other-provider.invalid")
    monkeypatch.setenv("LLM_FALLBACK_MODELS", "unused-fallback")
    monkeypatch.setattr(llm_fallback, "_optional_secret", lambda name: pytest.fail("Unused fallback credential resolved"))
    _, config = await llm_config.get_llm_config()
    assert config["config_list"] == [{"api_type": "anthropic", "model": "stored-model", "api_key": "stored-test-key"}]


@pytest.mark.asyncio
async def test_raw_only_invalidation_still_refreshes_existing_built_config(monkeypatch):
    stored = stored_config(monkeypatch, [{"model": "before", "api_key": "test-key"}])
    _, before = await llm_config.get_llm_config()
    stored.providers[0]["model"] = "after"
    llm_config.clear_llm_caches(raw=True, built=False)
    _, after = await llm_config.get_llm_config()
    assert before["config_list"][0]["model"] == "before"
    assert after["config_list"][0]["model"] == "after"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["api_type", "base_url", "order"])
async def test_expired_raw_config_refresh_invalidates_built_cache(change, monkeypatch):
    providers = [{"api_type": "openai", "model": "same-model", "api_key": "same-test-key"},
                 {"api_type": "openai", "model": "other-model", "api_key": "same-test-key"}]
    stored = stored_config(monkeypatch, providers)
    monkeypatch.setattr(llm_config, "_CACHE_TTL", 0)
    _, before = await llm_config.get_llm_config()
    if change == "order":
        stored.providers.reverse()
    else:
        stored.providers[0][change] = "anthropic" if change == "api_type" else "https://provider.invalid"
    _, after = await llm_config.get_llm_config()
    assert before["config_list"] != after["config_list"]
    assert after["config_list"][0]["model"] == stored.providers[0]["model"]
    assert after["config_list"][0]["api_type"] == stored.providers[0]["api_type"]
    assert after["config_list"][0].get("base_url") == stored.providers[0].get("base_url")


@pytest.mark.asyncio
async def test_explicit_config_variants_do_not_share_cache_or_log_keys(caplog):
    with caplog.at_level(logging.DEBUG, logger=llm_config.__name__):
        _, first = await llm_config.get_llm_config(extra_config={"config_list": [{"model": "one", "api_key": "SENTINEL-ONE"}]})
        _, second = await llm_config.get_llm_config(extra_config={"config_list": [{"model": "two", "api_key": "SENTINEL-TWO"}]})
    assert first["config_list"][0]["model"] == "one"
    assert second["config_list"][0]["model"] == "two"
    assert "SENTINEL" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["0", "-1", "1.5", "not-an-integer"])
async def test_invalid_environment_limit_fails_before_provider_resolution(value, monkeypatch):
    monkeypatch.setenv("LLM_MAX_TOKENS", value)
    with pytest.raises(ValueError, match="positive integer"):
        await llm_config.get_llm_config()


@pytest.mark.asyncio
async def test_anthropic_factory_selection_and_limit_reach_actual_ag2_config(monkeypatch):
    pytest.importorskip("anthropic")
    monkeypatch.setenv("LLM_PRIMARY_API_TYPE", "anthropic")
    monkeypatch.setenv("LLM_PRIMARY_MODEL", "test-anthropic-model")
    _, default = await llm_config.get_llm_config()
    assert llm_fallback.llm_config_to_ag2_config(default).max_tokens == 4096
    monkeypatch.setenv("LLM_MAX_TOKENS", "32000")
    _, config = await llm_config.get_llm_config()
    actual = llm_fallback.llm_config_to_ag2_config(config)
    assert actual.provider.value == "anthropic"
    assert actual.model == "test-anthropic-model"
    assert actual.max_tokens == 32000
    _, explicit = await llm_config.get_llm_config(extra_config={"max_tokens": 128})
    assert llm_fallback.llm_config_to_ag2_config(explicit).max_tokens == 128


@pytest.mark.asyncio
async def test_stored_anthropic_base_url_and_limits_reach_actual_ag2_config(monkeypatch):
    pytest.importorskip("anthropic")
    stored = stored_config(monkeypatch, [{"api_type": "anthropic", "model": "test-model", "api_key": "test-key",
                                         "base_url": "https://provider.invalid", "max_tokens": 256, "max_retries": 0}])
    monkeypatch.setattr(llm_config, "_CACHE_TTL", 0)
    monkeypatch.setenv("LLM_MAX_TOKENS", "32000")
    _, config = await llm_config.get_llm_config()
    actual = llm_fallback.llm_config_to_ag2_config(config)
    assert actual.base_url == "https://provider.invalid"
    assert actual.max_tokens == 256
    assert actual.max_retries == 0
    stored.providers[0]["max_tokens"] = 128
    _, updated = await llm_config.get_llm_config()
    assert llm_fallback.llm_config_to_ag2_config(updated).max_tokens == 128
    stored.providers[0]["max_tokens"] = -1
    _, invalid = await llm_config.get_llm_config()
    with pytest.raises(ValueError, match="max_tokens"):
        llm_fallback.llm_config_to_ag2_config(invalid)


def test_fallback_entries_preserve_selected_provider(monkeypatch):
    config = llm_fallback.build_fallback_config_list(
        primary_model="one", primary_api_type="anthropic", fallback_models=["two"],
    )
    assert [entry["api_type"] for entry in config] == ["anthropic", "anthropic"]
