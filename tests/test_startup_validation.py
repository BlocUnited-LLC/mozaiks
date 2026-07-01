"""Unit tests for mozaiksai.core.startup.validation.

Tests run_startup_checks, _can_resolve_api_key, and StartupConfigError
without any network calls, MongoDB, or live Key Vault endpoints.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

from mozaiksai.core.startup.validation import (
    StartupConfigError,
    _can_resolve_api_key,
    run_startup_checks,
)

# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

class _MockPingClient:
    """Minimal MongoDB client stub whose admin.command("ping") succeeds."""
    class _Admin:
        async def command(self, cmd: str):
            return {"ok": 1}
    admin = _Admin()


class _FailingPingClient:
    """Minimal MongoDB client stub whose admin.command("ping") raises."""
    class _Admin:
        async def command(self, cmd: str):
            raise ConnectionError("MongoDB not reachable in test")
    admin = _Admin()


# ---------------------------------------------------------------------------
# _can_resolve_api_key
# ---------------------------------------------------------------------------


class TestCanResolveApiKey:
    def test_returns_true_when_env_set(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-1234")
        assert _can_resolve_api_key() is True

    def test_returns_false_when_env_empty(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "")
        with patch(
            "mozaiksai.core.core_config.get_secret", side_effect=ValueError("not found")
        ):
            assert _can_resolve_api_key() is False

    def test_returns_false_when_env_missing(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with patch(
            "mozaiksai.core.core_config.get_secret", side_effect=ValueError("not found")
        ):
            assert _can_resolve_api_key() is False

    def test_returns_true_when_key_vault_resolves(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        # Patch the name in validation's namespace (imported by reference at module load)
        with patch(
            "mozaiksai.core.startup.validation.get_secret", return_value="sk-from-kv"
        ):
            assert _can_resolve_api_key() is True

    def test_returns_false_when_key_vault_returns_empty(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with patch("mozaiksai.core.startup.validation.get_secret", return_value=""):
            assert _can_resolve_api_key() is False

    def test_env_whitespace_only_treated_as_absent(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "   ")
        with patch(
            "mozaiksai.core.core_config.get_secret", side_effect=ValueError("not found")
        ):
            assert _can_resolve_api_key() is False


# ---------------------------------------------------------------------------
# run_startup_checks — warn mode (default)
# ---------------------------------------------------------------------------


class TestRunStartupChecksWarnMode:
    @pytest.mark.asyncio
    async def test_passes_when_api_key_in_env(self, monkeypatch, caplog):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("INTERNAL_API_KEY", "test-startup-api-key-long-enough")
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        with caplog.at_level(logging.INFO, logger="mozaiksai.startup.validation"):
            warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert warnings == []
        ok_records = [r for r in caplog.records if "STARTUP_CHECKS_PASSED" in r.getMessage()]
        assert ok_records

    @pytest.mark.asyncio
    async def test_warns_when_api_key_missing_and_no_mongo_config(self, monkeypatch, caplog):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("INTERNAL_API_KEY", "test-startup-api-key-long-enough")
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        with (
            patch(
                "mozaiksai.core.core_config.get_secret",
                side_effect=ValueError("not found"),
            ),
            patch(
                "mozaiksai.core.startup.validation._has_mongo_llm_config",
                new_callable=AsyncMock,
                return_value=False,
            ),
            caplog.at_level(logging.WARNING, logger="mozaiksai.startup.validation"),
        ):
            warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert len(warnings) == 1
        assert "OPENAI_API_KEY" in warnings[0]
        warn_records = [r for r in caplog.records if "STARTUP_CHECK_FAILED" in r.getMessage()]
        assert warn_records
        assert warn_records[0].levelno == logging.WARNING

    @pytest.mark.asyncio
    async def test_passes_when_llm_config_in_mongo(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("INTERNAL_API_KEY", "test-startup-api-key-long-enough")
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        with (
            patch(
                "mozaiksai.core.core_config.get_secret",
                side_effect=ValueError("not found"),
            ),
            patch(
                "mozaiksai.core.startup.validation._has_mongo_llm_config",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert warnings == []

    @pytest.mark.asyncio
    async def test_warns_when_workflows_path_missing(self, monkeypatch, tmp_path, caplog):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("INTERNAL_API_KEY", "test-startup-api-key-long-enough")
        monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(tmp_path / "nonexistent"))
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)

        with caplog.at_level(logging.WARNING, logger="mozaiksai.startup.validation"):
            warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert len(warnings) == 1
        assert "MOZAIKS_WORKFLOWS_PATH" in warnings[0]

    @pytest.mark.asyncio
    async def test_passes_when_workflows_path_exists(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("INTERNAL_API_KEY", "test-startup-api-key-long-enough")
        monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(tmp_path))
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert warnings == []

    @pytest.mark.asyncio
    async def test_skips_workflows_path_check_when_not_set(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("INTERNAL_API_KEY", "test-startup-api-key-long-enough")
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert warnings == []

    @pytest.mark.asyncio
    async def test_returns_multiple_warnings_when_llm_and_workflows_fail(self, monkeypatch, tmp_path):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("INTERNAL_API_KEY", "test-startup-api-key-long-enough")
        monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(tmp_path / "nonexistent"))
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)

        with (
            patch(
                "mozaiksai.core.core_config.get_secret",
                side_effect=ValueError("not found"),
            ),
            patch(
                "mozaiksai.core.startup.validation._has_mongo_llm_config",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert len(warnings) == 2

    @pytest.mark.asyncio
    async def test_does_not_raise_in_warn_mode_when_api_key_missing(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("INTERNAL_API_KEY", "test-startup-api-key-long-enough")
        monkeypatch.setenv("MOZAIKS_STARTUP_CHECKS", "warn")
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        with (
            patch(
                "mozaiksai.core.core_config.get_secret",
                side_effect=ValueError("not found"),
            ),
            patch(
                "mozaiksai.core.startup.validation._has_mongo_llm_config",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            # Must not raise
            warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert len(warnings) == 1


# ---------------------------------------------------------------------------
# run_startup_checks — MongoDB reachability check
# ---------------------------------------------------------------------------


class TestRunStartupChecksMongoCheck:
    @pytest.mark.asyncio
    async def test_warns_when_mongo_uri_not_set(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("INTERNAL_API_KEY", "test-startup-api-key-long-enough")
        monkeypatch.delenv("MONGO_URI", raising=False)
        monkeypatch.delenv("MONGODB_URI", raising=False)
        monkeypatch.delenv("MONGO_URL", raising=False)
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        with patch(
            "mozaiksai.core.startup.validation.get_secret",
            side_effect=ValueError("not found"),
        ):
            warnings = await run_startup_checks()

        assert len(warnings) == 1
        assert "MONGO_URI" in warnings[0]

    @pytest.mark.asyncio
    async def test_warns_when_mongo_not_reachable(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("INTERNAL_API_KEY", "test-startup-api-key-long-enough")
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        warnings = await run_startup_checks(_mongo_client=_FailingPingClient())

        assert len(warnings) == 1
        assert "MongoDB" in warnings[0]

    @pytest.mark.asyncio
    async def test_passes_when_mongo_reachable(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("INTERNAL_API_KEY", "test-startup-api-key-long-enough")
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert warnings == []

    @pytest.mark.asyncio
    async def test_strict_raises_when_mongo_uri_missing(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.delenv("MONGO_URI", raising=False)
        monkeypatch.delenv("MONGODB_URI", raising=False)
        monkeypatch.delenv("MONGO_URL", raising=False)
        monkeypatch.setenv("MOZAIKS_STARTUP_CHECKS", "strict")
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        with (
            patch(
                "mozaiksai.core.startup.validation.get_secret",
                side_effect=ValueError("not found"),
            ),
            pytest.raises(StartupConfigError) as exc_info,
        ):
            await run_startup_checks()

        assert "MONGO_URI" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_strict_raises_when_mongo_unreachable(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("MOZAIKS_STARTUP_CHECKS", "strict")
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        with pytest.raises(StartupConfigError) as exc_info:
            await run_startup_checks(_mongo_client=_FailingPingClient())

        assert "MongoDB" in str(exc_info.value)


# ---------------------------------------------------------------------------
# run_startup_checks — strict mode
# ---------------------------------------------------------------------------


class TestRunStartupChecksStrictMode:
    @pytest.mark.asyncio
    async def test_raises_in_strict_mode_when_api_key_missing(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("MOZAIKS_STARTUP_CHECKS", "strict")
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        with (
            patch(
                "mozaiksai.core.core_config.get_secret",
                side_effect=ValueError("not found"),
            ),
            patch(
                "mozaiksai.core.startup.validation._has_mongo_llm_config",
                new_callable=AsyncMock,
                return_value=False,
            ),
            pytest.raises(StartupConfigError),
        ):
            await run_startup_checks()

    @pytest.mark.asyncio
    async def test_strict_mode_passes_when_api_key_present(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("INTERNAL_API_KEY", "test-startup-api-key-long-enough")
        monkeypatch.setenv("MOZAIKS_STARTUP_CHECKS", "strict")
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert warnings == []

    @pytest.mark.asyncio
    async def test_strict_mode_error_message_mentions_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("MOZAIKS_STARTUP_CHECKS", "strict")
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        with (
            patch(
                "mozaiksai.core.core_config.get_secret",
                side_effect=ValueError("not found"),
            ),
            patch(
                "mozaiksai.core.startup.validation._has_mongo_llm_config",
                new_callable=AsyncMock,
                return_value=False,
            ),
            pytest.raises(StartupConfigError) as exc_info,
        ):
            await run_startup_checks()

        assert "OPENAI_API_KEY" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Structured log fields
# ---------------------------------------------------------------------------


class TestStartupValidationLogFields:
    @pytest.mark.asyncio
    async def test_ok_record_has_check_field(self, monkeypatch, caplog):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("INTERNAL_API_KEY", "test-startup-api-key-long-enough")
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        with caplog.at_level(logging.INFO, logger="mozaiksai.startup.validation"):
            await run_startup_checks(_mongo_client=_MockPingClient())

        ok_records = [
            r for r in caplog.records if r.__dict__.get("check") == "llm_api_key"
        ]
        assert ok_records
        assert ok_records[0].__dict__.get("source") == "env"

    @pytest.mark.asyncio
    async def test_fail_record_has_check_field(self, monkeypatch, caplog):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("INTERNAL_API_KEY", "test-startup-api-key-long-enough")
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        with (
            patch(
                "mozaiksai.core.core_config.get_secret",
                side_effect=ValueError("not found"),
            ),
            patch(
                "mozaiksai.core.startup.validation._has_mongo_llm_config",
                new_callable=AsyncMock,
                return_value=False,
            ),
            caplog.at_level(logging.WARNING, logger="mozaiksai.startup.validation"),
        ):
            await run_startup_checks(_mongo_client=_MockPingClient())

        fail_records = [
            r for r in caplog.records if r.__dict__.get("check") == "llm_api_key"
        ]
        assert fail_records
        assert fail_records[0].levelno == logging.WARNING

    @pytest.mark.asyncio
    async def test_summary_record_has_failure_count(self, monkeypatch, caplog):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("INTERNAL_API_KEY", "test-startup-api-key-long-enough")
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        with caplog.at_level(logging.INFO, logger="mozaiksai.startup.validation"):
            await run_startup_checks(_mongo_client=_MockPingClient())

        summary = [r for r in caplog.records if r.__dict__.get("check") == "summary"]
        assert summary
        assert summary[0].__dict__["failure_count"] == 0


# ---------------------------------------------------------------------------
# INTERNAL_API_KEY check
# ---------------------------------------------------------------------------


class TestInternalApiKeyCheck:
    @pytest.mark.asyncio
    async def test_warns_when_internal_api_key_not_set(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert len(warnings) == 1
        assert "INTERNAL_API_KEY" in warnings[0]

    @pytest.mark.asyncio
    async def test_no_warning_when_internal_api_key_set(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("INTERNAL_API_KEY", "strong-random-api-key-1234567890ab")
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert warnings == []

    @pytest.mark.asyncio
    async def test_internal_api_key_check_does_not_raise_in_strict_mode(self, monkeypatch):
        """INTERNAL_API_KEY absence is a warning, not a strict-mode error."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
        monkeypatch.setenv("MOZAIKS_STARTUP_CHECKS", "strict")
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        # Must not raise even in strict mode — internal key is defense-in-depth
        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert any("INTERNAL_API_KEY" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_warns_when_internal_api_key_too_short(self, monkeypatch):
        """Warn when INTERNAL_API_KEY is set but shorter than 32 chars."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("INTERNAL_API_KEY", "short-key")
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert len(warnings) == 1
        assert "INTERNAL_API_KEY" in warnings[0]
        assert "too short" in warnings[0]


# ---------------------------------------------------------------------------
# Upload storage writability check
# ---------------------------------------------------------------------------


class TestUploadStorageDirCheck:
    @pytest.mark.asyncio
    async def test_no_warning_when_upload_dir_not_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("INTERNAL_API_KEY", "test-startup-api-key-long-enough")
        monkeypatch.delenv("UPLOAD_STORAGE_DIR", raising=False)
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert warnings == []

    @pytest.mark.asyncio
    async def test_no_warning_when_upload_dir_exists_and_writable(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("INTERNAL_API_KEY", "test-startup-api-key-long-enough")
        monkeypatch.setenv("UPLOAD_STORAGE_DIR", str(tmp_path))
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert warnings == []

    @pytest.mark.asyncio
    async def test_no_warning_when_upload_dir_does_not_exist_yet(self, monkeypatch, tmp_path):
        """Non-existent upload dir is allowed — created on first upload."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("INTERNAL_API_KEY", "test-startup-api-key-long-enough")
        monkeypatch.setenv("UPLOAD_STORAGE_DIR", str(tmp_path / "nonexistent_uploads"))
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        # Non-existent dir is not warned about — it gets created on first upload
        assert not any("UPLOAD_STORAGE_DIR" in w for w in warnings)


# ---------------------------------------------------------------------------
# AUTH_ENABLED in production check
# ---------------------------------------------------------------------------


class TestAuthEnabledCheck:
    @pytest.mark.asyncio
    async def test_warns_when_auth_disabled_in_production(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("INTERNAL_API_KEY", "test-startup-api-key-long-enough")
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("AUTH_ENABLED", "false")
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert any("AUTH_ENABLED" in w for w in warnings)
        assert any("production" in w.lower() for w in warnings)

    @pytest.mark.asyncio
    async def test_no_warning_when_auth_disabled_in_development(self, monkeypatch):
        """AUTH_ENABLED=false is expected in dev — no warning."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("INTERNAL_API_KEY", "test-startup-api-key-long-enough")
        monkeypatch.setenv("ENV", "development")
        monkeypatch.setenv("AUTH_ENABLED", "false")
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert not any("AUTH_ENABLED" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_no_warning_when_auth_enabled_in_production(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("INTERNAL_API_KEY", "test-startup-api-key-long-enough")
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "jwt")
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert not any("AUTH_ENABLED" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_strict_raises_when_auth_disabled_in_production(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("AUTH_ENABLED", "false")
        monkeypatch.setenv("MOZAIKS_STARTUP_CHECKS", "strict")
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        with pytest.raises(StartupConfigError, match="AUTH_ENABLED"):
            await run_startup_checks(_mongo_client=_MockPingClient())

    @pytest.mark.asyncio
    async def test_no_warning_when_env_unset(self, monkeypatch):
        """When ENV is not set, no auth warning should fire (not in production)."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("INTERNAL_API_KEY", "test-startup-api-key-long-enough")
        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.setenv("AUTH_ENABLED", "false")
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert not any("AUTH_ENABLED" in w for w in warnings)


# ---------------------------------------------------------------------------
# Auth provider configured in production check
# ---------------------------------------------------------------------------


class TestAuthProviderCheck:
    """run_startup_checks warns when auth is not disabled but no provider is configured."""

    def _base_env(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("INTERNAL_API_KEY", "test-startup-api-key-long-enough")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("ENV", "production")
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)
        monkeypatch.delenv("AUTH_PROVIDER", raising=False)
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("KEYCLOAK_URL", raising=False)
        monkeypatch.delenv("KEYCLOAK_REALM", raising=False)
        monkeypatch.delenv("AUTH_JWKS_URL", raising=False)
        monkeypatch.delenv("AUTH_ISSUER", raising=False)
        monkeypatch.delenv("MOZAIKS_OIDC_AUTHORITY", raising=False)
        monkeypatch.delenv("MOZAIKS_OIDC_DISCOVERY_URL", raising=False)

    @pytest.mark.asyncio
    async def test_warns_in_production_when_auth_enabled_but_no_provider(self, monkeypatch):
        """AUTH_ENABLED=true in production with no provider silently falls back to demo mode."""
        self._base_env(monkeypatch)

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert any("auth provider" in w.lower() for w in warnings)
        assert any("production" in w.lower() for w in warnings)

    @pytest.mark.asyncio
    async def test_no_warning_when_explicit_auth_provider_set(self, monkeypatch):
        self._base_env(monkeypatch)
        monkeypatch.setenv("AUTH_PROVIDER", "jwt")

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert not any("auth provider" in w.lower() for w in warnings)

    @pytest.mark.asyncio
    async def test_no_warning_when_supabase_url_configured(self, monkeypatch):
        self._base_env(monkeypatch)
        monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert not any("auth provider" in w.lower() for w in warnings)

    @pytest.mark.asyncio
    async def test_no_warning_when_keycloak_configured(self, monkeypatch):
        self._base_env(monkeypatch)
        monkeypatch.setenv("KEYCLOAK_URL", "https://keycloak.example.com")
        monkeypatch.setenv("KEYCLOAK_REALM", "myrealm")

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert not any("auth provider" in w.lower() for w in warnings)

    @pytest.mark.asyncio
    async def test_no_warning_when_jwt_configured(self, monkeypatch):
        self._base_env(monkeypatch)
        monkeypatch.setenv("AUTH_JWKS_URL", "https://example.com/.well-known/jwks.json")
        monkeypatch.setenv("AUTH_ISSUER", "https://example.com")

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert not any("auth provider" in w.lower() for w in warnings)

    @pytest.mark.asyncio
    async def test_no_warning_when_oidc_authority_configured(self, monkeypatch):
        self._base_env(monkeypatch)
        monkeypatch.setenv("MOZAIKS_OIDC_AUTHORITY", "https://login.example.com")

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert not any("auth provider" in w.lower() for w in warnings)

    @pytest.mark.asyncio
    async def test_no_warning_when_oidc_discovery_url_configured(self, monkeypatch):
        self._base_env(monkeypatch)
        monkeypatch.setenv(
            "MOZAIKS_OIDC_DISCOVERY_URL",
            "https://login.example.com/.well-known/openid-configuration",
        )

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert not any("auth provider" in w.lower() for w in warnings)

    @pytest.mark.asyncio
    async def test_no_warning_in_development_without_provider(self, monkeypatch):
        """Missing provider is expected in dev — no warning outside production."""
        self._base_env(monkeypatch)
        monkeypatch.setenv("ENV", "development")

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert not any("auth provider" in w.lower() for w in warnings)

    @pytest.mark.asyncio
    async def test_no_warning_when_auth_explicitly_disabled_and_no_provider(self, monkeypatch):
        """AUTH_ENABLED=false already caught by the auth_enabled check; provider check skips."""
        self._base_env(monkeypatch)
        monkeypatch.setenv("AUTH_ENABLED", "false")

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        # auth_enabled fires, auth_provider does not
        assert any("AUTH_ENABLED" in w for w in warnings)
        assert not any("no auth provider" in w.lower() for w in warnings)

    @pytest.mark.asyncio
    async def test_strict_raises_when_production_auth_enabled_but_no_provider(self, monkeypatch):
        self._base_env(monkeypatch)
        monkeypatch.setenv("MOZAIKS_STARTUP_CHECKS", "strict")

        with pytest.raises(StartupConfigError, match="auth provider"):
            await run_startup_checks(_mongo_client=_MockPingClient())

    @pytest.mark.asyncio
    async def test_no_check_when_env_unset(self, monkeypatch):
        """Check only fires in production — not when ENV is unset."""
        self._base_env(monkeypatch)
        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.delenv("ENVIRONMENT", raising=False)

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert not any("auth provider" in w.lower() for w in warnings)


# ---------------------------------------------------------------------------
# RATE_LIMIT_ENABLED check
# ---------------------------------------------------------------------------


class TestRateLimitEnabledCheck:
    """run_startup_checks warns when RATE_LIMIT_ENABLED=false in production."""

    def _base_env(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("INTERNAL_API_KEY", "test-startup-api-key-long-enough")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "jwt")
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)
        monkeypatch.delenv("REDIS_URL", raising=False)

    @pytest.mark.asyncio
    async def test_warns_when_rate_limit_disabled_in_production(self, monkeypatch):
        self._base_env(monkeypatch)
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert any("RATE_LIMIT_ENABLED" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_no_warning_when_rate_limit_disabled_in_dev(self, monkeypatch):
        self._base_env(monkeypatch)
        monkeypatch.setenv("ENV", "development")
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert not any("RATE_LIMIT_ENABLED" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_no_warning_when_rate_limit_enabled_in_production(self, monkeypatch):
        self._base_env(monkeypatch)
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert not any("RATE_LIMIT_ENABLED" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_strict_raises_when_rate_limit_disabled_in_production(self, monkeypatch):
        self._base_env(monkeypatch)
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
        monkeypatch.setenv("MOZAIKS_STARTUP_CHECKS", "strict")

        with pytest.raises(StartupConfigError, match="RATE_LIMIT_ENABLED"):
            await run_startup_checks(_mongo_client=_MockPingClient())


# ---------------------------------------------------------------------------
# Redis connectivity check
# ---------------------------------------------------------------------------


class TestRedisConnectivityCheck:
    """run_startup_checks warns when REDIS_URL is set but Redis is unreachable."""

    def _base_env(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("INTERNAL_API_KEY", "test-startup-api-key-long-enough")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

    @pytest.mark.asyncio
    async def test_warns_when_redis_unreachable(self, monkeypatch):
        self._base_env(monkeypatch)
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6380")

        with patch(
            "mozaiksai.core.startup.validation.socket.create_connection",
            side_effect=ConnectionRefusedError("Connection refused"),
        ):
            warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert any("REDIS_URL" in w for w in warnings)
        assert any("not reachable" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_no_warning_when_redis_reachable(self, monkeypatch):
        self._base_env(monkeypatch)
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")

        mock_cm = patch(
            "mozaiksai.core.startup.validation.socket.create_connection",
        )
        with mock_cm as mock_conn:
            mock_conn.return_value.__enter__ = lambda s: s
            mock_conn.return_value.__exit__ = lambda s, *a: False
            warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert not any("REDIS_URL" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_no_check_when_redis_url_not_set(self, monkeypatch):
        self._base_env(monkeypatch)
        monkeypatch.delenv("REDIS_URL", raising=False)

        with patch(
            "mozaiksai.core.startup.validation.socket.create_connection",
            side_effect=AssertionError("Should not be called"),
        ):
            warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        # No Redis warnings should appear
        assert not any("REDIS_URL" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_redis_unreachable_does_not_raise_in_strict_mode(self, monkeypatch):
        """Redis failure is a warning only even in strict mode — in-memory fallback is functional."""
        self._base_env(monkeypatch)
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6380")
        monkeypatch.setenv("MOZAIKS_STARTUP_CHECKS", "strict")

        with patch(
            "mozaiksai.core.startup.validation.socket.create_connection",
            side_effect=ConnectionRefusedError("Connection refused"),
        ):
            # Should not raise — Redis degradation is handled gracefully
            warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert any("REDIS_URL" in w for w in warnings)
