"""OIDC discovery document client with in-memory caching."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class OIDCDiscoveryDocument:
    """Resolved OIDC discovery details required for JWT validation."""

    issuer: str
    jwks_uri: str
    raw: dict[str, Any]


class OIDCDiscoveryClient:
    """Fetch and cache OIDC discovery documents."""

    def __init__(
        self,
        discovery_url: str,
        *,
        cache_ttl_seconds: int = 3600,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not discovery_url.startswith(("http://", "https://")):
            raise ValueError("discovery_url must use http:// or https://")
        self._discovery_url = discovery_url
        self._cache_ttl_seconds = cache_ttl_seconds
        self._timeout_seconds = timeout_seconds
        self._lock = asyncio.Lock()
        self._cached_document: OIDCDiscoveryDocument | None = None
        self._expires_at: float = 0.0

    async def get_document(self, *, force_refresh: bool = False) -> OIDCDiscoveryDocument:
        """Return a cached document or fetch a fresh one."""

        now = time.time()
        if not force_refresh and self._cached_document is not None and now < self._expires_at:
            return self._cached_document

        async with self._lock:
            now = time.time()
            if not force_refresh and self._cached_document is not None and now < self._expires_at:
                return self._cached_document

            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.get(self._discovery_url)
                response.raise_for_status()
                payload: dict[str, Any] = response.json()

            issuer = payload.get("issuer")
            jwks_uri = payload.get("jwks_uri")
            if not isinstance(issuer, str) or not issuer:
                raise RuntimeError("OIDC discovery document is missing issuer.")
            if not isinstance(jwks_uri, str) or not jwks_uri:
                raise RuntimeError("OIDC discovery document is missing jwks_uri.")

            self._cached_document = OIDCDiscoveryDocument(
                issuer=issuer,
                jwks_uri=jwks_uri,
                raw=payload,
            )
            self._expires_at = now + self._cache_ttl_seconds
            return self._cached_document

    def clear_cache(self) -> None:
        """Drop the cached discovery document."""

        self._cached_document = None
        self._expires_at = 0.0


__all__ = ["OIDCDiscoveryClient", "OIDCDiscoveryDocument"]
