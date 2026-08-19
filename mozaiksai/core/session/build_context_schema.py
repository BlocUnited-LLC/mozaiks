"""Canonical schema validation for build-context ``context.yaml`` files.

Build context is projected into workflow context variables and agent prompts
before any selected pack reaches materialization. This module is the shared
structural allowlist for both phases.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_SAFE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")

ALLOWED_ROOT_KEYS: frozenset[str] = frozenset({
    "context_id",
    "description",
    "applies_to_workflows",
    "assets",
    "pack",
    "capabilities",
    "facades",
    "projections",
    "values",
})

ALLOWED_PACK_KEYS: frozenset[str] = frozenset({
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

VALID_STATUS: frozenset[str] = frozenset({"active", "inactive", "archived"})

VALID_CAPABILITY_SOURCES: frozenset[str] = frozenset({
    "config_file",
    "managed_capability",
    "generated_module",
    "operator_extension",
    "external_adapter",
    "framework_pack",
})

ALLOWED_ASSET_KEYS: frozenset[str] = frozenset({"path", "kind", "description", "projections"})
VALID_ASSET_KINDS: frozenset[str] = frozenset({"catalog", "contract", "templates"})


@dataclass
class PackContextDiagnostic:
    """A single diagnostic from build-context validation."""

    field: str
    message: str
    severity: str = "error"


@dataclass
class PackContextValidationResult:
    """Result of validating a build-context ``context.yaml`` mapping."""

    pack_id: str
    valid: bool
    diagnostics: list[PackContextDiagnostic] = field(default_factory=list)

    @property
    def errors(self) -> list[PackContextDiagnostic]:
        return [d for d in self.diagnostics if d.severity == "error"]

    @property
    def warnings(self) -> list[PackContextDiagnostic]:
        return [d for d in self.diagnostics if d.severity == "warning"]


def _require_mapping(value: Any, field: str, diagnostics: list[PackContextDiagnostic]) -> bool:
    if value is None or isinstance(value, dict):
        return True
    diagnostics.append(PackContextDiagnostic(field, f"{field} must be a mapping"))
    return False


def _require_list(value: Any, field: str, diagnostics: list[PackContextDiagnostic]) -> bool:
    if isinstance(value, list):
        return True
    diagnostics.append(PackContextDiagnostic(field, f"{field} must be a list"))
    return False


def validate_pack_context(context: dict[str, Any]) -> PackContextValidationResult:
    """Validate a build-context ``context.yaml`` dict against the canonical schema."""

    diagnostics: list[PackContextDiagnostic] = []

    if not isinstance(context, dict):
        return PackContextValidationResult(
            pack_id="",
            valid=False,
            diagnostics=[PackContextDiagnostic("root", "context.yaml must be a YAML mapping")],
        )

    for key in sorted(set(context.keys()) - ALLOWED_ROOT_KEYS):
        diagnostics.append(PackContextDiagnostic(
            field=key,
            message=(
                f"Unexpected top-level key '{key}' in context.yaml. "
                f"Allowed: {sorted(ALLOWED_ROOT_KEYS)}"
            ),
        ))

    context_id = str(context.get("context_id") or "").strip()
    if not context_id:
        diagnostics.append(PackContextDiagnostic("context_id", "context_id is required"))

    applies_to = context.get("applies_to_workflows")
    if not _require_list(applies_to, "applies_to_workflows", diagnostics):
        applies_to = []
    if isinstance(applies_to, list):
        for index, workflow_id in enumerate(applies_to):
            if not str(workflow_id or "").strip():
                diagnostics.append(PackContextDiagnostic(
                    f"applies_to_workflows[{index}]",
                    "workflow id must be a non-empty string",
                ))

    _require_mapping(context.get("projections"), "projections", diagnostics)
    _require_mapping(context.get("values"), "values", diagnostics)

    for list_field in ("capabilities", "facades"):
        value = context.get(list_field)
        if value is not None and not isinstance(value, list):
            diagnostics.append(PackContextDiagnostic(list_field, f"{list_field} must be a list"))

    description = context.get("description")
    if description is not None and not isinstance(description, str):
        diagnostics.append(PackContextDiagnostic("description", "description must be a string"))

    assets = context.get("assets")
    if _require_list(assets, "assets", diagnostics) and isinstance(assets, list):
        for index, asset in enumerate(assets):
            field = f"assets[{index}]"
            if not isinstance(asset, dict):
                diagnostics.append(PackContextDiagnostic(field, "asset must be a mapping"))
                continue
            unknown_asset_keys = sorted(set(asset) - ALLOWED_ASSET_KEYS)
            if unknown_asset_keys:
                diagnostics.append(PackContextDiagnostic(
                    field,
                    f"asset has unsupported fields: {unknown_asset_keys}",
                ))
            asset_path = str(asset.get("path") or "").strip()
            asset_kind = str(asset.get("kind") or "").strip()
            if not asset_path:
                diagnostics.append(PackContextDiagnostic(f"{field}.path", "asset.path is required"))
            if asset_kind not in VALID_ASSET_KINDS:
                diagnostics.append(PackContextDiagnostic(
                    f"{field}.kind",
                    f"asset.kind must be one of: {sorted(VALID_ASSET_KINDS)}",
                ))

    pack = context.get("pack")
    if pack is None:
        errors = [d for d in diagnostics if d.severity == "error"]
        return PackContextValidationResult(
            pack_id="",
            valid=len(errors) == 0,
            diagnostics=diagnostics,
        )

    if not isinstance(pack, dict):
        diagnostics.append(PackContextDiagnostic("pack", "pack must be a mapping"))
        return PackContextValidationResult(
            pack_id="",
            valid=False,
            diagnostics=diagnostics,
        )

    pack_id = str(pack.get("id") or "").strip()
    if not pack_id:
        diagnostics.append(PackContextDiagnostic("pack.id", "pack.id is required"))
    elif not _SAFE_ID_RE.match(pack_id):
        diagnostics.append(PackContextDiagnostic(
            "pack.id",
            f"pack.id '{pack_id}' must match [a-z][a-z0-9_]*; dot-separated segments allowed",
        ))

    status = str(pack.get("status") or "active").strip()
    if status not in VALID_STATUS:
        diagnostics.append(PackContextDiagnostic(
            "pack.status",
            f"pack.status '{status}' must be one of: {sorted(VALID_STATUS)}",
        ))

    version = pack.get("version")
    if version is None or not isinstance(version, str) or not version.strip():
        diagnostics.append(PackContextDiagnostic("pack.version", "pack.version is required and must be a string"))

    cap_source = pack.get("capability_source")
    if cap_source is not None:
        cap_source_str = str(cap_source).strip()
        if cap_source_str not in VALID_CAPABILITY_SOURCES:
            diagnostics.append(PackContextDiagnostic(
                "pack.capability_source",
                f"pack.capability_source '{cap_source_str}' is not a known value; "
                f"expected one of: {sorted(VALID_CAPABILITY_SOURCES)}",
            ))

    for str_field in ("author", "license", "source", "description", "display_name"):
        value = pack.get(str_field)
        if value is not None and not isinstance(value, str):
            diagnostics.append(PackContextDiagnostic(
                f"pack.{str_field}",
                f"pack.{str_field} must be a string, got {type(value).__name__}",
            ))

    for key in sorted(set(pack.keys()) - ALLOWED_PACK_KEYS):
        diagnostics.append(PackContextDiagnostic(
            f"pack.{key}",
            f"Unexpected key '{key}' in pack block. Allowed: {sorted(ALLOWED_PACK_KEYS)}",
        ))

    errors = [d for d in diagnostics if d.severity == "error"]
    return PackContextValidationResult(
        pack_id=pack_id,
        valid=len(errors) == 0,
        diagnostics=diagnostics,
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
