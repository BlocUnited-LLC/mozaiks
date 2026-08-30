from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any

from mozaiksai.core.core_config import get_mongo_client

from .adapter import IndexSpec, Projection, Query, SortSpec
from .naming import collection_name_for, scope_filter_for, scope_metadata

DEFAULT_APP_DATABASE_NAME = "mozaiks_apps"
MAX_FIND_MANY_LIMIT = 100


def _default_database_name() -> str:
    return (
        os.getenv("MOZAIKS_APP_DATABASE_NAME")
        or os.getenv("MOZAIKS_APPS_DATABASE")
        or DEFAULT_APP_DATABASE_NAME
    ).strip() or DEFAULT_APP_DATABASE_NAME


class MongoPersistenceCollection:
    """Mongo-backed collection wrapper for future generated module repositories.

    Generated code should eventually access this through ``ctx.persistence``, not
    by importing ``get_mongo_client()`` directly. This adapter is not injected
    into ModuleContext yet.
    """

    def __init__(
        self,
        *,
        collection: Any,
        app_id: str,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        self._collection = collection
        self._app_id = scope_metadata(app_id)["app_id"]
        self._scope_metadata = scope_metadata(
            app_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
        )

    def _scoped_query(self, query: Query) -> dict[str, Any]:
        return scope_filter_for(self._app_id, dict(query or {}))

    async def find_one(
        self,
        query: Query,
        projection: Projection | None = None,
    ) -> Mapping[str, Any] | None:
        return await self._collection.find_one(self._scoped_query(query), projection)  # type: ignore[no-any-return]

    async def find_many(
        self,
        query: Query,
        *,
        limit: int = 50,
        sort: SortSpec | None = None,
        projection: Projection | None = None,
    ) -> list[Mapping[str, Any]]:
        safe_limit = max(1, min(int(limit), MAX_FIND_MANY_LIMIT))
        cursor = self._collection.find(self._scoped_query(query), projection)
        if sort:
            cursor = cursor.sort(list(sort))
        cursor = cursor.limit(safe_limit)
        return await cursor.to_list(length=safe_limit)  # type: ignore[no-any-return]

    async def insert_one(self, document: Mapping[str, Any]) -> Any:
        if "app_id" in document and document["app_id"] != self._app_id:
            raise ValueError("document app_id cannot override context app_id")
        scoped_document = {**dict(document), **self._scope_metadata}
        return await self._collection.insert_one(scoped_document)

    async def update_one(
        self,
        query: Query,
        update: Mapping[str, Any],
        *,
        upsert: bool = False,
    ) -> Any:
        return await self._collection.update_one(
            self._scoped_query(query),
            dict(update),
            upsert=upsert,
        )

    async def delete_one(self, query: Query) -> Any:
        return await self._collection.delete_one(self._scoped_query(query))

    async def delete_many(self, query: Query) -> Any:
        return await self._collection.delete_many(self._scoped_query(query))

    async def count(self, query: Query) -> int:
        return int(await self._collection.count_documents(self._scoped_query(query)))

    async def aggregate(self, pipeline: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        scoped_pipeline: list[Mapping[str, Any]] = [{"$match": self._scoped_query({})}]
        scoped_pipeline.extend(dict(stage) for stage in pipeline or [])
        cursor = self._collection.aggregate(scoped_pipeline)
        return await cursor.to_list(length=None)  # type: ignore[no-any-return]

    async def ensure_indexes(self, indexes: Sequence[IndexSpec]) -> list[Any]:
        """Materialize and verify declared indexes before reporting readiness."""

        if not indexes:
            return []
        from .indexes import _ensure_raw_collection_indexes

        return await _ensure_raw_collection_indexes(
            self._collection,
            [dict(spec) for spec in indexes],
            collection_label=str(getattr(self._collection, "name", "collection")),
        )


class MongoPersistenceContext:
    """Mongo-backed generated-module persistence context.

    This is the concrete adapter injected into ModuleContext for generated
    module repos. Generated code uses ``ctx.persistence`` rather than direct
    Mongo client access.
    """

    def __init__(
        self,
        *,
        app_id: str,
        app_slug: str | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        user_id: str | None = None,
        database_name: str | None = None,
        client: Any | None = None,
    ) -> None:
        self._scope_metadata = scope_metadata(
            app_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
        )
        self._app_slug = app_slug
        self._database_name = (database_name or _default_database_name()).strip() or DEFAULT_APP_DATABASE_NAME
        self._client = client
        self._collections: dict[tuple[str, str], MongoPersistenceCollection] = {}

    @property
    def app_id(self) -> str:
        return self._scope_metadata["app_id"]

    @property
    def database_name(self) -> str:
        return self._database_name

    def _client_handle(self) -> Any:
        if self._client is None:
            self._client = get_mongo_client()
        return self._client

    def collection_name(self, module_id: str, entity_name: str) -> str:
        return collection_name_for(
            app_id=self.app_id,
            app_slug=self._app_slug,
            module_id=module_id,
            entity_name=entity_name,
        )

    def collection(self, module_id: str, entity_name: str) -> MongoPersistenceCollection:
        key = (module_id, entity_name)
        if key not in self._collections:
            collection_name = self.collection_name(module_id, entity_name)
            collection = self._client_handle()[self._database_name][collection_name]
            self._collections[key] = MongoPersistenceCollection(
                collection=collection,
                app_id=self.app_id,
                tenant_id=self._scope_metadata.get("tenant_id"),
                workspace_id=self._scope_metadata.get("workspace_id"),
                user_id=self._scope_metadata.get("user_id"),
            )
        return self._collections[key]

    def literal_collection(self, collection_name: str) -> Any:
        """Return a raw app-data collection by its literal Mongo collection name.

        Generated module repos should use ``collection(module_id, entity_name)``.
        App-data alias helpers use this method for explicit contract-declared
        collections such as hosted product records, shared aggregates, and
        migration/index targets that already own their own scope fields.
        """

        name = str(collection_name or "").strip()
        if not name:
            raise ValueError("collection_name is required")
        return self._client_handle()[self._database_name][name]

    def scope_filter(self, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return scope_filter_for(self.app_id, extra)

    async def ensure_indexes(self) -> None:
        """Reserved for database intent driven index setup in a later phase."""

        return None


__all__ = [
    "DEFAULT_APP_DATABASE_NAME",
    "MAX_FIND_MANY_LIMIT",
    "MongoPersistenceCollection",
    "MongoPersistenceContext",
]
