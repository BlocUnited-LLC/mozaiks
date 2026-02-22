"""JWKS client with keyed lookup and in-memory caching."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx


class JWKSClient:
    """Fetch and cache JSON Web Key Sets."""

    def __init__(
        self,
        jwks_url: str | None = None,
        *,
        cache_ttl_seconds: int = 3600,
        timeout_seconds: float = 10.0,
    ) -> None:
        if jwks_url and not jwks_url.startswith(("http://", "https://")):
            raise ValueError("jwks_url must use http:// or https://")
        self._jwks_url = jwks_url
        self._cache_ttl_seconds = cache_ttl_seconds
        self._timeout_seconds = timeout_seconds
        self._lock = asyncio.Lock()
        self._cached_jwks: dict[str, Any] | None = None
        self._keys_by_kid: dict[str, dict[str, Any]] = {}
        self._expires_at: float = 0.0

    async def get_jwks(
        self,
        jwks_url: str | None = None,
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Return cached JWKS or fetch fresh keys."""

        url = jwks_url or self._jwks_url
        if not url:
            raise ValueError("A jwks_url must be supplied.")

        now = time.time()
        if not force_refresh and self._cached_jwks is not None and now < self._expires_at:
            return self._cached_jwks

        async with self._lock:
            now = time.time()
            if not force_refresh and self._cached_jwks is not None and now < self._expires_at:
                return self._cached_jwks

            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.get(url)
                response.raise_for_status()
                payload: dict[str, Any] = response.json()

            raw_keys = payload.get("keys", [])
            if not isinstance(raw_keys, list):
                raise RuntimeError("JWKS payload contains an invalid keys field.")

            indexed_keys: dict[str, dict[str, Any]] = {}
            for entry in raw_keys:
                if not isinstance(entry, dict):
                    continue
                kid = entry.get("kid")
                if isinstance(kid, str) and kid:
                    indexed_keys[kid] = entry

            self._cached_jwks = payload
            self._keys_by_kid = indexed_keys
            self._expires_at = now + self._cache_ttl_seconds
            return payload

    async def get_key(self, kid: str, jwks_url: str | None = None) -> dict[str, Any] | None:
        """Return a key by kid, refreshing once on cache miss."""

        if kid in self._keys_by_kid and time.time() < self._expires_at:
            return self._keys_by_kid[kid]

        await self.get_jwks(jwks_url=jwks_url, force_refresh=False)
        key = self._keys_by_kid.get(kid)
        if key is not None:
            return key

        await self.get_jwks(jwks_url=jwks_url, force_refresh=True)
        return self._keys_by_kid.get(kid)

    def clear_cache(self) -> None:
        """Drop cached JWKS material."""

        self._cached_jwks = None
        self._keys_by_kid = {}
        self._expires_at = 0.0


__all__ = ["JWKSClient"]
