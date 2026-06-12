"""Typed CapabilityPack model — the OSS factory's operator extension point.

Any operator (hosted platform, enterprise self-hosted, OSS power user) injects
capability packs at workflow launch via context_variables["capability_packs"].
The factory workflows are capability-pack-aware: when the list is non-empty the
planning agents receive a rendered capabilities block; when it is empty the
factory behaves identically to a vanilla OSS run.

Pack data flows:
  1. Operator builds a list of CapabilityPack objects from their pack registry.
  2. The list is passed as context_variables["capability_packs"] at launch.
  3. The hook in AppGenerator reads the typed list and renders the context block.
  4. Assembly reads pack_source_path per selected pack and materializes every
     file under that pack's templates/ tree.

This module has zero hosted-product imports. It is the clean OSS boundary.
"""

from __future__ import annotations

from typing import Any

try:
    from pydantic import BaseModel, Field
    _PYDANTIC = True
except ImportError:
    _PYDANTIC = False


if _PYDANTIC:
    class CapabilityItem(BaseModel):
        """A single capability declared by a pack."""
        capability_id: str
        description: str = ""

    class CapabilityPack(BaseModel):
        """
        A capability pack descriptor passed to factory workflows at launch.

        Operators populate this from a build-context package. The factory
        consumes it without knowing anything about the specific capabilities or
        where they came from.

        Fields
        ------
        id
            Pack identifier. Must match the build-context pack id.
        display_name
            Human-readable label shown in agent planning context.
        description
            Short summary of what this pack provides.
        capabilities
            List of capability IDs this pack exposes.
        surfaces
            Surface groupings with facade/page descriptors.
        supported_domains
            Domain fit hints — which app kinds this pack is recommended for.
        branding
            UX/attribution guidance for generated app surfaces.
        pack_source_path
            Absolute filesystem path to this pack's directory, used during
            assembly for deterministic template materialization.
            None in OSS/dev contexts where no template files are needed.
        status
            "active" | "placeholder". Only active packs should be passed to
            factory workflows. Operators filter before launch.
        capability_source
            Taxonomy label: "hosted_pack" | "framework_pack" | "host_universal"
            | "generated_module" | "external_adapter".
        """
        id: str
        display_name: str = ""
        description: str = ""
        capabilities: list[CapabilityItem] = Field(default_factory=list)
        surfaces: list[dict[str, Any]] = Field(default_factory=list)
        supported_domains: list[dict[str, Any]] = Field(default_factory=list)
        branding: dict[str, Any] = Field(default_factory=dict)
        pack_source_path: str | None = None
        status: str = "active"
        capability_source: str = "hosted_pack"

else:
    # Pydantic not available — provide plain dataclass-style fallbacks so
    # import never fails in minimal environments. Validation is best-effort.

    class CapabilityItem:  # type: ignore[no-redef]
        def __init__(self, capability_id: str, description: str = "") -> None:
            self.capability_id = capability_id
            self.description = description

    class CapabilityPack:  # type: ignore[no-redef]
        def __init__(self, *, id: str, display_name: str = "", description: str = "",
                     capabilities: list | None = None,
                     surfaces: list | None = None, supported_domains: list | None = None,
                     branding: dict | None = None, pack_source_path: str | None = None,
                     status: str = "active",
                     capability_source: str = "hosted_pack") -> None:
            self.id = id
            self.display_name = display_name
            self.description = description
            self.capabilities = capabilities or []
            self.surfaces = surfaces or []
            self.supported_domains = supported_domains or []
            self.branding = branding or {}
            self.pack_source_path = pack_source_path
            self.status = status
            self.capability_source = capability_source


__all__ = ["CapabilityItem", "CapabilityPack"]
