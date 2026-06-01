"""Framework-owned artifact persistence helpers.

This layer keeps factory/runtime code operating on named artifacts instead of
raw Mongo collection strings. It owns the canonical builder artifact collections
under the framework system database.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .namespaces import SYSTEM_DATABASE, BuilderCollections
from .persistence_manager import AG2PersistenceManager


IndexSpec = Tuple[Sequence[Tuple[str, int]], Dict[str, Any]]


class BuilderArtifactStore:
    """Artifact-centric persistence wrapper for factory_app and related runtime flows."""

    def __init__(self, pm: Optional[AG2PersistenceManager] = None) -> None:
        self._pm = pm or AG2PersistenceManager()

    async def _client(self):
        await self._pm.persistence._ensure_client()  # noqa: SLF001 - existing runtime pattern
        client = self._pm.persistence.client
        if client is None:
            raise RuntimeError("Mongo client not initialized")
        return client

    async def _collection(self, collection_name: str):
        client = await self._client()
        return client[SYSTEM_DATABASE][collection_name]

    async def _ensure_indexes(self, collection_name: str, specs: Iterable[IndexSpec]) -> None:
        coll = await self._collection(collection_name)
        try:
            existing = await coll.list_indexes().to_list(length=None)
            names = {item.get("name") for item in existing if isinstance(item, dict)}
        except Exception:
            names = set()
        for keys, kwargs in specs:
            name = kwargs.get("name")
            if name and name in names:
                continue
            await coll.create_index(list(keys), **kwargs)

    async def save_concept(
        self,
        *,
        app_id: str,
        concept_record: Dict[str, Any],
        created_at: Optional[str] = None,
    ) -> None:
        coll = await self._collection(BuilderCollections.CONCEPTS)
        await coll.update_one(
            {"app_id": str(app_id)},
            {"$set": dict(concept_record), "$setOnInsert": {"created_at": created_at or datetime.now(UTC).isoformat()}},
            upsert=True,
        )

    async def get_concept(self, *, app_id: str) -> Optional[Dict[str, Any]]:
        coll = await self._collection(BuilderCollections.CONCEPTS)
        doc = await coll.find_one({"app_id": str(app_id)})
        if not isinstance(doc, dict):
            return None
        doc.pop("_id", None)
        return doc

    async def save_build_plan(self, *, app_id: str, build_plan: Dict[str, Any]) -> None:
        coll = await self._collection(BuilderCollections.BUILD_PLANS)
        await coll.update_one(
            {"app_id": str(app_id)},
            {"$set": dict(build_plan)},
            upsert=True,
        )

    async def get_build_plan(self, *, app_id: str) -> Optional[Dict[str, Any]]:
        coll = await self._collection(BuilderCollections.BUILD_PLANS)
        doc = await coll.find_one({"app_id": str(app_id)})
        if not isinstance(doc, dict):
            return None
        doc.pop("_id", None)
        return doc

    async def get_theme_capture(self, *, app_id: str) -> Optional[Dict[str, Any]]:
        coll = await self._collection(BuilderCollections.THEME_CAPTURES)
        doc = await coll.find_one({"app_id": str(app_id)})
        if not isinstance(doc, dict):
            return None
        doc.pop("_id", None)
        return doc

    async def ensure_design_doc_indexes(self) -> None:
        await self._ensure_indexes(
            BuilderCollections.DESIGN_DOCUMENTS,
            [
                ((("app_id", 1), ("kind", 1)), {"unique": True, "name": "dd_app_kind"}),
            ],
        )

    async def ensure_data_contract_indexes(self) -> None:
        await self._ensure_indexes(
            BuilderCollections.DATA_CONTRACTS,
            [
                ((("app_id", 1), ("build_id", 1)), {"unique": True, "name": "dc_app_build"}),
                ((("app_id", 1), ("updated_at", -1)), {"name": "dc_app_updated"}),
            ],
        )

    async def ensure_workflow_export_indexes(self) -> None:
        await self._ensure_indexes(
            BuilderCollections.WORKFLOW_EXPORTS,
            [
                ((("app_id", 1), ("workflow_type", 1), ("created_at_utc", -1)), {"name": "wfe_app_type_created"}),
            ],
        )

    async def ensure_database_migration_indexes(self) -> None:
        await self._ensure_indexes(
            BuilderCollections.DATABASE_MIGRATIONS,
            [
                ((("app_id", 1), ("migration_id", 1)), {"unique": True, "name": "dbm_app_migration"}),
                ((("app_id", 1), ("build_id", 1), ("updated_at", -1)), {"name": "dbm_app_build_updated"}),
                ((("artifact_version_id", 1),), {"name": "dbm_artifact_version"}),
            ],
        )

    async def upsert_design_doc(
        self,
        *,
        app_id: str,
        user_id: Optional[str],
        kind: str,
        stage: str,
        content: str,
        source_workflow: str,
        source_chat_id: Optional[str],
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> None:
        await self.ensure_design_doc_indexes()
        coll = await self._collection(BuilderCollections.DESIGN_DOCUMENTS)
        now = datetime.now(UTC)
        set_fields: Dict[str, Any] = {
            "app_id": app_id,
            "user_id": user_id,
            "kind": kind,
            "stage": stage,
            "content": content,
            "status": "succeeded",
            "source": {"workflow": source_workflow, "chat_id": source_chat_id},
            "updated_at": now,
        }
        if extra_fields:
            set_fields.update(extra_fields)

        await coll.update_one(
            {"app_id": app_id, "kind": kind},
            {
                "$set": set_fields,
                "$setOnInsert": {"created_at": now},
                "$push": {
                    "revisions": {
                        "$each": [
                            {
                                "stage": stage,
                                "content": content,
                                "workflow": source_workflow,
                                "chat_id": source_chat_id,
                                "created_at": now,
                            }
                        ],
                        "$slice": -5,
                    }
                },
            },
            upsert=True,
        )

    async def get_design_doc(self, *, app_id: str, kind: str) -> Optional[Dict[str, Any]]:
        coll = await self._collection(BuilderCollections.DESIGN_DOCUMENTS)
        doc = await coll.find_one({"app_id": str(app_id), "kind": str(kind)})
        if not isinstance(doc, dict):
            return None
        doc.pop("_id", None)
        return doc

    async def list_design_docs(self, *, app_id: str) -> List[Dict[str, Any]]:
        coll = await self._collection(BuilderCollections.DESIGN_DOCUMENTS)
        cursor = coll.find({"app_id": str(app_id)})
        rows = await cursor.to_list(length=None)
        docs: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            doc = dict(row)
            doc.pop("_id", None)
            docs.append(doc)
        return docs

    async def mark_design_doc_status(
        self,
        *,
        app_id: str,
        user_id: Optional[str],
        stage: str,
        status: str,
        kinds: Sequence[str],
        error: Optional[str] = None,
    ) -> None:
        await self.ensure_design_doc_indexes()
        coll = await self._collection(BuilderCollections.DESIGN_DOCUMENTS)
        now = datetime.now(UTC)
        for kind in kinds:
            update: Dict[str, Any] = {
                "$set": {
                    "app_id": app_id,
                    "user_id": user_id,
                    "kind": kind,
                    "stage": stage,
                    "status": status,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            }
            if error:
                update["$set"]["error"] = error
            await coll.update_one({"app_id": app_id, "kind": kind}, update, upsert=True)

    async def save_data_contract(
        self,
        *,
        app_id: str,
        build_id: str,
        artifact_version_id: Optional[str],
        change_class: Optional[str],
        data_contract: Dict[str, Any],
        user_id: Optional[str],
        source_workflow: str,
        source_chat_id: Optional[str],
    ) -> None:
        await self.ensure_data_contract_indexes()
        coll = await self._collection(BuilderCollections.DATA_CONTRACTS)
        now = datetime.now(UTC)
        await coll.update_one(
            {"app_id": app_id, "build_id": build_id},
            {
                "$set": {
                    "app_id": app_id,
                    "build_id": build_id,
                    "artifact_version_id": artifact_version_id,
                    "change_class": change_class,
                    "data_contract": data_contract,
                    "status": "succeeded",
                    "user_id": user_id,
                    "source": {"workflow": source_workflow, "chat_id": source_chat_id},
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

    async def get_latest_data_contract(self, *, app_id: str) -> Optional[Dict[str, Any]]:
        coll = await self._collection(BuilderCollections.DATA_CONTRACTS)
        cursor = coll.find({"app_id": str(app_id)}).sort("updated_at", -1).limit(1)
        docs = await cursor.to_list(length=1)
        if not docs or not isinstance(docs[0], dict):
            return None
        doc = dict(docs[0])
        doc.pop("_id", None)
        return doc

    async def save_theme_capture(
        self,
        *,
        app_id: str,
        chat_id: Optional[str],
        app_url: Optional[str],
        identity: Dict[str, Any],
        theme_config: Dict[str, Any],
    ) -> None:
        coll = await self._collection(BuilderCollections.THEME_CAPTURES)
        now = datetime.now(UTC).isoformat()
        await coll.update_one(
            {"app_id": str(app_id)},
            {
                "$set": {
                    "app_id": str(app_id),
                    "chat_id": str(chat_id) if chat_id else None,
                    "app_url": app_url,
                    "identity": identity,
                    "theme_config": theme_config,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

    async def record_workflow_export(
        self,
        *,
        app_id: str,
        user_id: Optional[str],
        workflow_type: str,
        repo_url: Optional[str],
        job_id: Optional[str],
        meta: Optional[Dict[str, Any]] = None,
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        await self.ensure_workflow_export_indexes()
        now = datetime.now(UTC).isoformat()
        doc: Dict[str, Any] = {
            "app_id": app_id,
            "appId": app_id,
            "user_id": user_id,
            "userId": user_id,
            "workflow_type": workflow_type,
            "workflowType": workflow_type,
            "repo_url": repo_url,
            "repoUrl": repo_url,
            "job_id": job_id,
            "jobId": job_id,
            "meta": meta or {},
            "created_at_utc": now,
            "createdAt": now,
            "updated_at_utc": now,
            "updatedAt": now,
        }
        if extra_fields:
            doc.update(extra_fields)
        coll = await self._collection(BuilderCollections.WORKFLOW_EXPORTS)
        await coll.insert_one(doc)
        return doc

    async def get_latest_workflow_export(self, *, app_id: str, workflow_type: str) -> Optional[Dict[str, Any]]:
        await self.ensure_workflow_export_indexes()
        coll = await self._collection(BuilderCollections.WORKFLOW_EXPORTS)
        cursor = coll.find({"app_id": app_id, "workflow_type": workflow_type}).sort("_id", -1).limit(1)
        docs = await cursor.to_list(length=1)
        return docs[0] if docs else None

    async def save_database_migration(
        self,
        *,
        app_id: str,
        build_id: str,
        artifact_version_id: Optional[str],
        change_class: Optional[str],
        migration: Dict[str, Any],
        status: str,
        source_workflow: str,
        source_chat_id: Optional[str],
        generated_app_dir: Optional[str] = None,
        bundle_relative_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        await self.ensure_database_migration_indexes()
        coll = await self._collection(BuilderCollections.DATABASE_MIGRATIONS)
        now = datetime.now(UTC)
        migration_id = str(migration.get("migration_id") or "").strip()
        if not migration_id:
            raise ValueError("migration.migration_id is required")
        relative_path = bundle_relative_path or f"config/data_migrations/{migration_id}.json"
        doc: Dict[str, Any] = {
            "app_id": app_id,
            "build_id": build_id,
            "migration_id": migration_id,
            "artifact_version_id": artifact_version_id,
            "change_class": change_class,
            "status": status,
            "bundle_relative_path": relative_path,
            "generated_app_dir": generated_app_dir,
            "source": {"workflow": source_workflow, "chat_id": source_chat_id},
            "migration": migration,
            "updated_at": now,
        }
        await coll.update_one(
            {"app_id": app_id, "migration_id": migration_id},
            {"$set": doc, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        return doc


__all__ = ["BuilderArtifactStore"]

