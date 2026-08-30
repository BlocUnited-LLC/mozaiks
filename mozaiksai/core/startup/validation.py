"""Startup configuration checks for the Mozaiks runtime host.

Validates critical configuration at boot time rather than deferring failures
to the first request. All checks emit structured log records so they can be
forwarded to any aggregator.

Behaviour is controlled by the ``MOZAIKS_STARTUP_CHECKS`` environment variable:
  ``"strict"`` — raise :exc:`StartupConfigError` on any required-config gap.
  ``"warn"``   — (default) emit WARNING log records but do not block startup.

Checks performed:
  LLM API key          — provider-specific API key resolvable via env based on
                         ``LLM_PRIMARY_API_TYPE`` (google → GEMINI_API_KEY /
                         GOOGLE_API_KEY; anthropic → ANTHROPIC_API_KEY; openai
                         → OPENAI_API_KEY / Key Vault alias ``OpenAIApiKey``),
                         or a MongoDB ``llm_config`` document.
  MongoDB              — MONGO_URI must be set (env or Key Vault alias ``MongoURI``)
                         and MongoDB must respond to a ping within the driver timeout.
  Workflows path       — ``MOZAIKS_WORKFLOWS_PATH``, if set, must exist on disk.
  Upload dir           — ``UPLOAD_STORAGE_DIR``, if set, must be writable when it exists.
  AUTH_ENABLED         — warns when ``ENV=production`` and ``AUTH_ENABLED=false``.
  Auth provider        — warns when ``ENV=production``, auth is not explicitly disabled,
                         and no auth provider env vars are configured (silently falls
                         back to demo mode without this check).
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

from mozaiksai.core.core_config import get_mongo_client, get_secret

logger = logging.getLogger("mozaiksai.startup.validation")


class StartupConfigError(RuntimeError):
    """Raised in strict mode when required startup configuration is absent."""


def _startup_mode() -> str:
    """Return the configured startup-check mode (``"strict"`` or ``"warn"``)."""
    return os.getenv("MOZAIKS_STARTUP_CHECKS", "warn").strip().lower()


def _can_resolve_api_key() -> tuple[bool, str]:
    """Return (resolvable, key_env_var_name) based on the configured LLM provider.

    Mirrors the provider-specific resolution order in ``llm_fallback.py`` so
    the startup check matches what the actual LLM adapter will attempt:
      - ``LLM_PRIMARY_API_TYPE=google``    → GEMINI_API_KEY / GOOGLE_API_KEY
      - ``LLM_PRIMARY_API_TYPE=anthropic`` → ANTHROPIC_API_KEY
      - otherwise (openai / default)       → OPENAI_API_KEY / Key Vault alias

    Key Vault is attempted only for the OpenAI case (existing behaviour) since
    that is the only alias currently registered.
    """
    api_type = os.getenv("LLM_PRIMARY_API_TYPE", "openai").strip().lower()

    if api_type == "google":
        key = os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()
        return bool(key), "GEMINI_API_KEY / GOOGLE_API_KEY"

    if api_type == "anthropic":
        key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        return bool(key), "ANTHROPIC_API_KEY"

    # openai / azure / default
    if os.getenv("OPENAI_API_KEY", "").strip():
        return True, "OPENAI_API_KEY"
    try:
        val = get_secret("OpenAIApiKey")
        return bool(str(val or "").strip()), "OpenAIApiKey (Key Vault)"
    except Exception:
        return False, "OPENAI_API_KEY"


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

    # ── LLM API key ──────────────────────────────────────────────────────────
    api_key_in_env, key_var_name = _can_resolve_api_key()
    api_key_in_mongo = False if api_key_in_env else await _has_mongo_llm_config()

    if not api_key_in_env and not api_key_in_mongo:
        msg = (
            f"{key_var_name} is not set and no llm_config document was found in MongoDB. "
            "Workflow LLM calls will fail at request time. "
            f"Set {key_var_name} in the environment (matching your LLM_PRIMARY_API_TYPE "
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
        source = "env" if api_key_in_env else "mongo_llm_config"
        logger.info(
            "STARTUP_CHECK_OK: LLM API key resolvable via %s",
            source,
            extra={"check": "llm_api_key", "mode": mode, "source": source},
        )

    # ── MongoDB reachability ──────────────────────────────────────────────────
    mongo_uri = os.getenv("MONGO_URI", "").strip()
    if not mongo_uri:
        try:
            get_secret("MongoURI")
        except Exception:
            pass
        mongo_uri = os.getenv("MONGO_URI", "").strip()
    if not mongo_uri:
        msg = (
            "MONGO_URI is not configured. The runtime requires MongoDB for session "
            "persistence. Set MONGO_URI in the environment or Key Vault secret 'MongoURI'."
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

    # ── AUTH_ENABLED in production ───────────────────────────────────────────
    # AUTH_ENABLED=false is intentional in dev but a critical gap in production.
    # Warn when the runtime environment is production and auth is disabled.
    env_name = os.getenv("ENV", os.getenv("ENVIRONMENT", "")).strip().lower()
    auth_enabled = os.getenv("AUTH_ENABLED", "true").strip().lower()
    if env_name == "production" and auth_enabled in {"false", "0", "no", "off"}:
        msg = (
            "AUTH_ENABLED=false in a production environment. "
            "All endpoints are exposed to unauthenticated access. "
            "Set AUTH_ENABLED=true and configure an auth provider before serving production traffic."
        )
        warnings.append(msg)
        logger.warning(
            "STARTUP_CHECK_FAILED: %s",
            msg,
            extra={"check": "auth_enabled", "mode": mode},
        )
        if mode == "strict":
            raise StartupConfigError(msg)
    else:
        logger.info(
            "STARTUP_CHECK_OK: AUTH_ENABLED=%s (env=%s)",
            auth_enabled,
            env_name or "unset",
            extra={"check": "auth_enabled", "mode": mode},
        )

    # ── Auth provider configured in production ───────────────────────────────
    # When auth is not explicitly disabled but no provider env vars are set,
    # _auto_detect_provider() silently falls back to "none" (demo mode).
    # Catch this at startup so operators are not surprised in production.
    if env_name == "production" and auth_enabled not in {"false", "0", "no", "off"}:
        explicit_provider = os.getenv("AUTH_PROVIDER", "").strip()
        has_supabase = bool(os.getenv("SUPABASE_URL", "").strip())
        has_keycloak = bool(
            os.getenv("KEYCLOAK_URL", "").strip() and os.getenv("KEYCLOAK_REALM", "").strip()
        )
        has_jwt_overrides = bool(
            os.getenv("AUTH_JWKS_URL", "").strip() and os.getenv("AUTH_ISSUER", "").strip()
        )
        has_oidc_discovery = bool(
            os.getenv("MOZAIKS_OIDC_DISCOVERY_URL", "").strip()
            or os.getenv("MOZAIKS_OIDC_AUTHORITY", "").strip()
        )
        has_jwt = has_jwt_overrides or has_oidc_discovery
        has_provider = bool(explicit_provider) or has_supabase or has_keycloak or has_jwt
        if not has_provider:
            msg = (
                "AUTH_ENABLED is not false but no auth provider is configured in production. "
                "The runtime will silently fall back to demo mode (no authentication). "
                "Set AUTH_PROVIDER or configure SUPABASE_URL, KEYCLOAK_URL, "
                "AUTH_JWKS_URL + AUTH_ISSUER, or MOZAIKS_OIDC_AUTHORITY."
            )
            warnings.append(msg)
            logger.warning(
                "STARTUP_CHECK_FAILED: %s",
                msg,
                extra={"check": "auth_provider", "mode": mode},
            )
            if mode == "strict":
                raise StartupConfigError(msg)
        else:
            detected = (
                explicit_provider
                or ("supabase" if has_supabase else ("keycloak" if has_keycloak else "jwt"))
            )
            logger.info(
                "STARTUP_CHECK_OK: auth provider configured (%s)",
                detected,
                extra={"check": "auth_provider", "mode": mode},
            )

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
