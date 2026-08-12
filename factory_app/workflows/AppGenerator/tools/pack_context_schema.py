"""Schema validation for capability pack context.yaml files.

Validates that pack context entries conform to the allowlisted schema before
catalog content is injected into AG2 reasoning or materialization runs.

This is structural allowlisting — not semantic prompt-injection detection.
Malformed or untrusted arbitrary YAML must not become free-form prompt context
merely because it exists in build_context.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_SAFE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")

# Allowlisted keys in context.yaml root
_ALLOWED_ROOT_KEYS: frozenset[str] = frozenset({
    "context_id",
    "applies_to_workflows",
    "assets",
    "pack",
    "capabilities",
    "facades",
    "projections",
})

# Allowlisted keys in the pack: block
_ALLOWED_PACK_KEYS: frozenset[str] = frozenset({
    "id",
    "version",
    "author",
    "license",
    "source",
    "status",
    "capability_source",
    "required_integrations",
    "deployment_env",
    "description",
    "display_name",
})

_VALID_STATUS: frozenset[str] = frozenset({"active", "inactive", "archived"})

_VALID_CAPABILITY_SOURCES: frozenset[str] = frozenset({
    "managed_capability",
    "generated_module",
    "operator_extension",
    "external_adapter",
    "framework_pack",
})


@dataclass
class PackContextDiagnostic:
    """A single diagnostic from pack context.yaml validation."""

    field: str
    message: str
    severity: str = "error"  # "error" | "warning"


@dataclass
class PackContextValidationResult:
    """Result of validating a pack context.yaml."""

    pack_id: str
    valid: bool
    diagnostics: list[PackContextDiagnostic] = field(default_factory=list)

    @property
    def errors(self) -> list[PackContextDiagnostic]:
        return [d for d in self.diagnostics if d.severity == "error"]

    @property
    def warnings(self) -> list[PackContextDiagnostic]:
        return [d for d in self.diagnostics if d.severity == "warning"]


def validate_pack_context(context: dict[str, Any]) -> PackContextValidationResult:
    """Validate a pack context.yaml dict against the allowlisted schema.

    Called before any pack context is used for catalog injection into AG2
    reasoning or template materialization.  Rejects unexpected keys and invalid
    field values through structural allowlisting.

    Returns a :class:`PackContextValidationResult` with ``valid=True`` when no
    error-severity diagnostics are found.  Warnings do not fail validation.
    """
    diagnostics: list[PackContextDiagnostic] = []

    if not isinstance(context, dict):
        return PackContextValidationResult(
            pack_id="",
            valid=False,
            diagnostics=[PackContextDiagnostic("root", "context.yaml must be a YAML mapping")],
        )

    # --- Root key allowlist ---
    for key in sorted(set(context.keys()) - _ALLOWED_ROOT_KEYS):
        diagnostics.append(PackContextDiagnostic(
            field=key,
            message=(
                f"Unexpected top-level key '{key}' in context.yaml. "
                f"Allowed: {sorted(_ALLOWED_ROOT_KEYS)}"
            ),
        ))

    # --- pack: block ---
    pack = context.get("pack")
    if pack is None:
        # Pack block is optional; contexts like AppGenerator omit it.
        errors = [d for d in diagnostics if d.severity == "error"]
        return PackContextValidationResult(
            pack_id="",
            valid=len(errors) == 0,
            diagnostics=diagnostics,
        )

    if not isinstance(pack, dict):
        diagnostics.append(PackContextDiagnostic("pack", "'pack' must be a YAML mapping"))
        return PackContextValidationResult(
            pack_id="",
            valid=False,
            diagnostics=diagnostics,
        )

    # --- pack.id ---
    pack_id = str(pack.get("id") or "").strip()
    if not pack_id:
        diagnostics.append(PackContextDiagnostic("pack.id", "pack.id is required"))
    elif not _SAFE_ID_RE.match(pack_id):
        diagnostics.append(PackContextDiagnostic(
            "pack.id",
            f"pack.id '{pack_id}' must match [a-z][a-z0-9_]* "
            "(dot-separated segments allowed)",
        ))

    # --- pack.status ---
    status = str(pack.get("status") or "active").strip()
    if status not in _VALID_STATUS:
        diagnostics.append(PackContextDiagnostic(
            "pack.status",
            f"pack.status '{status}' must be one of: {sorted(_VALID_STATUS)}",
        ))

    # --- pack.capability_source (warn, not error, for forward compatibility) ---
    cap_source = pack.get("capability_source")
    if cap_source is not None:
        cap_source_str = str(cap_source).strip()
        if cap_source_str not in _VALID_CAPABILITY_SOURCES:
            diagnostics.append(PackContextDiagnostic(
                "pack.capability_source",
                f"pack.capability_source '{cap_source_str}' is not a known value; "
                f"expected one of: {sorted(_VALID_CAPABILITY_SOURCES)}",
                severity="warning",
            ))

    # --- Optional string identity fields ---
    for str_field in ("version", "author", "license", "source", "description", "display_name"):
        val = pack.get(str_field)
        if val is not None and not isinstance(val, str):
            diagnostics.append(PackContextDiagnostic(
                f"pack.{str_field}",
                f"pack.{str_field} must be a string, got {type(val).__name__}",
            ))

    # --- Unexpected keys in pack block ---
    for key in sorted(set(pack.keys()) - _ALLOWED_PACK_KEYS):
        diagnostics.append(PackContextDiagnostic(
            f"pack.{key}",
            f"Unexpected key '{key}' in pack block. Allowed: {sorted(_ALLOWED_PACK_KEYS)}",
        ))

    errors = [d for d in diagnostics if d.severity == "error"]
    return PackContextValidationResult(
        pack_id=pack_id,
        valid=len(errors) == 0,
        diagnostics=diagnostics,
    )


__all__ = [
    "PackContextDiagnostic",
    "PackContextValidationResult",
    "validate_pack_context",
]
