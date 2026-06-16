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
        monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(tmp_path))
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert warnings == []

    @pytest.mark.asyncio
    async def test_skips_workflows_path_check_when_not_set(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)

        warnings = await run_startup_checks(_mongo_client=_MockPingClient())

        assert warnings == []

    @pytest.mark.asyncio
    async def test_returns_multiple_warnings_when_llm_and_workflows_fail(self, monkeypatch, tmp_path):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
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
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        warnings = await run_startup_checks(_mongo_client=_FailingPingClient())

        assert len(warnings) == 1
        assert "MongoDB" in warnings[0]

    @pytest.mark.asyncio
    async def test_passes_when_mongo_reachable(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
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
        monkeypatch.delenv("MOZAIKS_STARTUP_CHECKS", raising=False)
        monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

        with caplog.at_level(logging.INFO, logger="mozaiksai.startup.validation"):
            await run_startup_checks(_mongo_client=_MockPingClient())

        summary = [r for r in caplog.records if r.__dict__.get("check") == "summary"]
        assert summary
        assert summary[0].__dict__["failure_count"] == 0
