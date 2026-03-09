# ==============================================================================
# FILE: persistence_manager.py
# DESCRIPTION: 
# ==============================================================================

"""Persistence layer for MozaiksAI workflows.

Clean implementation aligned with AG2 event system:
  * PersistenceManager: Mongo client + indexes (runtime-owned only)
  * AG2PersistenceManager: chat sessions + real-time usage tracking
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, UTC
from typing import Dict, List, Any, Optional, Union, cast
import hashlib
from copy import deepcopy
import textwrap
from pymongo import ReturnDocument
from uuid import uuid4
from logs.logging_config import get_workflow_logger
from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.multitenant import build_app_scope_filter, coalesce_app_id, dual_write_app_scope
from ..models import WorkflowStatus
from autogen.events.base_event import BaseEvent
from autogen.events.agent_events import TextEvent
from mozaiksai.core.workflow.outputs.structured import agent_has_structured_output, get_structured_output_model_fields

logger = get_workflow_logger("persistence")


def _resolve_agent_log_limit(env_key: str, default: Optional[int]) -> Optional[int]:
    """Resolve agent conversation log length limits from environment."""
    raw_value = os.getenv(env_key)
    if raw_value is None:
        return default
    try:
        parsed = int(raw_value.strip())
    except ValueError:
        logger.warning(
            "Invalid %s value '%s'; using default %s", env_key, raw_value, default
        )
        return default
    return None if parsed <= 0 else parsed


_AGENT_CONV_JSON_MAX_LEN = _resolve_agent_log_limit("AGENT_CONV_JSON_MAX_LEN", None)
_AGENT_CONV_TEXT_MAX_LEN = _resolve_agent_log_limit("AGENT_CONV_TEXT_MAX_LEN", None)

_GENERAL_CHAT_COLLECTION = "GeneralChatSessions"
_GENERAL_CHAT_COUNTER_COLLECTION = "GeneralChatCounters"


class PersistenceManager:
    """Mongo connection holder for runtime persistence."""

    def __init__(self):
        self.client: Optional[Any] = None
        self._init_lock = asyncio.Lock()
        logger.info("PersistenceManager created (lazy init)")

    async def _ensure_client(self) -> None:
        if self.client is not None:
            return
        async with self._init_lock:
            if self.client is not None:
                return
            self.client = get_mongo_client()
            try:
                # Primary chat session collection (canonical)
                coll = self.client["MozaiksAI"]["ChatSessions"]
                # Check if index already exists before creating
                existing_indexes = await coll.list_indexes().to_list(length=None)
                index_names = [idx["name"] for idx in existing_indexes]
                
                # Create app/workflow/created index if not exists
                ent_wf_created_exists = any(
                    name in ["idx_ent_wf_created", "cs_ent_wf_created"] 
                    for name in index_names
                )
                if not ent_wf_created_exists:
                    await coll.create_index([("app_id", 1), ("workflow_name", 1), ("created_at", -1)], name="cs_ent_wf_created")
                    logger.debug("Created app/workflow/created index")

                # Canonical app/workflow/created index (new name)
                if "cs_app_wf_created" not in index_names:
                    await coll.create_index([("app_id", 1), ("workflow_name", 1), ("created_at", -1)], name="cs_app_wf_created")
                    logger.debug("Created app/workflow/created index")
                
                # Create status index if not exists  
                if "idx_status" not in index_names and "cs_status_created" not in index_names:
                    await coll.create_index("status", name="idx_status")
                    logger.debug("Created status index")
                    
                # Note: per-event normalized rows and their indexes in WorkflowStats
                # were removed to reduce collection noise; WorkflowStats now holds
                # live rollup documents (mon_ prefix) only, so no per-event index is needed.

                general_coll = self.client["MozaiksAI"][_GENERAL_CHAT_COLLECTION]
                general_indexes = await general_coll.list_indexes().to_list(length=None)
                general_index_names = [idx["name"] for idx in general_indexes]
                if "gc_ent_user_created" not in general_index_names:
                    await general_coll.create_index(
                        [("app_id", 1), ("user_id", 1), ("created_at", -1)],
                        name="gc_ent_user_created",
                    )
                    logger.debug("Created general chat app/user index")

                if "gc_app_user_created" not in general_index_names:
                    await general_coll.create_index(
                        [("app_id", 1), ("user_id", 1), ("created_at", -1)],
                        name="gc_app_user_created",
                    )
                    logger.debug("Created general chat app/user index")
                if "gc_status" not in general_index_names:
                    await general_coll.create_index("status", name="gc_status")
                    logger.debug("Created general chat status index")

                counter_coll = self.client["MozaiksAI"][_GENERAL_CHAT_COUNTER_COLLECTION]
                counter_indexes = await counter_coll.list_indexes().to_list(length=None)
                counter_names = [idx["name"] for idx in counter_indexes]
                if "gc_counter_ent_user" not in counter_names:
                    await counter_coll.create_index(
                        [("app_id", 1), ("user_id", 1)],
                        name="gc_counter_ent_user",
                        unique=True,
                    )
                    logger.debug("Created general chat counter unique index")

                if "gc_counter_app_user" not in counter_names:
                    await counter_coll.create_index(
                        [("app_id", 1), ("user_id", 1)],
                        name="gc_counter_app_user",
                        unique=True,
                    )
                    logger.debug("Created general chat app counter unique index")
            except Exception as e:  # pragma: no cover
                logger.warning(f"Index ensure issue: {e}")

class AG2PersistenceManager:
    """Lean persistence using two collections: ChatSessions and WorkflowStats.

    ChatSessions: one document per chat workflow with embedded messages (transcript).
    WorkflowStats: holds unified live rollup documents (mon_{app}_{workflow}).

    Per-event normalized rows were intentionally disabled to reduce collection noise.
    Replay/resume relies on ChatSessions.messages; metrics aggregate in real-time
    in the mon_ rollup documents.
    """

    def __init__(self):
        self.persistence = PersistenceManager()
        logger.info("AG2PersistenceManager (lean) ready")
        self._workflow_stats_indexes_checked = False

    async def _coll(self):
        await self.persistence._ensure_client()
        assert self.persistence.client is not None, "Mongo client not initialized"
        return self.persistence.client["MozaiksAI"]["ChatSessions"]

    async def _workflow_stats_coll(self):
        await self.persistence._ensure_client()
        assert self.persistence.client is not None, "Mongo client not initialized"
        coll = self.persistence.client["MozaiksAI"]["WorkflowStats"]
        if not getattr(self, "_workflow_stats_indexes_checked", False):
            await self._ensure_workflow_stats_indexes(coll)
        return coll

    async def _ensure_workflow_stats_indexes(self, coll):
        """Drop legacy unique indexes that conflict with rollup documents."""
        try:
            existing = await coll.list_indexes().to_list(length=None)
            legacy_index_names = {"ux_chat_seq", "ux_workflow_chat_seq"}
            for idx in existing:
                name = idx.get("name")
                if name in legacy_index_names:
                    try:
                        await coll.drop_index(name)
                        logger.info("Dropped legacy WorkflowStats index %s", name)
                    except Exception as drop_err:
                        logger.warning("Failed to drop legacy WorkflowStats index %s: %s", name, drop_err)
            self._workflow_stats_indexes_checked = True
        except Exception as idx_err:
            logger.warning("WorkflowStats index check failed: %s", idx_err)
            # Avoid repeated attempts in tight loops if Mongo unavailable
            self._workflow_stats_indexes_checked = True

    async def _general_coll(self):
        await self.persistence._ensure_client()
        assert self.persistence.client is not None, "Mongo client not initialized"
        return self.persistence.client["MozaiksAI"][_GENERAL_CHAT_COLLECTION]

    async def _general_counter_coll(self):
        await self.persistence._ensure_client()
        assert self.persistence.client is not None, "Mongo client not initialized"
        return self.persistence.client["MozaiksAI"][_GENERAL_CHAT_COUNTER_COLLECTION]

    async def get_or_assign_cache_seed(
        self,
        chat_id: str,
        app_id: Optional[str] = None,
    ) -> int:
        """Return a stable per-chat cache seed, assigning one if missing.

        Seed is deterministic by default (derived from chat_id and app_id if provided),
        and persisted to the ChatSessions document under "cache_seed" for visibility and reuse.
        """
        resolved_app_id = coalesce_app_id(app_id=app_id)
        coll = await self._coll()
        doc = await coll.find_one({"_id": chat_id}, {"cache_seed": 1})
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
                    f"[CACHE_SEED] Persisted seed could not be coerced to int (value={doc.get('cache_seed')!r}); will recompute",
                    extra={"chat_id": chat_id, "app_id": resolved_app_id},
                )
        # Derive a deterministic 32-bit seed from chat_id (+ app_id if provided)
        basis = chat_id if not resolved_app_id else f"{resolved_app_id}:{chat_id}"
        seed_bytes = hashlib.sha256(basis.encode("utf-8")).digest()[:4]
        seed = int.from_bytes(seed_bytes, "big", signed=False)
        try:
            await coll.update_one({"_id": chat_id}, {"$set": {"cache_seed": seed}})
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
            logger.debug(f"Failed to persist cache_seed for chat {chat_id}: {e}")
            logger.debug(
                "[CACHE_SEED] Proceeding with in-memory seed only (persistence failure)",
                extra={"chat_id": chat_id, "app_id": resolved_app_id, "seed": seed},
            )
        return seed

    # Chat sessions -----------------------------------------------------
    async def create_chat_session(
        self,
        chat_id: str,
        app_id: Optional[str] = None,
        workflow_name: str = "",
        user_id: str = "",
        *,
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> None:
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        try:
            coll = await self._coll()
            if await coll.find_one({"_id": chat_id}):
                return
            now = datetime.now(UTC)
            session_doc: Dict[str, Any] = {
                "_id": chat_id,
                "chat_id": chat_id,
                "app_id": resolved_app_id,
                "workflow_name": workflow_name,
                "user_id": user_id,
                "status": int(WorkflowStatus.IN_PROGRESS),
                "created_at": now,
                "last_updated_at": now,
                # per-session atomic sequence counter for message diffs
                "last_sequence": 0,
                # persisted UI context for multi-user resume of active artifact/tool panel
                # null until first artifact/tool emission is persisted via update_last_artifact()
                "last_artifact": None,
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
                }
                for k, v in list(extra_fields.items()):
                    if not isinstance(k, str) or not k.strip():
                        continue
                    if k in protected:
                        continue
                    session_doc[k] = v

            session_doc = dual_write_app_scope(session_doc, resolved_app_id)
            await coll.insert_one(session_doc)
            # Initialize / upsert unified real-time rollup doc (mon_{app_id}_{workflow_name})
            # We maintain a single rollup document that is updated live instead of
            # a per-chat metrics_{chat_id} document plus a completion rollup.
            stats_coll = await self._workflow_stats_coll()
            summary_id = f"mon_{resolved_app_id}_{workflow_name}"
            # Use $setOnInsert so we don't clobber existing real-time aggregates if concurrent chats start.
            await stats_coll.update_one(
                {"_id": summary_id},
                {"$setOnInsert": {
                    "_id": summary_id,
                    "app_id": resolved_app_id,
                    "workflow_name": workflow_name,
                    "last_updated_at": now,
                    # overall_avg block mirrors models.WorkflowSummaryDoc schema
                    "overall_avg": {
                        "avg_duration_sec": 0.0,
                        "avg_prompt_tokens": 0,
                        "avg_completion_tokens": 0,
                        "avg_total_tokens": 0,
                        "avg_cost_total_usd": 0.0,
                    },
                    "chat_sessions": {},
                    "agents": {}
                }},
                upsert=True
            )
            # Seed empty per-chat metrics container if not present
            await stats_coll.update_one(
                {"_id": summary_id, f"chat_sessions.{chat_id}": {"$exists": False}},
                {"$set": {f"chat_sessions.{chat_id}": {
                    "duration_sec": 0.0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "cost_total_usd": 0.0
                }, "last_updated_at": now}}
            )
        except Exception as e:  # pragma: no cover
            logger.error(f"Failed to create chat session {chat_id}: {e}")

    async def fetch_chat_session_extra_context(
        self,
        *,
        chat_id: str,
        app_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch non-canonical, non-message fields for a chat session.

        Purpose:
        - Allows runtime to seed AG2 ContextVariables from persisted session metadata
          (e.g., parent_chat_id, PatternSelection seeds for generator subruns).

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
                "last_artifact",
            }
            extra: Dict[str, Any] = {}
            for k, v in doc.items():
                if not isinstance(k, str) or not k.strip():
                    continue
                if k in protected:
                    continue
                extra[k] = v
            return extra
        except Exception as e:  # pragma: no cover
            logger.debug(f"[FETCH_EXTRA_CONTEXT] Failed chat_id={chat_id}: {e}")
            return {}

    async def create_general_chat_session(
        self,
        *,
        app_id: Optional[str] = None,
        user_id: str,
    ) -> Dict[str, Any]:
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
                "usage_prompt_tokens_final": 0,
                "usage_completion_tokens_final": 0,
                "usage_total_tokens_final": 0,
                "usage_total_cost_final": 0.0,
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

            logger.info(
                "[GENERAL_CHAT] Created general session",
                extra={"general_chat_id": general_chat_id, "app_id": ent_id, "user_id": user_id, "sequence": seq},
            )

            return {"chat_id": general_chat_id, "label": label, "sequence": seq}
        except Exception as e:  # pragma: no cover
            logger.error(f"Failed to create general chat session for app_id={resolved_app_id}, user={user_id}: {e}")
            raise

    async def mark_chat_completed(self, chat_id: str, app_id: Optional[str] = None) -> bool:
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        try:
            coll = await self._coll()
            now = datetime.now(UTC)
            # Fetch created_at & usage to compute duration for rollup averages
            base_doc = await coll.find_one({"_id": chat_id, **build_app_scope_filter(resolved_app_id)}, {"created_at": 1})
            created_at = base_doc.get("created_at") if base_doc else None
            if isinstance(created_at, datetime) and created_at.tzinfo is None:
                # Mongo can return naive datetimes when tz_aware=False; treat as UTC for compatibility.
                created_at = created_at.replace(tzinfo=UTC)
            dur = float((now - created_at).total_seconds()) if isinstance(created_at, datetime) else 0.0
            res = await coll.update_one({"_id": chat_id, **build_app_scope_filter(resolved_app_id)}, {"$set": {
                "status": int(WorkflowStatus.COMPLETED),
                "completed_at": now,
                "last_updated_at": now,
                "duration_sec": dur,
            }})
            # Fire & forget rollup refresh (no await block on success path)
            if res.modified_count > 0:
                try:  # pragma: no cover
                    from ..models import refresh_workflow_rollup_by_id  # local import to avoid circular at module import
                    # Need workflow_name for rollup; fetch minimally
                    doc = await coll.find_one({"_id": chat_id}, {"workflow_name": 1})
                    if doc and (wf := doc.get("workflow_name")):
                        summary_id = f"mon_{resolved_app_id}_{wf}"
                        asyncio.create_task(refresh_workflow_rollup_by_id(summary_id))
                except Exception as e:
                    logger.debug(f"Rollup refresh failed for {chat_id}: {e}")
            return res.modified_count > 0
        except Exception as e:  # pragma: no cover
            logger.error(f"Failed to mark chat {chat_id} as completed: {e}")
            return False

    async def update_last_artifact(
        self,
        *,
        chat_id: str,
        app_id: str,
        artifact: Dict[str, Any],
    ) -> None:
        """Persist latest artifact/tool panel context for multi-user resume.

        Expected artifact dict keys (best-effort, flexible):
            ui_tool_id: str
            event_id: str | None
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
                "ui_tool_id": artifact.get("ui_tool_id"),
                "event_id": artifact.get("event_id"),
                "display": artifact.get("display"),
                "workflow_name": artifact.get("workflow_name"),
                # Keep payload shallow (avoid huge memory copies); truncate strings if massive in future enhancement
                "payload": artifact.get("payload"),
                "updated_at": now,
            }
            await coll.update_one(
                {"_id": chat_id, **build_app_scope_filter(resolved_app_id)},
                {"$set": {"last_artifact": doc, "last_updated_at": now}},
            )
            logger.debug(
                "[LAST_ARTIFACT] Updated",
                extra={"chat_id": chat_id, "app_id": resolved_app_id, "ui_tool_id": doc.get("ui_tool_id")},
            )
        except Exception as e:  # pragma: no cover
            logger.debug(f"[LAST_ARTIFACT] Update failed chat_id={chat_id}: {e}")

    async def persist_initial_messages(
        self,
        *,
        chat_id: str,
        app_id: str,
        messages: List[Dict[str, Any]],
    ) -> None:
        """Persist initial seed / user messages that AG2 does NOT emit as TextEvents.

        Rationale:
            a_run_group_chat() consumes the provided initial message list as starting
            context but does not re-emit those messages as TextEvent instances. Our
            persistence layer previously only stored AG2 TextEvents, leaving brand-new
            ChatSessions with an empty messages[] array until the first agent reply.

        Behavior:
            - Each provided message gets an auto-assigned sequence (incrementing last_sequence).
            - event_id is generated with 'init_' prefix for traceability.
            - Skips if list empty or chat session missing.
            - Safe to call multiple times: we perform a basic duplicate guard by checking
              if an identical (role, content) pair already exists as the latest message to
              avoid accidental double insertion on rare retries.
        """
        if not messages:
            return
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        try:
            coll = await self._coll()
            base_doc = await coll.find_one({"_id": chat_id, **build_app_scope_filter(resolved_app_id)}, {"messages": {"$slice": -5}})
            recent: List[Dict[str, Any]] = []
            if base_doc and isinstance(base_doc.get("messages"), list):
                recent = [m for m in base_doc["messages"] if isinstance(m, dict)]
            for m in messages:
                role = m.get("role") or "user"
                content = m.get("content")
                if content is None:
                    continue
                # Duplicate guard: if last message matches role+content, skip
                if recent and isinstance(recent[-1], dict):
                    last = recent[-1]
                    if last.get("role") == role and last.get("content") == content:
                        continue
                # Increment sequence counter atomically & fetch new value
                bump = await coll.find_one_and_update(
                    {"_id": chat_id, **build_app_scope_filter(resolved_app_id)},
                    {"$inc": {"last_sequence": 1}, "$set": {"last_updated_at": datetime.now(UTC)}},
                    return_document=ReturnDocument.AFTER,
                )
                seq = int(bump.get("last_sequence", 1)) if bump else 1
                msg_doc = {
                    "role": role,
                    "content": str(content),
                    "timestamp": datetime.now(UTC),
                    "event_type": "message.created",
                    "event_id": f"init_{uuid4()}",
                    "sequence": seq,
                    "agent_name": m.get("name") or ("user" if role == "user" else "assistant"),
                }
                await coll.update_one(
                    {"_id": chat_id, **build_app_scope_filter(resolved_app_id)},
                    {"$push": {"messages": msg_doc}, "$set": {"last_updated_at": datetime.now(UTC)}},
                )
                recent.append(msg_doc)
                logger.debug(
                    "[INIT_MSG_PERSIST] Inserted initial message",
                    extra={"chat_id": chat_id, "app_id": resolved_app_id, "seq": seq, "role": role},
                )
        except Exception as e:  # pragma: no cover
            logger.debug(f"[INIT_MSG_PERSIST] Failed chat_id={chat_id}: {e}")

    async def resume_chat(self, chat_id: str, app_id: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
        """Return full message list for an in-progress chat.

        Strict mode: only active (IN_PROGRESS) sessions are resumable; completed
        sessions require explicit inspection via administrative paths (not a
        transparent fallback inside runtime code).
        """
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        try:
            coll = await self._coll()
            doc = await coll.find_one({"_id": chat_id, **build_app_scope_filter(resolved_app_id)}, {"messages": 1, "status": 1})
            
            if not doc:
                logger.warning(f"[RESUME_CHAT] No document found for chat_id={chat_id} app_id={resolved_app_id}")
                return None
            
            status = int(doc.get("status", -1))
            status_name = WorkflowStatus(status).name if status in [s.value for s in WorkflowStatus] else "UNKNOWN"
            msgs = doc.get("messages", [])
            
            logger.info(f"[RESUME_CHAT] chat_id={chat_id} status={status_name}({status}) messages_count={len(msgs)}")
            
            if status != int(WorkflowStatus.IN_PROGRESS):
                logger.warning(f"[RESUME_CHAT] Chat status is {status_name}, not IN_PROGRESS - returning None")
                return None
            
            return msgs
        except Exception as e:  # pragma: no cover
            logger.warning(f"[RESUME_CHAT] Failed to resume chat {chat_id}: {e}")
            return None

    async def append_general_message(
        self,
        *,
        general_chat_id: str,
        app_id: str,
        role: str,
        content: str,
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
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
        app_id: Optional[str] = None,
        user_id: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
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
        sessions: List[Dict[str, Any]] = []
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

    async def get_user_workflow_statuses(self, *, app_id: Optional[str] = None, user_id: str) -> Dict[str, Dict[str, Any]]:
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
            )
            docs = await cursor.to_list(length=None)
            result: Dict[str, Dict[str, Any]] = {}
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
            logger.warning(f"[GET_WORKFLOW_STATUSES] Failed to fetch workflows for app_id={resolved_app_id} user={user_id}: {e}")
            return {}

    async def build_pattern_context_from_user(self, *, app_id: Optional[str] = None, user_id: str) -> Dict[str, Any]:
        """Build the minimal pattern-context payload (app_id, user_id, workflows)

        This helper wraps `get_user_workflow_statuses` and returns the small
        shape expected by generators per the `PATTERN_CONTEXT_CONTRACT`.
        """
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        workflows = await self.get_user_workflow_statuses(app_id=resolved_app_id, user_id=user_id)
        return {
            "app_id": str(resolved_app_id),
            "user_id": user_id,
            "workflows": workflows,
        }

    async def fetch_general_chat_transcript(
        self,
        *,
        general_chat_id: str,
        app_id: Optional[str] = None,
        after_sequence: int = -1,
        limit: int = 500,
    ) -> Optional[Dict[str, Any]]:
        coll = await self._general_coll()
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        ent_id = str(resolved_app_id)
        doc = await coll.find_one({"_id": general_chat_id, **build_app_scope_filter(ent_id)})
        if not doc:
            return None

        messages = doc.get("messages", []) or []
        filtered: List[Dict[str, Any]] = []
        for message in messages:
            try:
                sequence_val = int(message.get("sequence", 0) or 0)
            except Exception:
                sequence_val = 0
            if after_sequence >= 0 and sequence_val <= after_sequence:
                continue
            filtered.append(message)

        if limit > 0:
            limit = max(1, min(int(limit), 2000))
            filtered = filtered[-limit:]

        payload = {
            "chat_id": doc.get("_id"),
            "label": doc.get("general_label") or doc.get("_id"),
            "sequence": int(doc.get("general_sequence", 0) or 0),
            "status": int(doc.get("status", -1)),
            "app_id": ent_id,
            "user_id": doc.get("user_id"),
            "messages": filtered,
            "last_sequence": int(doc.get("last_sequence", 0) or 0),
            "created_at": doc.get("created_at"),
            "last_updated_at": doc.get("last_updated_at"),
        }
        return payload

    async def fetch_event_diff(self, *, chat_id: str, app_id: Optional[str] = None, last_sequence: int) -> List[Dict[str, Any]]:
        """Return message diff (messages with sequence > last_sequence).

        Assumes every persisted message carries an authoritative 'sequence'
        integer; absence of that field is considered a data integrity issue and
        results in those messages being ignored for diff purposes.
        """
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        try:
            coll = await self._coll()
            doc = await coll.find_one({"_id": chat_id, **build_app_scope_filter(str(resolved_app_id))}, {"messages": 1})
            if not doc:
                return []
            msgs = doc.get("messages", [])
            return [m for m in msgs if isinstance(m, dict) and m.get("sequence", 0) > int(last_sequence)]
        except Exception as e:  # pragma: no cover
            logger.warning(f"Failed to fetch event diff for {chat_id}: {e}")
            return []

    # Events ------------------------------------------------------------
    async def save_event(self, event: BaseEvent, chat_id: str, app_id: Optional[str] = None) -> None:
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        try:
            # Only persist TextEvent messages; ignore all other AG2 event types
            if not isinstance(event, TextEvent):
                return
            # After the isinstance guard, we can safely treat event as TextEvent for type checkers
            text_event = cast(TextEvent, event)
            coll = await self._coll()
            # Atomically bump per-session sequence counter and read new value
            try:
                bump = await coll.find_one_and_update(
                    {"_id": chat_id, **build_app_scope_filter(str(resolved_app_id))},
                    {"$inc": {"last_sequence": 1}, "$set": {"last_updated_at": datetime.now(UTC)}},
                    return_document=ReturnDocument.AFTER,
                )
                seq = int(bump.get("last_sequence", 1)) if bump else 1
                wf_name = bump.get("workflow_name") if bump else None
            except Exception as e:
                logger.warning(f"Failed to update sequence counter for {chat_id}: {e}")
                seq = 1
                wf_name = None
            event_id = getattr(text_event, "id", None) or getattr(text_event, "event_id", None) or getattr(text_event, "event_uuid", None) or str(uuid4())
            sender_obj = getattr(text_event, "sender", None)
            raw_name = getattr(sender_obj, "name", None) if sender_obj else None
            raw_content = getattr(text_event, "content", "")

            # Helper: attempt extraction from dict-like content
            def _extract_name_from_content(rc: Any) -> Optional[str]:
                try:
                    if isinstance(rc, dict):
                        for k in ("sender", "agent", "agent_name", "name"):
                            v = rc.get(k)
                            if isinstance(v, str) and v.strip():
                                return v.strip()
                    return None
                except Exception:  # pragma: no cover
                    return None

            if not raw_name:
                # If the raw content is a pydantic / dataclass / object with dict method, attempt that
                if hasattr(raw_content, "model_dump"):
                    try:
                        raw_name = _extract_name_from_content(raw_content.model_dump())  # type: ignore
                    except Exception:
                        pass
                if not raw_name and hasattr(raw_content, "dict"):
                    try:
                        raw_name = _extract_name_from_content(raw_content.dict())  # type: ignore
                    except Exception:
                        pass
                if not raw_name:
                    raw_name = _extract_name_from_content(raw_content)
            # Fallback: parse from string representation if still missing
            if not raw_name:
                try:
                    txt_for_parse = None
                    if isinstance(raw_content, str):
                        txt_for_parse = raw_content
                    else:
                        # Convert to str only if small to avoid huge dumps
                        dumped = str(raw_content)
                        if len(dumped) < 5000:
                            txt_for_parse = dumped
                    if txt_for_parse:
                        import re
                        # Try both sender='Name' and "sender": "Name" JSON style
                        m = re.search(r"sender(?:=|\"\s*:)['\"](?P<sender>[^'\"\\]+)['\"]", txt_for_parse)
                        if m:
                            raw_name = m.group("sender").strip()
                except Exception as e:
                    logger.debug(f"Failed to parse sender from string content: {e}")
            if not raw_name:
                raw_name = "assistant"  # final fallback
            name_lower = raw_name.lower()
            role = "user" if name_lower in ("user", "userproxy", "userproxyagent") else "assistant"
            # Preserve structured content when possible
            if isinstance(raw_content, (dict, list)):
                try:
                    content_str = json.dumps(raw_content)[:10000]
                except (TypeError, ValueError) as e:
                    logger.debug(f"Failed to serialize content as JSON: {e}")
                    content_str = str(raw_content)
            else:
                content_str = str(raw_content)
            # --------------------------------------------------
            # Post-process: extract inner message content to avoid storing the
            # verbose debug string: "uuid=UUID('...') content='...' sender='Agent' ..."
            # We keep only the 'content' portion; if that portion looks like JSON
            # we attempt to parse & re-dump for clean storage.
            # --------------------------------------------------
            try:
                # Fast check to avoid regex cost when pattern absent
                if "content=" in content_str and " sender=" in content_str:
                    import re
                    import json as _json
                    # Non-greedy capture between content=quote and the next quote before sender=
                    m = re.search(r"content=(?:'|\")(?P<inner>.*?)(?:'|\")\s+sender=", content_str, re.DOTALL)
                    if m:
                        inner = m.group("inner").strip()
                        cleaned: Any = inner
                        if inner.startswith("{") or inner.startswith("["):
                            try:
                                parsed = _json.loads(inner)
                                # Re-dump to normalized string with no escaping issues
                                cleaned = _json.dumps(parsed, ensure_ascii=False)
                            except Exception:
                                # leave as raw string if JSON parse fails
                                pass
                        content_str = cleaned if isinstance(cleaned, str) else _json.dumps(cleaned, ensure_ascii=False)
            except Exception as _ce:  # pragma: no cover
                logger.debug(f"Content clean failed: {_ce}")
            ts = getattr(event, "timestamp", None)
            evt_ts = datetime.fromtimestamp(ts) if isinstance(ts, (int, float)) else datetime.now(UTC)
            msg = {
                "role": role,
                "content": content_str,
                "timestamp": evt_ts,
                "event_type": "message.created",
                "event_id": event_id,
                "sequence": seq,
            }
            if role == "assistant":
                msg["agent_name"] = raw_name
            else:
                msg["agent_name"] = "user"
            # Structured output attachment (if agent registered for structured outputs in workflow)
            try:
                if role == "assistant" and wf_name and raw_name and agent_has_structured_output(wf_name, raw_name):
                    # Attempt to parse JSON from cleaned content
                    parsed = self._extract_json_from_text(content_str, agent_name=raw_name)
                    if parsed:
                        normalized = self._normalize_structured_output(raw_name, parsed)
                        if normalized != parsed:
                            logger.warning(
                                f"[SAVE_EVENT] Normalized structured output for {raw_name}"
                            )
                        msg["structured_output"] = normalized
                        schema_fields = get_structured_output_model_fields(wf_name, raw_name) or {}
                        if schema_fields:
                            msg["structured_schema"] = schema_fields
                        logger.info(f"[SAVE_EVENT] ✓ Added structured_output for {raw_name}")
                    else:
                        logger.warning(f"[SAVE_EVENT] ✗ Failed to parse JSON for {raw_name}, content_preview: {content_str[:200] if content_str else '(empty)'}")
            except Exception as so_err:  # pragma: no cover
                logger.debug(f"[SAVE_EVENT] Structured output parse skipped agent={raw_name}: {so_err}")
            await coll.update_one(
                {"_id": chat_id, **build_app_scope_filter(str(resolved_app_id))},
                {"$push": {"messages": msg}, "$set": {"last_updated_at": datetime.now(UTC)}},
            )
            
            # Log agent conversation to dedicated file with pretty formatting
            try:
                import logging as _logging
                import json as _json
                agent_conv_logger = _logging.getLogger("mozaiks.workflow.agent_messages")
                agent_name = msg.get("agent_name", "unknown")
                
                # Try to parse and pretty-print JSON content
                display_content = content_str
                is_json = False
                if content_str.strip().startswith('{') or content_str.strip().startswith('['):
                    try:
                        parsed = _json.loads(content_str)
                        # Pretty print with indentation
                        display_content = _json.dumps(parsed, indent=2, ensure_ascii=False)
                        is_json = True
                    except Exception:
                        # Not valid JSON, use as-is
                        pass
                
                # Optional truncation (controlled via env) and text wrapping
                max_len = _AGENT_CONV_JSON_MAX_LEN if is_json else _AGENT_CONV_TEXT_MAX_LEN
                truncated_suffix = ""
                if max_len is not None and len(display_content) > max_len:
                    display_content = display_content[:max_len]
                    truncated_suffix = "\n... (truncated)"

                if not is_json and display_content:
                    wrapped_lines: list[str] = []
                    split_lines = display_content.splitlines() or [display_content]
                    for raw_line in split_lines:
                        if not raw_line.strip():
                            wrapped_lines.append("")
                            continue
                        wrapped_lines.extend(
                            textwrap.wrap(
                                raw_line,
                                width=100,
                                break_long_words=False,
                                break_on_hyphens=False,
                            )
                        )
                    display_content = "\n".join(wrapped_lines)

                if truncated_suffix:
                    display_content = f"{display_content}{truncated_suffix}"

                # Add visual separator and metadata block for readability
                separator = "=" * 80
                meta_lines = [
                    f"agent: {agent_name}",
                    f"chat_id: {chat_id}",
                    f"app_id: {resolved_app_id}",
                    f"app_id: {resolved_app_id}",
                    f"sequence: {seq}",
                    f"event_id: {event_id}",
                ]
                body = display_content if display_content else "(empty)"
                log_message = (
                    f"\n{separator}\n"
                    + "\n".join(meta_lines)
                    + f"\n{separator}\n{body}\n"
                )
                
                agent_conv_logger.info(
                    log_message,
                    extra={
                        "chat_id": chat_id,
                        "app_id": resolved_app_id,
                        "sequence": seq,
                        "event_id": event_id,
                    }
                )
            except Exception as log_err:  # pragma: no cover
                logger.debug(f"Failed to log agent conversation: {log_err}")
                
        except Exception as e:  # pragma: no cover
            logger.error(f"Failed to save event for {chat_id}: {e}")

    async def save_usage_summary_event(
        self,
        *,
        envelope: Dict[str, Any],
        chat_id: str,
        app_id: Optional[str] = None,
        workflow_name: str,
        user_id: str,
    ) -> None:
        """Process AG2 UsageSummaryEvent for metrics updates.
        
        Called directly from orchestration when UsageSummaryEvent is encountered.
        """
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        try:
            if not envelope or envelope.get("event_type") != "UsageSummaryEvent":
                logger.warning(f"Invalid UsageSummaryEvent envelope for {chat_id}")
                return
                
            meta = envelope.get("meta", {})
            # Extract an event timestamp (seconds since epoch) if provided, else now()
            raw_ts = meta.get("timestamp") or meta.get("ts") or envelope.get("timestamp")
            evt_dt: Optional[datetime] = None
            try:
                if isinstance(raw_ts, (int, float)):
                    evt_dt = datetime.utcfromtimestamp(float(raw_ts))
            except Exception:
                evt_dt = None
            await self.update_session_metrics(
                chat_id=chat_id,
                app_id=resolved_app_id,
                user_id=user_id,
                workflow_name=workflow_name,
                prompt_tokens=int(meta.get("prompt_tokens", 0)),
                completion_tokens=int(meta.get("completion_tokens", 0)),
                cost_usd=float(meta.get("cost_usd", 0.0)),
                agent_name=meta.get("agent") or envelope.get("name"),
                event_ts=evt_dt,
            )
        except Exception as e:  # pragma: no cover
            logger.error(f"Failed to process UsageSummaryEvent for {chat_id}: {e}")

    # Usage summary ----------------------------------------------------
    async def update_session_metrics(
        self,
        chat_id: str,
        user_id: str,
        workflow_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
        app_id: Optional[str] = None,
        *,
        agent_name: Optional[str] = None,
        event_ts: Optional[datetime] = None,
        duration_sec: float = 0.0,
        session_type: str = "workflow",
    ) -> None:
        """Update live unified rollup document with per-chat + per-agent metrics and usage aggregation.

        Replaces per-chat metrics document updates. We directly mutate the
        rollup doc (mon_{app_id}_{workflow_name}) so UI / analytics can read
        a single authoritative structure during execution.
        """
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        try:
            stats_coll = await self._workflow_stats_coll()
            summary_id = f"mon_{resolved_app_id}_{workflow_name}"
            total_tokens = prompt_tokens + completion_tokens
            now = datetime.now(UTC)
            if event_ts is None:
                event_ts = now
            # Ensure base summary & chat session containers exist
            await stats_coll.update_one(
                {"_id": summary_id},
                {"$setOnInsert": {
                    "_id": summary_id,
                    "app_id": resolved_app_id,
                    "workflow_name": workflow_name,
                    "last_updated_at": now,
                    "overall_avg": {
                        "avg_duration_sec": 0.0,
                        "avg_prompt_tokens": 0,
                        "avg_completion_tokens": 0,
                        "avg_total_tokens": 0,
                        "avg_cost_total_usd": 0.0,
                    },
                    "chat_sessions": {},
                    "agents": {}
                }}, upsert=True
            )
            # Seed chat session metrics if absent
            await stats_coll.update_one(
                {"_id": summary_id, f"chat_sessions.{chat_id}": {"$exists": False}},
                {"$set": {f"chat_sessions.{chat_id}": {
                    "duration_sec": 0.0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "cost_total_usd": 0.0,
                    # Track per-chat last event time to accumulate duration between usage deltas
                    "last_event_ts": event_ts
                }}, "$setOnInsert": {"last_updated_at": now}}
            )
            # Increment per-chat metrics
            inc_ops = {
                f"chat_sessions.{chat_id}.prompt_tokens": prompt_tokens,
                f"chat_sessions.{chat_id}.completion_tokens": completion_tokens,
                f"chat_sessions.{chat_id}.total_tokens": total_tokens,
                f"chat_sessions.{chat_id}.cost_total_usd": cost_usd,
            }
            # Compute chat-level duration delta, prefer provided duration_sec
            chat_duration_delta = max(0.0, duration_sec)
            if chat_duration_delta <= 0:
                try:
                    prev_ts_doc = await stats_coll.find_one({"_id": summary_id}, {f"chat_sessions.{chat_id}.last_event_ts": 1})
                    prev_session = None
                    if prev_ts_doc:
                        prev_cs_map = prev_ts_doc.get("chat_sessions") or {}
                        prev_session = prev_cs_map.get(chat_id) or {}
                    prev_ts_val = prev_session.get("last_event_ts") if prev_session else None  # type: ignore
                    if isinstance(prev_ts_val, datetime):
                        chat_duration_delta = max(0.0, (event_ts - prev_ts_val).total_seconds())  # type: ignore
                except Exception:
                    chat_duration_delta = 0.0
            if chat_duration_delta > 0:
                inc_ops[f"chat_sessions.{chat_id}.duration_sec"] = chat_duration_delta
            await stats_coll.update_one({"_id": summary_id}, {"$inc": inc_ops, "$set": {"last_updated_at": now, f"chat_sessions.{chat_id}.last_event_ts": event_ts}})

            # Also reflect usage counters directly inside ChatSessions doc so rollup recompute stays consistent
            chat_coll = await (self._general_coll() if session_type == "general" else self._coll())
            await chat_coll.update_one(
                {"_id": chat_id, **build_app_scope_filter(str(resolved_app_id))},
                {"$inc": {
                    "usage_prompt_tokens_final": prompt_tokens,
                    "usage_completion_tokens_final": completion_tokens,
                    "usage_total_tokens_final": total_tokens,
                    "usage_total_cost_final": cost_usd,
                }, "$set": {"last_updated_at": now, "app_id": resolved_app_id}}
            )

            # Per-agent session metrics (with duration accumulation based on event timestamp)
            if agent_name:
                # Seed agent container & agent.session container if absent
                await stats_coll.update_one(
                    {"_id": summary_id, f"agents.{agent_name}": {"$exists": False}},
                    {"$set": {f"agents.{agent_name}": {
                        "avg": {
                            "avg_duration_sec": 0.0,
                            "avg_prompt_tokens": 0,
                            "avg_completion_tokens": 0,
                            "avg_total_tokens": 0,
                            "avg_cost_total_usd": 0.0,
                        },
                        "sessions": {}
                    }}}
                )
                await stats_coll.update_one(
                    {"_id": summary_id, f"agents.{agent_name}.sessions.{chat_id}": {"$exists": False}},
                    {"$set": {f"agents.{agent_name}.sessions.{chat_id}": {
                        "duration_sec": 0.0,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                        "cost_total_usd": 0.0
                    }}}
                )
                agent_inc = {
                    f"agents.{agent_name}.sessions.{chat_id}.prompt_tokens": prompt_tokens,
                    f"agents.{agent_name}.sessions.{chat_id}.completion_tokens": completion_tokens,
                    f"agents.{agent_name}.sessions.{chat_id}.total_tokens": total_tokens,
                    f"agents.{agent_name}.sessions.{chat_id}.cost_total_usd": cost_usd,
                }
                # Compute duration delta using last_event_ts (stored per agent session)
                duration_delta = max(0.0, duration_sec)
                if duration_delta <= 0:
                    try:
                        prev_ts_doc = await stats_coll.find_one({"_id": summary_id}, {f"agents.{agent_name}.sessions.{chat_id}.last_event_ts": 1})
                        prev_session = None
                        if prev_ts_doc:
                            prev_agents = prev_ts_doc.get("agents") or {}
                            prev_agent = prev_agents.get(agent_name) or {}
                            prev_sessions = prev_agent.get("sessions") or {}
                            prev_session = prev_sessions.get(chat_id) or {}
                        prev_ts_val = prev_session.get("last_event_ts") if prev_session else None  # type: ignore
                        if isinstance(prev_ts_val, datetime):
                            duration_delta = max(0.0, (event_ts - prev_ts_val).total_seconds())  # type: ignore
                        else:
                            duration_delta = 0.0
                    except Exception:
                        duration_delta = 0.0
                if duration_delta > 0:
                    agent_inc[f"agents.{agent_name}.sessions.{chat_id}.duration_sec"] = duration_delta
                # Apply increments and set last_event_ts
                await stats_coll.update_one({"_id": summary_id}, {"$inc": agent_inc, "$set": {f"agents.{agent_name}.sessions.{chat_id}.last_event_ts": event_ts}})

            # Recompute averages (simple read & aggregate) -- small doc so acceptable.
            doc = await stats_coll.find_one({"_id": summary_id}, {"chat_sessions": 1, "agents": 1})
            if doc and isinstance(doc.get("chat_sessions"), dict):
                cs = doc["chat_sessions"]
                n = len(cs) if cs else 0
                if n:
                    total_prompt = sum(int(v.get("prompt_tokens", 0)) for v in cs.values())
                    total_completion = sum(int(v.get("completion_tokens", 0)) for v in cs.values())
                    total_total = sum(int(v.get("total_tokens", 0)) for v in cs.values())
                    total_cost = sum(float(v.get("cost_total_usd", 0.0)) for v in cs.values())
                    total_duration = sum(float(v.get("duration_sec", 0.0)) for v in cs.values())
                    await stats_coll.update_one(
                        {"_id": summary_id},
                        {"$set": {
                            "overall_avg.avg_prompt_tokens": int(total_prompt / n),
                            "overall_avg.avg_completion_tokens": int(total_completion / n),
                            "overall_avg.avg_total_tokens": int(total_total / n),
                            "overall_avg.avg_cost_total_usd": (total_cost / n),
                            "overall_avg.avg_duration_sec": (total_duration / n),
                        }}
                    )
            if agent_name:
                doc = await stats_coll.find_one({"_id": summary_id}, {f"agents.{agent_name}": 1})
                ag = doc.get("agents", {}).get(agent_name) if doc else None
                if ag and isinstance(ag.get("sessions"), dict):
                    sess_map = ag["sessions"]
                    an = len(sess_map)
                    if an:
                        ap = sum(int(v.get("prompt_tokens", 0)) for v in sess_map.values())
                        ac = sum(int(v.get("completion_tokens", 0)) for v in sess_map.values())
                        at = sum(int(v.get("total_tokens", 0)) for v in sess_map.values())
                        acost = sum(float(v.get("cost_total_usd", 0.0)) for v in sess_map.values())
                        adur = sum(float(v.get("duration_sec", 0.0)) for v in sess_map.values())
                        await stats_coll.update_one({"_id": summary_id}, {"$set": {
                            f"agents.{agent_name}.avg.avg_prompt_tokens": int(ap / an),
                            f"agents.{agent_name}.avg.avg_completion_tokens": int(ac / an),
                            f"agents.{agent_name}.avg.avg_total_tokens": int(at / an),
                            f"agents.{agent_name}.avg.avg_cost_total_usd": (acost / an),
                            f"agents.{agent_name}.avg.avg_duration_sec": (adur / an),
                        }})
        except Exception as e:  # pragma: no cover
            logger.error(f"Failed to update session metrics for {chat_id}: {e}")


#############################################
    # used for generate_and_download
#############################################
    @staticmethod
    def _extract_json_from_text(text: Any, agent_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
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
                    logger.info(f"[JSON_PARSE] {agent_name}: text is None")
                return None
            if isinstance(text, dict):
                if agent_name:
                    logger.info(f"[JSON_PARSE] {agent_name}: already a dict")
                return text
            if isinstance(text, list):
                if agent_name:
                    logger.info(f"[JSON_PARSE] {agent_name}: text is a list, returning None")
                return None
            
            s = text if isinstance(text, str) else str(text)
            s_strip = s.strip()
            
            if agent_name:
                logger.info(f"[JSON_PARSE] {agent_name}: original length={len(s)}, stripped length={len(s_strip)}")
            
            # CLEANING STEP 1: Remove Markdown code fences
            if s_strip.startswith("```") and "```" in s_strip[3:]:
                # Find the closing ```
                end_fence = s_strip.find("```", 3)
                s_strip = s_strip[3:end_fence].strip()
                if agent_name:
                    logger.info(f"[JSON_PARSE] {agent_name}: removed markdown fences, new length={len(s_strip)}")
            
            # CLEANING STEP 2: Remove "json" or "JSON" prefix if present
            if s_strip.lower().startswith("json"):
                s_strip = s_strip[4:].strip()
                if agent_name:
                    logger.info(f"[JSON_PARSE] {agent_name}: removed json prefix")
            
            # CLEANING STEP 3: Find JSON boundaries (first { or [ to last } or ])
            json_start = s_strip.find("{") if "{" in s_strip else s_strip.find("[")
            if json_start != -1:
                # Find last closing bracket
                json_end = s_strip.rfind("}") if "}" in s_strip else s_strip.rfind("]")
                if json_end != -1:
                    s_strip = s_strip[json_start:json_end + 1]
                    if agent_name:
                        logger.info(f"[JSON_PARSE] {agent_name}: extracted JSON boundaries, length={len(s_strip)}")
            
            # CLEANING STEP 4: Remove trailing commas before closing brackets (invalid JSON)
            import re
            s_strip = re.sub(r',\s*([\]}])', r'\1', s_strip)
            
            # Now try to parse the cleaned JSON
            decoder = json.JSONDecoder()
            idx = 0
            length = len(s_strip)
            while idx < length:
                brace_idx = s_strip.find("{", idx)
                if brace_idx == -1:
                    if agent_name:
                        logger.info(f"[JSON_PARSE] {agent_name}: no opening brace found")
                    return None
                try:
                    obj, end_idx = decoder.raw_decode(s_strip, brace_idx)
                    if isinstance(obj, dict):
                        if agent_name:
                            logger.info(f"[JSON_PARSE] {agent_name}: ✓ Successfully parsed JSON")
                        return obj
                    idx = end_idx
                    continue
                except json.JSONDecodeError as e:
                    if agent_name and idx == 0:  # Only log first attempt
                        logger.info(f"[JSON_PARSE] {agent_name}: JSONDecodeError at pos {e.pos}: {e.msg}, content preview: {s_strip[max(0, e.pos-50):e.pos+50]}")
                    idx = brace_idx + 1
                    continue
            if agent_name:
                logger.info(f"[JSON_PARSE] {agent_name}: exhausted all parse attempts")
            return None
        except Exception as ex:
            if agent_name:
                logger.info(f"[JSON_PARSE] {agent_name}: exception during parse: {ex}")
            return None

    @staticmethod
    def _normalize_structured_output(agent_name: Optional[str], payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict) or not agent_name:
            return payload

        adjusted = deepcopy(payload)

        try:
            if agent_name == "ToolsManagerAgent":
                tools = adjusted.get("tools")
                if isinstance(tools, list):
                    mutated = False
                    for entry in tools:
                        if not isinstance(entry, dict):
                            continue
                        ui_meta = entry.get("ui")
                        tool_type = entry.get("tool_type")
                        has_ui_component = isinstance(ui_meta, dict) and ui_meta.get("component")
                        if has_ui_component and tool_type != "UI_Tool":
                            entry["tool_type"] = "UI_Tool"
                            mutated = True
                        if not has_ui_component and tool_type == "UI_Tool":
                            entry["tool_type"] = "Agent_Tool"
                            mutated = True
                    if mutated:
                        logger.warning("[SAVE_EVENT] Coerced tool_type values for ToolsManagerAgent manifest")
        except Exception as normalize_err:
            logger.debug(f"[SAVE_EVENT] Structured output normalization skipped agent={agent_name}: {normalize_err}")

        return adjusted

    async def gather_latest_agent_jsons(
        self,
        *,
        chat_id: str,
        app_id: Optional[str] = None,
        agent_names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        try:
            msgs = await self.resume_chat(chat_id, resolved_app_id) or []
            logger.info(f"[GATHER_AGENT_JSONS] chat_id={chat_id} app_id={resolved_app_id} msgs_count={len(msgs) if msgs else 0}")
            
            if not msgs:
                logger.warning(f"[GATHER_AGENT_JSONS] resume_chat returned empty/None for chat_id={chat_id}")
                return result
            
            def agent_name_from(m: Dict[str, Any]) -> str:
                if m.get("role") == "assistant":
                    return str(m.get("agent_name") or "").strip()
                return "user"
            
            if agent_names:
                wanted = {n.strip() for n in agent_names}
                logger.info(f"[GATHER_AGENT_JSONS] Filtering for specific agents: {wanted}")
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
                        logger.info(f"[GATHER_AGENT_JSONS] ✓ Extracted JSON from {nm} (via structured_output field)")
                        continue
                    
                    # PRIORITY 2: Try content field
                    js = self._extract_json_from_text(m.get("content"))
                    if js is not None:
                        result[nm] = js
                        logger.info(f"[GATHER_AGENT_JSONS] ✓ Extracted JSON from {nm} (via content field)")
                    else:
                        logger.warning(f"[GATHER_AGENT_JSONS] ✗ No JSON found in {nm} message")
                return result
            
            seen: set[str] = set()
            agents_found = []
            for m in reversed(msgs):
                if not isinstance(m, dict):
                    continue
                nm = agent_name_from(m)
                if not nm or nm in seen:
                    continue
                
                # Log each agent message we encounter
                role = m.get("role")
                content_preview = str(m.get("content", ""))[:100] if m.get("content") else "(empty)"
                logger.debug(f"[GATHER_AGENT_JSONS] Processing message: role={role} agent={nm} content_preview={content_preview}")
                
                # PRIORITY 1: Check structured_output field first (for agents with structured_outputs_required: true)
                structured_output = m.get("structured_output")
                if isinstance(structured_output, dict):
                    result[nm] = structured_output
                    seen.add(nm)
                    agents_found.append(nm)
                    logger.info(f"[GATHER_AGENT_JSONS] ✓ Extracted JSON from {nm} (via structured_output field)")
                    continue
                
                # PRIORITY 2: Try to extract JSON from content field (fallback)
                js = self._extract_json_from_text(m.get("content"))
                if js is not None:
                    result[nm] = js
                    seen.add(nm)
                    agents_found.append(nm)
                    logger.info(f"[GATHER_AGENT_JSONS] ✓ Extracted JSON from {nm} (via content field)")
                else:
                    # Log first 500 chars of content for failed extractions
                    content = m.get("content", "")
                    content_sample = str(content)[:500] if content else "(empty)"
                    logger.warning(f"[GATHER_AGENT_JSONS] ✗ No JSON found in {nm} message (role={role})")
                    logger.debug(f"[GATHER_AGENT_JSONS]    Content sample: {content_sample}")
            
            logger.info(f"[GATHER_AGENT_JSONS] Completed: found {len(result)} agents with valid JSON: {agents_found}")
            return result
        except Exception as e:  # pragma: no cover
            logger.error(f"[GATHER_AGENT_JSONS] Failed for chat_id={chat_id}: {e}", exc_info=True)
            return result

    # UI Tool Persistence -----------------------------------------------
    async def attach_ui_tool_metadata(
        self,
        *,
        chat_id: str,
        app_id: Optional[str] = None,
        event_id: str,
        metadata: Dict[str, Any]
    ) -> None:
        """Attach UI tool metadata to the most recent agent message.
        
        This enables UI tool state to persist across reconnections.
        When a UI tool is invoked, we store its configuration and state
        in the last agent message's metadata field.
        
        Args:
            chat_id: Chat session identifier
            app_id: App identifier (legacy: app_id)
            event_id: UI tool event identifier (for correlation)
            metadata: UI tool metadata (ui_tool_id, display, payload, etc.)
        """
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        try:
            coll = await self._coll()
            
            # Find the chat document first
            doc = await coll.find_one(
                {"_id": chat_id, **build_app_scope_filter(str(resolved_app_id))},
                {"messages": 1}
            )
            
            if not doc:
                logger.warning(f"[UI_TOOL_METADATA] Chat {chat_id} not found")
                return
            
            messages = doc.get("messages", [])
            if not messages:
                logger.warning(f"[UI_TOOL_METADATA] No messages in chat {chat_id}")
                return
            
            # Find the last assistant message index
            last_assistant_idx = None
            for idx in range(len(messages) - 1, -1, -1):
                msg = messages[idx]
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    last_assistant_idx = idx
                    break
            
            if last_assistant_idx is None:
                logger.warning(f"[UI_TOOL_METADATA] No assistant message found in {chat_id}")
                return
            
            # Update the specific message with ui_tool metadata
            result = await coll.update_one(
                {
                    "_id": chat_id,
                    **build_app_scope_filter(str(resolved_app_id)),
                },
                {
                    "$set": {
                        f"messages.{last_assistant_idx}.metadata": {
                            "ui_tool": metadata
                        }
                    }
                }
            )
            
            if result.modified_count > 0:
                logger.info(
                    f"[UI_TOOL_METADATA] Attached ui_tool metadata to message[{last_assistant_idx}] "
                    f"in {chat_id} (tool={metadata.get('ui_tool_id')}, event={event_id})"
                )
            else:
                logger.warning(f"[UI_TOOL_METADATA] Failed to update message in {chat_id}")
        except Exception as e:
            logger.error(f"[UI_TOOL_METADATA] Failed to attach metadata for {chat_id}: {e}", exc_info=True)

    async def update_ui_tool_completion(
        self,
        *,
        chat_id: str,
        app_id: Optional[str] = None,
        event_id: str,
        completed: bool,
        status: str
    ) -> None:
        """Update UI tool completion status in persisted message metadata.
        
        Called after a UI tool interaction completes to mark the tool as done.
        This ensures that on reconnect, completed inline components show
        a "Completed" chip instead of the interactive component.
        
        Args:
            chat_id: Chat session identifier
            app_id: App identifier (legacy: app_id)
            event_id: UI tool event identifier (for correlation)
            completed: Whether the tool interaction is complete
            status: Completion status ("completed", "dismissed", etc.)
        """
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        try:
            coll = await self._coll()
            
            # Find the message with matching ui_tool.event_id
            result = await coll.update_one(
                {
                    "_id": chat_id,
                    **build_app_scope_filter(str(resolved_app_id)),
                    "messages.metadata.ui_tool.event_id": event_id
                },
                {
                    "$set": {
                        "messages.$[elem].metadata.ui_tool.ui_tool_completed": completed,
                        "messages.$[elem].metadata.ui_tool.ui_tool_status": status,
                        "messages.$[elem].metadata.ui_tool.completed_at": datetime.now(UTC).isoformat()
                    }
                },
                array_filters=[
                    {"elem.metadata.ui_tool.event_id": event_id}
                ]
            )
            
            if result.modified_count > 0:
                logger.info(
                    f"[UI_TOOL_COMPLETE] Updated completion for event={event_id} "
                    f"in {chat_id} (completed={completed}, status={status})"
                )
            else:
                logger.warning(
                    f"[UI_TOOL_COMPLETE] No message found with ui_tool.event_id={event_id} "
                    f"in {chat_id}"
                )
        except Exception as e:
            logger.error(f"[UI_TOOL_COMPLETE] Failed to update completion for {chat_id}: {e}", exc_info=True)

#############################################
#############################################
