from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set

from logs.logging_config import get_core_logger
from mozaiksai.core.data.persistence import AG2PersistenceManager
from mozaiksai.core.multitenant import build_app_scope_filter, coalesce_app_id, dual_write_app_scope

logger = get_core_logger("mfj_persistence")


class MFJCompletionStore:
    """Persistence store for MFJ completion checkpoints.

    Records are keyed by `(app_id, parent_chat_id, trigger_id)` so `requires`
    checks can survive process restarts.
    """

    def __init__(self, *, collection_name: str = "MFJCompletions", ttl_seconds: int = 7 * 24 * 3600) -> None:
        self._collection_name = str(collection_name or "MFJCompletions").strip() or "MFJCompletions"
        self._ttl_seconds = max(1, int(ttl_seconds))
        self._pm = AG2PersistenceManager()
        self._indexes_ready = False

    async def _coll(self) -> Any:
        await self._pm.persistence._ensure_client()  # noqa: SLF001
        client = self._pm.persistence.client  # noqa: SLF001
        if client is None:
            raise RuntimeError("Mongo client is not initialized")
        return client["MozaiksAI"][self._collection_name]

    async def ensure_indexes(self) -> None:
        if self._indexes_ready:
            return
        try:
            coll = await self._coll()
            existing = await coll.list_indexes().to_list(length=None)
            existing_names = {idx.get("name") for idx in existing if isinstance(idx, dict)}

            if "mfj_ttl_completed_at" not in existing_names:
                await coll.create_index(
                    [("completed_at", 1)],
                    name="mfj_ttl_completed_at",
                    expireAfterSeconds=self._ttl_seconds,
                )

            if "mfj_app_parent_trigger" not in existing_names:
                await coll.create_index(
                    [("app_id", 1), ("parent_chat_id", 1), ("trigger_id", 1)],
                    name="mfj_app_parent_trigger",
                    unique=True,
                )

            if "mfj_app_parent_completed_desc" not in existing_names:
                await coll.create_index(
                    [("app_id", 1), ("parent_chat_id", 1), ("completed_at", -1)],
                    name="mfj_app_parent_completed_desc",
                )
        except Exception as exc:
            logger.debug("[MFJ_STORE] ensure_indexes failed: %s", exc)
        finally:
            self._indexes_ready = True

    async def write_completion(
        self,
        *,
        app_id: str,
        parent_chat_id: str,
        trigger_id: str,
        mfj_cycle: int,
        child_count: int,
        succeeded_count: int,
        failed_count: int,
        child_chat_ids: Sequence[str],
        merge_summary_preview: Optional[Dict[str, Any]] = None,
    ) -> None:
        scope = coalesce_app_id(app_id=app_id)
        parent = str(parent_chat_id or "").strip()
        trigger = str(trigger_id or "").strip()
        if not scope or not parent or not trigger:
            return

        try:
            await self.ensure_indexes()
            coll = await self._coll()
            now = datetime.now(timezone.utc)
            doc: Dict[str, Any] = {
                "parent_chat_id": parent,
                "trigger_id": trigger,
                "mfj_cycle": int(max(1, mfj_cycle)),
                "child_count": int(max(0, child_count)),
                "succeeded_count": int(max(0, succeeded_count)),
                "failed_count": int(max(0, failed_count)),
                "child_chat_ids": [str(cid) for cid in child_chat_ids if str(cid).strip()],
                "merge_summary_preview": merge_summary_preview or {},
                "completed_at": now,
                "updated_at": now,
            }
            doc = dual_write_app_scope(doc, scope)
            await coll.update_one(
                {"parent_chat_id": parent, "trigger_id": trigger, **build_app_scope_filter(scope)},
                {"$set": doc, "$setOnInsert": {"created_at": now}},
                upsert=True,
            )
        except Exception as exc:
            logger.debug("[MFJ_STORE] write_completion failed app=%s parent=%s trigger=%s: %s", scope, parent, trigger, exc)

    async def load_completed_trigger_ids(self, *, app_id: str, parent_chat_id: str) -> Set[str]:
        scope = coalesce_app_id(app_id=app_id)
        parent = str(parent_chat_id or "").strip()
        if not scope or not parent:
            return set()

        try:
            await self.ensure_indexes()
            coll = await self._coll()
            cursor = coll.find(
                {"parent_chat_id": parent, **build_app_scope_filter(scope)},
                projection={"trigger_id": 1},
            )
            trigger_ids: Set[str] = set()
            async for row in cursor:
                if not isinstance(row, dict):
                    continue
                trigger = str(row.get("trigger_id") or "").strip()
                if trigger:
                    trigger_ids.add(trigger)
            return trigger_ids
        except Exception as exc:
            logger.debug("[MFJ_STORE] load_completed_trigger_ids failed app=%s parent=%s: %s", scope, parent, exc)
            return set()

    async def load_completions_for_parents(
        self,
        *,
        app_id: str,
        parent_chat_ids: Sequence[str],
    ) -> Dict[str, Set[str]]:
        scope = coalesce_app_id(app_id=app_id)
        parents = [str(pid).strip() for pid in parent_chat_ids if str(pid).strip()]
        if not scope or not parents:
            return {}

        try:
            await self.ensure_indexes()
            coll = await self._coll()
            cursor = coll.find(
                {"parent_chat_id": {"$in": parents}, **build_app_scope_filter(scope)},
                projection={"parent_chat_id": 1, "trigger_id": 1},
            )
            output: Dict[str, Set[str]] = {}
            async for row in cursor:
                if not isinstance(row, dict):
                    continue
                parent = str(row.get("parent_chat_id") or "").strip()
                trigger = str(row.get("trigger_id") or "").strip()
                if not parent or not trigger:
                    continue
                output.setdefault(parent, set()).add(trigger)
            return output
        except Exception as exc:
            logger.debug("[MFJ_STORE] load_completions_for_parents failed app=%s: %s", scope, exc)
            return {}

    async def load_recent_parent_ids(self, *, app_id: str, limit: int = 200) -> List[str]:
        scope = coalesce_app_id(app_id=app_id)
        if not scope:
            return []

        try:
            await self.ensure_indexes()
            coll = await self._coll()
            cursor = coll.find(
                build_app_scope_filter(scope),
                projection={"parent_chat_id": 1},
                sort=[("completed_at", -1)],
                limit=max(1, int(limit)),
            )
            out: List[str] = []
            seen: Set[str] = set()
            async for row in cursor:
                if not isinstance(row, dict):
                    continue
                parent = str(row.get("parent_chat_id") or "").strip()
                if not parent or parent in seen:
                    continue
                seen.add(parent)
                out.append(parent)
            return out
        except Exception as exc:
            logger.debug("[MFJ_STORE] load_recent_parent_ids failed app=%s: %s", scope, exc)
            return []


__all__ = ["MFJCompletionStore"]

