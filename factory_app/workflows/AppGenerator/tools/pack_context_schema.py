"""Compatibility re-export for the canonical build-context schema validator."""

from __future__ import annotations

from mozaiksai.core.session.build_context_schema import (
    ALLOWED_ASSET_KEYS,
    ALLOWED_PACK_KEYS,
    ALLOWED_ROOT_KEYS,
    VALID_ASSET_KINDS,
    VALID_CAPABILITY_SOURCES,
    VALID_STATUS,
    PackContextDiagnostic,
    PackContextValidationResult,
    validate_pack_context,
)

__all__ = [
    "ALLOWED_ASSET_KEYS",
    "ALLOWED_PACK_KEYS",
    "ALLOWED_ROOT_KEYS",
    "PackContextDiagnostic",
    "PackContextValidationResult",
    "VALID_ASSET_KINDS",
    "VALID_CAPABILITY_SOURCES",
    "VALID_STATUS",
    "validate_pack_context",
]
