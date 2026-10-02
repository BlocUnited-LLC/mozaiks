from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from typing import Any

from mozaiksai.core.core_config import get_mongo_client

from .adapter import (
    IndexSpec,
    PersistencePrincipal,
    PersistenceScopeError,
    Projection,
    Query,
    SortSpec,
)
from .alias_collection import GuardedAliasCollection
from .intent_loader import DataContract
from .naming import collection_name_for, scope_filter_for, scope_metadata
from .ownership import (
    CollectionOwnership,
    collection_bindings,
    collection_ownership,
    owned_update,
    validate_owned_pipeline,
)
from .platform_modules import CollectionAccess, PlatformModuleDeclarations

DEFAULT_APP_DATABASE_NAME = "mozaiks_apps"
MAX_FIND_MANY_LIMIT = 100


def _default_database_name() -> str:
    return (
        os.getenv("MOZAIKS_APP_DATABASE_NAME")
        or os.getenv("MOZAIKS_APPS_DATABASE")
        or DEFAULT_APP_DATABASE_NAME
    ).strip() or DEFAULT_APP_DATABASE_NAME


class MongoPersistenceCollection:
    """Mongo collection boundary injected into module repositories."""

    def __init__(
        self,
        *,
        collection: Any,
        app_id: str,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        user_id: str | None = None,
        ownership: CollectionOwnership | None = None,
        principal: PersistencePrincipal | Callable[[], PersistencePrincipal | None] | None = None,
        restrict_aggregation: bool = False,
    ) -> None:
        self._collection = collection
        self._app_id = scope_metadata(app_id)["app_id"]
        self._scope_metadata = scope_metadata(
            app_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
        )
        self._ownership = ownership
        self._principal = principal
        self._restrict_aggregation = restrict_aggregation or ownership is not None
        # Identity comparison must not inherit a case-insensitive collection collation.
        self._options = {"collation": {"locale": "simple"}} if ownership is not None else {}

    @property
    def _owner_scope(self) -> dict[str, str]:
        principal = self._principal() if callable(self._principal) else self._principal
        return self._ownership.scope(principal) if self._ownership is not None else {}

    def _scoped_query(self, query: Query) -> dict[str, Any]:
        owner_scope = self._owner_scope
        if owner_scope:
            owner_field = next(iter(owner_scope))
            scope: dict[str, Any] = {
                "app_id": self._app_id, **owner_scope,
                # Mongo equality also matches array elements. A malformed old
                # owner array must not grant its row to several principals.
                "$nor": [
                    {field: {"$type": "array"}}
                    for field in ("app_id", owner_field)
                ],
            }
            # A filter repeating the scope's own identity adds no condition, and
            # Mongo cannot infer an upsert's fields from a path matched twice.
            domain = {
                key: value for key, value in dict(query or {}).items()
                if not (key in {"app_id", owner_field} and isinstance(value, str) and value == scope[key])
            }
            return {"$and": [scope, domain]} if domain else scope
        return scope_filter_for(self._app_id, dict(query or {}))

    async def find_one(
        self,
        query: Query,
        projection: Projection | None = None,
    ) -> Mapping[str, Any] | None:
        return await self._collection.find_one(self._scoped_query(query), projection, **self._options)  # type: ignore[no-any-return]

    async def find_many(
        self,
        query: Query,
        *,
        limit: int = 50,
        sort: SortSpec | None = None,
        projection: Projection | None = None,
    ) -> list[Mapping[str, Any]]:
        safe_limit = max(1, min(int(limit), MAX_FIND_MANY_LIMIT))
        cursor = self._collection.find(self._scoped_query(query), projection, **self._options)
        if sort:
            cursor = cursor.sort(list(sort))
        cursor = cursor.limit(safe_limit)
        return await cursor.to_list(length=safe_limit)  # type: ignore[no-any-return]

    async def insert_one(self, document: Mapping[str, Any]) -> Any:
        owner_scope = self._owner_scope
        for field, identity in owner_scope.items():
            if field in document and document[field] != identity:
                raise PersistenceScopeError(f"Document cannot override ownership field {field!r}")
        if "app_id" in document and document["app_id"] != self._app_id:
            raise ValueError("document app_id cannot override context app_id")
        metadata = self._scope_metadata
        if self._ownership is not None:
            principal = self._principal() if callable(self._principal) else self._principal
            assert principal is not None  # _owner_scope validates before any write.
            metadata = scope_metadata(
                self._app_id, tenant_id=metadata.get("tenant_id"),
                user_id=principal.user_id, workspace_id=principal.workspace_id,
            )
        scoped_document = {**dict(document), **metadata, **owner_scope}
        return await self._collection.insert_one(scoped_document)

    async def update_one(
        self,
        query: Query,
        update: Mapping[str, Any],
        *,
        upsert: bool = False,
    ) -> Any:
        owner_scope = self._owner_scope
        safe_update = (
            owned_update(update, scope={"app_id": self._app_id, **owner_scope}, upsert=upsert)
            if owner_scope else dict(update)
        )
        return await self._collection.update_one(
            self._scoped_query(query),
            safe_update,
            upsert=upsert,
            **self._options,
        )

    async def delete_one(self, query: Query) -> Any:
        return await self._collection.delete_one(self._scoped_query(query), **self._options)

    async def delete_many(self, query: Query) -> Any:
        return await self._collection.delete_many(self._scoped_query(query), **self._options)

    async def count(self, query: Query) -> int:
        return int(await self._collection.count_documents(self._scoped_query(query), **self._options))

    async def aggregate(self, pipeline: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        stages = deepcopy(list(pipeline or []))
        if self._restrict_aggregation:
            validate_owned_pipeline(stages)
        scoped_pipeline: list[Mapping[str, Any]] = [{"$match": self._scoped_query({})}]
        scoped_pipeline.extend(stages)
        cursor = self._collection.aggregate(scoped_pipeline, **self._options)
        return await cursor.to_list(length=None)  # type: ignore[no-any-return]

    async def ensure_indexes(self, indexes: Sequence[IndexSpec]) -> list[Any]:
        """Materialize and verify declared indexes before reporting readiness."""

        if not indexes:
            return []
        if self._owner_scope:
            raise PersistenceScopeError("Owned collection indexes must be managed by host startup")
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
        data_contract: DataContract | None = None,
        principal: PersistencePrincipal | Callable[[], PersistencePrincipal | None] | None = None,
        platform_modules: PlatformModuleDeclarations | None = None,
        module_id: str | None = None,
    ) -> None:
        """Bind persistence to the loaded data contract.

        With ``platform_modules``, ``module_id`` names the dispatching module,
        and its allow-list is composed from the workspace ``data_contract`` and
        the declarations of the platform modules mounted into the workspace.
        """
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
        self._principal = principal
        access = (
            platform_modules.access_for(module_id, data_contract)
            if platform_modules is not None
            else CollectionAccess(data_contract=data_contract)
        )
        contract = access.data_contract
        self._ownership = collection_ownership(contract, app_id=self.app_id, app_slug=app_slug)
        self._bindings = collection_bindings(contract) if contract is not None else None
        self._reserved = frozenset(
            collection_name_for(app_id=self.app_id, app_slug=app_slug, module_id=owner, entity_name=name)
            for owner, name in access.reserved_collections
        ) | access.reserved_literals
        self._literal_names = None if access.literal_names is None else access.literal_names | {
            collection_name_for(app_id=self.app_id, app_slug=app_slug, module_id=owner, entity_name=name)
            for (owner, _reference), name in (self._bindings or {}).items()
        }

    @property
    def principal(self) -> PersistencePrincipal | None:
        return self._principal() if callable(self._principal) else self._principal

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

    def collection_name(self, module_id: str, collection_name: str) -> str:
        if self._bindings is not None:
            try:
                collection_name = self._bindings[(module_id, collection_name)]
            except KeyError as exc:
                raise PersistenceScopeError(f"Undeclared collection {module_id}.{collection_name}") from exc
        storage_name = collection_name_for(
            app_id=self.app_id,
            app_slug=self._app_slug,
            module_id=module_id,
            entity_name=collection_name,
        )
        if storage_name in self._reserved:
            raise PersistenceScopeError(f"Collection {module_id}.{collection_name} belongs to a platform module")
        return storage_name

    def collection(self, module_id: str, collection_name: str) -> MongoPersistenceCollection:
        key = (module_id, collection_name)
        if key not in self._collections:
            collection_name = self.collection_name(module_id, collection_name)
            collection = self._client_handle()[self._database_name][collection_name]
            self._collections[key] = MongoPersistenceCollection(
                collection=collection,
                app_id=self.app_id,
                tenant_id=self._scope_metadata.get("tenant_id"),
                workspace_id=self._scope_metadata.get("workspace_id"),
                user_id=self._scope_metadata.get("user_id"),
                ownership=self._ownership.get(collection_name),
                principal=lambda: self.principal,
                restrict_aggregation=bool(self._ownership),
            )
        return self._collections[key]

    def literal_collection(self, collection_name: str) -> Any:
        """Resolve a host-owned app-data alias target by its Mongo collection name.

        Generated module repos should use ``collection(module_id, collection_name)``.
        App-data alias helpers use this method for explicit contract-declared
        collections such as hosted product records, shared aggregates, and
        migration/index targets that already own their own scope fields.
        Access to a declared owned collection is forbidden. Other aliases use
        a bounded Mongo facade when this app has ownership contracts, retaining
        their explicit app-data semantics, including assignment stores. Apps
        without owned collections retain raw alias handles. A collection a
        mounted platform module declares is unavailable to every other module,
        and a platform module reaches only the collections declared for it.
        """

        name = str(collection_name or "").strip()
        if not name:
            raise ValueError("collection_name is required")
        if name in self._reserved:
            raise PersistenceScopeError("Raw collection access is unavailable for platform module collections")
        if self._literal_names is not None and name not in self._literal_names:
            raise PersistenceScopeError(f"Undeclared collection {name}")
        if name in self._ownership:
            raise PersistenceScopeError("Raw collection access is unavailable for owned collections")
        collection = self._client_handle()[self._database_name][name]
        return GuardedAliasCollection(collection) if self._ownership else collection

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
