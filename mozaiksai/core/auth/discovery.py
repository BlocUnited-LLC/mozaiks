"""
OIDC Discovery document fetching and caching.

Fetches the OpenID Connect discovery document to obtain:
- jwks_uri: URL for JSON Web Key Set
- issuer: Expected token issuer

This makes JWT validation provider-agnostic by dynamically discovering endpoints.
"""

import asyncio
import time
import os
from typing import Dict, Optional, Any
from dataclasses import dataclass

import aiohttp

from logs.logging_config import get_core_logger

logger = get_core_logger("auth.discovery")


# Mozaiks CIAM defaults
_DEFAULT_AUTHORITY = "https://mozaiks.ciamlogin.com"
_DEFAULT_TENANT_ID = "9d0073d5-42e8-46f0-a325-5b4be7b1a38d"
_DEFAULT_DISCOVERY_CACHE_TTL = 86400  # 24 hours (discovery rarely changes)


@dataclass
class CachedDiscovery:
    """Cached OIDC discovery document with expiry."""

    document: Dict[str, Any]
    fetched_at: float
    ttl_seconds: int

    def is_expired(self) -> bool:
        return time.time() > (self.fetched_at + self.ttl_seconds)

    @property
    def jwks_uri(self) -> Optional[str]:
        return self.document.get("jwks_uri")

    @property
    def issuer(self) -> Optional[str]:
        return self.document.get("issuer")


class OIDCDiscoveryClient:
    """
    Async OIDC discovery client with in-memory caching.

    Fetches the .well-known/openid-configuration document and caches it.
    Thread-safe via asyncio.Lock.
    """

    def __init__(
        self,
        discovery_url: Optional[str] = None,
        cache_ttl: Optional[int] = None,
    ):
        """
        Initialize OIDC discovery client.

        Args:
            discovery_url: Direct URL to discovery document. If None, computed from
                           MOZAIKS_OIDC_AUTHORITY and MOZAIKS_OIDC_TENANT_ID.
            cache_ttl: Cache TTL in seconds (default: 86400 = 24h)
        """
        # Allow explicit override via MOZAIKS_OIDC_DISCOVERY_URL
        explicit_url = os.getenv("MOZAIKS_OIDC_DISCOVERY_URL", "").strip()
        
        if explicit_url:
            self._discovery_url = explicit_url
        elif discovery_url:
            self._discovery_url = discovery_url
        else:
            # Compute from authority + tenant
            authority = os.getenv("MOZAIKS_OIDC_AUTHORITY", _DEFAULT_AUTHORITY).rstrip("/")
            tenant_id = os.getenv("MOZAIKS_OIDC_TENANT_ID", _DEFAULT_TENANT_ID)
            self._discovery_url = f"{authority}/{tenant_id}/v2.0/.well-known/openid-configuration"

        self._cache_ttl = cache_ttl or int(
            os.getenv("AUTH_DISCOVERY_CACHE_TTL", str(_DEFAULT_DISCOVERY_CACHE_TTL))
        )
        self._cache: Optional[CachedDiscovery] = None
        self._lock = asyncio.Lock()

    @property
    def discovery_url(self) -> str:
        """Return the configured discovery URL."""
        return self._discovery_url

    async def get_discovery(self) -> CachedDiscovery:
        """
        Get the OIDC discovery document (cached).

        Returns:
            CachedDiscovery with the document and metadata

        Raises:
            RuntimeError on fetch failure (fail-closed)
        """
        if self._cache is not None and not self._cache.is_expired():
            return self._cache

        return await self._fetch_discovery()

    async def get_jwks_uri(self) -> str:
        """
        Get the JWKS URI from the discovery document.

        Returns:
            The jwks_uri string

        Raises:
            RuntimeError if discovery fails or jwks_uri missing
        """
        discovery = await self.get_discovery()
        jwks_uri = discovery.jwks_uri
        if not jwks_uri:
            raise RuntimeError("Discovery document missing jwks_uri")
        return jwks_uri

    async def get_issuer(self) -> str:
        """
        Get the issuer from the discovery document.

        Returns:
            The issuer string

        Raises:
            RuntimeError if discovery fails or issuer missing
        """
        discovery = await self.get_discovery()
        issuer = discovery.issuer
        if not issuer:
            raise RuntimeError("Discovery document missing issuer")
        return issuer

    async def _fetch_discovery(self, force: bool = False) -> CachedDiscovery:
        """Fetch OIDC discovery document from the identity provider."""
        async with self._lock:
            # Double-check after acquiring lock
            if not force and self._cache is not None and not self._cache.is_expired():
                return self._cache

            logger.info(f"Fetching OIDC discovery from {self._discovery_url}")
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        self._discovery_url,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status != 200:
                            error_text = await resp.text()
                            logger.error(
                                f"OIDC discovery fetch failed: {resp.status} {error_text}"
                            )
                            raise RuntimeError(
                                f"Failed to fetch OIDC discovery: {resp.status}"
                            )

                        document = await resp.json()

                # Validate required fields
                if "jwks_uri" not in document:
                    raise RuntimeError("OIDC discovery missing jwks_uri")
                if "issuer" not in document:
                    raise RuntimeError("OIDC discovery missing issuer")

                self._cache = CachedDiscovery(
                    document=document,
                    fetched_at=time.time(),
                    ttl_seconds=self._cache_ttl,
                )

                logger.info(
                    f"OIDC discovery loaded: issuer={document.get('issuer')}, "
                    f"jwks_uri={document.get('jwks_uri')}"
                )
                return self._cache

            except asyncio.TimeoutError:
                logger.error("OIDC discovery fetch timed out")
                raise RuntimeError("OIDC discovery fetch timed out")
            except aiohttp.ClientError as e:
                logger.error(f"OIDC discovery fetch client error: {e}")
                raise RuntimeError(f"OIDC discovery fetch failed: {e}")

    def clear_cache(self) -> None:
        """Clear the discovery cache (useful for testing)."""
        self._cache = None


# Module-level singleton
_discovery_client: Optional[OIDCDiscoveryClient] = None


def get_discovery_client() -> OIDCDiscoveryClient:
    """Get or create the singleton OIDC discovery client."""
    global _discovery_client
    if _discovery_client is None:
        _discovery_client = OIDCDiscoveryClient()
    return _discovery_client


def reset_discovery_client() -> None:
    """Reset the singleton (for testing)."""
    global _discovery_client
    _discovery_client = None
