from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from mozaiksai.core.auth.dependencies import UserPrincipal

Document = Mapping[str, Any]
Query = Mapping[str, Any]
Projection = Mapping[str, Any]
SortSpec = Sequence[tuple[str, int]]
IndexSpec = Mapping[str, Any]


@dataclass(frozen=True)
class PersistencePrincipal:
    """Authenticated ownership identity, separate from requested dispatch scope.

    HTTP dispatch constructs this from a token-validated principal only. Host
    scope selection and action inputs must never populate these fields.
    """

    user_id: str
    workspace_id: str | None = None

    @classmethod
    def from_authenticated_user(cls, principal: UserPrincipal | None) -> PersistencePrincipal | None:
        """Capture validated HTTP claims before any host scope selection."""
        from mozaiksai.core.auth.dependencies import UserPrincipal

        if not isinstance(principal, UserPrincipal) or not principal.is_authenticated:
            return None
        return cls(user_id=principal.user_id, workspace_id=principal.workspace_id)


class PersistenceScopeError(PermissionError):
    """A persistence operation cannot satisfy the declared ownership policy."""


@runtime_checkable
class PersistenceCollection(Protocol):
    """Collection operations generated module repositories may depend on."""

    async def find_one(
        self,
        query: Query,
        projection: Projection | None = None,
    ) -> Document | None:
        ...

    async def find_many(
        self,
        query: Query,
        *,
        limit: int = 50,
        sort: SortSpec | None = None,
        projection: Projection | None = None,
    ) -> list[Document]:
        ...

    async def insert_one(self, document: Document) -> Any:
        ...

    async def update_one(
        self,
        query: Query,
        update: Mapping[str, Any],
        *,
        upsert: bool = False,
    ) -> Any:
        ...

    async def delete_one(self, query: Query) -> Any:
        ...

    async def delete_many(self, query: Query) -> Any:
        ...

    async def count(self, query: Query) -> int:
        ...

    async def aggregate(self, pipeline: Sequence[Mapping[str, Any]]) -> list[Document]:
        ...

    async def ensure_indexes(self, indexes: Sequence[IndexSpec]) -> list[Any]:
        """Return verified materialization results for every declared index."""

        ...


@runtime_checkable
class ModulePersistenceContext(Protocol):
    """App-scoped persistence access for a generated module."""

    @property
    def app_id(self) -> str:
        ...

    @property
    def principal(self) -> PersistencePrincipal | None:
        ...

    def collection(self, module_id: str, entity_name: str) -> PersistenceCollection:
        ...

    def scope_filter(self, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        ...

    async def ensure_indexes(self) -> None:
        ...


__all__ = [
    "Document",
    "IndexSpec",
    "ModulePersistenceContext",
    "PersistenceCollection",
    "PersistencePrincipal",
    "PersistenceScopeError",
    "Projection",
    "Query",
    "SortSpec",
]
