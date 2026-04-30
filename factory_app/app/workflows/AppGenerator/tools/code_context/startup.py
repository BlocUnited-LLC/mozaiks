from __future__ import annotations

import logging
import os
from typing import Optional

from .tools import initialize_code_context_tools

logger = logging.getLogger(__name__)

try:
    from pymongo import MongoClient  # type: ignore
except Exception:  # pragma: no cover
    MongoClient = None  # type: ignore

try:
    from app.services.connectors.mongodb import get_mongo_client  # type: ignore
except Exception:  # pragma: no cover
    get_mongo_client = None  # type: ignore


def _resolve_db_name() -> str:
    return str(os.getenv("MOZAIKS_CODE_CONTEXT_DB") or "MozaiksAI").strip() or "MozaiksAI"


def _resolve_uri() -> Optional[str]:
    for key in (
        "MOZAIKS_CODE_CONTEXT_MONGO_URI",
        "MOZAIKS_MONGO_URI",
        "MONGODB_URI",
        "MONGO_URI",
    ):
        raw = os.getenv(key)
        if raw and str(raw).strip():
            return str(raw).strip()
    return None


def _build_db():
    db_name = _resolve_db_name()
    uri = _resolve_uri()

    if MongoClient is not None and uri:
        try:
            client = MongoClient(uri)
            return client[db_name]
        except Exception as exc:
            logger.warning("Code context MongoClient init failed: %s", exc)

    if get_mongo_client is not None:
        try:
            client = get_mongo_client()
            return client[db_name]
        except Exception as exc:
            logger.warning("Code context core Mongo client init failed: %s", exc)

    return None


class CodeContextStartup:
    """Startup service to initialize code context tooling."""

    def __init__(self, enabled: Optional[bool] = None) -> None:
        raw = os.getenv("MOZAIKS_CODE_CONTEXT_ENABLED")
        if enabled is not None:
            self._enabled = bool(enabled)
        elif raw is None:
            self._enabled = True
        else:
            self._enabled = str(raw).strip().lower() in ("1", "true", "yes", "y", "on")

    def start(self):
        if not self._enabled:
            logger.info("Code context startup disabled")
            return None
        db = _build_db()
        if db is None:
            logger.warning("Code context startup skipped (no MongoDB client available)")
            return None
        initialize_code_context_tools(db)
        
        # Reset the indexed hashes tracker for this new workflow run
        try:
            from ..hook_index_agent_output import reset_indexed_hashes
            reset_indexed_hashes()
            logger.debug("Reset per-agent indexing tracker")
        except ImportError:
            pass  # Hook not installed
        logger.info("Code context tools initialized")
        return None

    async def stop(self):
        return None
