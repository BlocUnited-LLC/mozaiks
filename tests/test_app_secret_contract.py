from __future__ import annotations

from unittest.mock import Mock

import pytest
import yaml

from mozaiksai.core.secrets import (
    SecretContractError,
    SecretResolutionError,
    inspect_secret_config,
    load_secret_contract,
    resolve_secret,
    validate_secret_contract,
)


def _write_contract(app_root, document):
    app_root.mkdir(exist_ok=True)
    (app_root / "app.json").write_text('{"appName": "Independent"}')
    security = app_root / "security"
    security.mkdir()
    path = security / "secrets.yaml"
    path.write_text(yaml.safe_dump(document))
    return path


def test_generated_secret_contract_is_consumed_by_runtime(tmp_path, monkeypatch):
    from tests.test_app_family_materialization_b2a import _bundle_files

    document = yaml.safe_load(_bundle_files()["security/secrets.yaml"])
    _write_contract(tmp_path, document)
    env_name = document["secrets"][0]["env"]
    monkeypatch.setenv(env_name, "local-test-value")
    assert inspect_secret_config(env_name, app_root=tmp_path).declared
    assert resolve_secret(env_name, app_root=tmp_path) == "local-test-value"


@pytest.mark.parametrize("document", [
    {"version": 1, "secrets": ["EMAIL_API_KEY"]},
    {"version": 1, "secrets": [{"env": "EMAIL_API_KEY", "value": "raw-test-value"}]},
    {"version": 1, "secrets": [{"env": "EMAIL_API_KEY"}] * 2},
    {"version": 1, "secrets": [], "arbitrary": "raw-test-value"},
    {"version": 1, "secrets": [{"env": "EMAIL_API_KEY", "secret_name": "alias"}]},
])
def test_invalid_secret_contract_fails_at_generation_and_runtime(document, tmp_path):
    from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
        _scan_security_secret_contract,
    )

    path = _write_contract(tmp_path, document)
    with pytest.raises(SecretContractError) as error:
        load_secret_contract(app_root=tmp_path)
    assert "raw-test-value" not in str(error.value)
    findings = _scan_security_secret_contract({"security/secrets.yaml": path.read_text()})
    assert findings
    assert "raw-test-value" not in str(findings)


def test_selected_app_without_policy_never_borrows_another_app_policy(tmp_path, monkeypatch):
    selected = tmp_path / "selected"
    selected.mkdir()
    other = tmp_path / "other"
    _write_contract(other, {"version": 1, "secrets": [{"env": "OTHER_KEY"}]})
    monkeypatch.setenv("PLATFORM_PATH", str(other))
    monkeypatch.delenv("MOZAIKS_SECRETS_CONFIG_PATH", raising=False)
    assert load_secret_contract(app_root=selected) == {}


def test_explicit_missing_and_malformed_contracts_fail_closed(tmp_path):
    path = tmp_path / "secrets.yaml"
    with pytest.raises(SecretContractError):
        load_secret_contract(contract_path=path)
    path.write_text("secrets: [raw-test-value")
    with pytest.raises(SecretContractError) as error:
        load_secret_contract(contract_path=path)
    assert "raw-test-value" not in str(error.value)


def test_env_only_contract_needs_no_provider():
    assert validate_secret_contract({"version": 1, "secrets": [{"env": "SERVICE_KEY"}]}).provider is None


def test_explicit_env_provider_ignores_ambient_vault_references(tmp_path, monkeypatch):
    _write_contract(tmp_path, {
        "version": 1, "provider": {"type": "env"}, "secrets": [{"env": "SERVICE_KEY"}],
    })
    monkeypatch.delenv("SERVICE_KEY", raising=False)
    monkeypatch.delenv("MOZAIKS_SECRETS_CONFIG_PATH", raising=False)
    monkeypatch.setenv("SERVICE_KEY_SECRET_NAME", "service-key")
    monkeypatch.setenv("AZURE_KEY_VAULT_URL", "https://example.vault.azure.net")
    client_factory = Mock()
    config = inspect_secret_config("SERVICE_KEY", app_root=tmp_path)
    assert config.declared and config.source == "missing" and not config.configured
    assert config.secret_name is None and not config.vault_url_configured
    with pytest.raises(SecretResolutionError) as error:
        resolve_secret("SERVICE_KEY", app_root=tmp_path, client_factory=client_factory)
    assert error.value.source == "missing"
    client_factory.assert_not_called()

    monkeypatch.setenv("SERVICE_KEY", "local-test-value")
    assert inspect_secret_config("SERVICE_KEY", app_root=tmp_path).source == "env"
    assert resolve_secret("SERVICE_KEY", app_root=tmp_path, client_factory=client_factory) == "local-test-value"
    client_factory.assert_not_called()


def test_factory_secret_policy_is_names_only_and_provider_conditional(monkeypatch):
    from mozaiksai.resources import resolve_factory_app_root

    monkeypatch.delenv("MOZAIKS_SECRETS_CONFIG_PATH", raising=False)
    root = resolve_factory_app_root() / "app"
    policy = validate_secret_contract(load_secret_contract(app_root=root))
    assert policy.provider.type == "env"
    entries = {entry.env: entry for entry in policy.secrets}
    assert set(entries) == {
        "MONGO_URI", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY", "LLM_PRIMARY_API_KEY", "LLM_FALLBACK_API_KEYS",
    }
    assert entries["MONGO_URI"].required
    assert all(not entry.required for name, entry in entries.items() if name != "MONGO_URI")
    for name in entries:
        monkeypatch.delenv(name, raising=False)
    # Loading metadata never requires every credential to be supplied.
    assert load_secret_contract(app_root=root)["secrets"]


@pytest.mark.parametrize("provider,env_name", [
    ("google", "GEMINI_API_KEY"), ("google", "GOOGLE_API_KEY"),
    ("anthropic", "ANTHROPIC_API_KEY"), ("openai", "OPENAI_API_KEY"),
    ("openai", "LLM_PRIMARY_API_KEY"),
])
def test_model_and_startup_consume_selected_app_vault_policy(provider, env_name, monkeypatch, tmp_path):
    from mozaiksai.core.adapters.llm_fallback import build_fallback_config_list
    from mozaiksai.core.secrets import app_secrets
    from mozaiksai.core.startup.validation import _can_resolve_api_key

    policy = _write_contract(tmp_path, {
        "version": 1,
        "provider": {"type": "azure_key_vault", "azure_key_vault": {
            "vault_url": "https://example.vault.azure.net",
        }},
        "secrets": [
            {"env": env_name, "azure_key_vault": {"secret_name": "selected-model"}},
            {"env": "LLM_FALLBACK_API_KEYS", "azure_key_vault": {"secret_name": "fallback-models"}},
        ],
    })
    monkeypatch.setenv("MOZAIKS_SECRETS_CONFIG_PATH", str(policy))
    monkeypatch.setenv("LLM_PRIMARY_API_TYPE", provider)
    monkeypatch.setenv("LLM_FALLBACK_ENABLED", "true")
    for name in (
        "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY",
        "LLM_PRIMARY_API_KEY", "LLM_FALLBACK_API_KEYS",
    ):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"{name}_SECRET_NAME", raising=False)
    client = Mock()
    client.get_secret.side_effect = lambda name: Mock(value={
        "selected-model": "local-model-placeholder", "fallback-models": "fallback-one, fallback-two",
    }[name])
    monkeypatch.setattr(app_secrets, "build_secret_client", Mock(return_value=client))

    config = build_fallback_config_list(primary_model="example", fallback_models=["one", "two"])
    assert [entry["api_key"] for entry in config] == [
        "local-model-placeholder", "fallback-one", "fallback-two",
    ]
    assert _can_resolve_api_key()[0]
    assert {call.args[0] for call in client.get_secret.call_args_list} == {"selected-model", "fallback-models"}


def test_model_does_not_mask_configured_vault_failure(monkeypatch, tmp_path):
    from mozaiksai.core.adapters.llm_fallback import resolve_model_api_key
    from mozaiksai.core.secrets import app_secrets

    policy = _write_contract(tmp_path, {
        "version": 1,
        "provider": {"type": "azure_key_vault", "azure_key_vault": {
            "vault_url": "https://example.vault.azure.net",
        }},
        "secrets": [{"env": "GEMINI_API_KEY", "azure_key_vault": {"secret_name": "model"}}],
    })
    monkeypatch.setenv("MOZAIKS_SECRETS_CONFIG_PATH", str(policy))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PRIMARY_API_KEY", "otherwise-usable-key")
    monkeypatch.setattr(app_secrets, "build_secret_client", Mock(side_effect=RuntimeError("private-detail")))
    with pytest.raises(SecretResolutionError) as error:
        resolve_model_api_key("google")
    assert error.value.source == "azure_key_vault"
    assert "private-detail" not in str(error.value)
