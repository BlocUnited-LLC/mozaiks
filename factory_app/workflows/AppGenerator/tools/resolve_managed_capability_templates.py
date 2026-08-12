"""Materialize deterministic files from selected build-context packs."""

from __future__ import annotations

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
    ) -> None:
        self.pack_id = pack_id
        self.missing_packs: list[str] = missing_packs or []
        self.missing_capabilities: list[str] = missing_capabilities or []
        parts: list[str] = []
        if self.missing_packs:
            parts.append(f"missing required packs: {self.missing_packs}")
        if self.missing_capabilities:
            parts.append(f"missing required capabilities: {self.missing_capabilities}")
        super().__init__(
            f"Pack '{pack_id}' dependency check failed — {'; '.join(parts)}. "
            "Ensure all required packs are included in the selected capability_packs list."
        )


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
        logger.warning("Cannot read pack contract at %s: %s", contract_path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _extract_requires(contract: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return (required_pack_ids, required_capability_ids) from a contract dict.

    Supports two declaration forms:
      packs: [pack_id, ...]                      # simple list of strings
      packs: [{pack_id: name, reason: ...}, ...] # list of dicts with pack_id key
    """
    requires = contract.get("requires")
    if not isinstance(requires, dict):
        return [], []

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

    pack_ids = _extract_ids(requires.get("packs"), "pack_id")
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
        if raw_path and not pack.get("capabilities") and pid and _is_safe_identifier(pid):
            try:
                context_data = _read_pack_context(Path(raw_path).resolve(), pid)
                for cid in _capabilities_from_context(context_data):
                    available_capability_ids.add(cid)
            except Exception:
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

        missing_packs = [p for p in required_packs if p not in available_pack_ids]
        missing_caps = [c for c in required_caps if c not in available_capability_ids]

        if missing_packs or missing_caps:
            raise PackDependencyError(
                pack_id,
                missing_packs=missing_packs,
                missing_capabilities=missing_caps,
            )


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
    pack_file_map: list[tuple[str, str, list[dict[str, str]]]],
) -> str:
    """Build a provenance manifest JSON string.

    Args:
        pack_file_map: List of (pack_id, pack_version, [file_entries]) tuples.

    Returns:
        JSON string for ``.mozaiks/pack_provenance.json``.
    """
    try:
        from mozaiksai.version import __version__ as framework_version
    except Exception:
        framework_version = "unknown"

    packs_list = []
    for pack_id, pack_version, file_entries in pack_file_map:
        packs_list.append({
            "pack_id": pack_id,
            "pack_version": pack_version or "",
            "files": file_entries,
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
    # (pack_id, pack_version, owner_map, [produced filenames])
    provenance_entries: list[tuple[str, str, dict[str, str], list[str]]] = []

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
        # Read pack version from context.yaml pack block (optional field)
        context_data = _read_pack_context(pack_source_path.resolve(), pack_id)
        pack_block = context_data.get("pack") or {}
        pack_version = str(pack_block.get("version") or "").strip()

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
            provenance_entries.append((pack_id, pack_version, owner_map, pack_files))

    # --- Step 3: emit provenance manifest ---
    if provenance_entries:
        pack_file_map = [
            (
                pid,
                pver,
                [
                    {"path": f, "owner": omap.get(f, "templates")}
                    for f in sorted(files)
                ],
            )
            for pid, pver, omap, files in provenance_entries
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
    "ManagedCapabilityTemplateError",
    "PackDependencyError",
    "resolve_managed_capability_templates",
    "resolve_templates_for_pack",
]
