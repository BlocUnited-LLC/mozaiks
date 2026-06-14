"""SslProviderPort — generic SSL certificate provisioning port.

Concrete implementations live in app workspaces under
``app/services/adapters/ssl/{provider}.py``.  The OSS runtime declares only
the protocol; it does not depend on any specific SSL provider.

Status vocabulary (``status`` field in all return dicts):
    pending   — provisioning in progress, check again later
    issued    — certificate is active and serving
    failed    — provisioning failed; ``failure_reason`` may describe why
    unknown   — status could not be determined
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SslProviderPort(Protocol):
    """Protocol for SSL certificate lifecycle management.

    All methods are async. Implementations must be safe to call from a
    background startup service or a module action handler.
    """

    async def provision(
        self,
        domain: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Start or resume SSL certificate provisioning for *domain*.

        Args:
            domain: The fully-qualified domain name to provision.
            context: Optional provider-specific context (e.g. resource group,
                container app environment name).

        Returns:
            dict with at least ``{"provisioning_id": str, "status": str}``.
            ``status`` is one of the status vocabulary values above.
        """
        ...

    async def get_status(
        self,
        domain: str,
        *,
        provisioning_id: str | None = None,
    ) -> dict[str, Any]:
        """Check the current provisioning status for *domain*.

        Args:
            domain: The fully-qualified domain name.
            provisioning_id: Provider-specific ID returned by ``provision()``.

        Returns:
            dict with at least ``{"status": str}``.
        """
        ...

    async def deprovision(
        self,
        domain: str,
        *,
        provisioning_id: str | None = None,
    ) -> None:
        """Remove an issued certificate or cancel a pending provisioning.

        Must be idempotent — calling on a domain with no active certificate
        must not raise.
        """
        ...


__all__ = ["SslProviderPort"]
