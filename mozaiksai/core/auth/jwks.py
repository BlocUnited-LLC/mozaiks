"""
JWKS (JSON Web Key Set) client with caching.

Fetches public keys from the identity provider for JWT signature validation.
Supports OIDC discovery-driven jwks_uri or explicit URL override.
"""

import asyncio
import time
from typing import Dict, Optional, Any
from dataclasses import dataclass

import aiohttp

from mozaiksai.core.auth.config import get_auth_config
from logs.logging_config import get_core_logger

logger = get_core_logger("auth.jwks")


@dataclass
class CachedJWKS:
    """Cached JWKS response with expiry."""

    keys: Dict[str, Any]
    fetched_at: float
    ttl_seconds: int
    source_url: str  # Track which URL was used

    def is_expired(self) -> bool:
        return time.time() > (self.fetched_at + self.ttl_seconds)


class JWKSClient:
    """
    Async JWKS client with in-memory caching.

    Supports two modes:
    1. Discovery-driven: jwks_uri obtained from OIDC discovery document
    2. Explicit URL: jwks_url set directly via constructor or AUTH_JWKS_URL env var

    Thread-safe via asyncio.Lock.
    """

    def __init__(
        self,
        jwks_url: Optional[str] = None,
        cache_ttl: Optional[int] = None,
        use_discovery: bool = True,
    ):
        """
        Initialize JWKS client.

        Args:
            jwks_url: Explicit JWKS URL (skips discovery if set)
            cache_ttl: Cache TTL in seconds
            use_discovery: If True and jwks_url not set, fetch from OIDC discovery
        """
        config = get_auth_config()
        self._explicit_jwks_url = jwks_url or config.jwks_url_override
        self._cache_ttl = cache_ttl or config.jwks_cache_ttl_seconds
        self._use_discovery = use_discovery and not self._explicit_jwks_url
        self._cache: Optional[CachedJWKS] = None
        self._lock = asyncio.Lock()
        self._keys_by_kid: Dict[str, Dict[str, Any]] = {}
        self._resolved_jwks_url: Optional[str] = None

    async def _get_jwks_url(self) -> str:
        """
        Get the JWKS URL, either from explicit config or OIDC discovery.

        Returns:
            The jwks_uri string

        Raises:
            RuntimeError if unable to determine JWKS URL
        """
        if self._explicit_jwks_url:
            return self._explicit_jwks_url

        if self._use_discovery:
            from mozaiksai.core.auth.discovery import get_discovery_client
            discovery_client = get_discovery_client()
            return await discovery_client.get_jwks_uri()

        raise RuntimeError(
            "No JWKS URL configured. Set AUTH_JWKS_URL or enable OIDC discovery."
        )

    async def get_signing_key(self, kid: str) -> Optional[Dict[str, Any]]:
        """
        Get signing key by key ID (kid).

        Fetches JWKS if not cached or expired.
        Returns the JWK dict or None if not found.
        """
        await self._ensure_keys_loaded()

        key = self._keys_by_kid.get(kid)
        if key:
            return key

        # Key not found - force refresh in case of key rotation
        logger.info(f"Key {kid} not found, forcing JWKS refresh")
        await self._fetch_keys(force=True)
        return self._keys_by_kid.get(kid)

    async def get_all_keys(self) -> Dict[str, Dict[str, Any]]:
        """Get all signing keys (kid -> JWK)."""
        await self._ensure_keys_loaded()
        return self._keys_by_kid.copy()

    async def _ensure_keys_loaded(self) -> None:
        """Load keys if not cached or expired."""
        if self._cache is None or self._cache.is_expired():
            await self._fetch_keys()

    async def _fetch_keys(self, force: bool = False) -> None:
        """Fetch JWKS from the identity provider."""
        async with self._lock:
            # Double-check after acquiring lock
            if not force and self._cache is not None and not self._cache.is_expired():
                return

            # Resolve JWKS URL (may involve discovery)
            jwks_url = await self._get_jwks_url()
            self._resolved_jwks_url = jwks_url

            logger.info(f"Fetching JWKS from {jwks_url}")
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        jwks_url,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status != 200:
                            error_text = await resp.text()
                            logger.error(f"JWKS fetch failed: {resp.status} {error_text}")
                            raise RuntimeError(f"Failed to fetch JWKS: {resp.status}")

                        data = await resp.json()

                keys = data.get("keys", [])
                if not keys:
                    logger.warning("JWKS response contains no keys")

                # Index by kid
                self._keys_by_kid = {k["kid"]: k for k in keys if "kid" in k}
                self._cache = CachedJWKS(
                    keys=data,
                    fetched_at=time.time(),
                    ttl_seconds=self._cache_ttl,
                    source_url=jwks_url,
                )

                logger.info(f"Loaded {len(self._keys_by_kid)} signing keys from JWKS")

            except asyncio.TimeoutError:
                logger.error("JWKS fetch timed out")
                raise RuntimeError("JWKS fetch timed out")
            except aiohttp.ClientError as e:
                logger.error(f"JWKS fetch client error: {e}")
                raise RuntimeError(f"JWKS fetch failed: {e}")

    def clear_cache(self) -> None:
        """Clear the JWKS cache (useful for testing)."""
        self._cache = None
        self._keys_by_kid = {}
        self._resolved_jwks_url = None


# Module-level singleton
_jwks_client: Optional[JWKSClient] = None


def get_jwks_client() -> JWKSClient:
    """Get or create the singleton JWKS client."""
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = JWKSClient()
    return _jwks_client


def reset_jwks_client() -> None:
    """Reset the singleton (for testing)."""
    global _jwks_client
    _jwks_client = None
