"""Startup configuration checks for the Mozaiks runtime host.

Validates critical configuration at boot time rather than deferring failures
to the first request. All checks emit structured log records so they can be
forwarded to any aggregator.

Behaviour is controlled by the ``MOZAIKS_STARTUP_CHECKS`` environment variable:
  ``"strict"`` — raise :exc:`StartupConfigError` on any required-config gap.
  ``"warn"``   — (default) emit WARNING log records but do not block startup.

Checks performed:
  LLM API key     — OPENAI_API_KEY resolvable via env, Key Vault alias
                    ``OpenAIApiKey``, or a MongoDB ``llm_config`` document.
  MongoDB         — MONGO_URI must be set (env or Key Vault alias ``MongoURI``)
                    and MongoDB must respond to a ping within the driver timeout.
  Workflows path  — ``MOZAIKS_WORKFLOWS_PATH``, if set, must exist on disk.

Log record fields:
  check    — check identifier (``"llm_api_key"``, ``"workflows_path"``, ``"summary"``)
  mode     — configured check mode (``"strict"`` | ``"warn"``)
  source   — resolution source when a check passes (e.g. ``"env"``, ``"mongo_llm_config"``)
  failure_count — number of failed checks (summary record only)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from mozaiksai.core.core_config import get_mongo_client, get_secret

logger = logging.getLogger("mozaiksai.startup.validation")


class StartupConfigError(RuntimeError):
    """Raised in strict mode when required startup configuration is absent."""


def _startup_mode() -> str:
    """Return the configured startup-check mode (``"strict"`` or ``"warn"``)."""
    return os.getenv("MOZAIKS_STARTUP_CHECKS", "warn").strip().lower()


def _can_resolve_api_key() -> bool:
    """Return True when ``OPENAI_API_KEY`` is resolvable from env or Key Vault.

    Key Vault is attempted only when the env var is absent, so the fast path
    (env var set) incurs zero I/O.
    """
    if os.getenv("OPENAI_API_KEY", "").strip():
        return True
    try:
        val = get_secret("OpenAIApiKey")
        return bool(str(val or "").strip())
    except Exception:
        return False


async def _has_mongo_llm_config() -> bool:
    """Return True when MongoDB ``llm_config`` collection has at least one document."""
    try:
        from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE, BuilderCollections

        db = get_mongo_client()[SYSTEM_DATABASE]
        doc = await db[BuilderCollections.LLM_CONFIG].find_one()
        return doc is not None
    except Exception:
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
    api_key_in_env = _can_resolve_api_key()
    api_key_in_mongo = False if api_key_in_env else await _has_mongo_llm_config()

    if not api_key_in_env and not api_key_in_mongo:
        msg = (
            "OPENAI_API_KEY is not set and no llm_config document was found in MongoDB. "
            "Workflow LLM calls will fail at request time. "
            "Set OPENAI_API_KEY in the environment or insert an llm_config document "
            "into the mozaiks_system.llm_config collection."
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
