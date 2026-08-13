"""Local Community Component install and upgrade lifecycle operations."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .community_component_state import (
    InstalledComponentStateError,
    canonical_component_entry,
    component_by_id,
    load_installed_component_sources,
    load_installed_components,
    write_installed_component_sources,
    write_installed_components,
)
from .generated_bundle_scanner import scan_generated_bundle
from .resolve_managed_capability_templates import (
    PackDependencyError,
    PackIntegrityError,
    _capabilities_from_context,
    _extract_requires,
    _read_pack_context,
    _read_pack_contract,
    resolve_managed_capability_templates,
    resolve_templates_for_pack,
    verify_pack_integrity,
)

_PROVENANCE_PATH = ".mozaiks/pack_provenance.json"


class CommunityComponentLifecycleError(ValueError):
    """Raised when local component lifecycle validation fails."""


class CommunityComponentConflictError(CommunityComponentLifecycleError):
    """Raised when install or upgrade would overwrite non-owned changes."""


@dataclass(frozen=True)
class InstallResult:
    status: str
    pack_id: str
    version: str
    digest: str
    installed_state_path: str
    source_state_path: str


@dataclass(frozen=True)
class UpgradePlan:
    status: str
    identity_match: bool
    pack_id: str
    from_version: str
    to_version: str
    from_digest: str
    to_digest: str
    dependency_changes: dict[str, list[dict[str, str]]]
    owned_file_changes: dict[str, list[str]]
    added_files: list[str] = field(default_factory=list)
    removed_files: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    potential_conflicts: list[dict[str, str]] = field(default_factory=list)


def _pack_id_from_context(pack_source_path: Path) -> str:
    context = yaml.safe_load((pack_source_path / "context.yaml").read_text(encoding="utf-8")) or {}
    if not isinstance(context, dict):
        raise CommunityComponentLifecycleError(f"Pack context must be a mapping: {pack_source_path / 'context.yaml'}")
    raw_pack = context.get("pack")
    pack: dict[str, Any] = raw_pack if isinstance(raw_pack, dict) else {}
    pack_id = str(pack.get("id") or context.get("context_id") or "").strip()
    if not pack_id:
        raise CommunityComponentLifecycleError(f"Pack context does not declare a pack id: {pack_source_path}")
    return pack_id


def _dependency_block(contract: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    packs, capabilities = _extract_requires(contract)
    return {
        "packs": sorted(packs, key=lambda item: item["pack_id"]),
        "capabilities": [{"capability_id": cap} for cap in sorted(capabilities)],
    }


def _component_entry_from_verified_pack(pack_source_path: Path, pack_id: str) -> dict[str, Any]:
    metadata = verify_pack_integrity(pack_source_path, pack_id)
    context = _read_pack_context(pack_source_path.resolve(), pack_id)
    contract = _read_pack_contract(pack_source_path.resolve())
    return canonical_component_entry({
        "pack_id": metadata["pack_id"],
        "version": metadata["version"],
        "digest": metadata["digest"],
        "source": metadata["source"],
        "dependencies": _dependency_block(contract),
        "capabilities": [
            {"capability_id": cap_id}
            for cap_id in sorted(_capabilities_from_context(context))
        ],
    })


def _ensure_dependencies_satisfied(candidate: dict[str, Any], installed: dict[str, dict[str, Any]]) -> None:
    missing_packs: list[str] = []
    missing_capabilities: list[str] = []
    version_mismatches: list[str] = []

    for dependency in candidate["dependencies"]["packs"]:
        dep_id = dependency["pack_id"]
        installed_dep = installed.get(dep_id)
        if not installed_dep:
            missing_packs.append(dep_id)
            continue
        required_version = dependency.get("version")
        if required_version and installed_dep["version"] != required_version:
            version_mismatches.append(f"{dep_id} expected {required_version}, installed {installed_dep['version']}")

    provided_capabilities = {
        cap["capability_id"]
        for component in installed.values()
        for cap in component.get("capabilities") or []
        if isinstance(cap, dict)
    }
    provided_capabilities.update(
        cap["capability_id"]
        for cap in candidate.get("capabilities") or []
        if isinstance(cap, dict)
    )
    for dependency in candidate["dependencies"]["capabilities"]:
        cap_id = dependency["capability_id"]
        if cap_id not in provided_capabilities:
            missing_capabilities.append(cap_id)

    if missing_packs or missing_capabilities or version_mismatches:
        raise PackDependencyError(
            candidate["pack_id"],
            missing_packs=missing_packs,
            missing_capabilities=missing_capabilities,
            version_mismatches=version_mismatches,
        )


def install_verified_local_pack(
    *,
    pack_source_path: str | Path,
    build_context_root: str | Path,
) -> InstallResult:
    """Verify a local pack and pin it in workspace build-context state."""

    root = Path(build_context_root).resolve()
    source_path = Path(pack_source_path).resolve()
    pack_id = _pack_id_from_context(source_path)
    candidate = _component_entry_from_verified_pack(source_path, pack_id)
    state = load_installed_components(root)
    installed = component_by_id(state)
    existing = installed.get(pack_id)
    if existing and existing != candidate:
        raise CommunityComponentLifecycleError(
            f"Pack '{pack_id}' is already installed at {existing['version']} {existing['digest']}; "
            "use the deterministic upgrade plan instead."
        )

    _ensure_dependencies_satisfied(candidate, {k: v for k, v in installed.items() if k != pack_id})
    installed[pack_id] = candidate
    state_path = write_installed_components(root, {"components": list(installed.values())})
    sources = load_installed_component_sources(root)
    sources[pack_id] = str(source_path)
    source_state_path = write_installed_component_sources(root, sources)
    return InstallResult(
        status="installed" if existing is None else "unchanged",
        pack_id=pack_id,
        version=candidate["version"],
        digest=candidate["digest"],
        installed_state_path=str(state_path),
        source_state_path=str(source_state_path),
    )


def resolve_installed_component_descriptors(
    *,
    build_context_root: str | Path,
    selected_pack_ids: list[str],
) -> list[dict[str, Any]]:
    """Resolve explicitly selected installed components to materializer descriptors."""

    root = Path(build_context_root).resolve()
    state = load_installed_components(root)
    installed = component_by_id(state)
    sources = load_installed_component_sources(root)
    descriptors: list[dict[str, Any]] = []
    for pack_id in selected_pack_ids:
        installed_component = installed.get(pack_id)
        if not installed_component:
            raise InstalledComponentStateError(f"Selected pack is not installed: {pack_id}")
        source = sources.get(pack_id)
        if not source:
            raise InstalledComponentStateError(f"Installed pack has no non-portable local source sidecar: {pack_id}")
        metadata = verify_pack_integrity(Path(source), pack_id)
        if metadata["version"] != installed_component["version"] or metadata["digest"] != installed_component["digest"]:
            raise PackIntegrityError(f"Installed pack source no longer matches pinned state: {pack_id}")
        descriptor = {
            "id": pack_id,
            "pack_id": pack_id,
            "capability_pack_id": pack_id,
            "version": installed_component["version"],
            "digest": installed_component["digest"],
            "source": installed_component["source"],
            "capabilities": installed_component.get("capabilities") or [],
            "dependencies": installed_component.get("dependencies") or {"packs": [], "capabilities": []},
            "pack_source_path": source,
            "capability_source": "generated_module",
        }
        descriptors.append(descriptor)
    return descriptors


def _files_by_name(files: list[dict[str, str]]) -> dict[str, str]:
    return {str(file["filename"]): str(file["content"]) for file in files if file.get("filename")}


def _render_owned_files(pack_source_path: Path, pack_id: str) -> dict[str, str]:
    return _files_by_name(resolve_templates_for_pack(pack_source_path, pack_id))


def _provenance_pack_entries(app_root: Path) -> list[dict[str, Any]]:
    path = app_root.resolve() / _PROVENANCE_PATH
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CommunityComponentLifecycleError(f"Invalid pack provenance at {path}: {exc}") from exc
    packs = data.get("packs") if isinstance(data, dict) else None
    return [item for item in packs or [] if isinstance(item, dict)]


def _current_owner_by_path(app_root: Path) -> dict[str, str]:
    owner_by_path: dict[str, str] = {}
    for pack in _provenance_pack_entries(app_root):
        pack_id = str(pack.get("pack_id") or "").strip()
        for file_entry in pack.get("materialized_owned_files") or []:
            if isinstance(file_entry, dict) and file_entry.get("path"):
                owner_by_path[str(file_entry["path"])] = pack_id
    return owner_by_path


def plan_component_upgrade(
    *,
    build_context_root: str | Path,
    candidate_pack_source_path: str | Path,
    workspace_app_root: str | Path | None = None,
) -> UpgradePlan:
    """Dry-run a deterministic upgrade from installed pack state to a local candidate."""

    root = Path(build_context_root).resolve()
    candidate_source = Path(candidate_pack_source_path).resolve()
    pack_id = _pack_id_from_context(candidate_source)
    state = load_installed_components(root)
    installed = component_by_id(state)
    current = installed.get(pack_id)
    if not current:
        raise CommunityComponentLifecycleError(f"Candidate pack is not installed yet: {pack_id}")
    candidate = _component_entry_from_verified_pack(candidate_source, pack_id)
    _ensure_dependencies_satisfied(candidate, {k: v for k, v in installed.items() if k != pack_id})

    sources = load_installed_component_sources(root)
    old_source_raw = sources.get(pack_id)
    if not old_source_raw:
        raise CommunityComponentLifecycleError(f"Installed pack has no old local source sidecar: {pack_id}")
    old_source = Path(old_source_raw).resolve()
    old_files = _render_owned_files(old_source, pack_id)
    new_files = _render_owned_files(candidate_source, pack_id)

    old_paths = set(old_files)
    new_paths = set(new_files)
    added = sorted(new_paths - old_paths)
    removed = sorted(old_paths - new_paths)
    changed = sorted(path for path in old_paths & new_paths if old_files[path] != new_files[path])
    potential_conflicts: list[dict[str, str]] = []

    app_root = Path(workspace_app_root).resolve() if workspace_app_root else None
    if app_root:
        owner_by_path = _current_owner_by_path(app_root)
        for path in sorted(added + changed):
            owner = owner_by_path.get(path)
            if owner and owner != pack_id:
                potential_conflicts.append({"path": path, "kind": "owned_by_other_pack", "owner": owner})
            target = app_root / path
            if target.exists() and not owner:
                potential_conflicts.append({"path": path, "kind": "workspace_owned_file"})
        for path in sorted(changed + removed):
            target = app_root / path
            if not target.exists():
                continue
            current_content = target.read_text(encoding="utf-8")
            if current_content != old_files[path]:
                potential_conflicts.append({"path": path, "kind": "locally_modified_owned_file"})

    dependency_changes = {
        "added_packs": [item for item in candidate["dependencies"]["packs"] if item not in current["dependencies"]["packs"]],
        "removed_packs": [item for item in current["dependencies"]["packs"] if item not in candidate["dependencies"]["packs"]],
        "added_capabilities": [
            item for item in candidate["dependencies"]["capabilities"] if item not in current["dependencies"]["capabilities"]
        ],
        "removed_capabilities": [
            item for item in current["dependencies"]["capabilities"] if item not in candidate["dependencies"]["capabilities"]
        ],
    }
    return UpgradePlan(
        status="blocked" if potential_conflicts else "ready",
        identity_match=current["pack_id"] == candidate["pack_id"],
        pack_id=pack_id,
        from_version=current["version"],
        to_version=candidate["version"],
        from_digest=current["digest"],
        to_digest=candidate["digest"],
        dependency_changes=dependency_changes,
        owned_file_changes={
            "added": added,
            "removed": removed,
            "changed": changed,
        },
        added_files=added,
        removed_files=removed,
        changed_files=changed,
        potential_conflicts=potential_conflicts,
    )


def apply_component_upgrade(
    *,
    build_context_root: str | Path,
    candidate_pack_source_path: str | Path,
    workspace_app_root: str | Path,
) -> UpgradePlan:
    """Apply a safe local component upgrade and update installed state/provenance."""

    root = Path(build_context_root).resolve()
    app_root = Path(workspace_app_root).resolve()
    candidate_source = Path(candidate_pack_source_path).resolve()
    pack_id = _pack_id_from_context(candidate_source)
    plan = plan_component_upgrade(
        build_context_root=root,
        candidate_pack_source_path=candidate_source,
        workspace_app_root=app_root,
    )
    if plan.potential_conflicts:
        raise CommunityComponentConflictError(f"Upgrade for '{pack_id}' has conflicts: {plan.potential_conflicts}")

    candidate = _component_entry_from_verified_pack(candidate_source, pack_id)
    descriptors = [{"id": pack_id, "pack_id": pack_id, "pack_source_path": str(candidate_source)}]
    rendered = _files_by_name(resolve_managed_capability_templates(descriptors))
    rendered.pop(_PROVENANCE_PATH, None)
    for path in sorted(plan.added_files + plan.changed_files):
        target = app_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered[path], encoding="utf-8")
    for path in plan.removed_files:
        target = app_root / path
        if target.exists():
            target.unlink()

    provenance = _files_by_name(resolve_managed_capability_templates(descriptors))[_PROVENANCE_PATH]
    (app_root / _PROVENANCE_PATH).parent.mkdir(parents=True, exist_ok=True)
    (app_root / _PROVENANCE_PATH).write_text(provenance, encoding="utf-8")

    state = load_installed_components(root)
    installed = component_by_id(state)
    installed[pack_id] = candidate
    write_installed_components(root, {"components": list(installed.values())})
    sources = load_installed_component_sources(root)
    sources[pack_id] = str(candidate_source)
    write_installed_component_sources(root, sources)

    scanner_errors = scan_generated_bundle({
        path.relative_to(app_root).as_posix(): path.read_text(encoding="utf-8")
        for path in app_root.rglob("*")
        if path.is_file()
    })
    provenance_errors = [error for error in scanner_errors if "pack_provenance" in error]
    if provenance_errors:
        raise CommunityComponentLifecycleError(f"Updated component provenance failed validation: {provenance_errors}")
    return plan


__all__ = [
    "CommunityComponentConflictError",
    "CommunityComponentLifecycleError",
    "InstallResult",
    "UpgradePlan",
    "apply_component_upgrade",
    "install_verified_local_pack",
    "plan_component_upgrade",
    "resolve_installed_component_descriptors",
]
