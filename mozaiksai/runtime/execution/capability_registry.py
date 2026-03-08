"""CapabilityRegistry — maps capabilities to execution handlers.

The CapabilityRegistry maintains a mapping from capability names to
their handlers (typically WorkerPort implementations). This enables
capability-oriented execution where the runtime dispatches runs based
on what capability they require.

Design rules
------------
* Capability names are lowercase, dot-separated (e.g., "agent", "pipeline.dag")
* Each capability has exactly one active handler at a time
* Supports capability introspection for discovery endpoints
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CapabilityMetadata:
    """Metadata about a registered capability."""

    name: str
    worker_type: str = ""
    description: str = ""
    version: str = "1.0.0"
    features: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)


class CapabilityRegistry:
    """Registry for capability handlers.

    The registry:
    - Maps capability names to handler references
    - Stores capability metadata for discovery
    - Supports version constraints and feature queries

    Usage::

        registry = CapabilityRegistry()
        registry.register(
            "agent",
            handler=agent_worker,
            metadata=CapabilityMetadata(
                name="agent",
                description="Multi-agent orchestration",
                features=["handoffs", "tools"],
            ),
        )

        handler = registry.get_handler("agent")
    """

    def __init__(self):
        """Initialize the capability registry."""
        self._metadata: dict[str, CapabilityMetadata] = {}

    def register(
        self,
        capability: str,
        metadata: CapabilityMetadata | None = None,
    ) -> None:
        """Register metadata for a capability.

        Parameters
        ----------
        capability : str
            The capability name (e.g., "agent").
        metadata : CapabilityMetadata, optional
            Metadata about the capability, including its worker_type.

        Raises
        ------
        ValueError
            If metadata is already registered for this capability.
        """
        if capability in self._metadata:
            raise ValueError(f"Capability already registered: {capability}")

        self._metadata[capability] = metadata or CapabilityMetadata(name=capability)

        logger.info(
            f"[CAPABILITY_REGISTRY] Registered: capability={capability} "
            f"worker_type={self._metadata[capability].worker_type!r}"
        )

    def unregister(self, capability: str) -> None:
        """Unregister a capability.

        Parameters
        ----------
        capability : str
            The capability to unregister.
        """
        self._metadata.pop(capability, None)
        logger.info(f"[CAPABILITY_REGISTRY] Unregistered: capability={capability}")

    def get_metadata(self, capability: str) -> CapabilityMetadata | None:
        """Get metadata for a capability.

        Parameters
        ----------
        capability : str
            The capability name.

        Returns
        -------
        CapabilityMetadata | None
            Metadata for the capability, or None if not found.
        """
        return self._metadata.get(capability)

    def has_capability(self, capability: str) -> bool:
        """Check if a capability is registered.

        Parameters
        ----------
        capability : str
            The capability name.

        Returns
        -------
        bool
            True if the capability is registered.
        """
        return capability in self._handlers

    def list_capabilities(self) -> list[str]:
        """List all registered capabilities.

        Returns
        -------
        list[str]
            List of capability names.
        """
        return list(self._handlers.keys())

    def list_metadata(self) -> list[CapabilityMetadata]:
        """List metadata for all capabilities.

        Returns
        -------
        list[CapabilityMetadata]
            Metadata for all registered capabilities.
        """
        return list(self._metadata.values())

    def supports_feature(self, capability: str, feature: str) -> bool:
        """Check if a capability supports a feature.

        Parameters
        ----------
        capability : str
            The capability name.
        feature : str
            The feature to check.

        Returns
        -------
        bool
            True if the capability supports the feature.
        """
        metadata = self._metadata.get(capability)
        if not metadata:
            return False
        return feature in metadata.features

    def status(self) -> dict[str, Any]:
        """Return registry status for health checks.

        Returns
        -------
        dict
            Status information about the registry.
        """
        return {
            "total_capabilities": len(self._handlers),
            "capabilities": [
                {
                    "name": meta.name,
                    "description": meta.description,
                    "version": meta.version,
                    "features": meta.features,
                }
                for meta in self._metadata.values()
            ],
        }


# Global capability registry instance
_global_capability_registry: CapabilityRegistry | None = None


def get_capability_registry() -> CapabilityRegistry:
    """Get the global capability registry instance.

    Returns
    -------
    CapabilityRegistry
        The global registry.
    """
    global _global_capability_registry
    if _global_capability_registry is None:
        _global_capability_registry = CapabilityRegistry()
    return _global_capability_registry


def reset_capability_registry() -> None:
    """Reset the global capability registry (for testing)."""
    global _global_capability_registry
    _global_capability_registry = None


__all__ = [
    "CapabilityMetadata",
    "CapabilityRegistry",
    "get_capability_registry",
    "reset_capability_registry",
]
