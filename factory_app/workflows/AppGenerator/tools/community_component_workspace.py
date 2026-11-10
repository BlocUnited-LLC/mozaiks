"""Install, adopt, upgrade and verify Community Components in an existing app workspace.

The base lifecycle (:mod:`community_component_lifecycle`) pins packs and
upgrades packs AppGenerator already materialized. This module extends it to
any existing app: it writes a pack's files into the app, splices the pack's
entries into the app's shared registries, and records what the pack owns with
a content hash, so an edit to a pack-owned file or entry is detected by
``verify`` and blocks upgrades instead of being overwritten.

A pack entry in ``.mozaiks/pack_provenance.json`` written here carries
``"ownership": "content_hash"``. Provenance written by AppGenerator has no such
marker and keeps the base lifecycle's upgrade path.
"""

from __future__ import annotations

import ast
import copy
import json
import os
import stat
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from mozaiksai.core.runtime.app.provenance import resolve_build_timestamp
from mozaiksai.core.semantics.portable_path import PortablePathError, validate_portable_path
from mozaiksai.core.session.build_context import (
    BuildContextError,
    iter_context_assets,
    resolve_context_asset_path,
)
from mozaiksai.core.utils.path_containment import resolve_inside
from mozaiksai.core.workflow.generator_support.code_files import compile_data_contract
from mozaiksai.core.workflow.generator_support.persistence_artifacts import (
    normalize_data_contract_indexes,
)

from .community_component_composition import (
    KEYED_ROOTS,
    OWNERSHIP_CONTENT_HASH,
    WORKFLOW_REGISTRY_PATH,
    CompositionFormatError,
    KeyedRoot,
    content_sha256,
    entry_sha256,
    keyed_entries,
    merged_document,
    new_document_text,
    parse_document,
    splice_document,
)
from .community_component_lifecycle import (
    CommunityComponentConflictError,
    CommunityComponentLifecycleError,
    UpgradePlan,
    _component_entry_from_verified_pack,
    _ensure_dependencies_satisfied,
    _pack_id_from_context,
    apply_component_upgrade,
    plan_component_upgrade,
)
from .community_component_sources import (
    PackSource,
    PackSourceError,
    git_locator,
    materialized_source,
)
from .community_component_state import (
    component_by_id,
    installed_component_sources_path,
    installed_component_sources_text,
    installed_components_path,
    installed_components_text,
    load_installed_component_sources,
    load_installed_components,
)
from .generated_bundle_scanner import scan_pack_provenance
from .resolve_managed_capability_templates import (
    ManagedCapabilityTemplateError,
    PackDependencyError,
    _file_owner_map,
    _read_pack_context,
    _read_pack_contract,
    _safe_asset_files,
    resolve_templates_for_pack,
    verified_pack_data_contracts,
    verify_pack_integrity,
)

_PROVENANCE_PATH = ".mozaiks/pack_provenance.json"
_PROVENANCE_SCHEMA_VERSION = "mozaiks.pack_provenance.v1"
_LOCK_PATH = ".mozaiks/lifecycle.lock"
_DATA_CONTRACT_PATH = "data/contract.json"
_ROUTE_MANIFEST_PATH = "ui/route_manifest.json"
_ROOTS_BY_PATH: dict[str, KeyedRoot] = {root.path: root for root in KEYED_ROOTS}
# Hand-written registration code cannot be merged safely; library UI registers through module barrels.
_UNSUPPORTED_UI_BARRELS = frozenset({"ui/index.js"})
# Metadata a contribution file may carry beside its keyed containers; it is never written to the app.
_CONTRIBUTION_METADATA = {
    "ui/route_manifest.json": frozenset({"version", "schema_version"}),
    "admin/admin_registry.yaml": frozenset({"schema_version"}),
    WORKFLOW_REGISTRY_PATH: frozenset({"pack_name", "version", "extends", "description"}),
}
_NEW_ROOT_DOCUMENTS: dict[str, dict[str, Any]] = {
    "ui/route_manifest.json": {},
    "admin/admin_registry.yaml": {"schema_version": "mozaiks.admin.registry.v1"},
}
_BINARY = object()


class CommunityComponentLockedError(CommunityComponentLifecycleError):
    """Raised when another lifecycle operation holds the workspace lock."""


@dataclass(frozen=True)
class WorkspaceRoots:
    """Where an app workspace keeps its bundle, its workflows and its build context."""

    app_root: Path
    workflows_root: Path
    build_context_root: Path

    def base(self, name: str) -> Path:
        return self.app_root if name == "app" else self.workflows_root


def workspace_roots(
    workspace: str | Path = ".",
    *,
    app_root: str | Path | None = None,
    workflows_root: str | Path | None = None,
    build_context_root: str | Path | None = None,
) -> WorkspaceRoots:
    base = Path(workspace).absolute()
    roots = WorkspaceRoots(
        app_root=Path(app_root).absolute() if app_root else base / "app",
        workflows_root=Path(workflows_root).absolute() if workflows_root else base / "workflows",
        build_context_root=Path(build_context_root).absolute() if build_context_root else base / "build_context",
    )
    _check_workspace_paths(roots)
    # Keep lexical components until redirect checks complete, then use one
    # canonical root identity for plans, writes and explicit ../root options.
    roots = WorkspaceRoots(
        app_root=roots.app_root.resolve(),
        workflows_root=roots.workflows_root.resolve(),
        build_context_root=roots.build_context_root.resolve(),
    )
    if not _destination(roots.app_root, "app.json").is_file():
        raise CommunityComponentLifecycleError(
            f"No app bundle at {roots.app_root}: app.json not found. Run from the app workspace, or pass --app-root."
        )
    return roots


def _destination(root: Path, relative: str) -> Path:
    """Apply canonical portable/containment rules before touching a destination.

    lstat also rejects Windows junctions and dangling links, including in an
    explicitly selected root; resolving the root first would hide those redirects.
    """
    try:
        target = root.absolute() / validate_portable_path(relative).text
    except PortablePathError as exc:
        raise CommunityComponentLifecycleError(f"Unsafe workspace path: {relative}") from exc
    for part in (*reversed(target.parents), target):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise CommunityComponentLifecycleError(f"Unsafe workspace path: redirected destination {part}")
    return resolve_inside(root, target, error=CommunityComponentLifecycleError, message="Unsafe workspace path")


def _check_workspace_paths(roots: WorkspaceRoots) -> None:
    _destination(roots.app_root, _PROVENANCE_PATH)
    _destination(roots.workflows_root, WORKFLOW_REGISTRY_PATH)
    for name in (_LOCK_PATH, ".mozaiks/installed_components.json", ".mozaiks/installed_component_sources.json"):
        _destination(roots.build_context_root, name)


def _check_recorded_paths(manifest: Mapping[str, Any], roots: WorkspaceRoots) -> None:
    for entry in _pack_entries(manifest):
        for key, base in (("materialized_owned_files", roots.app_root), ("materialized_workflow_files", roots.workflows_root)):
            for row in entry.get(key) or []:
                if isinstance(row, dict) and row.get("path"):
                    _destination(base, row["path"])
    for key, base in (("composed_outputs", roots.app_root), ("composed_workflow_outputs", roots.workflows_root)):
        for relative in manifest.get(key) or {}:
            _destination(base, relative)


def _write_destination(path: Path, roots: WorkspaceRoots) -> Path:
    for root in (roots.app_root, roots.workflows_root, roots.build_context_root):
        if path.is_relative_to(root):
            return _destination(root, path.relative_to(root).as_posix())
    raise CommunityComponentLifecycleError(f"Unsafe workspace path: {path}")


def _empty_files() -> dict[str, list[str]]:
    return {"write": [], "adopt": [], "remove": [], "seed": [], "keep_seed": []}


def _empty_workflow_files() -> dict[str, list[str]]:
    return {"write": [], "adopt": [], "remove": []}


@dataclass
class LifecyclePlan:
    """What an install or upgrade does, or would do; the CLI prints :meth:`to_dict`."""

    operation: str
    status: str
    pack_id: str
    to_version: str
    to_digest: str
    source: str
    from_version: str | None = None
    from_digest: str | None = None
    files: dict[str, list[str]] = field(default_factory=_empty_files)
    workflow_files: dict[str, list[str]] = field(default_factory=_empty_workflow_files)
    composed: list[dict[str, str]] = field(default_factory=list)
    dependencies: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    host_requirements: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: {"modules": [], "imports": [], "secrets": []}
    )
    permissions_introduced: list[str] = field(default_factory=list)
    conflicts: list[dict[str, str]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "status": self.status,
            "pack_id": self.pack_id,
            "from": (
                {"version": self.from_version, "digest": self.from_digest}
                if self.from_version is not None else None
            ),
            "to": {"version": self.to_version, "digest": self.to_digest, "source": self.source},
            "files": {key: sorted(value) for key, value in self.files.items()},
            "workflow_files": {key: sorted(value) for key, value in self.workflow_files.items()},
            "composed": self.composed,
            "dependencies": self.dependencies,
            "host_requirements": self.host_requirements,
            "permissions_introduced": self.permissions_introduced,
            "conflicts": self.conflicts,
            "warnings": self.warnings,
        }


@dataclass
class _RenderedPack:
    pack_id: str
    entry: dict[str, Any]
    contract: dict[str, Any]
    owned: dict[str, str] = field(default_factory=dict)
    seeds: dict[str, str] = field(default_factory=dict)
    workflow_files: dict[str, str] = field(default_factory=dict)
    contributions: dict[str, dict[tuple[str, str], Any]] = field(default_factory=dict)
    ui_barrels: list[str] = field(default_factory=list)


@dataclass
class _Recorded:
    """What the app's provenance says one pack owns."""

    owned: dict[str, str] = field(default_factory=dict)
    seeds: dict[str, str] = field(default_factory=dict)
    workflow: dict[str, str] = field(default_factory=dict)
    entries: dict[str, dict[tuple[str, str], str]] = field(default_factory=dict)


@dataclass
class _Outcome:
    plan: LifecyclePlan
    writes: dict[Path, str | None] = field(default_factory=dict)
    owned_rows: list[dict[str, str]] = field(default_factory=list)
    workflow_rows: list[dict[str, str]] = field(default_factory=list)
    entry_rows: dict[str, list[dict[str, str]]] = field(default_factory=dict)


# --------------------------------------------------------------------------------------
# Reading the app
# --------------------------------------------------------------------------------------


def _read_text(path: Path) -> Any:
    """A file's text exactly as stored, None when absent, or ``_BINARY`` when it is not UTF-8."""
    if not path.is_file():
        return None
    data = path.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return _BINARY


def _same_text(current: Any, expected: str) -> bool:
    return isinstance(current, str) and content_sha256(current) == content_sha256(expected)


def _provenance(app_root: Path) -> dict[str, Any]:
    text = _read_text(_destination(app_root, _PROVENANCE_PATH))
    if text is None:
        return {}
    try:
        data = json.loads(text) if isinstance(text, str) else None
    except ValueError as exc:
        raise CommunityComponentLifecycleError(f"Invalid pack provenance at {app_root / _PROVENANCE_PATH}: {exc}") from exc
    if not isinstance(data, dict):
        raise CommunityComponentLifecycleError(f"Pack provenance must be a JSON object: {app_root / _PROVENANCE_PATH}")
    return data


def _pack_entries(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [entry for entry in manifest.get("packs") or [] if isinstance(entry, dict)]


def _recorded(manifest: Mapping[str, Any], pack_id: str) -> _Recorded:
    recorded = _Recorded()
    for entry in _pack_entries(manifest):
        if entry.get("pack_id") != pack_id:
            continue
        for row in entry.get("materialized_owned_files") or []:
            if isinstance(row, dict) and row.get("path"):
                target = recorded.seeds if row.get("owner") == "workspace" else recorded.owned
                target[str(row["path"])] = str(row.get("sha256") or "")
        for row in entry.get("materialized_workflow_files") or []:
            if isinstance(row, dict) and row.get("path"):
                recorded.workflow[str(row["path"])] = str(row.get("sha256") or "")
    for field_name in ("composed_outputs", "composed_workflow_outputs"):
        composed = manifest.get(field_name)
        for path, contributors in (composed.items() if isinstance(composed, dict) else ()):
            for contributor in contributors if isinstance(contributors, list) else []:
                if isinstance(contributor, dict) and contributor.get("pack_id") == pack_id:
                    recorded.entries[path] = {
                        (str(row["container"]), str(row["key"])): str(row["sha256"])
                        for row in contributor.get("entries") or [] if isinstance(row, dict)
                    }
    return recorded


def _other_owners(manifest: Mapping[str, Any], pack_id: str) -> tuple[dict[str, str], dict[str, str], dict[str, dict[tuple[str, str], str]]]:
    files: dict[str, str] = {}
    workflow_files: dict[str, str] = {}
    entries: dict[str, dict[tuple[str, str], str]] = {}
    for entry in _pack_entries(manifest):
        owner = str(entry.get("pack_id") or "")
        if owner == pack_id:
            continue
        for row in entry.get("materialized_owned_files") or []:
            if isinstance(row, dict) and row.get("path") and row.get("owner") != "workspace":
                files[str(row["path"])] = owner
        for row in entry.get("materialized_workflow_files") or []:
            if isinstance(row, dict) and row.get("path"):
                workflow_files[str(row["path"])] = owner
    for field_name in ("composed_outputs", "composed_workflow_outputs"):
        composed = manifest.get(field_name)
        for path, contributors in (composed.items() if isinstance(composed, dict) else ()):
            for contributor in contributors if isinstance(contributors, list) else []:
                if isinstance(contributor, dict) and contributor.get("pack_id") != pack_id:
                    for row in contributor.get("entries") or []:
                        if isinstance(row, dict):
                            entries.setdefault(path, {})[(str(row["container"]), str(row["key"]))] = str(
                                contributor["pack_id"]
                            )
    return files, workflow_files, entries


def _lifecycle_entry(manifest: Mapping[str, Any], pack_id: str) -> dict[str, Any] | None:
    for entry in _pack_entries(manifest):
        if entry.get("pack_id") == pack_id:
            return entry
    return None


# --------------------------------------------------------------------------------------
# Rendering a pack for an existing app
# --------------------------------------------------------------------------------------


def _normalize_entry(root_path: str, entry: Any) -> Any:
    if root_path == _DATA_CONTRACT_PATH and isinstance(entry, dict):
        normalized = normalize_data_contract_indexes({"version": "1", "surfaces": [copy.deepcopy(entry)], "shared_collections": []})
        return normalized["surfaces"][0]
    return entry


def _contribution_entries(
    root: KeyedRoot, content: str, *, pack_id: str, owner_map: dict[str, str], files: list[dict[str, str]],
) -> dict[tuple[str, str], Any]:
    if root.path == _DATA_CONTRACT_PATH:
        try:
            # Lifecycle provenance records an undeclared rendered output as template-owned; the data check agrees.
            owners = {**{str(file["filename"]): "templates" for file in files}, **owner_map}
            contributions = verified_pack_data_contracts([{"pack_id": pack_id, "owner_map": owners, "files": files}])
            compiled = compile_data_contract(
                {"version": "1", "surfaces": [], "shared_collections": []}, pack_data_contracts=contributions,
            )
        except (ValueError, TypeError) as exc:
            raise CommunityComponentLifecycleError(f"Pack '{pack_id}' data contract contribution is invalid: {exc}") from exc
        return {("surfaces", surface["surface_id"]): _normalize_entry(root.path, surface) for surface in compiled.get("surfaces") or []}
    try:
        document = parse_document(content, root.fmt)
    except CompositionFormatError as exc:
        raise CommunityComponentLifecycleError(f"Pack '{pack_id}' contribution {root.path} {exc}") from exc
    containers = {name for name, _key in root.containers}
    unknown = sorted(set(document) - containers - _CONTRIBUTION_METADATA.get(root.path, frozenset()))
    if unknown:
        raise CommunityComponentLifecycleError(
            f"Pack '{pack_id}' contribution {root.path} may only list {sorted(containers)}; it also sets {unknown}"
        )
    try:
        entries = keyed_entries(document, root)
    except CompositionFormatError as exc:
        raise CommunityComponentLifecycleError(f"Pack '{pack_id}' contribution {root.path} {exc}") from exc
    total = sum(len(document.get(name) or []) for name in containers)
    if total != len(entries):
        raise CommunityComponentLifecycleError(
            f"Pack '{pack_id}' contribution {root.path} has entries without a key; every entry needs one"
        )
    return entries


def _render(pack_dir: Path, pack_id: str, locator: str | None) -> _RenderedPack:
    entry = _component_entry_from_verified_pack(pack_dir, pack_id)
    if locator:
        entry = {**entry, "source": locator}
    context = _read_pack_context(pack_dir, pack_id)
    raw_pack = context.get("pack")
    pack_block: dict[str, Any] = raw_pack if isinstance(raw_pack, dict) else {}
    status = str(pack_block.get("status") or "active").strip()
    if status != "active":
        raise CommunityComponentLifecycleError(f"Pack '{pack_id}' is {status}; only an active pack can be installed")
    owner_map = _file_owner_map(pack_dir)
    try:
        files = resolve_templates_for_pack(pack_dir, pack_id)
    except UnicodeDecodeError as exc:
        raise CommunityComponentLifecycleError(f"Pack '{pack_id}' templates must be UTF-8 text files: {exc}") from exc
    rendered = _RenderedPack(pack_id=pack_id, entry=entry, contract=_read_pack_contract(pack_dir))
    for file in files:
        path, content = str(file["filename"]), str(file["content"])
        if path == ".mozaiks" or path.startswith(".mozaiks/"):
            raise CommunityComponentLifecycleError(f"Pack '{pack_id}' cannot ship lifecycle state: {path}")
        if path in _UNSUPPORTED_UI_BARRELS:
            rendered.ui_barrels.append(path)
            continue
        root = _ROOTS_BY_PATH.get(path)
        if root is not None and root.base == "app":
            rendered.contributions[path] = _contribution_entries(
                root, content, pack_id=pack_id, owner_map=owner_map, files=files,
            )
            continue
        target = rendered.seeds if owner_map.get(path) == "workspace" else rendered.owned
        target[path] = content
    for asset in iter_context_assets(context, kind="workflows"):
        try:
            asset_dir = resolve_context_asset_path(pack_dir, asset)
        except BuildContextError as exc:
            raise CommunityComponentLifecycleError(str(exc)) from exc
        if not asset_dir.is_dir():
            raise CommunityComponentLifecycleError(f"Pack '{pack_id}' workflows asset must be a directory: {asset_dir}")
        _render_workflow_asset(rendered, asset_dir, owner_map=owner_map, files=files)
    return rendered


def _render_workflow_asset(
    rendered: _RenderedPack, asset_dir: Path, *, owner_map: dict[str, str], files: list[dict[str, str]],
) -> None:
    pack_id = rendered.pack_id
    for path in sorted(asset_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(asset_dir)
        if any(part == "__pycache__" or part.startswith(".") for part in relative.parts) or path.suffix in {".pyc", ".pyo"}:
            continue
        name = relative.as_posix()
        if path.suffix == ".j2":
            raise CommunityComponentLifecycleError(f"Pack '{pack_id}' workflow files are copied verbatim; rename {name}")
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise CommunityComponentLifecycleError(f"Pack '{pack_id}' workflow file {name} must be UTF-8 text") from exc
        if name == WORKFLOW_REGISTRY_PATH:
            rendered.contributions[name] = _contribution_entries(
                _ROOTS_BY_PATH[name], content, pack_id=pack_id, owner_map=owner_map, files=files,
            )
            continue
        top = relative.parts[0]
        if top == "extended_orchestration":
            raise CommunityComponentLifecycleError(
                f"Pack '{pack_id}' may contribute only {WORKFLOW_REGISTRY_PATH} under extended_orchestration; "
                f"it ships {name}"
            )
        if len(relative.parts) < 2 or (top != "_shared" and not (asset_dir / top / "orchestrator.yaml").is_file()):
            raise CommunityComponentLifecycleError(
                f"Pack '{pack_id}' workflow file {name} must sit in a workflow folder with orchestrator.yaml, or in _shared"
            )
        existing = rendered.workflow_files.get(name)
        if existing is not None and existing != content:
            raise CommunityComponentLifecycleError(f"Pack '{pack_id}' workflows assets disagree on {name}")
        rendered.workflow_files[name] = content


# --------------------------------------------------------------------------------------
# Host requirements: modules, imports, secrets
# --------------------------------------------------------------------------------------


def _module_names(paths: list[str]) -> set[str]:
    """Dotted module names for root-relative ``.py`` paths (a package's ``__init__`` names the package)."""
    names: set[str] = set()
    for path in paths:
        if not path.endswith(".py"):
            continue
        dotted = path[: -len(".py")].replace("/", ".")
        if dotted.endswith(".__init__"):
            dotted = dotted[: -len(".__init__")]
        names.add(dotted)
    return names


def _host_module_exists(name: str, roots: WorkspaceRoots) -> bool:
    """Whether the app provides a module: ``app.x`` and bare ``x`` are bundle-relative, ``_shared.x`` workflow-shared."""
    parts = name.split(".")
    if parts[0] == "_shared":
        base, rest = roots.workflows_root, parts
    elif parts[0] == "app":
        base, rest = roots.app_root, parts[1:]
    else:
        base, rest = roots.app_root, parts
    if not rest:
        return False
    relative = "/".join(rest)
    return _destination(base, f"{relative}.py").is_file() or _destination(base, f"{relative}/__init__.py").is_file()


def _python_sources(rendered: _RenderedPack) -> list[tuple[str, str]]:
    sources = [(path, content) for path, content in {**rendered.owned, **rendered.seeds}.items() if path.endswith(".py")]
    sources += [(f"workflows/{path}", content) for path, content in rendered.workflow_files.items() if path.endswith(".py")]
    return sorted(sources)


def _host_namespaces(rendered: _RenderedPack, roots: WorkspaceRoots) -> set[str]:
    """Top-level import names that resolve inside the app: ``app``, ``_shared`` and the bundle's top-level packages.

    A Mozaiks app imports its own code either through the workspace (``app.services.x``)
    or from the bundle root on ``sys.path`` (``services.x``); both reach host code.
    """
    names = {"app", "_shared"}
    names.update(path.split("/", 1)[0] for path in [*rendered.owned, *rendered.seeds] if "/" in path)
    if roots.app_root.is_dir():
        for child in roots.app_root.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                names.add(child.name)
            elif child.suffix == ".py":
                names.add(child.stem)
    return names


def _host_imports(rendered: _RenderedPack, namespaces: set[str]) -> list[tuple[str, list[str]]]:
    """Every absolute import that resolves inside the app: (file, candidate module names, most specific first)."""
    found: list[tuple[str, list[str]]] = []
    for path, content in _python_sources(rendered):
        try:
            tree = ast.parse(content, filename=path)
        except SyntaxError as exc:
            raise CommunityComponentLifecycleError(f"Pack '{rendered.pack_id}' file {path} is not valid Python: {exc}") from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.extend((path, [alias.name]) for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.extend((path, [f"{node.module}.{alias.name}", node.module]) for alias in node.names)
    return [
        (path, names) for path, names in found
        if names[-1].split(".")[0] in namespaces and "." in names[0]
    ]


def _check_host_requirements(rendered: _RenderedPack, roots: WorkspaceRoots, plan: LifecyclePlan) -> None:
    requires = rendered.contract.get("requires") or {}
    bundle_modules = _module_names([*rendered.owned, *rendered.seeds])
    shared_modules = _module_names([path for path in rendered.workflow_files if path.startswith("_shared/")])
    provided = bundle_modules | {f"app.{name}" for name in bundle_modules} | shared_modules
    declared = [str(item) for item in requires.get("imports") or []]

    def is_declared(name: str) -> bool:
        return any(name == item or name.startswith(item + ".") for item in declared)

    for item in declared:
        present = _host_module_exists(item, roots)
        plan.host_requirements["imports"].append({"name": item, "status": "present" if present else "missing"})
        if not present:
            plan.conflicts.append({"kind": "missing_host_import", "key": item})
    undeclared: set[tuple[str, str]] = set()
    for path, names in _host_imports(rendered, _host_namespaces(rendered, roots)):
        if any(name in provided for name in names) or any(is_declared(name) for name in names):
            continue
        undeclared.add((path, names[-1]))
    plan.conflicts.extend({"kind": "undeclared_host_import", "path": path, "key": name} for path, name in sorted(undeclared))

    for item in requires.get("modules") or []:
        module_id = str(item.get("module_id"))
        manifest_path = f"modules/{module_id}/module.yaml"
        text = rendered.owned.get(manifest_path)
        if text is None:
            current = _read_text(_destination(roots.app_root, manifest_path))
            text = current if isinstance(current, str) else None
        actions = [str(action) for action in item.get("actions") or []]
        if text is None:
            plan.host_requirements["modules"].append({"module_id": module_id, "actions": actions, "status": "missing"})
            plan.conflicts.append({"kind": "missing_host_module", "key": module_id})
            continue
        try:
            manifest = yaml.safe_load(text) or {}
        except yaml.YAMLError:
            manifest = {}
        declared_actions = {
            str(action.get("id")) for action in (manifest.get("actions") or []) if isinstance(action, dict)
        } if isinstance(manifest, dict) else set()
        missing = [action for action in actions if action not in declared_actions]
        plan.host_requirements["modules"].append(
            {"module_id": module_id, "actions": actions, "status": "missing_actions" if missing else "present"}
        )
        plan.conflicts.extend({"kind": "missing_host_action", "key": f"{module_id}.{action}"} for action in missing)

    secrets_text = _read_text(_destination(roots.app_root, "security/secrets.yaml"))
    declared_envs: set[str] = set()
    if isinstance(secrets_text, str):
        try:
            secrets_doc = yaml.safe_load(secrets_text) or {}
        except yaml.YAMLError:
            secrets_doc = {}
        if isinstance(secrets_doc, dict):
            declared_envs = {
                str(row.get("env")) for row in secrets_doc.get("secrets") or [] if isinstance(row, dict) and row.get("env")
            }
    for item in requires.get("secrets") or []:
        env = str(item.get("env"))
        is_known = env in declared_envs
        plan.host_requirements["secrets"].append({"env": env, "declared": is_known})
        if not is_known:
            plan.warnings.append({
                "kind": "secret_not_declared",
                "detail": f"{env} is not declared in security/secrets.yaml; the runtime still reads it from the environment",
            })

    permissions: set[str] = set()
    for path, content in rendered.owned.items():
        if path.startswith("modules/") and path.endswith("/module.yaml") and path.count("/") == 2:
            try:
                manifest = yaml.safe_load(content) or {}
            except yaml.YAMLError:
                continue
            for permission in (manifest.get("permissions") or []) if isinstance(manifest, dict) else []:
                permission_id = permission.get("id") if isinstance(permission, dict) else permission
                if isinstance(permission_id, str) and permission_id:
                    permissions.add(permission_id)
    plan.permissions_introduced = sorted(permissions)


# --------------------------------------------------------------------------------------
# Dependencies
# --------------------------------------------------------------------------------------


def _check_dependencies(
    rendered: _RenderedPack, installed: dict[str, dict[str, Any]], plan: LifecyclePlan, *, current: dict[str, Any] | None,
) -> None:
    others = {key: value for key, value in installed.items() if key != rendered.pack_id}
    try:
        _ensure_dependencies_satisfied(rendered.entry, others)
    except PackDependencyError as exc:
        plan.conflicts.extend({"kind": "missing_pack", "key": pack} for pack in exc.missing_packs)
        plan.conflicts.extend({"kind": "missing_capability", "key": cap} for cap in exc.missing_capabilities)
        plan.conflicts.extend({"kind": "pack_version_mismatch", "key": detail} for detail in exc.version_mismatches)
    if current is not None:
        resulting = {**others, rendered.pack_id: rendered.entry}
        for other_id, component in sorted(others.items()):
            # The candidate can remove the last provider of a capability used by
            # a pack that has no direct pack/version dependency on it.
            try:
                _ensure_dependencies_satisfied(component, resulting)
            except PackDependencyError as exc:
                plan.conflicts.extend(
                    {"kind": "required_by", "key": other_id, "owner": other_id, "detail": f"missing capability {cap}"}
                    for cap in exc.missing_capabilities
                )
                plan.conflicts.extend(
                    {"kind": "required_by", "key": other_id, "owner": other_id, "detail": f"missing pack {pack}"}
                    for pack in exc.missing_packs
                )
            for dependency in component.get("dependencies", {}).get("packs") or []:
                pinned = dependency.get("version")
                if dependency.get("pack_id") == rendered.pack_id and pinned and pinned != rendered.entry["version"]:
                    plan.conflicts.append({"kind": "required_by", "key": other_id, "owner": other_id, "detail": f"requires {pinned}"})
    previous = (current or {}).get("dependencies") or {"packs": [], "capabilities": []}
    candidate = rendered.entry["dependencies"]
    plan.dependencies = {
        "added_packs": [item for item in candidate["packs"] if item not in previous["packs"]],
        "removed_packs": [item for item in previous["packs"] if item not in candidate["packs"]],
        "added_capabilities": [item for item in candidate["capabilities"] if item not in previous["capabilities"]],
        "removed_capabilities": [item for item in previous["capabilities"] if item not in candidate["capabilities"]],
    }


# --------------------------------------------------------------------------------------
# Planning files and entries
# --------------------------------------------------------------------------------------


def _plan_owned_files(
    outcome: _Outcome, rendered: _RenderedPack, recorded: _Recorded, roots: WorkspaceRoots,
    other_files: dict[str, str], *, adopt: bool,
) -> None:
    plan = outcome.plan
    for path in sorted(set(rendered.owned) | set(recorded.owned)):
        target = _destination(roots.app_root, path)
        current = _read_text(target)
        new = rendered.owned.get(path)
        old_hash = recorded.owned.get(path)
        if new is not None:
            outcome.owned_rows.append({"path": path, "owner": "templates", "sha256": content_sha256(new)})
        if new is not None and old_hash is None:
            if path in other_files:
                plan.conflicts.append({"kind": "owned_by_other_pack", "path": path, "owner": other_files[path]})
            elif current is None:
                plan.files["write"].append(path)
                outcome.writes[target] = new
            elif path in recorded.seeds or adopt:
                # A seed the pack now owns again, or an explicit adoption: identical content only.
                if _same_text(current, new):
                    plan.files["adopt"].append(path)
                else:
                    plan.conflicts.append({"kind": "existing_file_differs", "path": path})
            else:
                plan.conflicts.append({"kind": "workspace_owned_file", "path": path})
        elif new is not None:
            if current is None:
                plan.conflicts.append({"kind": "locally_deleted_owned_file", "path": path})
            elif not isinstance(current, str) or content_sha256(current) != old_hash:
                plan.conflicts.append({"kind": "locally_modified_owned_file", "path": path})
            elif content_sha256(new) != old_hash:
                plan.files["write"].append(path)
                outcome.writes[target] = new
        elif path in rendered.seeds or current is None:
            # Handed to the app as a seed, or already gone: nothing to remove.
            continue
        elif not isinstance(current, str) or content_sha256(current) != old_hash:
            plan.conflicts.append({"kind": "locally_modified_owned_file", "path": path})
        else:
            plan.files["remove"].append(path)
            outcome.writes[target] = None

    for path in sorted(rendered.seeds):
        new = rendered.seeds[path]
        outcome.owned_rows.append({"path": path, "owner": "workspace", "sha256": content_sha256(new)})
        target = _destination(roots.app_root, path)
        current = _read_text(target)
        old_hash = recorded.seeds.get(path)
        if path in other_files:
            plan.conflicts.append({"kind": "owned_by_other_pack", "path": path, "owner": other_files[path]})
        elif current is None and old_hash is None and path not in recorded.owned:
            plan.files["seed"].append(path)
            outcome.writes[target] = new
        else:
            plan.files["keep_seed"].append(path)
            if old_hash and old_hash != content_sha256(new):
                plan.warnings.append({
                    "kind": "seed_changed_upstream",
                    "detail": f"{path}: the library's seed changed; the app's copy is kept",
                })


def _plan_workflow_files(
    outcome: _Outcome, rendered: _RenderedPack, recorded: _Recorded, roots: WorkspaceRoots,
    other_files: dict[str, str], *, adopt: bool,
) -> None:
    plan = outcome.plan
    for path in sorted(set(rendered.workflow_files) | set(recorded.workflow)):
        target = _destination(roots.workflows_root, path)
        current = _read_text(target)
        new = rendered.workflow_files.get(path)
        old_hash = recorded.workflow.get(path)
        if new is not None:
            outcome.workflow_rows.append({"path": path, "owner": "templates", "sha256": content_sha256(new)})
        if new is not None and old_hash is None:
            if path in other_files:
                plan.conflicts.append({"kind": "owned_by_other_pack", "path": f"workflows/{path}", "owner": other_files[path]})
            elif current is None:
                plan.workflow_files["write"].append(path)
                outcome.writes[target] = new
            elif not adopt:
                plan.conflicts.append({"kind": "workspace_owned_file", "path": f"workflows/{path}"})
            elif _same_text(current, new):
                plan.workflow_files["adopt"].append(path)
            else:
                plan.conflicts.append({"kind": "existing_file_differs", "path": f"workflows/{path}"})
        elif new is not None:
            if current is None:
                plan.conflicts.append({"kind": "locally_deleted_owned_file", "path": f"workflows/{path}"})
            elif not isinstance(current, str) or content_sha256(current) != old_hash:
                plan.conflicts.append({"kind": "locally_modified_owned_file", "path": f"workflows/{path}"})
            elif content_sha256(new) != old_hash:
                plan.workflow_files["write"].append(path)
                outcome.writes[target] = new
        elif current is None:
            continue
        elif not isinstance(current, str) or content_sha256(current) != old_hash:
            plan.conflicts.append({"kind": "locally_modified_owned_file", "path": f"workflows/{path}"})
        else:
            plan.workflow_files["remove"].append(path)
            outcome.writes[target] = None


def _data_conflicts(document: Mapping[str, Any], pack_surfaces: dict[str, Any], plan: LifecyclePlan) -> None:
    """A pack surface may not reuse a collection or alias the app already maps elsewhere."""
    collections: dict[str, str] = {}
    aliases: dict[str, str] = {}
    for surface in document.get("surfaces") or []:
        if not isinstance(surface, dict) or surface.get("surface_id") in pack_surfaces:
            continue
        for collection in surface.get("collections") or []:
            if isinstance(collection, dict):
                if collection.get("mongo_collection"):
                    collections[str(collection["mongo_collection"])] = str(surface.get("surface_id"))
                if collection.get("data_alias") and collection.get("mongo_collection"):
                    aliases[str(collection["data_alias"])] = str(collection["mongo_collection"])
    for shared in document.get("shared_collections") or []:
        if isinstance(shared, dict) and shared.get("mongo_collection"):
            owner = str(shared.get("owner_module") or "")
            surface = pack_surfaces.get(owner) or {}
            if surface.get("surface_kind") == "module" and any(
                collection.get("mongo_collection") == shared["mongo_collection"]
                and collection.get("owner_module") == owner
                and collection.get("ownership") == {"surface_id": owner, "surface_kind": "module"}
                and collection.get("data_alias") == shared.get("primary_alias")
                for collection in surface.get("collections") or [] if isinstance(collection, dict)
            ):
                # This grants readers access to the same owner's collection.
                # The grant remains app-owned and is not changed by composition.
                continue
            collections.setdefault(str(shared["mongo_collection"]), f"shared:{shared.get('owner_module') or ''}")
    for alias in document.get("aliases") or []:
        if isinstance(alias, dict) and (alias.get("alias") or alias.get("data_alias")) and alias.get("collection"):
            aliases.setdefault(str(alias.get("alias") or alias.get("data_alias")), str(alias["collection"]))
    for surface_id, surface in sorted(pack_surfaces.items()):
        for collection in surface.get("collections") or []:
            if not isinstance(collection, dict):
                continue
            name = str(collection.get("mongo_collection") or "")
            alias = str(collection.get("data_alias") or "")
            if name and name in collections:
                plan.conflicts.append({
                    "kind": "alias_conflict", "root": _DATA_CONTRACT_PATH, "key": surface_id,
                    "detail": f"collection {name!r} already belongs to {collections[name]!r}",
                })
            if alias and alias in aliases and name and aliases[alias] != name:
                plan.conflicts.append({
                    "kind": "alias_conflict", "root": _DATA_CONTRACT_PATH, "key": surface_id,
                    "detail": f"alias {alias!r} maps to {aliases[alias]!r} in the app, not {name!r}",
                })


def _route_conflicts(document: Mapping[str, Any], pack_keys: set[str], plan: LifecyclePlan) -> None:
    by_path: dict[str, list[str]] = {}
    for page in document.get("pages") or []:
        if isinstance(page, dict) and isinstance(page.get("path"), str):
            by_path.setdefault(page["path"], []).append(str(page.get("id") or ""))
    for path, ids in sorted(by_path.items()):
        if len(ids) > 1 and pack_keys.intersection(ids):
            plan.conflicts.append({"kind": "route_path_conflict", "root": _ROUTE_MANIFEST_PATH, "key": path})


def _splice(text: str, root: KeyedRoot, upserts: dict[tuple[str, str], Any], removals: set[tuple[str, str]]) -> str:
    crlf = "\r\n" in text
    result = splice_document(text.replace("\r\n", "\n"), root, upserts=upserts, removals=removals)
    return result.replace("\n", "\r\n") if crlf else result


def _plan_entries(
    outcome: _Outcome, rendered: _RenderedPack, recorded: _Recorded, roots: WorkspaceRoots,
    other_entries: dict[str, dict[tuple[str, str], str]], *, adopt: bool,
) -> None:
    plan = outcome.plan
    for root in KEYED_ROOTS:
        new_entries = rendered.contributions.get(root.path, {})
        old_hashes = recorded.entries.get(root.path, {})
        if not new_entries and not old_hashes:
            continue
        label = root.path if root.base == "app" else f"workflows/{root.path}"
        target = _destination(roots.base(root.base), root.path)
        text = _read_text(target)
        if not isinstance(text, str):
            if text is _BINARY:
                plan.conflicts.append({"kind": "composed_output_conflict", "root": label})
                continue
            if root.path == WORKFLOW_REGISTRY_PATH:
                if new_entries:
                    plan.warnings.append({
                        "kind": "workflow_registry_absent",
                        "detail": "the app has no workflow registry, so every loaded workflow launches; entrypoints are not created",
                    })
                continue
            if old_hashes:
                plan.conflicts.extend(
                    {"kind": "locally_modified_composed_entry", "root": label, "key": f"{container}:{key}"}
                    for container, key in sorted(old_hashes)
                )
                continue
            document = dict(_NEW_ROOT_DOCUMENTS.get(root.path, {}))
            if root.path == _DATA_CONTRACT_PATH:
                document = {"version": "1", "surfaces": [], "shared_collections": []}
            merged = merged_document(document, root, upserts=new_entries, removals=set())
            outcome.writes[target] = new_document_text(merged, root.fmt)
            for container, key in sorted(new_entries):
                plan.composed.append({"root": label, "container": container, "key": key, "action": "add"})
            outcome.entry_rows[root.path] = [
                {"container": container, "key": key, "sha256": entry_sha256(value)}
                for (container, key), value in sorted(new_entries.items())
            ]
            continue
        try:
            document = parse_document(text, root.fmt)
            current = keyed_entries(document, root)
        except CompositionFormatError as exc:
            plan.conflicts.append({"kind": "composition_format_unsupported", "root": label, "detail": str(exc)})
            continue
        upserts: dict[tuple[str, str], Any] = {}
        removals: set[tuple[str, str]] = set()
        conflicts_before = len(plan.conflicts)
        for address in sorted(set(new_entries) | set(old_hashes)):
            container, key = address
            new = new_entries.get(address)
            old_hash = old_hashes.get(address)
            present = address in current
            normalized = _normalize_entry(root.path, current[address]) if present else None
            row = {"root": label, "container": container, "key": key}
            if new is not None and old_hash is None:
                owner = other_entries.get(root.path, {}).get(address)
                if owner:
                    plan.conflicts.append({"kind": "composed_entry_owned_by_other_pack", **row, "owner": owner})
                elif not present:
                    upserts[address] = new
                    plan.composed.append({**row, "action": "add"})
                elif not adopt:
                    plan.conflicts.append({"kind": "composed_entry_owned_by_app", **row})
                elif normalized == new:
                    plan.composed.append({**row, "action": "adopt"})
                else:
                    plan.conflicts.append({"kind": "existing_entry_differs", **row})
            elif new is not None:
                if not present or entry_sha256(normalized) != old_hash:
                    plan.conflicts.append({"kind": "locally_modified_composed_entry", **row})
                elif entry_sha256(new) != old_hash:
                    upserts[address] = new
                    plan.composed.append({**row, "action": "replace"})
                else:
                    plan.composed.append({**row, "action": "unchanged"})
            elif present:
                if entry_sha256(normalized) != old_hash:
                    plan.conflicts.append({"kind": "locally_modified_composed_entry", **row})
                else:
                    removals.add(address)
                    plan.composed.append({**row, "action": "remove"})
        outcome.entry_rows[root.path] = [
            {"container": container, "key": key, "sha256": entry_sha256(value)}
            for (container, key), value in sorted(new_entries.items())
        ]
        merged = merged_document(document, root, upserts=upserts, removals=removals)
        if root.path == _DATA_CONTRACT_PATH:
            _data_conflicts(document, {key: value for (_c, key), value in new_entries.items()}, plan)
        if root.path == _ROUTE_MANIFEST_PATH:
            _route_conflicts(merged, {key for (_c, key) in new_entries}, plan)
        if len(plan.conflicts) != conflicts_before or not (upserts or removals):
            continue
        try:
            outcome.writes[target] = _splice(text, root, upserts, removals)
        except CompositionFormatError as exc:
            plan.conflicts.append({"kind": "composition_format_unsupported", "root": label, "detail": str(exc)})


# --------------------------------------------------------------------------------------
# Provenance, state and the write transaction
# --------------------------------------------------------------------------------------


def _framework_version() -> str:
    try:
        from mozaiksai.version import __version__
    except Exception:  # pragma: no cover - version metadata is always present in a checkout
        return "unknown"
    return str(__version__)


def _updated_provenance(manifest: Mapping[str, Any], outcome: _Outcome, rendered: _RenderedPack) -> dict[str, Any]:
    pack_id = rendered.pack_id
    identity = {
        "pack_id": pack_id,
        "version": rendered.entry["version"],
        "source": rendered.entry["source"],
        "digest": rendered.entry["digest"],
    }
    packs: list[dict[str, Any]] = [entry for entry in _pack_entries(manifest) if entry.get("pack_id") != pack_id]
    updated: dict[str, Any] = {
        "schema_version": _PROVENANCE_SCHEMA_VERSION,
        "framework_version": manifest.get("framework_version") or _framework_version(),
        "generated_at": manifest.get("generated_at") or resolve_build_timestamp(None),
        "packs": packs,
    }
    pack_entry: dict[str, Any] = {
        **identity,
        "ownership": OWNERSHIP_CONTENT_HASH,
        "materialized_owned_files": sorted(outcome.owned_rows, key=lambda row: row["path"]),
    }
    if outcome.workflow_rows:
        pack_entry["materialized_workflow_files"] = sorted(outcome.workflow_rows, key=lambda row: row["path"])
    packs.append(pack_entry)
    packs.sort(key=lambda entry: str(entry.get("pack_id") or ""))
    for field_name, base in (("composed_outputs", "app"), ("composed_workflow_outputs", "workflows")):
        existing = manifest.get(field_name)
        composed: dict[str, list[dict[str, Any]]] = {
            path: [row for row in rows if isinstance(row, dict) and row.get("pack_id") != pack_id]
            for path, rows in (existing.items() if isinstance(existing, dict) else ())
            if isinstance(rows, list)
        }
        for root in KEYED_ROOTS:
            rows = outcome.entry_rows.get(root.path)
            if root.base == base and rows:
                composed.setdefault(root.path, []).append({**identity, "entries": rows})
        composed = {path: rows for path, rows in sorted(composed.items()) if rows}
        if composed:
            updated[field_name] = composed
    return updated


def _validate_provenance(
    manifest: dict[str, Any], roots: WorkspaceRoots, writes: Mapping[Path, str | bytes | None],
) -> str:
    text = json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    files_map = {_PROVENANCE_PATH: text}
    for root in KEYED_ROOTS:
        target = _destination(roots.app_root, root.path)
        if root.base == "app" and (writes.get(target) is not None or (target not in writes and target.is_file())):
            files_map[root.path] = ""
    errors = scan_pack_provenance(files_map)
    if errors:
        raise CommunityComponentLifecycleError(f"Updated component provenance failed validation: {errors}")
    return text


def _missing_parents(path: Path) -> list[Path]:
    missing: list[Path] = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    return missing


def _commit(
    writes: Mapping[Path, str | bytes | None], roots: WorkspaceRoots, *, directories: tuple[Path, ...] = (),
) -> None:
    """Replace files atomically and restore their prior contents after failure.

    Unsafe or unwritable rollback destinations remain untouched; exception
    notes report cleanup failures without replacing the original exception.
    """
    backups: list[tuple[Path, bytes | None]] = []
    created: list[Path] = []
    temporary: Path | None = None
    for path in (*writes, *directories):
        _write_destination(path, roots)
    try:
        for directory in directories:
            directory = _write_destination(directory, roots)
            created.extend(_missing_parents(directory / "placeholder"))
            directory.mkdir(parents=True, exist_ok=True)
        for path, text in writes.items():
            path = _write_destination(path, roots)
            backups.append((path, path.read_bytes() if path.is_file() else None))
            if text is None:
                if path.is_file():
                    path.unlink()
                continue
            created.extend(_missing_parents(path))
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_destination(path, roots)
            descriptor, temporary_name = tempfile.mkstemp(prefix=".mz-", suffix=".tmp", dir=path.parent)
            temporary = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(text if isinstance(text, bytes) else text.encode("utf-8"))
            os.replace(_write_destination(temporary, roots), _write_destination(path, roots))
            temporary = None
    except BaseException as failure:
        if temporary is not None:
            try:
                _write_destination(temporary, roots).unlink(missing_ok=True)
            except (OSError, CommunityComponentLifecycleError) as exc:
                failure.add_note(f"Rollback could not remove temporary file {temporary}: {exc}")
        for path, previous in reversed(backups):
            try:
                path = _write_destination(path, roots)
                if previous is None:
                    if path.is_file():
                        path.unlink()
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(previous)
            except (OSError, CommunityComponentLifecycleError) as exc:
                failure.add_note(f"Rollback could not restore {path}: {exc}")
        for directory in sorted(set(created), key=lambda item: len(item.parts), reverse=True):
            try:
                _write_destination(directory, roots).rmdir()
            except (OSError, CommunityComponentLifecycleError) as exc:
                failure.add_note(f"Rollback could not remove directory {directory}: {exc}")
        raise


def _prune_empty_parents(paths: list[Path], stop: tuple[Path, ...], roots: WorkspaceRoots) -> None:
    for path in paths:
        parent = path.parent
        while parent not in stop and any(parent.is_relative_to(base) for base in stop):
            try:
                _write_destination(parent, roots).rmdir()
            except OSError:
                break
            parent = parent.parent


@contextmanager
def _lifecycle_lock(build_context_root: Path) -> Iterator[None]:
    path = _destination(build_context_root, _LOCK_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(_destination(build_context_root, _LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise CommunityComponentLockedError(
            f"Another pack operation holds {path}. If none is running, delete that file and try again."
        ) from exc
    failure: BaseException | None = None
    try:
        os.write(descriptor, f"{os.getpid()} {int(time.time())}\n".encode())
        os.close(descriptor)
        yield
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            _destination(build_context_root, _LOCK_PATH).unlink()
        except FileNotFoundError:
            pass
        except (OSError, CommunityComponentLifecycleError) as exc:
            if failure is None:
                raise
            failure.add_note(f"Could not safely remove lifecycle lock {path}: {exc}")


# --------------------------------------------------------------------------------------
# Operations
# --------------------------------------------------------------------------------------


@dataclass
class _Candidate:
    pack_dir: Path
    pack_id: str
    locator: str | None


@contextmanager
def _candidate(source: PackSource) -> Iterator[_Candidate]:
    try:
        with materialized_source(source) as pack_dir:
            locator = git_locator(source) if source.is_git else None
            yield _Candidate(pack_dir=pack_dir, pack_id=_pack_id_from_context(pack_dir), locator=locator)
    except PackSourceError as exc:
        raise CommunityComponentLifecycleError(str(exc)) from exc


def _new_plan(operation: str, rendered: _RenderedPack, current: dict[str, Any] | None) -> LifecyclePlan:
    return LifecyclePlan(
        operation=operation,
        status="ready",
        pack_id=rendered.pack_id,
        to_version=rendered.entry["version"],
        to_digest=rendered.entry["digest"],
        source=rendered.entry["source"],
        from_version=current["version"] if current else None,
        from_digest=current["digest"] if current else None,
    )


def _plan_workspace_change(
    operation: str, candidate: _Candidate, roots: WorkspaceRoots, *, adopt: bool,
) -> tuple[_Outcome, _RenderedPack, dict[str, Any]]:
    try:
        rendered = _render(candidate.pack_dir, candidate.pack_id, candidate.locator)
    except ManagedCapabilityTemplateError as exc:
        raise CommunityComponentLifecycleError(str(exc)) from exc
    installed = component_by_id(load_installed_components(roots.build_context_root))
    manifest = _provenance(roots.app_root)
    _check_recorded_paths(manifest, roots)
    current = installed.get(rendered.pack_id)
    record_entry = _lifecycle_entry(manifest, rendered.pack_id)
    plan = _new_plan(operation, rendered, current if operation == "upgrade" else None)
    outcome = _Outcome(plan=plan)
    same_identity = current is not None and (current["version"], current["digest"]) == (
        rendered.entry["version"], rendered.entry["digest"],
    )
    if operation == "install":
        if current is not None and not same_identity:
            raise CommunityComponentLifecycleError(
                f"Pack '{rendered.pack_id}' is already installed at {current['version']} {current['digest']}; "
                "use `mozaiks pack upgrade` to change its version."
            )
        if same_identity and record_entry is not None:
            plan.status = "unchanged"
            return outcome, rendered, manifest
        if record_entry is not None:
            raise CommunityComponentLifecycleError(
                f"The app's provenance records pack '{rendered.pack_id}' but the workspace has no matching pin; "
                "restore build_context/.mozaiks/installed_components.json from version control."
            )
    else:
        if current is None or record_entry is None:
            raise CommunityComponentLifecycleError(
                f"Pack '{rendered.pack_id}' is not installed in this app; use `mozaiks pack install`."
            )
        if same_identity:
            plan.status = "unchanged"
            return outcome, rendered, manifest
    recorded = _recorded(manifest, rendered.pack_id) if record_entry is not None else _Recorded()
    other_files, other_workflow_files, other_entries = _other_owners(manifest, rendered.pack_id)
    plan.conflicts.extend(
        {"kind": "ui_barrel_contribution_unsupported", "path": path} for path in sorted(rendered.ui_barrels)
    )
    _check_dependencies(rendered, installed, plan, current=current if operation == "upgrade" else None)
    _check_host_requirements(rendered, roots, plan)
    _plan_owned_files(outcome, rendered, recorded, roots, other_files, adopt=adopt)
    _plan_workflow_files(outcome, rendered, recorded, roots, other_workflow_files, adopt=adopt)
    _plan_entries(outcome, rendered, recorded, roots, other_entries, adopt=adopt)
    if plan.conflicts:
        plan.status = "blocked"
    return outcome, rendered, manifest


def _source_binding_writes(
    candidate: _Candidate, rendered: _RenderedPack, roots: WorkspaceRoots, *, repair: bool = False,
) -> tuple[dict[Path, str | bytes | None], tuple[Path, ...]]:
    """Keep verified Git input alive for the existing installed-source resolver."""
    root = roots.build_context_root
    sources = load_installed_component_sources(root)
    writes: dict[Path, str | bytes | None] = {}
    directories: tuple[Path, ...] = ()
    if candidate.locator is None:
        sources[rendered.pack_id] = str(candidate.pack_dir)
    else:
        installed = component_by_id(load_installed_components(root)).get(rendered.pack_id)
        if repair and installed != rendered.entry:
            raise CommunityComponentLifecycleError(
                f"Pack '{rendered.pack_id}' source does not match the installed pin; use the upgrade plan."
            )
        context = _read_pack_context(candidate.pack_dir, rendered.pack_id)
        # A short digest prefix names the snapshot: the full 64-character digest pushed
        # cached template paths past Windows' 260-character limit in ordinary workspaces.
        # The pin keeps the full digest, the cached bytes are re-verified against it, and
        # a changed snapshot is refused below.
        cache = _destination(root, f".mozaiks/source-cache/{rendered.entry['digest'].split(':', 1)[1][:16]}")
        expected = {
            _destination(cache, path.relative_to(candidate.pack_dir).as_posix()): path.read_bytes()
            for path in _safe_asset_files(candidate.pack_dir, context)
        }
        directories = tuple(
            _destination(cache, resolve_context_asset_path(candidate.pack_dir, asset).relative_to(candidate.pack_dir).as_posix())
            for asset in iter_context_assets(context)
            if resolve_context_asset_path(candidate.pack_dir, asset).is_dir()
        )
        # Never overwrite a changed snapshot or follow a redirected cache path.
        if cache.exists():
            for path in cache.rglob("*"):
                path = _destination(cache, path.relative_to(cache).as_posix())
                if path.is_file() and (path not in expected or path.read_bytes() != expected[path]):
                    raise CommunityComponentLifecycleError(f"Installed pack source cache has changed: {rendered.pack_id}")
        writes.update(expected)
        sources[rendered.pack_id] = str(cache)
    writes[installed_component_sources_path(root)] = installed_component_sources_text(sources)
    return writes, directories


def _apply_workspace_change(
    outcome: _Outcome, rendered: _RenderedPack, manifest: dict[str, Any], candidate: _Candidate, roots: WorkspaceRoots,
) -> None:
    """Write the app's files, its provenance and the workspace pin as one transaction."""
    _check_workspace_paths(roots)
    provenance = _updated_provenance(manifest, outcome, rendered)
    writes: dict[Path, str | bytes | None] = dict(outcome.writes)
    writes[roots.app_root / _PROVENANCE_PATH] = _validate_provenance(provenance, roots, writes)
    root = roots.build_context_root
    installed = component_by_id(load_installed_components(root))
    installed[rendered.pack_id] = rendered.entry
    writes[installed_components_path(root)] = installed_components_text({"components": list(installed.values())})
    source_writes, directories = _source_binding_writes(candidate, rendered, roots)
    writes.update(source_writes)
    _commit(writes, roots, directories=directories)
    _prune_empty_parents(
        [path for path, text in writes.items() if text is None], (roots.app_root, roots.workflows_root), roots,
    )


def plan_component_install(*, source: PackSource, roots: WorkspaceRoots, adopt: bool = False) -> LifecyclePlan:
    """Dry-run installing a pack into an existing app; nothing is written."""
    _check_workspace_paths(roots)
    with _candidate(source) as candidate:
        outcome, _rendered, _manifest = _plan_workspace_change("install", candidate, roots, adopt=adopt)
    return outcome.plan


def apply_component_install(*, source: PackSource, roots: WorkspaceRoots, adopt: bool = False) -> LifecyclePlan:
    """Install a pack into an existing app, or raise :class:`CommunityComponentConflictError` writing nothing."""
    _check_workspace_paths(roots)
    with _lifecycle_lock(roots.build_context_root), _candidate(source) as candidate:
        outcome, rendered, manifest = _plan_workspace_change("install", candidate, roots, adopt=adopt)
        plan = outcome.plan
        if plan.status == "unchanged":
            if candidate.locator is not None:
                writes, directories = _source_binding_writes(candidate, rendered, roots, repair=True)
                _commit(writes, roots, directories=directories)
            return plan
        if plan.status == "blocked":
            raise _blocked(plan)
        _apply_workspace_change(outcome, rendered, manifest, candidate, roots)
        plan.status = "applied"
        return plan


def plan_workspace_upgrade(*, source: PackSource, roots: WorkspaceRoots) -> LifecyclePlan:
    """Dry-run upgrading an installed pack to the version ``source`` names; nothing is written."""
    _check_workspace_paths(roots)
    with _candidate(source) as candidate:
        if _is_generator_materialized(roots, candidate.pack_id):
            return _generator_upgrade_plan(candidate, roots, apply=False)
        outcome, _rendered, _manifest = _plan_workspace_change("upgrade", candidate, roots, adopt=False)
    return outcome.plan


def apply_workspace_upgrade(*, source: PackSource, roots: WorkspaceRoots) -> LifecyclePlan:
    """Upgrade an installed pack, preserving the app's own files and entries, or raise writing nothing."""
    _check_workspace_paths(roots)
    with _lifecycle_lock(roots.build_context_root), _candidate(source) as candidate:
        if _is_generator_materialized(roots, candidate.pack_id):
            return _generator_upgrade_plan(candidate, roots, apply=True)
        outcome, rendered, manifest = _plan_workspace_change("upgrade", candidate, roots, adopt=False)
        plan = outcome.plan
        if plan.status == "unchanged":
            if candidate.locator is not None:
                writes, directories = _source_binding_writes(candidate, rendered, roots, repair=True)
                _commit(writes, roots, directories=directories)
            return plan
        if plan.status == "blocked":
            raise _blocked(plan)
        _apply_workspace_change(outcome, rendered, manifest, candidate, roots)
        plan.status = "applied"
        return plan


def _blocked(plan: LifecyclePlan) -> CommunityComponentConflictError:
    """The error a blocked apply raises; it carries the plan so callers can report every conflict."""
    error = CommunityComponentConflictError(
        f"{plan.operation.capitalize()} of '{plan.pack_id}' is blocked; nothing was written: {plan.conflicts}"
    )
    error.plan = plan  # type: ignore[attr-defined]
    return error


def _is_generator_materialized(roots: WorkspaceRoots, pack_id: str) -> bool:
    entry = _lifecycle_entry(_provenance(roots.app_root), pack_id)
    return entry is not None and entry.get("ownership") != OWNERSHIP_CONTENT_HASH


def _generator_upgrade_plan(candidate: _Candidate, roots: WorkspaceRoots, *, apply: bool) -> LifecyclePlan:
    """Upgrade a pack AppGenerator materialized, through the base lifecycle's unchanged path."""
    try:
        metadata = verify_pack_integrity(candidate.pack_dir, candidate.pack_id)
        installed = component_by_id(load_installed_components(roots.build_context_root))
        current = installed.get(candidate.pack_id)
        if current is None:
            raise CommunityComponentLifecycleError(f"Pack '{candidate.pack_id}' is not installed; use `mozaiks pack install`.")
        if apply:
            if candidate.locator is not None:
                # The base lifecycle re-renders this source at the next upgrade, so it must outlive the command.
                raise CommunityComponentLifecycleError(
                    f"Pack '{candidate.pack_id}' was materialized by AppGenerator; upgrade it from a directory "
                    "checkout of that commit rather than --git, because its upgrade path re-reads the source later."
                )
            result = apply_component_upgrade(
                build_context_root=roots.build_context_root,
                candidate_pack_source_path=candidate.pack_dir,
                workspace_app_root=roots.app_root,
            )
        else:
            result = plan_component_upgrade(
                build_context_root=roots.build_context_root,
                candidate_pack_source_path=candidate.pack_dir,
                workspace_app_root=roots.app_root,
            )
    except ManagedCapabilityTemplateError as exc:
        raise CommunityComponentLifecycleError(str(exc)) from exc
    return _plan_from_base_upgrade(result, metadata, candidate, applied=apply)


def _plan_from_base_upgrade(result: UpgradePlan, metadata: dict[str, Any], candidate: _Candidate, *, applied: bool) -> LifecyclePlan:
    plan = LifecyclePlan(
        operation="upgrade",
        status="applied" if applied else result.status,
        pack_id=result.pack_id,
        to_version=result.to_version,
        to_digest=result.to_digest,
        source=candidate.locator or metadata["source"],
        from_version=result.from_version,
        from_digest=result.from_digest,
    )
    plan.files["write"] = sorted(result.added_files + result.changed_files)
    plan.files["remove"] = sorted(result.removed_files)
    plan.composed = [{"root": path, "container": "", "key": "", "action": "recompose"} for path in result.recomposed_files]
    plan.dependencies = dict(result.dependency_changes)
    plan.conflicts = [dict(conflict) for conflict in result.potential_conflicts]
    plan.warnings.append({
        "kind": "generator_provenance",
        "detail": "this pack was materialized by AppGenerator; its upgrade uses the base lifecycle and is not hash-verified",
    })
    return plan


def verify_installed_components(*, roots: WorkspaceRoots) -> dict[str, Any]:
    """Check every pack-owned file and entry against its recorded content hash."""
    _check_workspace_paths(roots)
    installed = component_by_id(load_installed_components(roots.build_context_root))
    manifest = _provenance(roots.app_root)
    _check_recorded_paths(manifest, roots)
    reports: list[dict[str, Any]] = []
    entries_seen: set[str] = set()
    for entry in sorted(_pack_entries(manifest), key=lambda item: str(item.get("pack_id") or "")):
        pack_id = str(entry.get("pack_id") or "")
        entries_seen.add(pack_id)
        report: dict[str, Any] = {"pack_id": pack_id, "version": entry.get("version"), "findings": []}
        if entry.get("ownership") != OWNERSHIP_CONTENT_HASH:
            reports.append({**report, "verifiable": False})
            continue
        findings: list[dict[str, str]] = report["findings"]
        pin = installed.get(pack_id)
        if pin is None or (pin["version"], pin["digest"]) != (entry.get("version"), entry.get("digest")):
            findings.append({"kind": "provenance_without_pin"})
        recorded = _recorded(manifest, pack_id)
        for path, digest in sorted(recorded.owned.items()):
            current = _read_text(_destination(roots.app_root, path))
            if current is None:
                findings.append({"kind": "missing_owned_file", "path": path})
            elif not isinstance(current, str) or content_sha256(current) != digest:
                findings.append({"kind": "modified_owned_file", "path": path})
        for path, digest in sorted(recorded.workflow.items()):
            current = _read_text(_destination(roots.workflows_root, path))
            if current is None:
                findings.append({"kind": "missing_owned_file", "path": f"workflows/{path}"})
            elif not isinstance(current, str) or content_sha256(current) != digest:
                findings.append({"kind": "modified_owned_file", "path": f"workflows/{path}"})
        for root in KEYED_ROOTS:
            hashes = recorded.entries.get(root.path)
            if not hashes:
                continue
            label = root.path if root.base == "app" else f"workflows/{root.path}"
            text = _read_text(_destination(roots.base(root.base), root.path))
            try:
                current_entries = keyed_entries(parse_document(text, root.fmt), root) if isinstance(text, str) else {}
            except CompositionFormatError:
                current_entries = {}
            for (container, key), digest in sorted(hashes.items()):
                value = current_entries.get((container, key))
                if value is None:
                    findings.append({"kind": "missing_composed_entry", "root": label, "key": f"{container}:{key}"})
                elif entry_sha256(_normalize_entry(root.path, value)) != digest:
                    findings.append({"kind": "modified_composed_entry", "root": label, "key": f"{container}:{key}"})
        reports.append({**report, "verifiable": True})
    for pack_id, pin in sorted(installed.items()):
        if pack_id not in entries_seen:
            reports.append({
                "pack_id": pack_id, "version": pin["version"], "verifiable": True,
                "findings": [{"kind": "pin_without_provenance"}],
            })
    status = "findings" if any(report["findings"] for report in reports) else "clean"
    return {"status": status, "packs": reports}


def installed_component_status(*, roots: WorkspaceRoots) -> dict[str, Any]:
    """Installed packs with their identity, source and how much of the app each owns."""
    _check_workspace_paths(roots)
    installed = component_by_id(load_installed_components(roots.build_context_root))
    manifest = _provenance(roots.app_root)
    _check_recorded_paths(manifest, roots)
    packs: list[dict[str, Any]] = []
    for pack_id in sorted(set(installed) | {str(entry.get("pack_id") or "") for entry in _pack_entries(manifest)}):
        pin = installed.get(pack_id) or {}
        entry = _lifecycle_entry(manifest, pack_id) or {}
        recorded = _recorded(manifest, pack_id)
        packs.append({
            "pack_id": pack_id,
            "version": pin.get("version") or entry.get("version"),
            "digest": pin.get("digest") or entry.get("digest"),
            "source": pin.get("source") or entry.get("source"),
            "ownership": (
                "content_hash" if entry.get("ownership") == OWNERSHIP_CONTENT_HASH
                else "generator" if entry else "pinned_only"
            ),
            "owned_files": len(recorded.owned),
            "seeded_files": len(recorded.seeds),
            "workflow_files": len(recorded.workflow),
            "composed_entries": sum(len(rows) for rows in recorded.entries.values()),
        })
    return {"packs": packs}


__all__ = [
    "CommunityComponentLockedError",
    "LifecyclePlan",
    "PackSource",
    "WorkspaceRoots",
    "apply_component_install",
    "apply_workspace_upgrade",
    "installed_component_status",
    "plan_component_install",
    "plan_workspace_upgrade",
    "verify_installed_components",
    "workspace_roots",
]
