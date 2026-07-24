# ==============================================================================
# FILE: mozaiksai/core/data/persistence/persistence_manager.py
# DESCRIPTION: Mongo-backed persistence layer for chat sessions, AG2 streams, and runtime UI state.
# ==============================================================================

"""Persistence layer for mozaiksai workflows.

Runtime persistence owns durable run records, AG2 event history, artifacts, and
AG2 stream storage:
  * PersistenceManager: Mongo client + indexes (runtime-owned only)
  * AG2PersistenceManager: ChatSessions + AG2 run stream projections

AG2 1.0 beta telemetry is middleware-based OpenTelemetry instrumentation and lives
in ``mozaiksai.core.observability``. Do not add agent-turn telemetry collectors
or in-memory performance counters here.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import os
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pymongo import ReturnDocument, UpdateOne

from logs.logging_config import get_workflow_logger
from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.multitenant import build_app_scope_filter, coalesce_app_id, dual_write_app_scope

from ..models import WorkflowStatus, WorkflowUIState
from .namespaces import SYSTEM_DATABASE, RuntimeCollections

# Lazy import to avoid circular dependency - see _format_message_for_storage

logger = get_workflow_logger("persistence")


def _has_index_with_keys(existing_indexes: list[dict[str, Any]], expected_keys: list[tuple[str, int]]) -> bool:
    """Return whether an equivalent index key pattern already exists."""
    expected = list(expected_keys)
    for idx in existing_indexes:
        key_spec = idx.get("key")
        if hasattr(key_spec, "items"):
            try:
                if list(key_spec.items()) == expected:
                    return True
            except Exception:
                continue
    return False


_GENERAL_CHAT_COLLECTION = RuntimeCollections.GENERAL_CHAT_SESSIONS
_GENERAL_CHAT_COUNTER_COLLECTION = RuntimeCollections.GENERAL_CHAT_COUNTERS


# Canonical field names and defaults are derived directly from WorkflowUIState()
# so the dict representation can never diverge from the Pydantic model.
_WORKFLOW_UI_STATE_MODEL_DEFAULTS: dict[str, Any] = WorkflowUIState().model_dump()


def build_default_workflow_ui_state() -> dict[str, Any]:
    return deepcopy(_WORKFLOW_UI_STATE_MODEL_DEFAULTS)


def extract_workflow_ui_state(chat_doc: Any) -> dict[str, Any]:
    normalized = build_default_workflow_ui_state()
    if not isinstance(chat_doc, dict):
        return normalized

    raw_state = chat_doc.get("workflow_ui_state")
    if not isinstance(raw_state, dict):
        return normalized

    # Forward every field that the model declares; type-check dict-valued fields.
    for field_name in WorkflowUIState.model_fields:
        if field_name not in raw_state:
            continue
        raw_value = raw_state[field_name]
        if raw_value is None:
            normalized[field_name] = None
        elif field_name == "tool_calls" and isinstance(raw_value, dict):
            normalized[field_name] = {
                str(k): deepcopy(v)
                for k, v in raw_value.items()
                if isinstance(k, str) and isinstance(v, dict)
            }
        elif isinstance(raw_value, dict):
            normalized[field_name] = deepcopy(raw_value)
        else:
            # Scalar fields (e.g. schema_version)
            normalized[field_name] = raw_value
    return normalized


def extract_last_artifact(chat_doc: Any) -> dict[str, Any] | None:
    artifact = extract_workflow_ui_state(chat_doc).get("last_artifact")
    return artifact if isinstance(artifact, dict) else None


def extract_pending_input_request(chat_doc: Any) -> dict[str, Any] | None:
    pending = extract_workflow_ui_state(chat_doc).get("pending_input_request")
    return pending if isinstance(pending, dict) else None


class PersistenceManager:
    """Mongo connection holder for runtime persistence."""

    def __init__(self):
        self.client: Any | None = None
        self._init_lock = asyncio.Lock()
        logger.info("PersistenceManager created (lazy init)")

    async def _backfill_chat_sessions_workflow_ui_state(self, coll: Any) -> None:
        query = {
            "$or": [
                {"workflow_ui_state": {"$exists": False}},
                {"workflow_ui_state.schema_version": {"$exists": False}},
                {"last_artifact": {"$exists": True}},
                {"pending_input_request": {"$exists": True}},
            ]
        }
        projection = {
            "workflow_ui_state": 1,
            "last_artifact": 1,
            "pending_input_request": 1,
        }

        operations: list[UpdateOne] = []
        migrated = 0
        async for doc in coll.find(query, projection):
            if not isinstance(doc, dict):
                continue
            chat_id = doc.get("_id")
            if not chat_id:
                continue

            workflow_ui_state = extract_workflow_ui_state(doc)
            legacy_last_artifact = doc.get("last_artifact")
            legacy_pending_input = doc.get("pending_input_request")

            if workflow_ui_state.get("last_artifact") is None and isinstance(legacy_last_artifact, dict):
                workflow_ui_state["last_artifact"] = deepcopy(legacy_last_artifact)
            if workflow_ui_state.get("pending_input_request") is None and isinstance(legacy_pending_input, dict):
                workflow_ui_state["pending_input_request"] = deepcopy(legacy_pending_input)

            operations.append(
                UpdateOne(
                    {"_id": chat_id},
                    {
                        "$set": {"workflow_ui_state": workflow_ui_state},
                        "$unset": {
                            "last_artifact": "",
                            "pending_input_request": "",
                        },
                    },
                )
            )

            if len(operations) >= 200:
                await coll.bulk_write(operations, ordered=False)
                migrated += len(operations)
                operations = []

        if operations:
            await coll.bulk_write(operations, ordered=False)
            migrated += len(operations)

        if migrated > 0:
            logger.info(
                "[WORKFLOW_UI_STATE] Migrated %d ChatSessions docs to canonical workflow_ui_state",
                migrated,
            )

    async def _ensure_client(self) -> None:
        if self.client is not None:
            return
        async with self._init_lock:
            if self.client is not None:
                return
            self.client = get_mongo_client()
            try:
                # Primary chat session collection (canonical)
                coll = self.client[SYSTEM_DATABASE][RuntimeCollections.CHAT_SESSIONS]
                # Check if index already exists before creating
                existing_indexes = await coll.list_indexes().to_list(length=None)
                index_names = [idx["name"] for idx in existing_indexes]
                app_workflow_created_keys = [("app_id", 1), ("workflow_name", 1), ("created_at", -1)]
                
                # Create app/workflow/created index if not exists
                ent_wf_created_exists = any(
                    name in ["idx_ent_wf_created", "cs_ent_wf_created"] 
                    for name in index_names
                )
                if not ent_wf_created_exists:
                    await coll.create_index(app_workflow_created_keys, name="cs_ent_wf_created")
                    logger.debug("Created app/workflow/created index")

                # Canonical app/workflow/created index (new name)
                if "cs_app_wf_created" not in index_names and not _has_index_with_keys(existing_indexes, app_workflow_created_keys):
                    await coll.create_index(app_workflow_created_keys, name="cs_app_wf_created")
                    logger.debug("Created app/workflow/created index")
                
                # Create status index if not exists
                if "idx_status" not in index_names and "cs_status_created" not in index_names:
                    await coll.create_index("status", name="idx_status")
                    logger.debug("Created status index")

                # Compound index for idempotency window check in start_chat:
                # query: {app_id, user_id, workflow_name, status, created_at range}
                # ESR order: equality fields first, range field last.
                idempotency_keys = [
                    ("app_id", 1),
                    ("user_id", 1),
                    ("workflow_name", 1),
                    ("status", 1),
                    ("created_at", -1),
                ]
                if "cs_idempotency" not in index_names and not _has_index_with_keys(existing_indexes, idempotency_keys):
                    await coll.create_index(idempotency_keys, name="cs_idempotency")
                    logger.debug("Created ChatSessions idempotency compound index")

                # TTL index — auto-expire old sessions to prevent unbounded collection growth.
                # Configurable via CHAT_SESSION_TTL_DAYS; default 90 days.
                # Set to 0 to disable TTL.
                ttl_days = int(os.getenv("CHAT_SESSION_TTL_DAYS", "90"))
                if ttl_days > 0 and "cs_ttl" not in index_names:
                    await coll.create_index(
                        "created_at",
                        name="cs_ttl",
                        expireAfterSeconds=ttl_days * 86400,
                    )
                    logger.debug("Created ChatSessions TTL index (%d days)", ttl_days)

                general_coll = self.client[SYSTEM_DATABASE][_GENERAL_CHAT_COLLECTION]
                general_indexes = await general_coll.list_indexes().to_list(length=None)
                general_index_names = [idx["name"] for idx in general_indexes]
                app_user_created_keys = [("app_id", 1), ("user_id", 1), ("created_at", -1)]
                if "gc_ent_user_created" in general_index_names and "gc_app_user_created" not in general_index_names:
                    # Rename: drop the old name so the canonical name can be created.
                    await general_coll.drop_index("gc_ent_user_created")
                    await general_coll.create_index(app_user_created_keys, name="gc_app_user_created")
                    logger.debug("Migrated gc_ent_user_created → gc_app_user_created")
                elif "gc_app_user_created" not in general_index_names and "gc_ent_user_created" not in general_index_names:
                    await general_coll.create_index(app_user_created_keys, name="gc_app_user_created")
                    logger.debug("Created general chat app/user index")

                if "gc_status" not in general_index_names:
                    await general_coll.create_index("status", name="gc_status")
                    logger.debug("Created general chat status index")

                counter_coll = self.client[SYSTEM_DATABASE][_GENERAL_CHAT_COUNTER_COLLECTION]
                counter_indexes = await counter_coll.list_indexes().to_list(length=None)
                counter_names = [idx["name"] for idx in counter_indexes]
                app_user_counter_keys = [("app_id", 1), ("user_id", 1)]
                if "gc_counter_ent_user" in counter_names and "gc_counter_app_user" not in counter_names:
                    # Rename: drop the old name so the canonical name can be created.
                    await counter_coll.drop_index("gc_counter_ent_user")
                    await counter_coll.create_index(app_user_counter_keys, name="gc_counter_app_user", unique=True)
                    logger.debug("Migrated gc_counter_ent_user → gc_counter_app_user")
                elif "gc_counter_app_user" not in counter_names and "gc_counter_ent_user" not in counter_names:
                    await counter_coll.create_index(app_user_counter_keys, name="gc_counter_app_user", unique=True)
                    logger.debug("Created general chat counter unique index")

                try:
                    await self._backfill_chat_sessions_workflow_ui_state(coll)
                except Exception as migration_err:
                    logger.warning("Workflow UI-state backfill skipped: %s", migration_err)
            except Exception as e:  # pragma: no cover
                logger.warning("Index ensure issue: %s", e)

class AG2PersistenceManager:
    """Runtime persistence for ChatSessions and AG2 run stream history.

    ChatSessions: one document per workflow run with status, UI artifact,
    and session/journey correlation metadata.

    LLM token usage belongs to RuntimeUsageEvents. Agent turn, LLM-call,
    tool-call, and HITL telemetry spans belong to AG2 1.0 beta
    TelemetryMiddleware/OpenTelemetry.
    """

    def __init__(self):
        self.persistence = PersistenceManager()
        logger.info("AG2PersistenceManager (lean) ready")

    async def _coll(self):
        await self.persistence._ensure_client()
        if self.persistence.client is None:
            raise RuntimeError("Mongo client not initialized")
        return self.persistence.client[SYSTEM_DATABASE][RuntimeCollections.CHAT_SESSIONS]

    async def _general_coll(self):
        await self.persistence._ensure_client()
        if self.persistence.client is None:
            raise RuntimeError("Mongo client not initialized")
        return self.persistence.client[SYSTEM_DATABASE][_GENERAL_CHAT_COLLECTION]

    async def _general_counter_coll(self):
        await self.persistence._ensure_client()
        if self.persistence.client is None:
            raise RuntimeError("Mongo client not initialized")
        return self.persistence.client[SYSTEM_DATABASE][_GENERAL_CHAT_COUNTER_COLLECTION]

    async def get_or_assign_cache_seed(
        self,
        chat_id: str,
        app_id: str | None = None,
    ) -> int:
        """Return a stable per-chat cache seed, assigning one if missing.

        Seed is deterministic by default (derived from chat_id and app_id if provided),
        and persisted to the ChatSessions document under "cache_seed" for visibility and reuse.
        """
        resolved_app_id = coalesce_app_id(app_id=app_id)
        coll = await self._coll()
        # Include app_id in the filter for defense-in-depth tenant isolation.
        _seed_filter: dict = {"_id": chat_id}
        if resolved_app_id:
            _seed_filter.update(build_app_scope_filter(resolved_app_id))
        doc = await coll.find_one(_seed_filter, {"cache_seed": 1})
        if doc and isinstance(doc.get("cache_seed"), (int, float)):
            try:
                reused_seed = int(doc["cache_seed"])
                logger.debug(
                    "[CACHE_SEED] Reusing existing per-chat seed",
                    extra={
                        "chat_id": chat_id,
                        "app_id": resolved_app_id,
                        "seed": reused_seed,
                        "source": "persisted",
                    },
                )
                return reused_seed  # normalize to int
            except Exception:
                logger.debug(
                    "[CACHE_SEED] Persisted seed could not be coerced to int (value=%s); will recompute", doc.get('cache_seed'),
                    extra={"chat_id": chat_id, "app_id": resolved_app_id},
                )
        # Derive a deterministic 32-bit seed from chat_id (+ app_id if provided)
        basis = chat_id if not resolved_app_id else f"{resolved_app_id}:{chat_id}"
        seed_bytes = hashlib.sha256(basis.encode("utf-8")).digest()[:4]
        seed = int.from_bytes(seed_bytes, "big", signed=False)
        try:
            await coll.update_one(_seed_filter, {"$set": {"cache_seed": seed}})
            logger.debug(
                "[CACHE_SEED] Assigned new deterministic per-chat seed",
                extra={
                    "chat_id": chat_id,
                    "app_id": resolved_app_id,
                    "seed": seed,
                    "basis": basis,
                    "basis_hash_prefix": hashlib.sha256(basis.encode('utf-8')).hexdigest()[:10],
                },
            )
        except Exception as e:
            logger.debug("Failed to persist cache_seed for chat %s: %s", chat_id, e)
            logger.debug(
                "[CACHE_SEED] Proceeding with in-memory seed only (persistence failure)",
                extra={"chat_id": chat_id, "app_id": resolved_app_id, "seed": seed},
            )
        return seed

    # Chat sessions -----------------------------------------------------
    async def create_chat_session(
        self,
        chat_id: str,
        app_id: str | None = None,
        workflow_name: str = "",
        user_id: str = "",
        *,
        extra_fields: dict[str, Any] | None = None,
    ) -> None:
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        try:
            coll = await self._coll()
            # Scope the duplicate check to this app to maintain tenant isolation.
            if await coll.find_one({"_id": chat_id, **build_app_scope_filter(resolved_app_id)}):
                return
            now = datetime.now(UTC)
            session_doc: dict[str, Any] = {
                "_id": chat_id,
                "chat_id": chat_id,
                "app_id": resolved_app_id,
                "ag2_stream_id": str(self._run_stream_id(chat_id=chat_id, app_id=resolved_app_id)),
                "workflow_name": workflow_name,
                "user_id": user_id,
                "status": int(WorkflowStatus.IN_PROGRESS),
                "created_at": now,
                "last_updated_at": now,
                "session_version": 0,
                "workflow_ui_state": build_default_workflow_ui_state(),
                "messages": [],
            }

            if isinstance(extra_fields, dict) and extra_fields:
                # Prevent callers from overwriting canonical identifiers/state.
                protected = {
                    "_id",
                    "chat_id",
                    "app_id",
                    "workflow_name",
                    "user_id",
                    "status",
                    "created_at",
                    "last_updated_at",
                    "last_sequence",
                    "messages",
                    "workflow_ui_state",
                    "last_artifact",
                    "pending_input_request",
                }
                for k, v in list(extra_fields.items()):
                    if not isinstance(k, str) or not k.strip():
                        continue
                    if k in protected:
                        continue
                    session_doc[k] = v

            session_doc = dual_write_app_scope(session_doc, resolved_app_id)
            await coll.insert_one(session_doc)
        except Exception as e:  # pragma: no cover
            logger.error("Failed to create chat session %s: %s", chat_id, e, exc_info=True)

    async def fetch_chat_session_extra_context(
        self,
        *,
        chat_id: str,
        app_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch non-canonical, non-message fields for a chat session.

        Purpose:
        - Allows runtime to seed AG2 ContextVariables from persisted session metadata
          (e.g., parent_chat_id, seeds for generator subruns).

        Notes:
        - Excludes messages for performance.
        - Strips canonical identifiers/state to prevent accidental overwrites.
        """
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")

        try:
            coll = await self._coll()
            doc = await coll.find_one(
                {"_id": chat_id, **build_app_scope_filter(str(resolved_app_id))},
                {"messages": 0},
            )
            if not isinstance(doc, dict):
                return {}

            protected = {
                "_id",
                "chat_id",
                "app_id",
                "workflow_name",
                "user_id",
                "status",
                "created_at",
                "last_updated_at",
                "last_sequence",
                "messages",
                "workflow_ui_state",
                "last_artifact",
                "pending_input_request",
            }
            extra: dict[str, Any] = {}
            for k, v in doc.items():
                if not isinstance(k, str) or not k.strip():
                    continue
                if k in protected:
                    continue
                extra[k] = v
            return extra
        except Exception as e:  # pragma: no cover
            logger.debug("[FETCH_EXTRA_CONTEXT] Failed chat_id=%s: %s", chat_id, e)
            return {}

    async def persist_context_variables(
        self,
        *,
        chat_id: str,
        app_id: str | None = None,
        variables: dict[str, Any] | None = None,
    ) -> None:
        """Persist runtime context variables as chat-session extra fields.

        Only non-canonical session fields are written so workflow-specific
        runtime metadata and summaries survive across turns.
        """
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")

        if not isinstance(variables, dict) or not variables:
            return

        protected = {
            "_id",
            "chat_id",
            "app_id",
            "workflow_name",
            "user_id",
            "status",
            "created_at",
            "last_updated_at",
            "last_sequence",
            "messages",
            "workflow_ui_state",
            "last_artifact",
            "pending_input_request",
        }
        safe_updates: dict[str, Any] = {}
        for key, value in variables.items():
            if not isinstance(key, str) or not key.strip() or key in protected:
                continue
            safe_updates[key] = deepcopy(value)

        if not safe_updates:
            return

        safe_updates["last_updated_at"] = datetime.now(UTC)

        try:
            coll = await self._coll()
            await coll.update_one(
                {"_id": chat_id, **build_app_scope_filter(str(resolved_app_id))},
                {"$set": safe_updates, "$inc": {"session_version": 1}},
            )
        except Exception as e:  # pragma: no cover
            logger.debug("[PERSIST_CONTEXT_VARIABLES] Failed chat_id=%s: %s", chat_id, e)

    async def create_general_chat_session(
        self,
        *,
        app_id: str | None = None,
        user_id: str,
    ) -> dict[str, Any]:
        """Allocate and persist a brand-new general (non-AG2) chat session."""

        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")

        try:
            ent_id = str(resolved_app_id)
            counters = await self._general_counter_coll()
            now = datetime.now(UTC)
            counter_doc = await counters.find_one_and_update(
                {"user_id": user_id, **build_app_scope_filter(ent_id)},
                {"$inc": {"sequence": 1}, "$setOnInsert": {"created_at": now}, "$set": {"app_id": ent_id}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
            seq = int(counter_doc.get("sequence", 1)) if counter_doc else 1
            general_chat_id = f"generalchat-{ent_id}-{user_id}-{seq:04d}"
            label = f"General Chat #{seq}"

            general_doc = dual_write_app_scope(
                {
                "_id": general_chat_id,
                "chat_id": general_chat_id,
                "app_id": ent_id,
                "user_id": user_id,
                "session_type": "general",
                "general_label": label,
                "general_sequence": seq,
                "status": int(WorkflowStatus.IN_PROGRESS),
                "created_at": now,
                "last_updated_at": now,
                "last_sequence": 0,
                "last_artifact": None,
                "messages": [],
            },
                ent_id,
            )

            general_coll = await self._general_coll()
            set_on_insert = dict(general_doc)
            set_on_insert.pop("last_updated_at", None)
            await general_coll.update_one(
                {"_id": general_chat_id},
                {"$setOnInsert": set_on_insert, "$set": {"last_updated_at": now}},
                upsert=True,
            )

            logger.debug(
                "GENERAL_CHAT_SESSION_CREATED general_chat_id=%s app_id=%s user_id=%s sequence=%s",
                general_chat_id, ent_id, user_id, seq,
            )

            return {"chat_id": general_chat_id, "label": label, "sequence": seq}
        except Exception as e:  # pragma: no cover
            logger.error("Failed to create general chat session app_id=%s user=%s: %s", resolved_app_id, user_id, e, exc_info=True)
            raise

    async def mark_chat_completed(self, chat_id: str, app_id: str | None = None) -> bool:
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        try:
            coll = await self._coll()
            now = datetime.now(UTC)
            # Fetch created_at to compute run duration.
            base_doc = await coll.find_one(
                {"_id": chat_id, **build_app_scope_filter(resolved_app_id)},
                {"created_at": 1},
            )
            created_at = base_doc.get("created_at") if base_doc else None
            if isinstance(created_at, datetime) and created_at.tzinfo is None:
                # Mongo can return naive datetimes when tz_aware=False; treat them as UTC.
                created_at = created_at.replace(tzinfo=UTC)
            dur = float((now - created_at).total_seconds()) if isinstance(created_at, datetime) else 0.0
            # Use dot-path write so unrecognized workflow_ui_state fields are preserved.
            res = await coll.update_one({"_id": chat_id, **build_app_scope_filter(resolved_app_id)}, {
                "$set": {
                    "status": int(WorkflowStatus.COMPLETED),
                    "completed_at": now,
                    "last_updated_at": now,
                    "duration_sec": dur,
                    "workflow_ui_state.pending_input_request": None,
                },
                "$inc": {"session_version": 1},
            })
            return res.modified_count > 0
        except Exception as e:  # pragma: no cover
            logger.error("Failed to mark chat %s as completed: %s", chat_id, e, exc_info=True)
            return False

    async def update_last_artifact(
        self,
        *,
        chat_id: str,
        app_id: str,
        artifact: dict[str, Any],
    ) -> None:
        """Persist latest artifact/tool panel context for multi-user resume.

        Expected artifact dict keys (best-effort, flexible):
            tool_name: str
            tool_call_id: str | None
            display: str (e.g., 'artifact')
            workflow_name: str
            payload: arbitrary JSON-safe structure
        
            Semantics / lifecycle:
                - Only the most recent artifact-mode UI tool event is stored (overwrite strategy).
                - Cleared implicitly when a new chat session is created; we do NOT keep historical artifacts here.
                - Frontend uses /api/chats/meta and websocket chat_meta (last_artifact field) to restore the panel
                  when a second user joins or a browser refresh occurs without local cache.
                - Large payloads: currently stored verbatim. If future payloads exceed practical limits, introduce
                  truncation or a separate GridFS storage; shape kept minimal to ease migration.
        """
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        try:
            coll = await self._coll()
            now = datetime.now(UTC)
            doc = {
                "tool_name": artifact.get("tool_name"),
                "tool_call_id": artifact.get("tool_call_id"),
                "component_type": artifact.get("component_type"),
                "display": artifact.get("display"),
                "workflow_name": artifact.get("workflow_name"),
                # Keep payload shallow (avoid huge memory copies); truncate strings if massive in future enhancement
                "payload": artifact.get("payload"),
                "updated_at": now.isoformat(),
            }
            await coll.update_one(
                {"_id": chat_id, **build_app_scope_filter(resolved_app_id)},
                {"$set": {"workflow_ui_state.last_artifact": doc, "last_updated_at": now}, "$inc": {"session_version": 1}},
            )
            logger.debug(
                "[LAST_ARTIFACT] Updated",
                extra={"chat_id": chat_id, "app_id": resolved_app_id, "tool_name": doc.get("tool_name")},
            )
        except Exception as e:  # pragma: no cover
            logger.debug("[LAST_ARTIFACT] Update failed chat_id=%s: %s", chat_id, e)

    async def get_session_version(self, chat_id: str, app_id: str | None = None) -> int | None:
        """Return the current session_version for a chat session, or None if not found.

        Used by callers that need optimistic concurrency control — read the version
        before starting a workflow, then call ``check_session_not_stale`` at a
        checkpoint to verify no concurrent write has advanced the version.
        """
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            return None
        try:
            coll = await self._coll()
            doc = await coll.find_one(
                {"_id": chat_id, **build_app_scope_filter(str(resolved_app_id))},
                {"session_version": 1},
            )
            if not isinstance(doc, dict):
                return None
            raw = doc.get("session_version")
            return int(raw) if raw is not None else 0
        except Exception as e:  # pragma: no cover
            logger.debug("[SESSION_VERSION] Failed to read version chat_id=%s: %s", chat_id, e)
            return None

    async def check_session_not_stale(
        self,
        chat_id: str,
        app_id: str | None = None,
        *,
        known_version: int,
    ) -> bool:
        """Return True if the session version still matches ``known_version``.

        Returns False (stale) when another instance has written to the session since
        the caller read it.  A result of None from ``get_session_version`` (session
        not found) is treated as stale.

        This is a soft guard — the distributed lock in
        ``core/runtime/persistence/distributed_lock.py`` provides the hard
        mutual-exclusion boundary for concurrent execution.  Use this method to
        detect silent concurrent writes that the lock does not cover (e.g. metadata
        updates from a different code path while the lock-holder is running).
        """
        current = await self.get_session_version(chat_id, app_id)
        if current is None:
            return False
        is_fresh = current == known_version
        if not is_fresh:
            logger.warning(
                "[SESSION_VERSION] Stale session detected — chat_id=%s known=%s current=%s",
                chat_id,
                known_version,
                current,
            )
        return is_fresh

    def _run_stream_id(self, *, chat_id: str, app_id: str):
        from mozaiksai.core.adapters.ag2_stream_storage import stream_id_for_run

        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        return stream_id_for_run(resolved_app_id, chat_id)

    def build_run_stream(self, *, chat_id: str, app_id: str):
        from ag2 import MemoryStream

        from mozaiksai.core.adapters.ag2_stream_storage import MongoAG2StreamStorage

        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        return MemoryStream(
            storage=MongoAG2StreamStorage(app_id=resolved_app_id),
            id=self._run_stream_id(chat_id=chat_id, app_id=resolved_app_id),
        )

    async def load_run_events(self, *, chat_id: str, app_id: str) -> list[Any]:
        stream = self.build_run_stream(chat_id=chat_id, app_id=app_id)
        return list(await stream.history.get_events())

    @staticmethod
    def _is_json_container_text(content: str) -> bool:
        candidate = str(content or "").strip()
        if not candidate or candidate[0] not in "{[":
            return False
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            return False
        return isinstance(parsed, (dict, list))

    @staticmethod
    def _is_ui_hidden_run_message(content: str, metadata: dict[str, Any]) -> bool:
        if str(metadata.get("ui_visibility") or "").strip().lower() == "hidden":
            return True
        if metadata.get("trace_hidden") is True:
            return True

        source = str(metadata.get("source") or "").strip()
        if source != "ag2_network_wal":
            return False

        candidate = str(content or "").strip()
        if candidate == "NEXT":
            return True
        return AG2PersistenceManager._is_json_container_text(candidate)

    @staticmethod
    def _run_event_to_message(event: Any) -> dict[str, Any] | None:
        from ag2.events import ModelResponse
        from ag2.events.input_events import TextInput

        if isinstance(event, TextInput) and getattr(event, "content", None):
            return {
                "role": "user",
                "name": "user",
                "content": str(event.content),
            }

        if isinstance(event, ModelResponse) and event.content:
            metadata = event.metadata if isinstance(event.metadata, dict) else {}
            content = str(event.content)
            if AG2PersistenceManager._is_ui_hidden_run_message(content, metadata):
                return None
            message: dict[str, Any] = {
                "role": "assistant",
                "name": str(metadata.get("agent_name") or metadata.get("agent") or "assistant"),
                "content": content,
            }
            if event.model or metadata:
                message["metadata"] = {
                    "source": "agent",
                    **({"model": event.model} if event.model else {}),
                    **metadata,
                }
            return message

        return None

    def project_run_events_to_messages(self, events: list[Any]) -> list[dict[str, Any]]:
        history: list[dict[str, Any]] = []
        for event in events:
            message = self._run_event_to_message(event)
            if isinstance(message, dict):
                history.append(message)
        return history

    async def load_run_history(self, *, chat_id: str, app_id: str) -> list[dict[str, Any]]:
        """Return UI replay messages projected from canonical AG2 run events."""
        events = await self.load_run_events(chat_id=chat_id, app_id=app_id)
        return self.project_run_events_to_messages(events)

    async def append_run_assistant_message(
        self,
        *,
        chat_id: str,
        app_id: str,
        content: str,
        agent_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        from types import SimpleNamespace

        from ag2.events import ModelResponse
        from ag2.events.types import ModelMessage

        from mozaiksai.core.adapters.ag2_stream_storage import MongoAG2StreamStorage

        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        text = str(content or "").strip()
        if not text:
            return

        merged_metadata = dict(metadata or {})
        if agent_name:
            merged_metadata.setdefault("agent_name", agent_name)

        event = ModelResponse(
            ModelMessage(text, metadata=merged_metadata),
            model="mozaiks.runtime",
            provider="mozaiks.runtime",
            finish_reason="stop",
        )
        storage = MongoAG2StreamStorage(app_id=resolved_app_id)
        stream_context = SimpleNamespace(
            stream=SimpleNamespace(id=self._run_stream_id(chat_id=chat_id, app_id=resolved_app_id))
        )
        await storage.save_event(event, stream_context)

    async def append_run_user_message(
        self,
        *,
        chat_id: str,
        app_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        from types import SimpleNamespace

        from ag2.events.input_events import TextInput

        from mozaiksai.core.adapters.ag2_stream_storage import MongoAG2StreamStorage

        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        text = str(content or "").strip()
        if not text:
            return

        event = TextInput(text)
        if metadata:
            try:
                event.metadata = dict(metadata)
            except Exception:
                pass
        storage = MongoAG2StreamStorage(app_id=resolved_app_id)
        stream_context = SimpleNamespace(
            stream=SimpleNamespace(id=self._run_stream_id(chat_id=chat_id, app_id=resolved_app_id))
        )
        await storage.save_event(event, stream_context)

    async def append_general_message(
        self,
        *,
        general_chat_id: str,
        app_id: str,
        role: str,
        content: str,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist general (non-AG2) capability exchanges inside the general chat collection."""

        normalized_role = role if role in {"user", "assistant"} else "assistant"
        metadata = metadata or {}
        metadata.setdefault("source", "general_agent")
        if user_id and "user_id" not in metadata:
            metadata["user_id"] = user_id

        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        ent_id = str(resolved_app_id)
        coll = await self._general_coll()
        now = datetime.now(UTC)
        bump = await coll.find_one_and_update(
            {"_id": general_chat_id, **build_app_scope_filter(ent_id)},
            {"$inc": {"last_sequence": 1}, "$set": {"last_updated_at": now, "app_id": ent_id}},
            return_document=ReturnDocument.AFTER,
        )
        seq = int(bump.get("last_sequence", 1)) if bump else 1

        message_doc = {
            "role": normalized_role,
            "content": str(content),
            "timestamp": now,
            "event_type": "general_agent.message",
            "event_id": f"general_{uuid4()}",
            "sequence": seq,
            "agent_name": (
                "user"
                if normalized_role == "user"
                else (metadata.get("agent_name") or metadata.get("agent") or "assistant")
            ),
            "metadata": metadata,
        }

        await coll.update_one(
            {"_id": general_chat_id, **build_app_scope_filter(ent_id)},
            {"$push": {"messages": message_doc}, "$set": {"last_updated_at": now}},
        )

        logger.debug(
            "[GENERAL_MSG] Persisted general agent message",
            extra={
                "general_chat_id": general_chat_id,
                "app_id": ent_id,
                "sequence": seq,
                "role": normalized_role,
            },
        )

        return message_doc

    async def list_general_chats(
        self,
        *,
        app_id: str | None = None,
        user_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        coll = await self._general_coll()
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        ent_id = str(resolved_app_id)
        limit = max(1, min(int(limit or 1), 200))
        docs = (
            await coll.find({"user_id": user_id, **build_app_scope_filter(ent_id)})
            .sort("created_at", -1)
            .limit(limit)
            .to_list(length=limit)
        )
        sessions: list[dict[str, Any]] = []
        for doc in docs:
            sessions.append(
                {
                    "chat_id": doc.get("_id"),
                    "label": doc.get("general_label") or doc.get("_id"),
                    "sequence": int(doc.get("general_sequence", 0) or 0),
                    "status": int(doc.get("status", -1)),
                    "created_at": doc.get("created_at"),
                    "last_updated_at": doc.get("last_updated_at"),
                    "last_sequence": int(doc.get("last_sequence", 0) or 0),
                }
            )
        return sessions

    async def delete_general_chat(
        self,
        *,
        general_chat_id: str,
        app_id: str,
        user_id: str,
    ) -> bool:
        """Delete a single general chat session owned by the given user."""
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        ent_id = str(resolved_app_id)
        coll = await self._general_coll()
        result = await coll.delete_one(
            {"_id": general_chat_id, "user_id": user_id, **build_app_scope_filter(ent_id)}
        )
        return result.deleted_count > 0

    async def fetch_general_chat_transcript(
        self,
        *,
        general_chat_id: str,
        app_id: str,
        after_sequence: int = -1,
        limit: int = 200,
    ) -> dict[str, Any] | None:
        """Return the general chat session document with messages filtered by sequence."""
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        ent_id = str(resolved_app_id)
        coll = await self._general_coll()
        doc = await coll.find_one(
            {"_id": general_chat_id, **build_app_scope_filter(ent_id)}
        )
        if doc is None:
            return None
        result = dict(doc)
        messages = [m for m in (result.get("messages") or []) if int(m.get("sequence", 0) or 0) > after_sequence]
        result["messages"] = messages[:limit]
        return result

    async def get_user_workflow_statuses(self, *, app_id: str | None = None, user_id: str) -> dict[str, dict[str, Any]]:
        """Return a mapping of workflow_name -> { chat_id, status } for a given user.

        - Queries `ChatSessions` for the app/user and returns a simple dict
          suitable for seeding the `workflows` field in the pattern context contract.
        - `status` is normalized to the canonical strings: `not_started`,
          `in_progress`, `completed`, or `unknown`.
        """
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        try:
            coll = await self._coll()
            # Deterministic selection: when multiple sessions exist for the same workflow,
            # we treat the *most recently created* session as canonical (prevents cross-run bleed).
            cursor = (
                coll.find(
                    {"user_id": user_id, **build_app_scope_filter(str(resolved_app_id))},
                    {"_id": 1, "workflow_name": 1, "status": 1, "created_at": 1},
                )
                .sort("created_at", -1)
                .limit(500)
            )
            docs = await cursor.to_list(length=500)
            result: dict[str, dict[str, Any]] = {}
            for d in docs:
                wf = d.get("workflow_name") or d.get("workflow") or "unnamed_workflow"
                if wf in result:
                    continue
                chat_id = d.get("_id")
                status_int = int(d.get("status", -1) or -1)
                try:
                    status_name = WorkflowStatus(status_int).name.lower()
                except Exception:
                    status_name = "unknown"
                # normalize to expected minimal set
                if status_name not in ("not_started", "in_progress", "completed"):
                    if status_name == "unknown":
                        normalized = "unknown"
                    else:
                        normalized = "in_progress"
                else:
                    normalized = status_name

                result[wf] = {"chat_id": chat_id, "status": normalized}
            return result
        except Exception as e:
            logger.warning("[GET_WORKFLOW_STATUSES] Failed to fetch workflows for app_id=%s user=%s: %s", resolved_app_id, user_id, e)
            return {}

    async def build_pattern_context_from_user(self, *, app_id: str | None = None, user_id: str) -> dict[str, Any]:
        """Build the minimal pattern-context payload (app_id, user_id, workflows)

        This helper wraps `get_user_workflow_statuses` and returns the small
        shape expected by generators per the `PATTERN_CONTEXT_CONTRACT`.
        """
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")

#############################################
    # used for generate_and_download
#############################################
    @staticmethod
    def _normalize_wrapped_text_content(text: Any) -> str:
        """Unwrap AG2 repr strings like ``content='...' sender='Agent'``.

        When AG2 serializes TextEvent content through object repr strings, the inner
        JSON body is Python-escaped. That leaves structured-output payloads looking
        like ``{\\n  \\\"Field\\\": ...}`` in persistence, which later parse attempts
        reject. This helper decodes that one wrapper layer and returns canonical JSON
        text when the payload is JSON-shaped.
        """
        if text is None:
            return ""
        if not isinstance(text, str):
            return str(text)
        if "content=" not in text or " sender=" not in text:
            return text

        try:
            import re

            match = re.search(
                r"content=(?P<quote>'|\")(?P<inner>.*?)(?P=quote)\s+sender=",
                text,
                re.DOTALL,
            )
            if not match:
                return text

            quote = match.group("quote")
            inner = match.group("inner")
            try:
                inner = ast.literal_eval(f"{quote}{inner}{quote}")
            except Exception:
                pass

            normalized = inner.strip() if isinstance(inner, str) else str(inner)
            if normalized.startswith("{") or normalized.startswith("["):
                parsed = AG2PersistenceManager._extract_json_from_text(normalized)
                if parsed is not None:
                    return json.dumps(parsed, ensure_ascii=False)
            return normalized
        except Exception as exc:  # pragma: no cover
            logger.debug("Wrapped content normalization failed: %s", exc)
            return text

    @staticmethod
    def _extract_json_from_text(text: Any, agent_name: str | None = None) -> dict[str, Any] | None:
        """
        Extract JSON from text, with cleaning to handle common agent output issues.
        
        Handles:
        - Markdown code fences (```json ... ```)
        - Language identifiers (json, JSON)
        - Trailing garbage after JSON
        - Trailing commas before closing brackets
        """
        try:
            if text is None:
                if agent_name:
                    logger.debug("[JSON_PARSE] %s: text is None", agent_name)
                return None
            if isinstance(text, dict):
                if agent_name:
                    logger.debug("[JSON_PARSE] %s: already a dict", agent_name)
                return text
            if isinstance(text, list):
                if agent_name:
                    logger.debug("[JSON_PARSE] %s: text is a list, returning None", agent_name)
                return None
            
            s = AG2PersistenceManager._normalize_wrapped_text_content(text)
            s_strip = s.strip()
            
            if agent_name:
                logger.debug("[JSON_PARSE] %s: original length=%s, stripped length=%s", agent_name, len(s), len(s_strip))
            
            # CLEANING STEP 1: Remove Markdown code fences
            if s_strip.startswith("```") and "```" in s_strip[3:]:
                # Find the closing ```
                end_fence = s_strip.find("```", 3)
                s_strip = s_strip[3:end_fence].strip()
                if agent_name:
                    logger.debug("[JSON_PARSE] %s: removed markdown fences, new length=%s", agent_name, len(s_strip))
            
            # CLEANING STEP 2: Remove "json" or "JSON" prefix if present
            if s_strip.lower().startswith("json"):
                s_strip = s_strip[4:].strip()
                if agent_name:
                    logger.debug("[JSON_PARSE] %s: removed json prefix", agent_name)
            
            # CLEANING STEP 3: Find JSON boundaries (first { or [ to last } or ])
            json_start = s_strip.find("{") if "{" in s_strip else s_strip.find("[")
            if json_start != -1:
                # Find last closing bracket
                json_end = s_strip.rfind("}") if "}" in s_strip else s_strip.rfind("]")
                if json_end != -1:
                    s_strip = s_strip[json_start:json_end + 1]
                    if agent_name:
                        logger.debug("[JSON_PARSE] %s: extracted JSON boundaries, length=%s", agent_name, len(s_strip))
            
            # CLEANING STEP 4: Remove trailing commas before closing brackets (invalid JSON)
            import re
            s_strip = re.sub(r',\s*([\]}])', r'\1', s_strip)

            # CLEANING STEP 5: AG2 sometimes escapes apostrophes inside JSON
            # string values (e.g. Here\'s), which is invalid JSON.
            s_strip = s_strip.replace("\\'", "'")
            
            # Now try to parse the cleaned JSON
            decoder = json.JSONDecoder()
            idx = 0
            length = len(s_strip)
            while idx < length:
                brace_idx = s_strip.find("{", idx)
                if brace_idx == -1:
                    if agent_name:
                        logger.debug("[JSON_PARSE] %s: no opening brace found", agent_name)
                    return None
                try:
                    obj, end_idx = decoder.raw_decode(s_strip, brace_idx)
                    if isinstance(obj, dict):
                        if agent_name:
                            logger.debug("[JSON_PARSE] %s: JSON parsed ok", agent_name)
                        return obj
                    idx = end_idx
                    continue
                except json.JSONDecodeError as e:
                    if agent_name and idx == 0:  # Only log first attempt
                        logger.debug("[JSON_PARSE] %s: JSONDecodeError at pos %s: %s, content preview: %s", agent_name, e.pos, e.msg, s_strip[max(0, e.pos-50):e.pos+50])
                    idx = brace_idx + 1
                    continue
            if agent_name:
                logger.debug("[JSON_PARSE] %s: exhausted all parse attempts", agent_name)
            return None
        except Exception as ex:
            if agent_name:
                logger.debug("[JSON_PARSE] %s: exception during parse: %s", agent_name, ex)
            return None

    @staticmethod
    def _normalize_structured_output(agent_name: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict) or not agent_name:
            return payload

        adjusted = deepcopy(payload)

        try:
            if agent_name == "ToolsManagerAgent":
                return adjusted
        except Exception as normalize_err:
            logger.debug("[SAVE_EVENT] Structured output normalization skipped agent=%s: %s", agent_name, normalize_err)

        return adjusted

    async def gather_latest_agent_jsons(
        self,
        *,
        chat_id: str,
        app_id: str | None = None,
        agent_names: list[str] | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        try:
            msgs = await self.load_run_history(chat_id=chat_id, app_id=resolved_app_id)
            logger.debug("[GATHER_AGENT_JSONS] chat_id=%s app_id=%s msgs_count=%s", chat_id, resolved_app_id, len(msgs))

            if not msgs:
                logger.debug("[GATHER_AGENT_JSONS] No AG2 run history for chat_id=%s", chat_id)
                return result
            
            def agent_name_from(m: dict[str, Any]) -> str:
                if m.get("role") == "assistant":
                    return str(m.get("agent_name") or "").strip()
                return "user"
            
            if agent_names:
                wanted = {n.strip() for n in agent_names}
                logger.debug("[GATHER_AGENT_JSONS] Filtering for specific agents: %s", wanted)
                for m in reversed(msgs):
                    if not isinstance(m, dict):
                        continue
                    nm = agent_name_from(m)
                    if not nm or nm not in wanted or nm in result:
                        continue
                    
                    # PRIORITY 1: Check structured_output field first
                    structured_output = m.get("structured_output")
                    if isinstance(structured_output, dict):
                        result[nm] = structured_output
                        logger.debug("[GATHER_AGENT_JSONS] Extracted JSON from %s (via structured_output field)", nm)
                        continue
                    
                    # PRIORITY 2: Try content field
                    js = self._extract_json_from_text(m.get("content"))
                    if js is not None:
                        result[nm] = js
                        logger.debug("[GATHER_AGENT_JSONS] Extracted JSON from %s (via content field)", nm)
                    else:
                        logger.warning("[GATHER_AGENT_JSONS] No JSON found in %s message", nm)
                return result
            
            seen: set[str] = set()
            agents_found = []
            for m in reversed(msgs):
                if not isinstance(m, dict):
                    continue
                role = m.get("role")
                if role != "assistant":
                    continue
                nm = agent_name_from(m)
                if not nm or nm in seen:
                    continue
                
                # Log each agent message we encounter
                content_preview = str(m.get("content", ""))[:100] if m.get("content") else "(empty)"
                logger.debug("[GATHER_AGENT_JSONS] Processing message: role=%s agent=%s content_preview=%s", role, nm, content_preview)
                
                # PRIORITY 1: Check structured_output field first (for agents with structured_outputs_required: true)
                structured_output = m.get("structured_output")
                if isinstance(structured_output, dict):
                    result[nm] = structured_output
                    seen.add(nm)
                    agents_found.append(nm)
                    logger.debug("[GATHER_AGENT_JSONS] Extracted JSON from %s (via structured_output field)", nm)
                    continue
                
                # PRIORITY 2: Try to extract JSON from content field (fallback)
                js = self._extract_json_from_text(m.get("content"))
                if js is not None:
                    result[nm] = js
                    seen.add(nm)
                    agents_found.append(nm)
                    logger.debug("[GATHER_AGENT_JSONS] Extracted JSON from %s (via content field)", nm)
                else:
                    # Log first 500 chars of content for failed extractions
                    content = m.get("content", "")
                    content_sample = str(content)[:500] if content else "(empty)"
                    logger.debug("[GATHER_AGENT_JSONS] No JSON found in %s message (role=%s)", nm, role)
                    logger.debug("[GATHER_AGENT_JSONS]    Content sample: %s", content_sample)
            
            logger.debug("[GATHER_AGENT_JSONS] Completed: found %s agents with valid JSON: %s", len(result), agents_found)
            return result
        except Exception as e:  # pragma: no cover
            logger.error("[GATHER_AGENT_JSONS] Failed for chat_id=%s: %s", chat_id, e, exc_info=True)
            return result

    # UI Tool Persistence -----------------------------------------------
    def _workflow_tool_call_storage_key(self, event_id: str) -> str:
        return hashlib.sha1(str(event_id or "").encode("utf-8")).hexdigest()

    async def get_workflow_tool_call_states(
        self,
        *,
        chat_id: str,
        app_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return persisted workflow UI tool state for resume/reconnect."""
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            return []
        try:
            coll = await self._coll()
            doc = await coll.find_one(
                {"_id": chat_id, **build_app_scope_filter(str(resolved_app_id))},
                {"workflow_ui_state.tool_calls": 1},
            )
            workflow_ui_state = (doc or {}).get("workflow_ui_state") or {}
            tool_calls = workflow_ui_state.get("tool_calls")
            if not isinstance(tool_calls, dict):
                return []

            states: list[dict[str, Any]] = []
            for raw_state in tool_calls.values():
                if isinstance(raw_state, dict) and raw_state.get("tool_call_id"):
                    states.append(deepcopy(raw_state))

            states.sort(
                key=lambda state: (
                    int(state.get("message_index", -1)) if isinstance(state.get("message_index"), int) else -1,
                    str(state.get("updated_at") or state.get("timestamp") or ""),
                )
            )
            return states
        except Exception as e:
            logger.error("[TOOL_CALL_STATE] Failed to load workflow UI state for %s: %s", chat_id, e, exc_info=True)
            return []

    async def attach_tool_call_metadata(
        self,
        *,
        chat_id: str,
        app_id: str | None = None,
        event_id: str,
        metadata: dict[str, Any]
    ) -> None:
        """Persist workflow UI tool state for resume without mutating chat messages."""
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        try:
            history = await self.load_run_history(chat_id=chat_id, app_id=str(resolved_app_id))
            last_assistant_idx = None
            for idx in range(len(history) - 1, -1, -1):
                msg = history[idx]
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    last_assistant_idx = idx
                    break

            if last_assistant_idx is None:
                logger.debug("[TOOL_CALL_METADATA] No assistant message found in run history for %s", chat_id)
                return

            coll = await self._coll()
            now = datetime.now(UTC)
            tool_call_id = str((metadata or {}).get("tool_call_id") or event_id)
            storage_key = self._workflow_tool_call_storage_key(tool_call_id)
            state_doc = deepcopy(metadata if isinstance(metadata, dict) else {})
            state_doc["tool_call_id"] = tool_call_id
            state_doc["message_index"] = last_assistant_idx
            state_doc["updated_at"] = now.isoformat()
            state_doc.setdefault("timestamp", now.isoformat())

            result = await coll.update_one(
                {
                    "_id": chat_id,
                    **build_app_scope_filter(str(resolved_app_id)),
                },
                {
                    "$set": {
                        f"workflow_ui_state.tool_calls.{storage_key}": state_doc,
                        "last_updated_at": now,
                    }
                }
            )

            if result.modified_count > 0:
                logger.debug(
                    "[TOOL_CALL_METADATA] Persisted workflow UI state for message[%s] ", last_assistant_idx)
            else:
                logger.debug("[TOOL_CALL_METADATA] Failed to persist tool-call state in %s", chat_id)
        except Exception as e:
            logger.error("[TOOL_CALL_METADATA] Failed to attach metadata for %s: %s", chat_id, e, exc_info=True)

    async def update_tool_call_state(
        self,
        *,
        chat_id: str,
        app_id: str | None = None,
        event_id: str,
        status: str,
        completed: bool | None = None,
    ) -> None:
        """Update persisted tool-call lifecycle state for reconnect/resume."""
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        try:
            coll = await self._coll()
            now = datetime.now(UTC).isoformat()
            storage_key = self._workflow_tool_call_storage_key(event_id)
            updates: dict[str, Any] = {
                f"workflow_ui_state.tool_calls.{storage_key}.tool_call_status": status,
                f"workflow_ui_state.tool_calls.{storage_key}.updated_at": now,
                "last_updated_at": datetime.now(UTC),
            }
            if completed is not None:
                updates[f"workflow_ui_state.tool_calls.{storage_key}.tool_call_completed"] = completed
            if status == "responded":
                updates[f"workflow_ui_state.tool_calls.{storage_key}.responded_at"] = now
            if completed or status in {"completed", "dismissed", "cancelled", "skipped"}:
                updates[f"workflow_ui_state.tool_calls.{storage_key}.completed_at"] = now

            result = await coll.update_one(
                {
                    "_id": chat_id,
                    **build_app_scope_filter(str(resolved_app_id)),
                    f"workflow_ui_state.tool_calls.{storage_key}.tool_call_id": event_id,
                },
                {
                    "$set": updates
                },
            )

            if result.modified_count > 0:
                logger.debug(
                    "[TOOL_CALL_STATE] Updated tool_call state for event=%s in %s (completed=%s, status=%s)",
                    event_id, chat_id, completed, status)
            else:
                logger.debug(
                    "[TOOL_CALL_STATE] No persisted tool-call state found for event=%s in %s",
                    event_id, chat_id)
        except Exception as e:
            logger.error("[TOOL_CALL_STATE] Failed to update tool_call state for %s: %s", chat_id, e, exc_info=True)

    # Pending Input Request Persistence ------------------------------------
    async def save_pending_input_request(
        self,
        *,
        chat_id: str,
        app_id: str | None = None,
        request_id: str,
        agent: str,
        prompt: str,
        component_type: str | None = None,
        workflow_name: str | None = None,
        tool_name: str | None = None,
        display: str = "composer",
        interaction_type: str = "input_request",
        password: bool = False,
        raw_payload: dict[str, Any] | None = None,
    ) -> None:
        """Save pending input request state to the chat document.

        When an input_request is issued, we persist its metadata so that
        on resume, the UI can be notified that user input is still needed.

        Args:
            chat_id: Chat session identifier
            app_id: App identifier
            request_id: The input request ID
            agent: The agent that requested input
            prompt: The prompt text shown to the user
        """
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        try:
            coll = await self._coll()
            await coll.update_one(
                {"_id": chat_id, **build_app_scope_filter(str(resolved_app_id))},
                {
                    "$set": {
                        "workflow_ui_state.pending_input_request": {
                            "request_id": request_id,
                            "agent": agent,
                            "prompt": prompt,
                            "component_type": component_type,
                            "workflow_name": workflow_name,
                            "tool_name": tool_name,
                            "display": display,
                            "interaction_type": interaction_type,
                            "password": bool(password),
                            "raw_payload": raw_payload if isinstance(raw_payload, dict) else {},
                            "created_at": datetime.now(UTC).isoformat(),
                        },
                        "last_updated_at": datetime.now(UTC),
                    },
                    "$inc": {"session_version": 1},
                }
            )
            logger.debug("[PENDING_INPUT] Saved pending input request %s for chat %s", request_id, chat_id)
        except Exception as e:
            logger.error("[PENDING_INPUT] Failed to save pending input request for %s: %s", chat_id, e, exc_info=True)

    async def clear_pending_input_request(
        self,
        *,
        chat_id: str,
        app_id: str | None = None,
    ) -> None:
        """Clear pending input request state from the chat document.

        Called when user provides input or the workflow completes.
        """
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        try:
            coll = await self._coll()
            await coll.update_one(
                {"_id": chat_id, **build_app_scope_filter(str(resolved_app_id))},
                {
                    "$set": {
                        "workflow_ui_state.pending_input_request": None,
                        "last_updated_at": datetime.now(UTC),
                    },
                    "$inc": {"session_version": 1},
                }
            )
            logger.debug("[PENDING_INPUT] Cleared pending input request for chat %s", chat_id)
        except Exception as e:
            logger.error("[PENDING_INPUT] Failed to clear pending input request for %s: %s", chat_id, e, exc_info=True)

    async def get_pending_input_request(
        self,
        *,
        chat_id: str,
        app_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Get pending input request state from the chat document.

        Returns:
            The pending input request dict if exists, None otherwise.
        """
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            return None
        try:
            coll = await self._coll()
            doc = await coll.find_one(
                {"_id": chat_id, **build_app_scope_filter(str(resolved_app_id))},
                {"workflow_ui_state.pending_input_request": 1}
            )
            return extract_pending_input_request(doc)
        except Exception as e:
            logger.error("[PENDING_INPUT] Failed to get pending input request for %s: %s", chat_id, e, exc_info=True)
            return None


#############################################
#############################################
