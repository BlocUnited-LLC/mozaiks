from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from logs.logging_config import get_core_logger

if TYPE_CHECKING:
    from mozaiksai.core.auth.dependencies import UserPrincipal
    from mozaiksai.core.auth.websocket_auth import WebSocketUser

logger = get_core_logger("persistence.principal")

Document = Mapping[str, Any]
Query = Mapping[str, Any]
Projection = Mapping[str, Any]
SortSpec = Sequence[tuple[str, int]]
IndexSpec = Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PersistencePrincipal:
    """Authenticated ownership identity, separate from requested dispatch scope.

    Runtime authentication or an explicit local development identity supplies
    the actor. ``tenant_id`` is the tenant the validated credential is bound
    to; requested dispatch scope never sets it. Only a host's verified
    membership assertion may select tenant or workspace. Persistence
    ownership reads user and workspace; module entitlement lookups read user,
    tenant and workspace.
    """

    user_id: str
    workspace_id: str | None = None
    source: Literal["authenticated", "development"] = "authenticated"
    tenant_id: str | None = None

    @classmethod
    def from_authenticated_user(cls, principal: UserPrincipal | None) -> PersistencePrincipal | None:
        """Capture HTTP identity before any host scope selection."""
        from mozaiksai.core.auth.adapters import AuthError
        from mozaiksai.core.auth.adapters.registry import is_auth_enabled
        from mozaiksai.core.auth.dependencies import UserPrincipal

        if not isinstance(principal, UserPrincipal):
            return None
        try:
            auth_enabled = is_auth_enabled()
        except AuthError:
            return None
        if principal.is_authenticated and auth_enabled:
            return cls(
                user_id=principal.user_id,
                workspace_id=principal.workspace_id,
                tenant_id=principal.tenant_id or None,
            )
        return cls._development(principal.user_id)

    @classmethod
    def from_websocket_user(cls, principal: WebSocketUser | None) -> PersistencePrincipal | None:
        """Capture the server-bound socket identity, never UI context fields."""
        import math
        import time

        from mozaiksai.core.auth.adapters import AuthError
        from mozaiksai.core.auth.adapters.registry import is_auth_enabled
        from mozaiksai.core.auth.websocket_auth import WebSocketUser

        if not isinstance(principal, WebSocketUser):
            return None
        try:
            auth_enabled = is_auth_enabled()
        except AuthError:
            return None
        if not auth_enabled:
            return cls._development(principal.user_id)
        if principal.provider == "none":
            return None
        expires = principal.raw_claims.get("exp")
        if expires is not None:
            try:
                if not math.isfinite(float(expires)) or float(expires) <= time.time():
                    return None
            except (TypeError, ValueError):
                return None
        return cls(
            user_id=principal.user_id,
            workspace_id=principal.workspace_id,
            tenant_id=principal.tenant_id or None,
        )

    @classmethod
    def _development(cls, user_id: str) -> PersistencePrincipal | None:
        """Ownership identity of an unauthenticated principal.

        Only an explicit disable in an environment that permits it qualifies;
        implicit demo mode owns nothing. The user id is the principal's: a
        caller-named user only with development access, otherwise the fixed
        anonymous visitor id.
        """
        from mozaiksai.core.auth.adapters import AuthError
        from mozaiksai.core.auth.adapters.registry import resolve_auth_config

        try:
            config = resolve_auth_config()
        except AuthError:
            return None
        if not (config.explicitly_disabled and config.environment.permits_no_auth):
            return None
        logger.warning(
            "PERSISTENCE_DEVELOPMENT_PRINCIPAL user_id=%s workspace_id=development", user_id,
        )
        return cls(user_id=user_id, workspace_id="development", source="development")

    def with_host_scope(self, scope: Mapping[str, Any]) -> PersistencePrincipal:
        """Apply the hook registry's explicit, host-verified membership assertions.

        Only the registry's private verified keys are read. A plain requested
        ``tenant_id`` or ``workspace_id`` in the same scope never changes the
        principal.
        """
        asserted = {
            field: scope[key]
            for key, field in (("_verified_tenant_id", "tenant_id"), ("_verified_workspace_id", "workspace_id"))
            if key in scope
        }
        return replace(self, **asserted) if asserted else self


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

    def collection(self, module_id: str, collection_name: str) -> PersistenceCollection:
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
