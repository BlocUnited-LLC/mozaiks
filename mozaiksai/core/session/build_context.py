"""Workspace build-context provider for workflow launch variables.

Build context is optional workspace-level input for factory workflows. It lives
beside ``app/`` at ``build_context/{context_name}/`` and is projected into a
workflow only through the named context's ``context.yaml``.

Env vars:
  MOZAIKS_BUILD_CONTEXT_PATH - explicit path to a build_context directory
  MOZAIKS_APP_WORKSPACE_PATH - workspace root; build_context resolved from there
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from mozaiksai.core.session.build_context_schema import validate_pack_context

if TYPE_CHECKING:
    from mozaiksai.core.workflow.context.authority import ContextAuthorityPolicy


class BuildContextError(RuntimeError):
    """Raised when a workspace build-context file is invalid."""


def resolve_build_context_root(
    *,
    build_context_root: str | os.PathLike[str] | None = None,
    workspace_path: str | os.PathLike[str] | None = None,
) -> Path | None:
    """Resolve the active build_context directory from args or environment."""

    raw = build_context_root or os.getenv("MOZAIKS_BUILD_CONTEXT_PATH")
    if raw:
        root = Path(raw).expanduser().resolve()
        if not root.is_dir():
            raise BuildContextError(f"Configured build context root must be an existing directory: {root}")
        return root

    workspace_raw = workspace_path or os.getenv("MOZAIKS_APP_WORKSPACE_PATH")
    if workspace_raw:
        return (Path(workspace_raw).expanduser().resolve() / "build_context").resolve()

    return None


def discover_build_context_files(root: Path, workflow_id: str | None = None) -> list[Path]:
    """Return named build-context files under ``root``."""

    if not root.exists():
        return []
    if not root.is_dir():
        raise BuildContextError(f"Build context root must be a directory: {root}")

    context_files = sorted(root.glob("*/context.yaml"))
    if not workflow_id:
        return context_files

    selected: list[Path] = []
    for context_path in context_files:
        data = load_build_context(context_path)
        workflows = data.get("applies_to_workflows") or []
        if workflow_id in {str(item) for item in workflows if item}:
            selected.append(context_path)
    return selected


def _load_yaml_mapping(path: Path, *, label: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise BuildContextError(f"{label} is invalid YAML: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise BuildContextError(f"{label} must be a mapping: {path}")
    return data


def load_build_context(context_path: Path) -> dict[str, Any]:
    """Load and validate ``build_context/{context_name}/context.yaml``."""

    if not context_path.exists():
        raise BuildContextError(f"Build context file not found: {context_path}")
    data = _load_yaml_mapping(context_path, label="Build context file")
    result = validate_pack_context(data)
    if not result.valid:
        error_messages = "; ".join(f"{d.field}: {d.message}" for d in result.errors)
        raise BuildContextError(f"Build context file failed schema validation: {context_path}: {error_messages}")
    return data


def iter_context_assets(config: Mapping[str, Any], *, kind: str | None = None) -> list[dict[str, Any]]:
    """Return explicit build-context asset declarations."""

    expected_kind = str(kind or "").strip()
    assets = config.get("assets") or []
    if not isinstance(assets, list):
        return []
    selected: list[dict[str, Any]] = []
    for asset in assets:
        if not isinstance(asset, Mapping):
            continue
        normalized = dict(asset)
        asset_kind = str(normalized.get("kind") or "").strip()
        asset_path = str(normalized.get("path") or "").strip()
        if not asset_path or not asset_kind:
            continue
        if expected_kind and asset_kind != expected_kind:
            continue
        normalized["kind"] = asset_kind
        normalized["path"] = asset_path
        selected.append(normalized)
    return selected


def resolve_context_asset_path(root: Path, asset: Mapping[str, Any]) -> Path:
    """Resolve an asset path and ensure it stays inside the named context root."""

    raw_path = str(asset.get("path") or "").strip()
    if not raw_path:
        raise BuildContextError(f"Build context asset has no path: {root}")
    path = (root / raw_path).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise BuildContextError(f"Build context asset must stay inside context root: {path}") from exc
    return path


def normalize_pack_descriptor(pack_data: Mapping[str, Any], *, context_root: Path) -> dict[str, Any]:
    """Convert a build-context pack section to the prompt-safe descriptor shape."""

    pack_section = pack_data.get("pack")
    descriptor: dict[str, Any] = {}
    if isinstance(pack_section, Mapping):
        descriptor.update(dict(pack_section))
    descriptor_keys = {
        "branding",
        "capabilities",
        "facades",
        "required_integrations",
        "supported_domains",
        "surfaces",
    }
    for key in sorted(descriptor_keys):
        if key in pack_data:
            descriptor.setdefault(key, pack_data[key])

    pack_id = str(descriptor.get("id") or descriptor.get("pack_id") or context_root.name).strip()
    if not pack_id:
        raise BuildContextError(f"Capability pack context has no id: {context_root}")
    descriptor["id"] = pack_id
    descriptor.setdefault("pack_id", pack_id)
    descriptor.setdefault("status", "active")
    descriptor.setdefault("capability_source", "operator_pack")
    descriptor.setdefault("pack_source_path", str(context_root))
    return descriptor


def discover_pack_descriptors(root: Path, config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Discover the active pack descriptor from a pack context."""

    if "pack" not in config:
        return []
    descriptor = normalize_pack_descriptor(config, context_root=root)
    if str(descriptor.get("status") or "active").strip() != "active":
        return []
    return [descriptor]


def load_catalog_descriptors(root: Path, config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Load prompt-safe descriptor YAMLs from explicit catalog assets."""

    descriptors: list[dict[str, Any]] = []
    for asset in iter_context_assets(config, kind="catalog"):
        path = resolve_context_asset_path(root, asset)
        if not path.exists():
            raise BuildContextError(f"Declared build context catalog asset not found: {path}")
        if not path.is_file():
            raise BuildContextError(f"Build context catalog asset must be a file: {path}")
        descriptor = _load_yaml_mapping(path, label="Build context catalog")
        descriptor.setdefault("contract_id", path.stem)
        descriptor.setdefault("source_path", str(path))
        descriptors.append(descriptor)
    return descriptors


def load_contract_descriptors(root: Path, config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Load prompt-safe contract YAMLs from explicit contract assets."""

    descriptors: list[dict[str, Any]] = []
    for asset in iter_context_assets(config, kind="contract"):
        path = resolve_context_asset_path(root, asset)
        if not path.exists():
            raise BuildContextError(f"Declared build context contract asset not found: {path}")
        if not path.is_file():
            raise BuildContextError(f"Build context contract asset must be a file: {path}")
        descriptor = _load_yaml_mapping(path, label="Build context contract")
        descriptor.setdefault("contract_id", path.stem)
        descriptor.setdefault("source_path", str(path))
        descriptors.append(descriptor)
    return descriptors


def build_provider_values(*, root: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    """Compute values available to projection rules."""

    structural_keys = {
        "context_id",
        "description",
        "applies_to_workflows",
        "assets",
        "projections",
        "values",
        "workflows",
    }
    values: dict[str, Any] = {}

    for key, value in config.items():
        if key not in structural_keys:
            values[str(key)] = value

    nested_values = config.get("values")
    if isinstance(nested_values, Mapping):
        for key, value in nested_values.items():
            values[str(key)] = value

    values["capability_packs"] = discover_pack_descriptors(root, config)
    values["operator_contracts"] = load_contract_descriptors(root, config)
    values["operator_catalogs"] = load_catalog_descriptors(root, config)
    return values


def _read_dotted(source: Mapping[str, Any], path: str) -> Any:
    current: Any = source
    for part in path.split("."):
        key = part.strip()
        if not key:
            return None
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def _project_rule(
    rule: Any,
    *,
    provider_values: Mapping[str, Any],
    context_variables: Mapping[str, Any],
    trigger_payload: Mapping[str, Any],
) -> tuple[bool, Any]:
    if isinstance(rule, str):
        value = _read_dotted(provider_values, rule)
        return (value is not None, value)
    if not isinstance(rule, Mapping):
        return (False, None)

    if "value" in rule:
        return (True, rule["value"])
    if "from" in rule:
        value = _read_dotted(provider_values, str(rule["from"]))
        if value is not None:
            return (True, value)
    if "from_context" in rule:
        value = _read_dotted(context_variables, str(rule["from_context"]))
        if value is not None:
            return (True, value)
    if "from_trigger" in rule:
        value = _read_dotted(trigger_payload, str(rule["from_trigger"]))
        if value is not None:
            return (True, value)
    if "default" in rule:
        return (True, rule["default"])
    return (False, None)


def project_build_context(
    *,
    workflow_id: str,
    config: Mapping[str, Any],
    provider_values: Mapping[str, Any],
    context_variables: Mapping[str, Any],
    trigger_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Project build-context values for one workflow."""

    applies_to = config.get("applies_to_workflows") or []
    if workflow_id not in {str(item) for item in applies_to if item}:
        return {}

    projections = config.get("projections") or {}
    context_projection = projections.get("context_variables") if isinstance(projections, Mapping) else None
    if not isinstance(context_projection, Mapping):
        return {}

    projected: dict[str, Any] = {}
    for key, rule in context_projection.items():
        context_key = str(key or "").strip()
        if not context_key:
            continue
        has_value, value = _project_rule(
            rule,
            provider_values=provider_values,
            context_variables=context_variables,
            trigger_payload=trigger_payload,
        )
        if has_value:
            projected[context_key] = value
    return projected


def merge_build_context(
    *,
    workflow_id: str,
    context_variables: Mapping[str, Any] | None = None,
    trigger_payload: Mapping[str, Any] | None = None,
    app_id: str | None = None,
    user_id: str | None = None,
    trigger_source: str | None = None,
    build_context_root: str | os.PathLike[str] | None = None,
    workspace_path: str | os.PathLike[str] | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Merge workspace build-context projections into workflow context variables.

    Caller-owned values retain precedence. Protected build-context values are
    admitted separately from static registry declarations and cannot be supplied
    by callers or recovered from descriptive prompt context.
    """

    merged = dict(context_variables or {})
    root = resolve_build_context_root(
        build_context_root=build_context_root,
        workspace_path=workspace_path,
    )
    if root is None or not root.exists():
        return merged

    from mozaiksai.core.data.persistence.persistence_manager import (
        _context_authority_policy_for_workflow,
    )

    policy = _context_authority_policy_for_workflow(workflow_id)
    trusted = load_trusted_build_context(policy, build_context_root=root)
    for key in policy.build_context_keys.intersection(merged):
        if key not in trusted or merged[key] != trusted[key]:
            raise BuildContextError(f"Untrusted override of protected build-context key {key!r}")

    for context_path in discover_build_context_files(root.resolve(), workflow_id):
        context_root = context_path.parent.resolve()
        config = load_build_context(context_path)
        provider_values = build_provider_values(root=context_root, config=config)
        projected = project_build_context(
            workflow_id=workflow_id,
            config=config,
            provider_values=provider_values,
            context_variables=merged,
            trigger_payload=dict(trigger_payload or {}),
        )

        for key, value in projected.items():
            if key not in policy.build_context_keys and key not in merged:
                merged[key] = value
    merged.update(trusted)
    return merged


def _merge_trusted_projection(target: dict[str, Any], projected: Mapping[str, Any]) -> None:
    for key, value in projected.items():
        if key not in target or target[key] == value:
            target[key] = value
        elif isinstance(target[key], list) and isinstance(value, list):
            combined = list(target[key])
            for item in value:
                if item not in combined:
                    combined.append(item)
            target[key] = combined
        else:
            raise BuildContextError(f"Conflicting trusted build-context projections for {key!r}")


def _trusted_projection(
    policy: ContextAuthorityPolicy, context_path: Path, config: Mapping[str, Any],
) -> dict[str, Any]:
    from mozaiksai.core.workflow.context.authority import _is_valid_context_value

    rules = (config.get("projections") or {}).get("context_variables", {})
    if not isinstance(rules, Mapping):
        raise BuildContextError(f"context_variables projection must be a mapping: {context_path}")
    selected: dict[str, Any] = {}
    for key, rule in rules.items():
        if key not in policy.variables:
            raise BuildContextError(f"Undeclared build-context projection {key!r} for {policy.workflow_name}")
        if key not in policy.build_context_keys:
            continue
        if isinstance(rule, Mapping):
            if set(rule) - {"from", "value"} or len(rule) != 1:
                raise BuildContextError(f"Protected build-context projection {key!r} requires a static registry source")
        elif not isinstance(rule, str) or not rule.strip():
            raise BuildContextError(f"Malformed build-context projection {key!r}")
        # Registration comes from validated pack declarations, never descriptive text.
        if key == "capability_packs" and rule not in ("capability_packs", {"from": "capability_packs"}):
            raise BuildContextError("capability_packs must project registered pack declarations")
        selected[key] = rule
    if not selected:
        return {}
    values = build_provider_values(root=context_path.parent, config=config)
    projected: dict[str, Any] = {}
    for key, rule in selected.items():
        present, value = _project_rule(rule, provider_values=values, context_variables={}, trigger_payload={})
        if not present or value is None or not _is_valid_context_value(value, value_type=policy.variables[key].value_type):
            raise BuildContextError(f"Invalid trusted build-context value for {key!r} in {context_path}")
        projected[key] = value
    return projected


def load_trusted_build_context(
    policy: ContextAuthorityPolicy,
    *,
    build_context_root: str | os.PathLike[str] | None = None,
    workspace_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Re-admit protected values from current registries, never from chat data.

    This is the shared launch/replay trust boundary. Stored copies are evidence,
    not provenance: every hydration resolves applicable declarations again.
    """
    if not policy.build_context_keys:
        return {}
    root = resolve_build_context_root(build_context_root=build_context_root, workspace_path=workspace_path)
    if root is None or not root.exists():
        return {}
    projected: dict[str, Any] = {}
    workspace_pack_ids: set[str] = set()
    for context_path in discover_build_context_files(root, policy.workflow_name):
        config = load_build_context(context_path)
        if isinstance(config.get("pack"), Mapping):
            workspace_pack_ids.add(config["pack"]["id"])
        _merge_trusted_projection(projected, _trusted_projection(policy, context_path, config))

    # Resolve exact operator-selected pack IDs against the installed public
    # registry. A workspace directory/catalog alone does not register a provider.
    selected = projected.get("operator_capabilities", [])
    if not isinstance(selected, list) or any(not isinstance(item, str) for item in selected):
        raise BuildContextError("operator_capabilities must contain canonical capability IDs")
    if selected and "capability_packs" in policy.build_context_keys:
        from mozaiksai.resources import resolve_factory_app_root

        factory_root = resolve_factory_app_root()
        if factory_root is not None and (factory_root / "build_context").resolve() != root.resolve():
            for context_path in discover_build_context_files(factory_root / "build_context", policy.workflow_name):
                config = load_build_context(context_path)
                pack = config.get("pack") or {}
                if pack.get("id") in selected and pack.get("id") not in workspace_pack_ids and pack.get("status", "active") == "active":
                    _merge_trusted_projection(projected, _trusted_projection(policy, context_path, config))
    packs = projected.get("capability_packs", [])
    identities: set[str] = set()
    for pack in packs:
        pack_id = pack["id"]
        if pack_id in identities:
            raise BuildContextError(f"Conflicting registered capability pack {pack_id!r}")
        identities.add(pack_id)
    return projected


def revalidate_build_context(
    policy: ContextAuthorityPolicy, values: Mapping[str, Any], *, reject_overrides: bool = False,
) -> dict[str, Any]:
    """Replace stored/provider copies with freshly admitted registry projections."""
    trusted = load_trusted_build_context(policy)
    if reject_overrides:
        for key in policy.build_context_keys.intersection(values):
            if key not in trusted or values[key] != trusted[key]:
                raise BuildContextError(f"Untrusted override of protected build-context key {key!r}")
    return {**{key: value for key, value in values.items() if key not in policy.build_context_keys}, **trusted}


__all__ = [
    "BuildContextError",
    "build_provider_values",
    "discover_build_context_files",
    "discover_pack_descriptors",
    "iter_context_assets",
    "load_build_context",
    "load_catalog_descriptors",
    "load_contract_descriptors",
    "load_trusted_build_context",
    "merge_build_context",
    "normalize_pack_descriptor",
    "project_build_context",
    "resolve_build_context_root",
    "resolve_context_asset_path",
]
