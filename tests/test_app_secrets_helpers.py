"""
App secrets pure helper unit tests.

Covers:
  _azure_provider_config:
    - no "provider" key → {}
    - provider is not dict → {}
    - provider has no "azure_key_vault" key → {}
    - azure_key_vault is not dict → {}
    - valid config → returns azure dict

  _secret_entry:
    - no "secrets" key → {}
    - secrets is not list → {}
    - no matching env → {}
    - matching env by exact name → entry returned
    - whitespace in env name stripped on match

  _secret_name_suffix:
    - no azure config → _DEFAULT_SECRET_NAME_SUFFIX ("_SECRET_NAME")
    - azure config with secret_name_env_suffix → that value
    - empty suffix in azure config → _DEFAULT_SECRET_NAME_SUFFIX

  _secret_ref_env:
    - appends suffix to env_name
    - uses default suffix when no contract
    - uses custom suffix from contract

  _declared_secret_name:
    - no matching entry → ""
    - entry with top-level secret_name → that value
    - entry with azure_key_vault.secret_name → that value (takes precedence)
    - entry with empty secret_name → ""

  _vault_name (env-dependent):
    - env var set for vault_name_env → returned
    - no env → falls back to azure.vault_name in contract
    - no config → ""

  _vault_url (env-dependent):
    - env var set for vault_url_env → returned
    - no env, configured vault_url → that url
    - no env, no url, vault_name set → constructed https url
    - no config → ""
"""
from __future__ import annotations

from mozaiksai.core.secrets.app_secrets import (
    _azure_provider_config,
    _declared_secret_name,
    _secret_entry,
    _secret_name_suffix,
    _secret_ref_env,
    _vault_name,
    _vault_url,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _contract(*, azure_config: dict | None = None, secrets: list | None = None) -> dict:
    result: dict = {}
    if azure_config is not None:
        result["provider"] = {"azure_key_vault": azure_config}
    if secrets is not None:
        result["secrets"] = secrets
    return result


# ---------------------------------------------------------------------------
# 1. _azure_provider_config
# ---------------------------------------------------------------------------

class TestAzureProviderConfig:
    def test_empty_contract_returns_empty(self):
        assert _azure_provider_config({}) == {}

    def test_no_provider_key_returns_empty(self):
        assert _azure_provider_config({"secrets": []}) == {}

    def test_provider_not_dict_returns_empty(self):
        assert _azure_provider_config({"provider": "azure"}) == {}

    def test_provider_has_no_azure_key_vault_returns_empty(self):
        assert _azure_provider_config({"provider": {"other": {}}}) == {}

    def test_azure_key_vault_not_dict_returns_empty(self):
        assert _azure_provider_config({"provider": {"azure_key_vault": "url"}}) == {}

    def test_valid_config_returned(self):
        azure = {"vault_name": "my-vault"}
        contract = {"provider": {"azure_key_vault": azure}}
        assert _azure_provider_config(contract) == azure

    def test_returns_full_azure_dict(self):
        azure = {"vault_name": "v", "vault_url": "https://v.vault.azure.net/", "secret_name_env_suffix": "_NAME"}
        result = _azure_provider_config({"provider": {"azure_key_vault": azure}})
        assert result == azure


# ---------------------------------------------------------------------------
# 2. _secret_entry
# ---------------------------------------------------------------------------

class TestSecretEntry:
    def test_no_secrets_key_returns_empty(self):
        assert _secret_entry({}, "MY_SECRET") == {}

    def test_secrets_not_list_returns_empty(self):
        assert _secret_entry({"secrets": "bad"}, "MY_SECRET") == {}

    def test_no_matching_env_returns_empty(self):
        contract = {"secrets": [{"env": "OTHER_SECRET", "secret_name": "other"}]}
        assert _secret_entry(contract, "MY_SECRET") == {}

    def test_matching_env_returned(self):
        entry = {"env": "MY_SECRET", "secret_name": "my-secret-name"}
        contract = {"secrets": [entry]}
        result = _secret_entry(contract, "MY_SECRET")
        assert result == entry

    def test_whitespace_in_entry_env_stripped(self):
        entry = {"env": "  MY_SECRET  ", "secret_name": "name"}
        contract = {"secrets": [entry]}
        result = _secret_entry(contract, "MY_SECRET")
        assert result == entry

    def test_first_match_returned(self):
        entries = [
            {"env": "MY_SECRET", "secret_name": "first"},
            {"env": "MY_SECRET", "secret_name": "second"},
        ]
        result = _secret_entry({"secrets": entries}, "MY_SECRET")
        assert result["secret_name"] == "first"

    def test_non_dict_entry_skipped(self):
        contract = {"secrets": ["not-a-dict", {"env": "MY_SECRET", "secret_name": "ok"}]}
        result = _secret_entry(contract, "MY_SECRET")
        assert result["secret_name"] == "ok"


# ---------------------------------------------------------------------------
# 3. _secret_name_suffix
# ---------------------------------------------------------------------------

class TestSecretNameSuffix:
    def test_empty_contract_returns_default(self):
        assert _secret_name_suffix({}) == "_SECRET_NAME"

    def test_no_azure_config_returns_default(self):
        assert _secret_name_suffix({"provider": {}}) == "_SECRET_NAME"

    def test_custom_suffix_returned(self):
        contract = _contract(azure_config={"secret_name_env_suffix": "_NAME"})
        assert _secret_name_suffix(contract) == "_NAME"

    def test_empty_suffix_falls_back_to_default(self):
        contract = _contract(azure_config={"secret_name_env_suffix": ""})
        assert _secret_name_suffix(contract) == "_SECRET_NAME"

    def test_whitespace_only_suffix_falls_back(self):
        contract = _contract(azure_config={"secret_name_env_suffix": "   "})
        assert _secret_name_suffix(contract) == "_SECRET_NAME"

    def test_none_suffix_falls_back(self):
        contract = _contract(azure_config={"secret_name_env_suffix": None})
        assert _secret_name_suffix(contract) == "_SECRET_NAME"


# ---------------------------------------------------------------------------
# 4. _secret_ref_env
# ---------------------------------------------------------------------------

class TestSecretRefEnv:
    def test_appends_default_suffix(self):
        result = _secret_ref_env("MY_API_KEY")
        assert result == "MY_API_KEY_SECRET_NAME"

    def test_appends_default_suffix_no_contract(self):
        assert _secret_ref_env("STRIPE_SECRET", None) == "STRIPE_SECRET_SECRET_NAME"

    def test_custom_suffix_from_contract(self):
        contract = _contract(azure_config={"secret_name_env_suffix": "_REF"})
        assert _secret_ref_env("MY_KEY", contract) == "MY_KEY_REF"

    def test_empty_contract_uses_default(self):
        assert _secret_ref_env("X", {}) == "X_SECRET_NAME"


# ---------------------------------------------------------------------------
# 5. _declared_secret_name
# ---------------------------------------------------------------------------

class TestDeclaredSecretName:
    def test_no_entry_returns_empty(self):
        assert _declared_secret_name("MY_SECRET", {}) == ""

    def test_top_level_secret_name(self):
        contract = _contract(secrets=[{"env": "MY_SECRET", "secret_name": "my-vault-secret"}])
        assert _declared_secret_name("MY_SECRET", contract) == "my-vault-secret"

    def test_azure_key_vault_name_takes_precedence(self):
        contract = _contract(secrets=[
            {
                "env": "MY_SECRET",
                "secret_name": "top-level",
                "azure_key_vault": {"secret_name": "az-level"},
            }
        ])
        assert _declared_secret_name("MY_SECRET", contract) == "az-level"

    def test_azure_key_vault_empty_name_falls_back_to_top(self):
        contract = _contract(secrets=[
            {
                "env": "MY_SECRET",
                "secret_name": "top-level",
                "azure_key_vault": {"secret_name": ""},
            }
        ])
        assert _declared_secret_name("MY_SECRET", contract) == "top-level"

    def test_missing_secret_name_field_returns_empty(self):
        contract = _contract(secrets=[{"env": "MY_SECRET"}])
        assert _declared_secret_name("MY_SECRET", contract) == ""

    def test_no_match_returns_empty(self):
        contract = _contract(secrets=[{"env": "OTHER", "secret_name": "other"}])
        assert _declared_secret_name("MY_SECRET", contract) == ""


# ---------------------------------------------------------------------------
# 6. _vault_name (env-dependent)
# ---------------------------------------------------------------------------

class TestVaultName:
    def test_no_config_no_env_returns_empty(self, monkeypatch):
        monkeypatch.delenv("AZURE_KEY_VAULT_NAME", raising=False)
        assert _vault_name({}) == ""

    def test_env_var_set_returned(self, monkeypatch):
        monkeypatch.setenv("AZURE_KEY_VAULT_NAME", "prod-vault")
        contract = _contract(azure_config={})
        assert _vault_name(contract) == "prod-vault"

    def test_azure_vault_name_in_config(self, monkeypatch):
        monkeypatch.delenv("AZURE_KEY_VAULT_NAME", raising=False)
        contract = _contract(azure_config={"vault_name": "config-vault"})
        assert _vault_name(contract) == "config-vault"

    def test_custom_vault_name_env_key(self, monkeypatch):
        monkeypatch.setenv("MY_VAULT", "custom-vault")
        contract = _contract(azure_config={"vault_name_env": "MY_VAULT"})
        assert _vault_name(contract) == "custom-vault"

    def test_env_overrides_config(self, monkeypatch):
        monkeypatch.setenv("AZURE_KEY_VAULT_NAME", "env-vault")
        contract = _contract(azure_config={"vault_name": "config-vault"})
        assert _vault_name(contract) == "env-vault"


# ---------------------------------------------------------------------------
# 7. _vault_url (env-dependent)
# ---------------------------------------------------------------------------

class TestVaultUrl:
    def test_no_config_no_env_returns_empty(self, monkeypatch):
        monkeypatch.delenv("AZURE_KEY_VAULT_URL", raising=False)
        monkeypatch.delenv("AZURE_KEY_VAULT_NAME", raising=False)
        assert _vault_url({}) == ""

    def test_env_var_set_returned(self, monkeypatch):
        monkeypatch.setenv("AZURE_KEY_VAULT_URL", "https://prod.vault.azure.net/")
        assert _vault_url({}) == "https://prod.vault.azure.net/"

    def test_configured_vault_url_used_when_no_env(self, monkeypatch):
        monkeypatch.delenv("AZURE_KEY_VAULT_URL", raising=False)
        contract = _contract(azure_config={"vault_url": "https://myapp.vault.azure.net/"})
        assert _vault_url(contract) == "https://myapp.vault.azure.net/"

    def test_vault_name_constructs_url(self, monkeypatch):
        monkeypatch.delenv("AZURE_KEY_VAULT_URL", raising=False)
        monkeypatch.delenv("AZURE_KEY_VAULT_NAME", raising=False)
        contract = _contract(azure_config={"vault_name": "my-vault"})
        result = _vault_url(contract)
        assert result == "https://my-vault.vault.azure.net/"

    def test_env_url_overrides_config_url(self, monkeypatch):
        monkeypatch.setenv("AZURE_KEY_VAULT_URL", "https://env.vault.azure.net/")
        contract = _contract(azure_config={"vault_url": "https://config.vault.azure.net/"})
        assert _vault_url(contract) == "https://env.vault.azure.net/"
