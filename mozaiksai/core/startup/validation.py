"""Startup configuration checks for the Mozaiks runtime host.

Validates critical configuration at boot time rather than deferring failures
to the first request. All checks emit structured log records so they can be
forwarded to any aggregator.

Behaviour is controlled by the ``MOZAIKS_STARTUP_CHECKS`` environment variable:
  ``"strict"`` — raise :exc:`StartupConfigError` on any required-config gap.
  ``"warn"``   — (default) emit WARNING log records but do not block startup.

Checks performed:
  Secret policy        — any selected app/security/secrets.yaml or explicitly
                         configured manifest must be valid, even in warn mode.
                         Required flags document needs; consumers enforce them.
  LLM API key          — selected provider key through the shared app secret
                         resolver, or a MongoDB llm_config document. Ollama
                         needs no API key.
  MongoDB              — MONGO_URI through the same resolver used by the Mongo
                         client, followed by a ping within the driver timeout.
  Workflows path       — ``MOZAIKS_WORKFLOWS_PATH``, if set, must exist on disk.
  Upload dir           — ``UPLOAD_STORAGE_DIR``, if set, must be writable when it exists.
  Auth configuration   — mode-INDEPENDENT hard gate: the canonical auth
                         resolution must succeed. Enabled auth without a usable
                         provider, contradictory declarations, unrecognized
                         AUTH_ENABLED values, conflicting ENV/ENVIRONMENT
                         declarations, and any no-auth operation outside a
                         recognized local/development/test environment abort
                         startup regardless of ``MOZAIKS_STARTUP_CHECKS``.
  INTERNAL_API_KEY     — warns when the key is absent or shorter than 32 chars
                         (defense-in-depth; not a hard gate).
  RATE_LIMIT_ENABLED   — warns when ``ENV=production`` and ``RATE_LIMIT_ENABLED=false``.
  Redis connectivity   — when ``REDIS_URL`` is set, validates TCP reachability on startup.

Log record fields:
  check    — check identifier (``"llm_api_key"``, ``"workflows_path"``, ``"summary"``)
  mode     — configured check mode (``"strict"`` | ``"warn"``)
  source   — resolution source when a check passes (e.g. ``"env"``, ``"mongo_llm_config"``)
  failure_count — number of failed checks (summary record only)
"""
from __future__ import annotations

import logging
import os
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from mozaiksai.core.adapters.llm_fallback import MODEL_API_KEY_ENV_NAMES, resolve_model_api_key
from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.environment import EnvironmentConfigError, deployment_environment
from mozaiksai.core.secrets import (
    SecretContractError,
    SecretResolutionError,
    load_secret_contract,
    resolve_secret,
)

logger = logging.getLogger("mozaiksai.startup.validation")


class StartupConfigError(RuntimeError):
    """Raised in strict mode when required startup configuration is absent."""


def _startup_mode() -> str:
    """Return the configured startup-check mode (``"strict"`` or ``"warn"``)."""
    return os.getenv("MOZAIKS_STARTUP_CHECKS", "warn").strip().lower()


def _can_resolve_api_key() -> tuple[bool, str]:
    """Use the AG2 adapter's selected-provider resolution for startup too."""
    api_type = os.getenv("LLM_PRIMARY_API_TYPE", "openai").strip().lower()
    names = MODEL_API_KEY_ENV_NAMES.get(api_type, MODEL_API_KEY_ENV_NAMES["openai"])
    if not names:
        return True, "ollama (no API key required)"
    return bool(resolve_model_api_key(api_type)), " / ".join(names)


async def _has_mongo_llm_config() -> bool:
    """Return True when MongoDB ``llm_config`` collection has at least one document."""
    try:
        from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE, BuilderCollections

        db = get_mongo_client()[SYSTEM_DATABASE]
        doc = await db[BuilderCollections.LLM_CONFIG].find_one()
        return doc is not None
    except Exception as exc:
        logger.debug("LLM config MongoDB lookup failed (treating as absent): %s", exc)
        return False


async def _ping_mongo(client: Any) -> None:
    """Ping the MongoDB server; raises if unreachable."""
    await client.admin.command("ping")


async def run_startup_checks(*, _mongo_client: Any = None) -> list[str]:
    """Run all boot-time configuration checks.

    Returns a list of warning strings (empty when everything is OK).
    In ``strict`` mode raises :exc:`StartupConfigError` on the first gap.

    The ``_mongo_client`` parameter is reserved for testing; pass ``None``
    (the default) in production code.
    """
    mode = _startup_mode()
    warnings: list[str] = []

    # An invalid authored policy is a contract error, independent of readiness mode.
    try:
        load_secret_contract()
    except SecretContractError as exc:
        raise StartupConfigError(f"Secret configuration is invalid: {exc}") from exc

    # ── LLM API key ──────────────────────────────────────────────────────────
    try:
        api_key_resolved, key_var_name = _can_resolve_api_key()
    except SecretResolutionError as exc:
        raise StartupConfigError(f"Model API key resolution failed: {exc}") from exc
    api_key_in_mongo = False if api_key_resolved else await _has_mongo_llm_config()

    if not api_key_resolved and not api_key_in_mongo:
        msg = (
            f"{key_var_name} is not set and no llm_config document was found in MongoDB. "
            "Workflow LLM calls will fail at request time. "
            f"Configure {key_var_name} through the app secret policy (matching your LLM_PRIMARY_API_TYPE "
            "setting) or insert an llm_config document into the mozaiks_system.llm_config "
            "collection."
        )
        warnings.append(msg)
        logger.warning(
            "STARTUP_CHECK_FAILED: %s",
            msg,
            extra={"check": "llm_api_key", "mode": mode},
        )
        if mode == "strict":
            raise StartupConfigError(msg)
    else:
        source = "selected_provider" if api_key_resolved else "mongo_llm_config"
        logger.info(
            "STARTUP_CHECK_OK: LLM API key resolvable via %s",
            source,
            extra={"check": "llm_api_key", "mode": mode, "source": source},
        )

    # ── MongoDB reachability ──────────────────────────────────────────────────
    try:
        mongo_uri = resolve_secret("MONGO_URI")
    except SecretResolutionError as exc:
        if exc.source != "missing":
            raise StartupConfigError(f"MONGO_URI secret resolution failed: {exc}") from exc
        mongo_uri = ""
    if not mongo_uri:
        msg = (
            "MONGO_URI is not configured. The runtime requires MongoDB for session "
            "persistence. Configure the MONGO_URI handle through the app secret policy or environment."
        )
        warnings.append(msg)
        logger.warning("STARTUP_CHECK_FAILED: %s", msg, extra={"check": "mongo_uri", "mode": mode})
        if mode == "strict":
            raise StartupConfigError(msg)
    else:
        try:
            client = _mongo_client if _mongo_client is not None else get_mongo_client()
            await _ping_mongo(client)
            logger.info(
                "STARTUP_CHECK_OK: MongoDB reachable",
                extra={"check": "mongo_uri", "mode": mode},
            )
        except Exception as ping_err:
            msg = f"MongoDB is not reachable: {ping_err}"
            warnings.append(msg)
            logger.warning(
                "STARTUP_CHECK_FAILED: %s",
                msg,
                extra={"check": "mongo_uri", "mode": mode},
            )
            if mode == "strict":
                raise StartupConfigError(msg) from ping_err

    # ── Workflows path ────────────────────────────────────────────────────────
    workflows_path = os.getenv("MOZAIKS_WORKFLOWS_PATH", "").strip()
    if workflows_path:
        p = Path(workflows_path)
        if not p.exists():
            msg = f"MOZAIKS_WORKFLOWS_PATH={workflows_path!r} does not exist on disk."
            warnings.append(msg)
            logger.warning(
                "STARTUP_CHECK_FAILED: %s",
                msg,
                extra={"check": "workflows_path", "mode": mode},
            )
        else:
            logger.info(
                "STARTUP_CHECK_OK: workflows path exists at %s",
                workflows_path,
                extra={"check": "workflows_path", "mode": mode},
            )

    # ── Upload storage writability ───────────────────────────────────────────
    upload_dir = os.getenv("UPLOAD_STORAGE_DIR", "").strip()
    if upload_dir:
        upload_path = Path(upload_dir)
        if upload_path.exists() and not os.access(upload_path, os.W_OK):
            msg = (
                f"UPLOAD_STORAGE_DIR={upload_dir!r} exists but is not writable. "
                "File uploads will fail at request time. Check directory permissions."
            )
            warnings.append(msg)
            logger.warning(
                "STARTUP_CHECK_FAILED: %s",
                msg,
                extra={"check": "upload_storage_dir", "mode": mode},
            )
        else:
            logger.info(
                "STARTUP_CHECK_OK: UPLOAD_STORAGE_DIR configured at %s",
                upload_dir,
                extra={"check": "upload_storage_dir", "mode": mode},
            )

    # ── Auth configuration resolution (fail closed, mode-independent) ────────
    # The canonical auth resolution (mozaiksai.core.auth.adapters.registry)
    # is fatal — never a warning — for: explicitly enabled auth whose provider
    # is missing/unknown/incomplete, contradictory explicit declarations,
    # unrecognized AUTH_ENABLED values, conflicting ENV/ENVIRONMENT
    # declarations, and ANY no-auth operation (explicit disable or implicit
    # demo mode) outside a recognized local/development/test environment.
    # MOZAIKS_STARTUP_CHECKS mode does not weaken this.
    from mozaiksai.core.auth.adapters.base import AuthError
    from mozaiksai.core.auth.adapters.registry import validate_auth_provider_configuration

    try:
        env_name = deployment_environment()
        resolved_provider = validate_auth_provider_configuration()
        logger.info(
            "STARTUP_CHECK_OK: auth provider resolved (%s, env=%s)",
            resolved_provider,
            env_name or "unset",
            extra={"check": "auth_provider_resolution", "mode": mode},
        )
    except (AuthError, EnvironmentConfigError) as auth_exc:
        msg = f"Authentication configuration is invalid: {auth_exc}"
        logger.error(
            "STARTUP_CHECK_FAILED: %s",
            msg,
            extra={"check": "auth_provider_resolution", "mode": mode},
        )
        raise StartupConfigError(msg) from auth_exc

    # ── INTERNAL_API_KEY ─────────────────────────────────────────────────────
    # When not set, service-to-service requests bypass the key check (dev mode).
    # Warn operators so this is not accidentally left unset in production.
    # Also warn when the key is too short to provide meaningful entropy (< 32 chars).
    internal_key = os.getenv("INTERNAL_API_KEY", "").strip()
    if not internal_key:
        msg = (
            "INTERNAL_API_KEY is not set. Service-to-service API key validation "
            "will be skipped. Set INTERNAL_API_KEY to a strong random value in "
            "production to protect internal runtime endpoints."
        )
        warnings.append(msg)
        logger.warning(
            "STARTUP_CHECK_FAILED: %s",
            msg,
            extra={"check": "internal_api_key", "mode": mode},
        )
        # Not raised in strict mode — the endpoint still requires user auth;
        # the internal key is a defense-in-depth layer, not the only gate.
    elif len(internal_key) < 32:
        msg = (
            f"INTERNAL_API_KEY is set but may be too short ({len(internal_key)} chars). "
            "Use a randomly generated key of at least 32 characters for adequate entropy."
        )
        warnings.append(msg)
        logger.warning(
            "STARTUP_CHECK_FAILED: %s",
            msg,
            extra={"check": "internal_api_key", "mode": mode},
        )
    else:
        logger.info(
            "STARTUP_CHECK_OK: INTERNAL_API_KEY is configured",
            extra={"check": "internal_api_key", "mode": mode},
        )

    # ── RATE_LIMIT_ENABLED in production ─────────────────────────────────────
    rate_limit_enabled = os.getenv("RATE_LIMIT_ENABLED", "true").strip().lower()
    if env_name == "production" and rate_limit_enabled in {"false", "0", "no", "off"}:
        msg = (
            "RATE_LIMIT_ENABLED=false in a production environment. "
            "API rate limiting is disabled — the service is vulnerable to request flooding. "
            "Set RATE_LIMIT_ENABLED=true before serving production traffic."
        )
        warnings.append(msg)
        logger.warning(
            "STARTUP_CHECK_FAILED: %s",
            msg,
            extra={"check": "rate_limit_enabled", "mode": mode},
        )
        if mode == "strict":
            raise StartupConfigError(msg)
    else:
        logger.info(
            "STARTUP_CHECK_OK: RATE_LIMIT_ENABLED=%s (env=%s)",
            rate_limit_enabled,
            env_name or "unset",
            extra={"check": "rate_limit_enabled", "mode": mode},
        )

    # ── Redis connectivity ────────────────────────────────────────────────────
    # When REDIS_URL is set, validate connectivity at startup. Without Redis,
    # the rate limiter silently falls back to in-memory storage, which does not
    # enforce limits across multiple runtime instances or pods.
    redis_url = os.getenv("REDIS_URL", "").strip()
    if redis_url:
        try:
            parsed = urlparse(redis_url)
            host = parsed.hostname or "localhost"
            port = parsed.port or 6379
            with socket.create_connection((host, port), timeout=3):
                pass
            logger.info(
                "STARTUP_CHECK_OK: Redis reachable at %s:%s",
                host,
                port,
                extra={"check": "redis_url", "mode": mode},
            )
        except Exception as exc:
            safe_url = redis_url.split("@")[-1] if "@" in redis_url else redis_url
            msg = (
                f"REDIS_URL is set but Redis is not reachable ({safe_url}): {exc}. "
                "The rate limiter will fall back to in-memory storage, which does not "
                "enforce limits across multiple runtime instances."
            )
            warnings.append(msg)
            logger.warning(
                "STARTUP_CHECK_FAILED: %s",
                msg,
                extra={"check": "redis_url", "mode": mode},
            )
            # Not raised in strict mode — in-memory fallback is functional;
            # this is a configuration warning, not a hard failure.

    # ── Summary ───────────────────────────────────────────────────────────────
    if not warnings:
        logger.info(
            "STARTUP_CHECKS_PASSED: all checks passed",
            extra={"check": "summary", "mode": mode, "failure_count": 0},
        )
    else:
        logger.warning(
            "STARTUP_CHECKS_INCOMPLETE: %d check(s) need attention (mode=%s)",
            len(warnings),
            mode,
            extra={"check": "summary", "mode": mode, "failure_count": len(warnings)},
        )

    return warnings


__all__ = ["StartupConfigError", "run_startup_checks"]
