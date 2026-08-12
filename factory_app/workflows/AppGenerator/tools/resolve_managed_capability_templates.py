"""Materialize deterministic files from selected build-context packs."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Template

from factory_app.workflows.AppGenerator.tools.pack_context_schema import (
    validate_pack_context,
)
from mozaiksai.core.session.build_context import (
    BuildContextError,
    iter_context_assets,
    resolve_context_asset_path,
)

logger = logging.getLogger(__name__)

_PROVENANCE_PATH = ".mozaiks/pack_provenance.json"
_PROVENANCE_SCHEMA_VERSION = "mozaiks.pack_provenance.v1"


class ManagedCapabilityTemplateError(Exception):
    """Raised when a selected pack template tree cannot be materialized."""


class PackDependencyError(ManagedCapabilityTemplateError):
    """Raised when a required pack or capability is not available before materialization.

    Carries structured diagnostics so callers can surface precise missing-dep messages
    rather than relying on the LLM to remember implicit dependencies.
    """

    def __init__(
        self,
        pack_id: str,
        *,
        missing_packs: list[str] | None = None,
        missing_capabilities: list[str] | None = None,
        version_mismatches: list[str] | None = None,
    ) -> None:
        self.pack_id = pack_id
        self.missing_packs: list[str] = missing_packs or []
        self.missing_capabilities: list[str] = missing_capabilities or []
        self.version_mismatches: list[str] = version_mismatches or []
        parts: list[str] = []
        if self.missing_packs:
            parts.append(f"missing required packs: {self.missing_packs}")
        if self.missing_capabilities:
            parts.append(f"missing required capabilities: {self.missing_capabilities}")
        if self.version_mismatches:
            parts.append(f"dependency version mismatches: {self.version_mismatches}")
        super().__init__(
            f"Pack '{pack_id}' dependency check failed — {'; '.join(parts)}. "
            "Ensure all required packs are included in the selected capability_packs list."
        )


class PackIntegrityError(ManagedCapabilityTemplateError):
    """Raised when a local pack fails deterministic trust/integrity verification."""


def _normalized_source(pack: dict[str, Any], context: dict[str, Any]) -> str:
    pack_value = context.get("pack")
    pack_block = pack_value if isinstance(pack_value, dict) else {}
    return str(pack.get("source") or pack_block.get("source") or "local").strip()


def _is_safe_identifier(name: str) -> bool:
    if not name or name != name.strip():
        return False
    normalized = name.replace("\\", "/")
    return "/" not in normalized and ".." not in normalized


def _read_pack_context(pack_source_path: Path, pack_id: str) -> dict[str, Any]:
    context_path = pack_source_path / "context.yaml"
    if not context_path.exists():
        raise ManagedCapabilityTemplateError(f"Managed capability pack context not found: {context_path}")
    try:
        data = yaml.safe_load(context_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise ManagedCapabilityTemplateError(f"Cannot read pack context at {context_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ManagedCapabilityTemplateError(f"Pack context at {context_path} is not a YAML mapping")

    # --- Schema validation: structural allowlist before use ---
    result = validate_pack_context(data)
    if not result.valid:
        error_messages = "; ".join(d.message for d in result.errors)
        raise ManagedCapabilityTemplateError(
            f"Pack context at {context_path} failed schema validation: {error_messages}"
        )
    for warn in result.warnings:
        logger.warning("[pack:%s] context.yaml schema warning — %s: %s", pack_id, warn.field, warn.message)

    pack = data.get("pack")
    declared_id = str((pack or {}).get("id") or data.get("context_id") or "").strip() if isinstance(pack, dict) else ""
    if declared_id and declared_id != pack_id:
        raise ManagedCapabilityTemplateError(
            f"Pack context id mismatch: expected '{pack_id}', found '{declared_id}' at {context_path}"
        )
    return data


def _read_pack_contract(pack_source_path: Path) -> dict[str, Any]:
    """Read contract.yaml from a pack directory, returning empty dict if absent."""
    contract_path = pack_source_path / "contract.yaml"
    if not contract_path.exists():
        return {}
    try:
        data = yaml.safe_load(contract_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise ManagedCapabilityTemplateError(f"Cannot read pack contract at {contract_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ManagedCapabilityTemplateError(f"Pack contract at {contract_path} is not a YAML mapping")
    return data


def _extract_requires(contract: dict[str, Any]) -> tuple[list[dict[str, str]], list[str]]:
    """Return (required_packs, required_capability_ids) from a contract dict.

    Supports two declaration forms:
      packs: [pack_id, ...]                      # simple list of strings
      packs: [{pack_id: name, version: ..., reason: ...}]
    """
    requires = contract.get("requires")
    if not isinstance(requires, dict):
        return [], []

    def _extract_pack_entries(entries: Any) -> list[dict[str, str]]:
        if not isinstance(entries, list):
            return []
        packs: list[dict[str, str]] = []
        for entry in entries:
            if isinstance(entry, str):
                pack_id = entry.strip()
                if pack_id:
                    packs.append({"pack_id": pack_id})
            elif isinstance(entry, dict):
                pack_id = str(entry.get("pack_id") or "").strip()
                version = str(entry.get("version") or "").strip()
                if pack_id:
                    item = {"pack_id": pack_id}
                    if version:
                        item["version"] = version
                    packs.append(item)
        return packs

    def _extract_ids(entries: Any, id_key: str) -> list[str]:
        if not isinstance(entries, list):
            return []
        ids: list[str] = []
        for entry in entries:
            if isinstance(entry, str):
                ids.append(entry.strip())
            elif isinstance(entry, dict):
                val = str(entry.get(id_key) or "").strip()
                if val:
                    ids.append(val)
        return ids

    pack_ids = _extract_pack_entries(requires.get("packs"))
    cap_ids = _extract_ids(requires.get("capabilities"), "capability_id")
    return pack_ids, cap_ids


def _capabilities_from_context(context: dict[str, Any]) -> list[str]:
    """Extract capability_ids from a pack context.yaml dict."""
    ids: list[str] = []
    for cap in context.get("capabilities") or []:
        if isinstance(cap, dict):
            cid = str(cap.get("capability_id") or "").strip()
            if cid:
                ids.append(cid)
        elif isinstance(cap, str):
            ids.append(cap.strip())
    return ids


def _validate_pack_dependencies(
    capability_packs: list[dict[str, Any]],
) -> None:
    """Check that every pack's declared ``requires`` is satisfied by the selection.

    Reads each pack's ``contract.yaml`` for ``requires.packs`` and
    ``requires.capabilities``.  Raises :class:`PackDependencyError` with
    structured missing-dep lists on the first unsatisfied pack.

    Does not auto-install anything.
    """
    # Build available sets.
    # For capabilities, prefer the descriptor's capabilities[] list but fall back
    # to reading context.yaml — the descriptor may not carry the capabilities list
    # when built programmatically (e.g. in tests or lightweight integrations).
    available_pack_ids: set[str] = set()
    available_pack_versions: dict[str, str] = {}
    available_capability_ids: set[str] = set()

    for pack in capability_packs:
        if not isinstance(pack, dict):
            continue
        pid = str(pack.get("id") or pack.get("pack_id") or pack.get("capability_pack_id") or "").strip()
        if pid:
            available_pack_ids.add(pid)

        # Capabilities from descriptor (fast path)
        for cap in pack.get("capabilities") or []:
            if isinstance(cap, dict):
                cid = str(cap.get("capability_id") or "").strip()
                if cid:
                    available_capability_ids.add(cid)
            elif isinstance(cap, str):
                available_capability_ids.add(cap.strip())

        # Capabilities from context.yaml (fallback when descriptor lacks them).
        # Only attempt when pack_id is safe — unsafe IDs will be caught later
        # by resolve_templates_for_pack's _is_safe_identifier check.
        raw_path = pack.get("pack_source_path")
        if raw_path and pid and _is_safe_identifier(pid):
            try:
                context_data = _read_pack_context(Path(raw_path).resolve(), pid)
                pack_value = context_data.get("pack")
                pack_block = pack_value if isinstance(pack_value, dict) else {}
                available_pack_versions[pid] = str(pack_block.get("version") or "").strip()
                for cid in _capabilities_from_context(context_data):
                    available_capability_ids.add(cid)
            except Exception:
                if not pack.get("capabilities"):
                    pass

    # Validate each pack's declared requires
    for pack in capability_packs:
        if not isinstance(pack, dict):
            continue
        raw_path = pack.get("pack_source_path")
        pack_id = str(pack.get("id") or pack.get("pack_id") or pack.get("capability_pack_id") or "").strip()
        if not raw_path or not pack_id:
            continue

        contract = _read_pack_contract(Path(raw_path))
        required_packs, required_caps = _extract_requires(contract)

        missing_packs = [p["pack_id"] for p in required_packs if p["pack_id"] not in available_pack_ids]
        missing_caps = [c for c in required_caps if c not in available_capability_ids]
        version_mismatches: list[str] = []
        for required in required_packs:
            required_version = required.get("version")
            if not required_version or required["pack_id"] not in available_pack_ids:
                continue
            installed_version = available_pack_versions.get(required["pack_id"], "")
            if installed_version != required_version:
                version_mismatches.append(
                    f"{required['pack_id']} expected {required_version}, installed {installed_version or '<missing>'}"
                )

        if missing_packs or missing_caps or version_mismatches:
            raise PackDependencyError(
                pack_id,
                missing_packs=missing_packs,
                missing_capabilities=missing_caps,
                version_mismatches=version_mismatches,
            )


def _safe_asset_files(context_root: Path, context: dict[str, Any]) -> list[Path]:
    files: list[Path] = [context_root / "context.yaml"]
    seen: set[Path] = {files[0].resolve()}
    for asset in iter_context_assets(context):
        try:
            asset_path = resolve_context_asset_path(context_root, asset)
        except BuildContextError as exc:
            raise PackIntegrityError(str(exc)) from exc
        if not asset_path.exists():
            raise PackIntegrityError(f"Declared pack asset not found: {asset_path}")
        if asset_path.is_file():
            resolved = asset_path.resolve()
            if resolved not in seen:
                files.append(asset_path)
                seen.add(resolved)
            continue
        if not asset_path.is_dir():
            raise PackIntegrityError(f"Declared pack asset must be a file or directory: {asset_path}")
        for path in sorted(item for item in asset_path.rglob("*") if item.is_file()):
            relative = path.relative_to(asset_path)
            if any(part == "__pycache__" or part.startswith(".") for part in relative.parts):
                continue
            if path.suffix in {".pyc", ".pyo"}:
                continue
            resolved = path.resolve()
            if resolved not in seen:
                files.append(path)
                seen.add(resolved)
    return files


def compute_pack_digest(pack_source_path: Path, pack_id: str) -> str:
    """Return the canonical SHA-256 digest for declared pack-owned content."""

    context_root = pack_source_path.resolve()
    context = _read_pack_context(context_root, pack_id)
    entries: list[dict[str, str]] = []
    for path in _safe_asset_files(context_root, context):
        try:
            relative = path.resolve().relative_to(context_root).as_posix()
        except ValueError as exc:
            raise PackIntegrityError(f"Pack digest path escapes context root: {path}") from exc
        entries.append({
            "path": relative,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    payload = {
        "schema_version": "mozaiks.pack_digest.v1",
        "pack_id": pack_id,
        "files": sorted(entries, key=lambda item: item["path"]),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _validate_pack_contract_schema(pack_source_path: Path, contract: dict[str, Any]) -> None:
    schema_version = str(contract.get("schema_version") or "").strip()
    contract_type = str(contract.get("contract_type") or "").strip()
    if schema_version == "mozaiks.provider_api_contract.v1":
        allowed_provider_root = {
            "schema_version",
            "contract_id",
            "description",
            "auth",
            "endpoints",
            "error_handling",
            "drift_guard",
        }
        unknown_provider = sorted(set(contract) - allowed_provider_root)
        if unknown_provider:
            raise PackIntegrityError(
                f"{pack_source_path / 'provider_api_contract.yaml'} has unsupported fields: {unknown_provider}"
            )
        if not isinstance(contract.get("endpoints"), list):
            raise PackIntegrityError("provider_api_contract.yaml endpoints must be a list")
        return

    if contract_type != "build_pack_instructions":
        raise PackIntegrityError(
            f"{pack_source_path / 'contract.yaml'} contract_type must be build_pack_instructions"
        )

    allowed_root = {
        "contract_id",
        "contract_type",
        "requires",
        "cross_pack_integrations",
        "selection_rules",
        "recommended_domains",
        "required_integrations",
        "required_outputs",
        "forbidden_outputs",
        "runtime_boundaries",
        "facades",
        "provides_capabilities",
        "inactive_surfaces",
        "managed_assignment_writer_contract",
        "provider_api_response_contract",
        "provider_lifecycle_boundary",
    }
    unknown = sorted(set(contract) - allowed_root)
    if unknown:
        raise PackIntegrityError(f"{pack_source_path / 'contract.yaml'} has unsupported fields: {unknown}")

    requires = contract.get("requires")
    if requires is not None:
        if not isinstance(requires, dict):
            raise PackIntegrityError("contract.yaml requires must be a mapping")
        unknown_requires = sorted(set(requires) - {"packs", "capabilities"})
        if unknown_requires:
            raise PackIntegrityError(f"contract.yaml requires has unsupported fields: {unknown_requires}")
        packs = requires.get("packs")
        if packs is not None:
            if not isinstance(packs, list):
                raise PackIntegrityError("contract.yaml requires.packs must be a list")
            for index, item in enumerate(packs):
                if isinstance(item, str):
                    continue
                if not isinstance(item, dict):
                    raise PackIntegrityError(f"contract.yaml requires.packs[{index}] must be a string or mapping")
                unknown = sorted(set(item) - {"pack_id", "version", "reason"})
                if unknown:
                    raise PackIntegrityError(
                        f"contract.yaml requires.packs[{index}] has unsupported fields: {unknown}"
                    )
                if not str(item.get("pack_id") or "").strip():
                    raise PackIntegrityError(f"contract.yaml requires.packs[{index}].pack_id is required")
        capabilities = requires.get("capabilities")
        if capabilities is not None:
            if not isinstance(capabilities, list):
                raise PackIntegrityError("contract.yaml requires.capabilities must be a list")
            for index, item in enumerate(capabilities):
                if isinstance(item, str):
                    continue
                if not isinstance(item, dict):
                    raise PackIntegrityError(
                        f"contract.yaml requires.capabilities[{index}] must be a string or mapping"
                    )
                unknown = sorted(set(item) - {"capability_id", "reason"})
                if unknown:
                    raise PackIntegrityError(
                        f"contract.yaml requires.capabilities[{index}] has unsupported fields: {unknown}"
                    )
                if not str(item.get("capability_id") or "").strip():
                    raise PackIntegrityError(
                        f"contract.yaml requires.capabilities[{index}].capability_id is required"
                    )

    for field in ("selection_rules", "recommended_domains", "required_outputs", "forbidden_outputs", "runtime_boundaries", "facades", "provides_capabilities"):
        value = contract.get(field)
        if value is not None and not isinstance(value, list):
            raise PackIntegrityError(f"contract.yaml {field} must be a list")


def verify_pack_integrity(pack_source_path: Path, pack_id: str) -> dict[str, Any]:
    """Verify a local pack before materialization and return canonical metadata."""

    if not _is_safe_identifier(pack_id):
        raise PackIntegrityError(
            f"Unsafe pack_id '{pack_id}': must be a simple identifier without path separators or traversal sequences"
        )
    context_root = pack_source_path.resolve()
    context = _read_pack_context(context_root, pack_id)
    pack_block = context.get("pack") if isinstance(context.get("pack"), dict) else {}
    if not isinstance(pack_block, dict):
        raise PackIntegrityError(f"Pack '{pack_id}' must declare a pack block before materialization")
    version = str(pack_block.get("version") or "").strip()
    if not version:
        raise PackIntegrityError(f"Pack '{pack_id}' must declare pack.version before materialization")
    _safe_asset_files(context_root, context)
    for asset in iter_context_assets(context, kind="catalog"):
        path = resolve_context_asset_path(context_root, asset)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise PackIntegrityError(f"Catalog asset must be a YAML mapping: {path}")
    for asset in iter_context_assets(context, kind="contract"):
        path = resolve_context_asset_path(context_root, asset)
        contract_data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(contract_data, dict):
            raise PackIntegrityError(f"Contract asset must be a YAML mapping: {path}")
        _validate_pack_contract_schema(context_root, contract_data)
    return {
        "pack_id": pack_id,
        "version": version,
        "source": _normalized_source(pack_block, context),
        "digest": compute_pack_digest(context_root, pack_id),
    }


def _file_owner_map(pack_source_path: Path) -> dict[str, str]:
    """Return {output_path: owner} from a pack's contract.yaml required_outputs."""
    contract = _read_pack_contract(pack_source_path)
    owner_map: dict[str, str] = {}
    for entry in contract.get("required_outputs") or []:
        if isinstance(entry, dict):
            path = str(entry.get("path") or "").strip()
            owner = str(entry.get("owner") or "templates").strip()
            if path:
                owner_map[path] = owner
    return owner_map


def _build_provenance_manifest(
    pack_file_map: list[tuple[dict[str, Any], list[dict[str, str]]]],
) -> str:
    """Build a provenance manifest JSON string.

    Args:
        pack_file_map: List of (pack_metadata, [file_entries]) tuples.

    Returns:
        JSON string for ``.mozaiks/pack_provenance.json``.
    """
    try:
        from mozaiksai.version import __version__ as framework_version
    except Exception:
        framework_version = "unknown"

    packs_list = []
    for metadata, file_entries in pack_file_map:
        packs_list.append({
            "pack_id": metadata["pack_id"],
            "version": metadata["version"],
            "source": metadata["source"],
            "digest": metadata["digest"],
            "materialized_owned_files": file_entries,
        })

    manifest = {
        "schema_version": _PROVENANCE_SCHEMA_VERSION,
        "framework_version": framework_version,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "packs": packs_list,
    }
    return json.dumps(manifest, indent=2)


def _template_output_path(path: Path, templates_root: Path) -> str:
    try:
        relative = path.resolve().relative_to(templates_root.resolve())
    except ValueError as exc:
        raise ManagedCapabilityTemplateError(f"Template path escapes templates root: {path}") from exc
    output_path = relative.as_posix()
    if output_path.endswith(".j2"):
        output_path = output_path[: -len(".j2")]
    if not output_path or output_path.startswith("/") or ".." in output_path.split("/"):
        raise ManagedCapabilityTemplateError(f"Unsafe template output path: {output_path}")
    return output_path


def _template_roots(context_root: Path, context: dict[str, Any]) -> list[Path]:
    roots: list[Path] = []
    for asset in iter_context_assets(context, kind="templates"):
        try:
            path = resolve_context_asset_path(context_root, asset)
        except BuildContextError as exc:
            raise ManagedCapabilityTemplateError(str(exc)) from exc
        if not path.exists():
            raise ManagedCapabilityTemplateError(f"Declared pack templates asset not found: {path}")
        if not path.is_dir():
            raise ManagedCapabilityTemplateError(f"Pack templates asset must be a directory: {path}")
        roots.append(path)
    return roots


def _is_materializable_template_file(path: Path, templates_root: Path) -> bool:
    relative = path.relative_to(templates_root)
    if any(part == "__pycache__" or part.startswith(".") for part in relative.parts):
        return False
    if path.suffix in {".pyc", ".pyo"}:
        return False
    return True


def _template_variables(
    *,
    pack: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    variables: dict[str, Any] = {}
    for source in (context, pack):
        for key, value in source.items():
            if key in {"assets", "capabilities", "facades", "pack"}:
                continue
            variables[key] = value
    variables.setdefault("pack_id", str(pack.get("id") or pack.get("pack_id") or ""))
    variables.setdefault("capability_source", str(pack.get("capability_source") or ""))
    return variables


def _context_to_dict(context_variables: Any | None) -> dict[str, Any]:
    if context_variables is None:
        return {}
    if isinstance(context_variables, dict):
        return dict(context_variables)
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return dict(data)
    if hasattr(context_variables, "get"):
        try:
            keys = [
                "readiness_profile",
                "evidence_mode",
                "evidence_ledger_path",
                "launch_check_command",
                "monetization_check_command",
                "pack_source_path",
            ]
            result: dict[str, Any] = {}
            for key in keys:
                value = context_variables.get(key)
                if value is not None:
                    result[key] = value
            return result
        except Exception:
            return {}
    return {}


def resolve_templates_for_pack(
    pack_source_path: Path,
    pack_id: str,
    *,
    context_variables: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Return all files under a selected pack's ``templates/`` tree."""

    if not _is_safe_identifier(pack_id):
        raise ManagedCapabilityTemplateError(
            f"Unsafe pack_id '{pack_id}': must be a simple identifier without path separators or traversal sequences"
        )

    context_root = pack_source_path.resolve()
    context = _read_pack_context(context_root, pack_id)
    _pack_raw = context.get("pack")
    pack: dict[str, Any] = _pack_raw if isinstance(_pack_raw, dict) else {}
    if str(pack.get("status") or "active").strip() != "active":
        logger.info("Skipping template materialization for inactive pack '%s'", pack_id)
        return []

    template_vars = _template_variables(pack=pack, context={**context, **_context_to_dict(context_variables)})
    template_roots = _template_roots(context_root, context)
    if not template_roots:
        return []

    files: list[dict[str, str]] = []
    by_filename: dict[str, str] = {}
    for templates_root in template_roots:
        for path in sorted(
            item
            for item in templates_root.rglob("*")
            if item.is_file() and _is_materializable_template_file(item, templates_root)
        ):
            output_path = _template_output_path(path, templates_root)
            content = path.read_text(encoding="utf-8")
            if path.suffix == ".j2":
                content = Template(content).render(**template_vars)
            existing = by_filename.get(output_path)
            if existing is not None and existing != content:
                raise ManagedCapabilityTemplateError(
                    f"Pack template assets contain conflicting output '{output_path}'"
                )
            by_filename[output_path] = content
    for filename, content in sorted(by_filename.items()):
        files.append({"filename": filename, "content": content})
    return files


def resolve_managed_capability_templates(
    capability_packs: list[dict[str, Any]] | None,
    *,
    context_variables: Any | None = None,
) -> list[dict[str, str]]:
    """Materialize all template files from selected managed capabilities.

    Extended behaviour (community pack foundation):

    1. **Schema validation** — each pack's ``context.yaml`` is validated against
       the structural allowlist before materialization begins.  Unexpected keys
       and invalid field values raise :class:`ManagedCapabilityTemplateError`.

    2. **Dependency validation** — each pack's ``contract.yaml`` ``requires``
       block is checked against the selected pack list before any templates are
       rendered.  Missing deps raise :class:`PackDependencyError` with structured
       diagnostics.

    3. **Provenance manifest** — after all templates are rendered a
       ``.mozaiks/pack_provenance.json`` entry is appended to the result so the
       generated app bundle can answer "which pack produced this file?"
    """
    if not capability_packs:
        return []

    # --- Step 1: dependency validation (fast fail before rendering) ---
    _validate_pack_dependencies(capability_packs)

    # --- Step 2: render templates, tracking per-pack output for provenance ---
    results_by_filename: dict[str, str] = {}
    # (metadata, owner_map, [produced filenames])
    provenance_entries: list[tuple[dict[str, Any], dict[str, str], list[str]]] = []

    for pack in capability_packs:
        if not isinstance(pack, dict):
            continue
        raw_path = pack.get("pack_source_path")
        pack_id = str(pack.get("id") or pack.get("pack_id") or pack.get("capability_pack_id") or "").strip()
        if not raw_path or not pack_id:
            continue

        if not _is_safe_identifier(pack_id):
            raise ManagedCapabilityTemplateError(
                f"Unsafe pack_id '{pack_id}': must be a simple identifier without path separators or traversal sequences"
            )
        pack_source_path = Path(raw_path)
        metadata = verify_pack_integrity(pack_source_path, pack_id)

        # Ownership map: output_path → owner (from contract.yaml required_outputs)
        owner_map = _file_owner_map(pack_source_path)

        pack_files: list[str] = []
        for file in resolve_templates_for_pack(
            pack_source_path,
            pack_id,
            context_variables=context_variables or {},
        ):
            filename = file["filename"]
            content = file["content"]
            existing = results_by_filename.get(filename)
            if existing is not None and existing != content:
                raise ManagedCapabilityTemplateError(
                    f"Multiple selected pack templates resolve to '{filename}'"
                )
            results_by_filename[filename] = content
            pack_files.append(filename)

        if pack_files:
            provenance_entries.append((metadata, owner_map, pack_files))

    # --- Step 3: emit provenance manifest ---
    if provenance_entries:
        pack_file_map = [
            (
                metadata,
                [
                    {"path": f, "owner": omap.get(f, "templates")}
                    for f in sorted(files)
                ],
            )
            for metadata, omap, files in provenance_entries
        ]
        provenance_json = _build_provenance_manifest(pack_file_map)
        existing_prov = results_by_filename.get(_PROVENANCE_PATH)
        if existing_prov is None:
            results_by_filename[_PROVENANCE_PATH] = provenance_json

    return [
        {"filename": filename, "content": content}
        for filename, content in sorted(results_by_filename.items())
    ]


__all__ = [
    "compute_pack_digest",
    "ManagedCapabilityTemplateError",
    "PackDependencyError",
    "PackIntegrityError",
    "resolve_managed_capability_templates",
    "resolve_templates_for_pack",
    "verify_pack_integrity",
]
