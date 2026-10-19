"""Local Community Component install and upgrade lifecycle operations."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from mozaiksai.core.runtime.persistence.intent_loader import load_data_contract
from mozaiksai.core.workflow.generator_support.code_files import compile_data_contract
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    SHARED_PACK_OUTPUT_PATHS,
    SHARED_UI_OUTPUT_PATHS,
)

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
    _file_owner_map,
    _read_pack_context,
    _read_pack_contract,
    pre_contract_rule_digest,
    resolve_managed_capability_templates,
    resolve_templates_for_pack,
    verified_pack_data_contracts,
    verify_pack_integrity,
)

_PROVENANCE_PATH = ".mozaiks/pack_provenance.json"
# The resolver composes every shared root for every caller; a pack never owns one.
_DATA_CONTRACT_PATH = "data/contract.json"
_REASSEMBLE_REMEDY = (
    "A shared composition root is composed from the app's and every pack's declarations, never one "
    "pack's file; provenance that claims one as a pack's exclusive file predates composition. Reassemble "
    "the app to recompose its shared roots, then plan the upgrade again."
)


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
    recomposed_files: list[str] = field(default_factory=list)


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


def _uncovered_contract_refusal(pack_id: str, source: Path, recorded: dict[str, Any]) -> str | None:
    """Why an installed pin cannot verify a source whose ``contract.yaml`` the pin never covered; None otherwise.

    The pin was recorded with a digest that left out an undeclared
    ``contract.yaml``: before pack digests covered it, or before the file
    existed. Its declared files, and the capabilities they declare, still
    verify to that pin. The one thing the pin recorded from the contract, its
    dependencies, is compared and reported, but that cannot show that the
    contract the pack was installed with (ownership, provided capabilities,
    restrictions) is the one in the source now, so nothing adopts it: the
    refusal names the installation boundary that verifies and pins a whole pack
    instead.
    """
    if pre_contract_rule_digest(source, pack_id) != recorded["digest"]:
        return None
    current = _component_entry_from_verified_pack(source, pack_id)
    if current["version"] != recorded["version"]:
        return None
    if current["dependencies"] == recorded["dependencies"]:
        dependencies = "the dependencies it recorded from the contract match"
    else:
        dependencies = (
            "the dependencies it recorded from the contract differ (recorded "
            f"{recorded['dependencies']!r}, source now {current['dependencies']!r})"
        )
    return (
        f"Installed pack '{pack_id}' was pinned with a digest that does not cover its contract.yaml (pinned before "
        "pack digests covered an undeclared contract.yaml, or before that file existed), so the contract it was "
        f"installed with cannot be verified against {source}. Of what the pin recorded, the version, the declared "
        f"files and the capabilities they declare match the source, and {dependencies}; that does not show that "
        "the contract's ownership, provided capabilities or restrictions are unchanged, and the lifecycle does not "
        "adopt a contract its pin never covered. To adopt the current contract, install the pack from its "
        "verified source into a build context that does not hold this pin: installation verifies and pins the "
        "whole pack, contract.yaml included. Nothing was changed."
    )


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
    """Verify a local pack and pin it in workspace build-context state.

    A pack already installed with another identity is refused: a change goes
    through the upgrade plan, and a pin that never covered the pack's
    ``contract.yaml`` is refused with its cause (:func:`_uncovered_contract_refusal`);
    installation never re-labels an existing pin.
    """

    root = Path(build_context_root).resolve()
    source_path = Path(pack_source_path).resolve()
    pack_id = _pack_id_from_context(source_path)
    candidate = _component_entry_from_verified_pack(source_path, pack_id)
    state = load_installed_components(root)
    installed = component_by_id(state)
    existing = installed.get(pack_id)
    if existing and existing != candidate:
        refusal = _uncovered_contract_refusal(pack_id, source_path, existing)
        if refusal is not None:
            raise CommunityComponentLifecycleError(refusal)
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
            refusal = _uncovered_contract_refusal(pack_id, Path(source), installed_component)
            raise PackIntegrityError(refusal or f"Installed pack source no longer matches pinned state: {pack_id}")
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


def _render_pack(
    pack_source_path: Path, pack_id: str,
) -> tuple[dict[str, str], dict[str, str], list[dict[str, Any]]]:
    """Split rendered templates into owned files, composed UI root contributions and verified data contributions."""
    files = resolve_templates_for_pack(pack_source_path, pack_id)
    contributions = verified_pack_data_contracts([
        {"pack_id": pack_id, "owner_map": _file_owner_map(pack_source_path), "files": files},
    ])
    rendered = _files_by_name(files)
    owned = {path: content for path, content in rendered.items() if path not in SHARED_PACK_OUTPUT_PATHS}
    ui_roots = {path: content for path, content in rendered.items() if path in SHARED_UI_OUTPUT_PATHS}
    return owned, ui_roots, contributions


def _recompose_data_contract(
    app_root: Path, old: list[dict[str, Any]], new: list[dict[str, Any]],
) -> tuple[str | None, str | None]:
    """Swap one pack's data contribution in the app contract.

    Returns the recomposed contract text, or the conflict kind that blocks it,
    or neither when an app without a contract has nothing to compose.
    App-owned surfaces and other packs' contributions are preserved.
    """
    try:
        current = load_data_contract(app_root)
        if current is None and not new:
            return None, None
        empty = {"version": "1", "surfaces": [], "shared_collections": []}
        contract = compile_data_contract(current or empty)
        retired = compile_data_contract(empty, pack_data_contracts=old)["surfaces"]
        surfaces = contract.get("surfaces") or []
        if any(row.get("surface_id") == surface["surface_id"] and row != surface for row in surfaces for surface in retired):
            return None, "locally_modified_composed_output"
        contract["surfaces"] = [row for row in surfaces if row not in retired]
        recomposed = compile_data_contract(contract, pack_data_contracts=new)
    except (ValueError, TypeError, AttributeError):
        # The app contract is workspace input; malformed rows block instead of crashing.
        return None, "composed_output_conflict"
    return json.dumps(recomposed, indent=2, ensure_ascii=False), None


def _provenance_manifest(app_root: Path) -> dict[str, Any]:
    path = app_root.resolve() / _PROVENANCE_PATH
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CommunityComponentLifecycleError(f"Invalid pack provenance at {path}: {exc}") from exc
    return data if isinstance(data, dict) else {}


def _provenance_pack_entries(app_root: Path) -> list[dict[str, Any]]:
    packs = _provenance_manifest(app_root).get("packs")
    return [item for item in packs or [] if isinstance(item, dict)]


def _composed_contributors(app_root: Path, path: str) -> set[str]:
    """The pack ids the app's provenance records as contributors to a composed root."""
    composed = _provenance_manifest(app_root).get("composed_outputs")
    rows = composed.get(path) if isinstance(composed, dict) else None
    return {str(row.get("pack_id") or "").strip() for row in rows or [] if isinstance(row, dict)}


def _merged_provenance(app_root: Path, upgraded: dict[str, Any], pack_id: str) -> dict[str, Any]:
    """Replace one pack's provenance entry and composed-root contributions; keep every other pack's."""
    existing = _provenance_manifest(app_root).get("composed_outputs")
    composed: dict[str, list[dict[str, Any]]] = {
        path: [row for row in rows if isinstance(row, dict) and row.get("pack_id") != pack_id]
        for path, rows in (existing.items() if isinstance(existing, dict) else ())
        if isinstance(rows, list)
    }
    for path, rows in (upgraded.get("composed_outputs") or {}).items():
        composed.setdefault(path, []).extend(rows)
    upgraded["packs"] = [
        pack for pack in _provenance_pack_entries(app_root) if str(pack.get("pack_id") or "").strip() != pack_id
    ] + list(upgraded.get("packs") or [])
    upgraded.pop("composed_outputs", None)
    if any(composed.values()):
        upgraded["composed_outputs"] = {path: rows for path, rows in composed.items() if rows}
    return upgraded


def _pack_owned_paths(app_root: Path, pack_id: str) -> set[str]:
    paths: set[str] = set()
    for pack in _provenance_pack_entries(app_root):
        if str(pack.get("pack_id") or "").strip() != pack_id:
            continue
        for file_entry in pack.get("materialized_owned_files") or []:
            if isinstance(file_entry, dict) and file_entry.get("path"):
                paths.add(str(file_entry["path"]))
    return paths


def _current_owner_by_path(app_root: Path) -> dict[str, str]:
    owner_by_path: dict[str, str] = {}
    for pack in _provenance_pack_entries(app_root):
        pack_id = str(pack.get("pack_id") or "").strip()
        for file_entry in pack.get("materialized_owned_files") or []:
            if isinstance(file_entry, dict) and file_entry.get("path"):
                owner_by_path[str(file_entry["path"])] = pack_id
    return owner_by_path


def _exclusive_shared_root_claims(app_root: Path) -> list[dict[str, str]]:
    """Every pack entry that claims a shared composition root as an exclusive file, whatever the order."""
    return sorted(
        ({"path": str(file_entry["path"]), "owner": str(pack.get("pack_id") or "").strip()}
         for pack in _provenance_pack_entries(app_root)
         for file_entry in pack.get("materialized_owned_files") or []
         if isinstance(file_entry, dict) and file_entry.get("path") in SHARED_PACK_OUTPUT_PATHS),
        key=lambda claim: (claim["path"], claim["owner"]),
    )


def plan_component_upgrade(
    *,
    build_context_root: str | Path,
    candidate_pack_source_path: str | Path,
    workspace_app_root: str | Path | None = None,
) -> UpgradePlan:
    """Dry-run a deterministic upgrade from installed pack state to a local candidate."""

    return _plan_upgrade(
        build_context_root=build_context_root,
        candidate_pack_source_path=candidate_pack_source_path,
        workspace_app_root=workspace_app_root,
    )[0]


def _plan_upgrade(
    *,
    build_context_root: str | Path,
    candidate_pack_source_path: str | Path,
    workspace_app_root: str | Path | None,
) -> tuple[UpgradePlan, str | None]:
    """Plan an upgrade and the recomposed data contract its apply step writes."""

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
    # The comparison base is the content the app was materialized from: the old source must still
    # verify to the version and digest installed state pinned, as installed resolution requires.
    old_metadata = verify_pack_integrity(old_source, pack_id)
    if (old_metadata["version"], old_metadata["digest"]) != (current["version"], current["digest"]):
        refusal = _uncovered_contract_refusal(pack_id, old_source, current)
        if refusal is not None:
            raise CommunityComponentLifecycleError(refusal)
        if old_metadata["version"] != current["version"]:
            cause = f"it is now version {old_metadata['version']}"
        else:
            cause = "its bytes changed since installation (an in-place edit, or a re-checkout with other line endings)"
        raise CommunityComponentLifecycleError(
            f"Installed pack '{pack_id}' source {old_source} no longer matches its pinned state (installed "
            f"{current['version']} {current['digest']}, source now {old_metadata['version']} "
            f"{old_metadata['digest']}): {cause}, so the app's files cannot be compared with the content they "
            "were materialized from. Restore that source as it was installed, and plan the upgrade from a "
            "separate candidate directory. Nothing was changed."
        )
    old_files, old_ui_roots, old_data = _render_pack(old_source, pack_id)
    new_files, new_ui_roots, new_data = _render_pack(candidate_source, pack_id)
    recomposed = [_DATA_CONTRACT_PATH] if old_data != new_data else []
    data_contract: str | None = None

    old_paths = set(old_files)
    new_paths = set(new_files)
    added = sorted(new_paths - old_paths)
    removed = sorted(old_paths - new_paths)
    changed = sorted(path for path in old_paths & new_paths if old_files[path] != new_files[path])
    potential_conflicts: list[dict[str, str]] = []

    app_root = Path(workspace_app_root).resolve() if workspace_app_root else None
    if app_root:
        owner_by_path = _current_owner_by_path(app_root)
        # Provenance that claims a shared root as one pack's file predates composition.
        # Nothing is planned on it: the app is reassembled first, whichever pack claims it.
        legacy_claims = _exclusive_shared_root_claims(app_root)
        potential_conflicts.extend({**claim, "kind": "exclusive_shared_root_claim"} for claim in legacy_claims)
        # A composed UI root merges the app's and every contributor's declarations into
        # one file, so one pack's part cannot be swapped in place; assembly rebuilds it.
        # An unchanged contribution keeps the root as it is, provided the app's root
        # was composed with it.
        for path in sorted(set(old_ui_roots) | set(new_ui_roots)):
            if old_ui_roots.get(path) != new_ui_roots.get(path) or not (app_root / path).is_file() or (
                pack_id not in _composed_contributors(app_root, path)
            ):
                potential_conflicts.append({"path": path, "kind": "composed_output_requires_reassembly"})
        for path in sorted(added + changed):
            owner = owner_by_path.get(path)
            if owner and owner != pack_id:
                potential_conflicts.append({"path": path, "kind": "owned_by_other_pack", "owner": owner})
            target = app_root / path
            if target.exists() and not owner:
                potential_conflicts.append({"path": path, "kind": "workspace_owned_file"})
        owned_by_candidate = _pack_owned_paths(app_root, pack_id)
        for path in sorted(changed + removed):
            target = app_root / path
            if not target.exists():
                continue
            if owned_by_candidate and path not in owned_by_candidate:
                potential_conflicts.append({"path": path, "kind": "not_owned_by_installed_pack"})
                continue
            current_content = target.read_text(encoding="utf-8")
            if current_content != old_files[path]:
                potential_conflicts.append({"path": path, "kind": "locally_modified_owned_file"})
        if recomposed and not legacy_claims:
            data_contract, conflict = _recompose_data_contract(app_root, old_data, new_data)
            if conflict:
                potential_conflicts.append({"path": _DATA_CONTRACT_PATH, "kind": conflict})
            elif data_contract is None:
                recomposed = []

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
        recomposed_files=recomposed,
    ), data_contract


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
    plan, data_contract = _plan_upgrade(
        build_context_root=root,
        candidate_pack_source_path=candidate_source,
        workspace_app_root=app_root,
    )
    if plan.potential_conflicts:
        remedy = (
            f" {_REASSEMBLE_REMEDY}"
            if any(conflict["kind"] == "exclusive_shared_root_claim" for conflict in plan.potential_conflicts)
            else ""
        )
        raise CommunityComponentConflictError(
            f"Upgrade for '{pack_id}' has conflicts: {plan.potential_conflicts}.{remedy}"
        )

    candidate = _component_entry_from_verified_pack(candidate_source, pack_id)
    # The candidate is an external source: select it with the identity just verified, as any external pack.
    descriptors = [{
        "id": pack_id, "pack_id": pack_id, "pack_source_path": str(candidate_source),
        "version": candidate["version"], "digest": candidate["digest"],
    }]
    rendered = _files_by_name(resolve_managed_capability_templates(descriptors))
    provenance = _merged_provenance(app_root, json.loads(rendered.pop(_PROVENANCE_PATH)), pack_id)
    # Validate the upgraded provenance against the app as it will be, before anything is written.
    planned = _app_files(app_root)
    planned.update({path: rendered[path] for path in plan.added_files + plan.changed_files})
    for path in plan.removed_files:
        planned.pop(path, None)
    if data_contract is not None:
        planned[_DATA_CONTRACT_PATH] = data_contract
    planned[_PROVENANCE_PATH] = json.dumps(provenance, indent=2, sort_keys=True) + "\n"
    provenance_errors = [error for error in scan_generated_bundle(planned) if "pack_provenance" in error]
    if provenance_errors:
        raise CommunityComponentLifecycleError(f"Updated component provenance failed validation: {provenance_errors}")
    for path in sorted(plan.added_files + plan.changed_files):
        target = app_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered[path], encoding="utf-8")
    for path in plan.removed_files:
        target = app_root / path
        if target.exists():
            target.unlink()
    if data_contract is not None:
        (app_root / _DATA_CONTRACT_PATH).parent.mkdir(parents=True, exist_ok=True)
        (app_root / _DATA_CONTRACT_PATH).write_text(data_contract, encoding="utf-8")

    (app_root / _PROVENANCE_PATH).parent.mkdir(parents=True, exist_ok=True)
    (app_root / _PROVENANCE_PATH).write_text(planned[_PROVENANCE_PATH], encoding="utf-8")

    state = load_installed_components(root)
    installed = component_by_id(state)
    installed[pack_id] = candidate
    write_installed_components(root, {"components": list(installed.values())})
    sources = load_installed_component_sources(root)
    sources[pack_id] = str(candidate_source)
    write_installed_component_sources(root, sources)
    return plan


def _app_files(app_root: Path) -> dict[str, str]:
    """The workspace app's text files by bundle-relative path, as the scanner reads them."""
    return {
        path.relative_to(app_root).as_posix(): path.read_text(encoding="utf-8")
        for path in app_root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and ".pytest_cache" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    }


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
