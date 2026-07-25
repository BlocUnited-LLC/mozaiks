"""
App bundle path helper unit tests (mozaiksai.core.runtime.app.paths).

Covers:
  - normalize_app_path: strips leading ./ backslashes, whitespace
  - is_data_migration_path: exactly 3 parts (data/migrations/xxx.json)
  - is_disallowed_legacy_app_path: exact and prefix matches
  - is_sensitive_app_config_path: detects secret-like tokens in config/ paths
  - is_canonical_app_config_path: non-config passes, canonical set passes,
    sensitive blocked, integrations/ extension allowed
  - disallowed_legacy_app_paths: filters a list to disallowed entries
  - noncanonical_app_config_paths: returns only non-canonical config/ paths
  - noncanonical_app_root_paths: returns paths not in root dirs or root files
"""
from __future__ import annotations

from mozaiksai.core.runtime.app.paths import (
    disallowed_legacy_app_paths,
    is_canonical_app_config_path,
    is_data_migration_path,
    is_disallowed_legacy_app_path,
    is_sensitive_app_config_path,
    noncanonical_app_config_paths,
    noncanonical_app_root_paths,
    normalize_app_path,
)

# ---------------------------------------------------------------------------
# 1. normalize_app_path
# ---------------------------------------------------------------------------

class TestNormalizeAppPath:
    def test_strips_leading_dot_slash(self):
        assert normalize_app_path("./app/config") == "app/config"

    def test_strips_leading_slash(self):
        assert normalize_app_path("/app/config") == "app/config"

    def test_normalizes_backslashes(self):
        assert normalize_app_path("app\\config\\file.json") == "app/config/file.json"

    def test_strips_whitespace(self):
        assert normalize_app_path("  data/contract.json  ") == "data/contract.json"

    def test_none_returns_empty_string(self):
        assert normalize_app_path(None) == ""

    def test_empty_string_returns_empty(self):
        assert normalize_app_path("") == ""

    def test_already_clean_path_unchanged(self):
        assert normalize_app_path("config/ai.json") == "config/ai.json"

    def test_multiple_leading_dots_stripped(self):
        result = normalize_app_path("../../config/ai.json")
        assert not result.startswith(".")


# ---------------------------------------------------------------------------
# 2. is_data_migration_path
# ---------------------------------------------------------------------------

class TestIsDataMigrationPath:
    def test_valid_migration_path(self):
        assert is_data_migration_path("data/migrations/001_initial.json") is True

    def test_invalid_wrong_root(self):
        assert is_data_migration_path("app/migrations/001.json") is False

    def test_invalid_too_few_parts(self):
        assert is_data_migration_path("data/migrations") is False

    def test_invalid_too_many_parts(self):
        assert is_data_migration_path("data/migrations/sub/001.json") is False

    def test_invalid_wrong_extension(self):
        assert is_data_migration_path("data/migrations/001.yaml") is False

    def test_invalid_empty_filename(self):
        assert is_data_migration_path("data/migrations/.json") is False

    def test_with_leading_dot_slash(self):
        assert is_data_migration_path("./data/migrations/v1.json") is True


# ---------------------------------------------------------------------------
# 3. is_disallowed_legacy_app_path
# ---------------------------------------------------------------------------

class TestIsDisallowedLegacyAppPath:
    def test_disallowed_exact_match_data_json(self):
        assert is_disallowed_legacy_app_path("config/data.json") is True

    def test_disallowed_exact_match_secrets_yaml(self):
        assert is_disallowed_legacy_app_path("config/secrets.yaml") is True

    def test_disallowed_prefix_data_migrations(self):
        assert is_disallowed_legacy_app_path("config/data_migrations/v1.json") is True

    def test_disallowed_prefix_services_data(self):
        assert is_disallowed_legacy_app_path("services/data/schema.json") is True

    def test_disallowed_prefix_services_security(self):
        assert is_disallowed_legacy_app_path("services/security/vault.py") is True

    def test_allowed_canonical_config_path(self):
        assert is_disallowed_legacy_app_path("config/ai.json") is False

    def test_allowed_modules_path(self):
        assert is_disallowed_legacy_app_path("modules/wallet/module.yaml") is False


# ---------------------------------------------------------------------------
# 4. is_sensitive_app_config_path
# ---------------------------------------------------------------------------

class TestIsSensitiveAppConfigPath:
    def test_api_keys_in_config(self):
        assert is_sensitive_app_config_path("config/api_keys.json") is True

    def test_credentials_in_config(self):
        assert is_sensitive_app_config_path("config/credentials.yaml") is True

    def test_passwords_in_config(self):
        assert is_sensitive_app_config_path("config/passwords.json") is True

    def test_secrets_in_config(self):
        assert is_sensitive_app_config_path("config/secrets.yaml") is True

    def test_tokens_in_config(self):
        assert is_sensitive_app_config_path("config/tokens.json") is True

    def test_non_config_path_not_sensitive(self):
        assert is_sensitive_app_config_path("data/secrets.json") is False

    def test_canonical_ai_config_not_sensitive(self):
        assert is_sensitive_app_config_path("config/ai.json") is False

    def test_canonical_shell_not_sensitive(self):
        assert is_sensitive_app_config_path("config/shell.json") is False


# ---------------------------------------------------------------------------
# 5. is_canonical_app_config_path
# ---------------------------------------------------------------------------

class TestIsCanonicalAppConfigPath:
    def test_non_config_path_always_canonical(self):
        assert is_canonical_app_config_path("modules/wallet/module.yaml") is True

    def test_data_path_canonical(self):
        assert is_canonical_app_config_path("data/contract.json") is True

    def test_known_canonical_config_ai(self):
        assert is_canonical_app_config_path("config/ai.json") is True

    def test_known_canonical_config_auth(self):
        assert is_canonical_app_config_path("config/auth.yaml") is True

    def test_known_canonical_config_shell(self):
        assert is_canonical_app_config_path("config/shell.json") is True

    def test_known_canonical_config_subscriptions(self):
        assert is_canonical_app_config_path("config/subscriptions.yaml") is True

    def test_known_canonical_config_integrations_yaml(self):
        assert is_canonical_app_config_path("config/integrations.yaml") is True

    def test_known_canonical_config_targets_json(self):
        assert is_canonical_app_config_path("config/targets.json") is True

    def test_sensitive_config_not_canonical(self):
        assert is_canonical_app_config_path("config/secrets.yaml") is False

    def test_integrations_subdir_json_canonical(self):
        assert is_canonical_app_config_path("config/integrations/payment_provider.json") is True

    def test_integrations_subdir_yaml_canonical(self):
        assert is_canonical_app_config_path("config/integrations/twilio.yaml") is True

    def test_unknown_config_file_not_canonical(self):
        assert is_canonical_app_config_path("config/my_custom_settings.json") is False

    def test_integrations_subdir_with_sensitive_name_blocked(self):
        # e.g. config/integrations/api_secrets.json — has "secrets" token
        result = is_canonical_app_config_path("config/integrations/api_secrets.json")
        # sensitive token blocks it
        assert result is False


# ---------------------------------------------------------------------------
# 6. disallowed_legacy_app_paths
# ---------------------------------------------------------------------------

class TestDisallowedLegacyAppPaths:
    def test_filters_disallowed_paths(self):
        paths = [
            "config/ai.json",
            "config/data.json",
            "services/data/schema.json",
            "modules/wallet/module.yaml",
        ]
        result = disallowed_legacy_app_paths(paths)
        assert "config/data.json" in result
        assert "services/data/schema.json" in result
        assert "config/ai.json" not in result
        assert "modules/wallet/module.yaml" not in result

    def test_empty_list_returns_empty(self):
        assert disallowed_legacy_app_paths([]) == []

    def test_returns_sorted(self):
        paths = ["services/data/b.py", "services/data/a.py"]
        result = disallowed_legacy_app_paths(paths)
        assert result == sorted(result)

    def test_deduplicates(self):
        paths = ["config/data.json", "config/data.json"]
        result = disallowed_legacy_app_paths(paths)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# 7. noncanonical_app_config_paths
# ---------------------------------------------------------------------------

class TestNoncanonicalAppConfigPaths:
    def test_only_returns_config_prefix_paths(self):
        paths = ["data/contract.json", "config/custom.json", "config/ai.json"]
        result = noncanonical_app_config_paths(paths)
        assert "config/custom.json" in result
        assert "data/contract.json" not in result
        assert "config/ai.json" not in result

    def test_empty_list_returns_empty(self):
        assert noncanonical_app_config_paths([]) == []

    def test_returns_sorted(self):
        paths = ["config/z.json", "config/a.json"]
        result = noncanonical_app_config_paths(paths)
        assert result == sorted(result)


# ---------------------------------------------------------------------------
# 8. noncanonical_app_root_paths
# ---------------------------------------------------------------------------

class TestNoncanonicalAppRootPaths:
    def test_canonical_root_dir_excluded(self):
        paths = ["modules/wallet/module.yaml", "config/ai.json"]
        result = noncanonical_app_root_paths(paths)
        assert not result  # both are under canonical root dirs

    def test_canonical_root_file_excluded(self):
        paths = [
            "app.json",
            "Dockerfile",
            "package.json",
            "requirements.txt",
            "vite.config.js",
            ".env.example",
            ".env.staging.example",
            ".env.production.example",
        ]
        result = noncanonical_app_root_paths(paths)
        assert not result

    def test_noncanonical_root_dir_included(self):
        paths = ["custom_dir/file.py"]
        result = noncanonical_app_root_paths(paths)
        assert "custom_dir/file.py" in result

    def test_returns_sorted(self):
        paths = ["z_dir/file.py", "a_dir/file.py"]
        result = noncanonical_app_root_paths(paths)
        assert result == sorted(result)

    def test_empty_path_excluded(self):
        result = noncanonical_app_root_paths([""])
        assert not result
