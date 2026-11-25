"""Project action identifiers from approved design and collection contracts."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal, NamedTuple

import yaml

from mozaiksai.core.runtime.persistence.collection_writes import (
    CANONICAL_WRITE_OPERATIONS,
    canonical_write_action_id,
    canonical_write_fields,
    entity_identifier,
)
from mozaiksai.core.runtime.persistence.intent_loader import iter_data_contract_collections
from mozaiksai.core.session.build_context import (
    BuildContextError,
    iter_context_assets,
    load_build_context,
    load_contract_descriptors,
    resolve_context_asset_path,
)
from mozaiksai.core.workflow.context.frozen import detach

logger = logging.getLogger(__name__)

# Pack templates contribute declarations to these app-wide compiler outputs;
# they cannot exclusively own a worker's contribution at the same path.
SHARED_UI_OUTPUT_PATHS = frozenset({"ui/index.js", "ui/route_manifest.json"})
SHARED_PACK_OUTPUT_PATHS = SHARED_UI_OUTPUT_PATHS | {"data/contract.json"}
TEMPLATE_BACKEND_FILES = (
    "module.yaml", "backend/service.py", "backend/repo.py", "backend/policy.py", "backend/schemas.py",
)


def _bundle_relative_path(raw: Any) -> str | None:
    """Normalize a declared pack output path; reject anything outside the bundle."""
    if not isinstance(raw, str):
        return None
    path = raw.replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    parts = [part for part in path.split("/") if part]
    if (
        not parts
        or path.startswith("/")
        or ".." in parts
        or ":" in parts[0]
        or any(part.startswith(".") or part == "__pycache__" for part in parts)
    ):
        return None
    return "/".join(parts)


def canonical_read_action_id(collection_name: str, operation: str) -> str:
    """Name a canonical read using the declared storage collection identifier."""
    if operation not in {"list", "get"}:
        raise ValueError(f"Unsupported canonical read operation {operation!r}; choose list or get.")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", collection_name):
        raise ValueError(f"Canonical read requires a valid collection name, got {collection_name!r}.")
    return f"{operation}_{collection_name}"


def collection_has_canonical_writes(collection: Mapping[str, Any]) -> bool:
    """Only collections written by module actions receive code-owned writes."""
    lifecycle = collection.get("lifecycle")
    return isinstance(lifecycle, Mapping) and lifecycle.get("write_mode") == "module_action"


def canonical_write_operations(collection: Mapping[str, Any]) -> tuple[str, ...]:
    """The ordered operations the approved collection lifecycle gives its module."""
    policy = canonical_write_fields(collection)
    if not collection_has_canonical_writes(collection):
        return ()
    return tuple(op for op in CANONICAL_WRITE_OPERATIONS if policy is None or op in policy)


CANONICAL_WRITE_EVENT_VERBS = {"create": "created", "update": "updated", "delete": "deleted"}


def canonical_write_event_type(entity: str, operation: str) -> str:
    """Name the domain event a canonical write emits: ``domain.<entity>.<created|updated|deleted>``.

    This is the one naming rule for canonical write events; module.yaml emits,
    events.yaml declarations, the rendered service and every alias reconciliation
    use it.
    """
    if operation not in CANONICAL_WRITE_EVENT_VERBS:
        raise ValueError(f"Unsupported canonical write operation {operation!r}; choose create, update or delete.")
    return f"domain.{entity_identifier(entity)}.{CANONICAL_WRITE_EVENT_VERBS[operation]}"


def _event_spellings(entity: str, collection_name: str, operation: str, module_id: str) -> set[str]:
    verb = CANONICAL_WRITE_EVENT_VERBS[operation]
    names = {entity_identifier(entity), entity.lower()}
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", collection_name or ""):
        names.add(collection_name.lower())
    subjects = {f"{name}{separator}{verb}" for name in names for separator in (".", "_")}
    qualifiers = {*names, module_id.lower()} - {""}
    spellings = set(subjects)
    for qualifier in qualifiers:
        spellings.add(f"{qualifier}.{verb}")
        spellings.update(f"{qualifier}.{subject}" for subject in subjects)
    return spellings | {f"domain.{spelling}" for spelling in spellings}


def canonical_write_event_aliases(data_contract: Any) -> dict[str, str]:
    """Map each spelling of a canonical write event to its canonical type.

    A spelling names the write's subject (the entity identifier, entity name or
    collection name) joined to ``created``/``updated``/``deleted`` by ``.`` or
    ``_``, optionally qualified by the owning module id, the collection or the
    entity (``task_management.task_created``), or the qualifier alone followed
    by the verb (``task_management.created``); each with or without the
    ``domain.`` prefix, compared case-insensitively. A spelling two collections
    share is ambiguous and maps to neither.
    """
    contract = detach(data_contract)
    if not isinstance(contract, Mapping):
        return {}
    claims: dict[str, set[str]] = {}
    try:
        collections = list(iter_data_contract_collections(dict(contract), require_complete_ownership=False))
    except (TypeError, ValueError, KeyError):
        return {}
    for owner, owner_kind, collection in collections:
        if owner_kind != "module" or not collection_has_canonical_writes(collection):
            continue
        entity = str(collection.get("entity") or "")
        try:
            events = {operation: canonical_write_event_type(entity, operation) for operation in canonical_write_operations(collection)}
        except ValueError:
            continue  # module closure reports the entity name
        for operation, event_type in events.items():
            for spelling in _event_spellings(entity, str(collection.get("name") or ""), operation, str(owner)):
                claims.setdefault(spelling, set()).add(event_type)
    return {spelling: next(iter(types)) for spelling, types in claims.items() if len(types) == 1}


def canonical_write_event_for(event_type: Any, aliases: Mapping[str, str]) -> str | None:
    """Return the canonical write event a spelling names, or None for a custom event."""
    if not isinstance(event_type, str):
        return None
    return aliases.get(event_type.strip().lower())


def _surface_collections(surface: Mapping[str, Any], data_contract: Any) -> list[dict[str, Any]]:
    """Use explicit collection entity declarations, never inferred singular names."""
    contract = detach(data_contract)
    if not isinstance(contract, Mapping):
        return []
    module_id = surface.get("surface_id")
    entities = set(surface.get("primary_entities") or [])
    return [
        collection for owner_id, owner_kind, collection in iter_data_contract_collections(dict(contract))
        if owner_id == module_id and owner_kind == "module" and collection.get("entity") in entities
    ]


def canonical_read_actions_for_surface(surface: Mapping[str, Any], data_contract: Any) -> list[str]:
    """Canonical list/get ids for every approved collection the surface owns."""
    return sorted({
        canonical_read_action_id(collection["name"], operation)
        for collection in _surface_collections(surface, data_contract)
        for operation in ("list", "get")
    })


def canonical_write_actions_for_surface(surface: Mapping[str, Any], data_contract: Any) -> list[str]:
    """Canonical create/update/delete ids for approved module-written collections."""
    return sorted({
        canonical_write_action_id(collection["entity"], operation)
        for collection in _surface_collections(surface, data_contract)
        if collection_has_canonical_writes(collection)
        for operation in canonical_write_operations(collection)
    })


def authored_module_actions(surface: Mapping[str, Any], data_contract: Any) -> list[str]:
    """Combine the reviewed module actions with its collection's canonical actions."""
    return sorted({
        *(surface.get("owned_mutations") or []), *(surface.get("custom_reads") or []),
        *canonical_read_actions_for_surface(surface, data_contract), *canonical_write_actions_for_surface(surface, data_contract),
    })


def all_module_actions(context_variables: Any) -> dict[str, list[str]]:
    """Return the one approved inventory for page references and gate selection.

    Facades use their complete pack contract. Canonical collection writes are
    approved by the data contract itself; gate selection separately excludes
    facade actions and canonical collection reads.
    """
    if context_variables is None:
        return {}
    surface_map = detach(context_variables.get("design_surface_map"))
    if not isinstance(surface_map, Mapping):
        return {}
    contract = context_variables.get("data_contract")
    facades = managed_facade_actions(context_variables)
    return {
        surface["surface_id"]: facades[surface["surface_id"]] if surface["surface_id"] in facades else authored_module_actions(surface, contract)
        for surface in surface_map.get("surfaces") or []
        if surface.get("surface_kind") == "module" and surface.get("owner") == "app"
    }


def unapproved_module_actions_error(
    module_id: str, action_ids: Iterable[str], approved_actions: Mapping[str, list[str]],
) -> str | None:
    """Describe unknown action identities against the existing closed inventory."""
    if module_id not in approved_actions:
        return None
    unapproved = sorted(set(action_ids) - set(approved_actions[module_id]))
    if not unapproved:
        return None
    return (
        f"modules/{module_id}/module.yaml: unapproved module actions {unapproved}; every action must be declared "
        "in DesignDocs owned_mutations/custom_reads or be a canonical collection action. "
        f"Valid approved action ids: {sorted(approved_actions[module_id])}. "
        "Remove unapproved declarations; do not retain alternate spellings alongside canonical ids."
    )


ModuleActionIndex = dict[str, dict[str, dict[str, Any]]]


def scoped_module_implementation_sources(files: Mapping[str, str], owned_paths: Iterable[str]) -> dict[str, str]:
    """Only manifests with an owned handler/service/repo may trigger implementation generation."""
    manifests = {
        f"modules/{match[1]}/module.yaml" for path in owned_paths
        if (match := re.fullmatch(r"modules/([^/]+)/backend/(?:handler|service|repo)\.py", path))
    }
    if not manifests:
        return {}
    # Keep companion inputs such as events and service hooks even when another task owns them.
    return {
        path: source for path, source in files.items()
        if path in manifests or not re.fullmatch(r"modules/[^/]+/module\.yaml", path)
    }


def module_action_index(file_map: dict[str, str], *, http_only: bool = True) -> ModuleActionIndex:
    """Index declared module actions, including their exact schemas; page callers see HTTP actions."""
    index: ModuleActionIndex = {}
    for path, content in (file_map or {}).items():
        if not str(path).replace("\\", "/").endswith("/module.yaml"):
            continue
        try:
            document = yaml.safe_load(content)
        except yaml.YAMLError:
            continue
        if not isinstance(document, dict):
            continue
        module = document.get("module")
        module_id = str((module or {}).get("id") or "").strip() if isinstance(module, dict) else ""
        if not module_id:
            continue
        actions = document.get("actions")
        index[module_id] = {
            str(action.get("id")).strip(): action
            for action in (actions if isinstance(actions, list) else [])
            if isinstance(action, dict) and str(action.get("id") or "").strip()
            and (not http_only or action.get("api_surface") not in {"internal", "admin_internal"})
        }
    return index


def approved_workflow_surface_ids(context_variables: Any) -> list[str]:
    """Return selectable app workflow surfaces when the concept has agentic capabilities."""
    if context_variables is None:
        return []
    concept = detach(context_variables.get("concept_blueprint"))
    if not isinstance(concept, Mapping) or not concept.get("agentic_capabilities"):
        return []
    surface_map = detach(context_variables.get("design_surface_map"))
    if not isinstance(surface_map, Mapping):
        return []
    return sorted({
        surface["surface_id"]
        for surface in surface_map.get("surfaces") or []
        if isinstance(surface, Mapping) and surface.get("surface_kind") == "workflow"
        and surface.get("owner") == "app" and isinstance(surface.get("surface_id"), str)
        and surface["surface_id"]
    })


def managed_pack_contracts(context_variables: Any) -> list[dict[str, Any]]:
    """Read managed ownership only from the selected, verified pack sources."""
    from factory_app.workflows._shared.pack_selection import selected_pack_rows

    packs = selected_pack_rows(context_variables)
    result: list[dict[str, Any]] = []
    for pack in packs:
        if pack.get("status", "active") != "active":
            continue
        claims_managed = pack.get("capability_source") == "managed_capability"
        entry, refusal = _verified_pack_entry(pack, context_variables)
        if refusal is not None:
            if not claims_managed:
                continue  # Assembly refuses it; it claims no managed ownership to enforce here.
            raise ValueError(
                f"Selected managed pack '{_row_pack_id(pack) or '<unnamed>'}' cannot be verified, so what it "
                f"owns cannot be read: {refusal}"
            )
        if entry is None or entry.get("capability_source") != "managed_capability":
            continue  # Selects no pack, or the pack's own declaration is not managed.
        root = Path(entry["source_path"])
        result.extend(load_contract_descriptors(root, load_build_context(root / "context.yaml")))
    return result


def managed_facade_actions(context_variables: Any) -> dict[str, list[str]]:
    """Project complete facade actions from selected, declared pack contracts."""
    result: dict[str, list[str]] = {}
    for contract in managed_pack_contracts(context_variables):
        for facade in contract.get("facades") or []:
            module_id = facade["module_id"]
            actions = sorted({action for page in facade.get("pages") or [] for action in page.get("primary_actions") or []})
            if module_id in result and result[module_id] != actions:
                raise ValueError(f"Conflicting managed facade action contracts for {module_id!r}")
            result[module_id] = actions
    return result


class _SelectedPack(NamedTuple):
    pack_id: str
    root: Path | None
    config: Mapping[str, Any] | None
    contracts: list[dict[str, Any]]
    # The verified identity (pack_id, version, digest, source, resolution, authority); None when the row
    # selects no pack and its declarations come from the projected operator contracts.
    identity: dict[str, str] | None = None


def _row_pack_id(pack: Mapping[str, Any]) -> str:
    """The resolver's one pack-identity precedence (``selected_pack_id``)."""
    return str(pack.get("id") or pack.get("pack_id") or pack.get("capability_pack_id") or "").strip()


def _verified_pack_entry(pack: Mapping[str, Any], context_variables: Any) -> tuple[dict[str, Any] | None, str | None]:
    """The canonical resolver's verified selection for one row, as ``(entry, refusal)``.

    The resolver lives with the AppGenerator assembly that applies the
    templates; it is imported here so planning reads exactly the packs
    assembly materializes, id-only rows included, and only after the same
    verification: the source's fresh version and digest compared with the
    row's pins, and the authority that vouches for it recorded. ``(entry,
    None)`` for a verified pack; ``(None, None)`` for a row that selects no
    pack; ``(None, diagnostic)`` for a row the resolver refuses, from which
    nothing is read (assembly refuses it with the same diagnostic).
    """
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        ManagedCapabilityTemplateError,
        verified_pack_selection,
    )

    try:
        return verified_pack_selection(dict(pack), context_variables=context_variables), None
    except (ManagedCapabilityTemplateError, BuildContextError, OSError, yaml.YAMLError) as exc:
        logger.warning(
            "SELECTED_PACK_UNRESOLVED pack=%s: %s; nothing is read from it, and assembly refuses it",
            _row_pack_id(pack) or "<unnamed>", exc,
        )
        return None, str(exc)


def _selected_packs(context_variables: Any) -> list[_SelectedPack]:
    """Read contracts from the same reviewed sources that assembly applies."""
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        pack_identity,
    )

    if context_variables is None:
        return []
    from factory_app.workflows._shared.pack_selection import selected_pack_rows

    packs = selected_pack_rows(context_variables)
    result: list[_SelectedPack] = []
    for pack in packs:
        if not isinstance(pack, Mapping):
            continue
        entry, refusal = _verified_pack_entry(pack, context_variables)
        if refusal is not None:
            continue
        if entry is not None:
            root = Path(entry["source_path"])
            try:
                config = load_build_context(root / "context.yaml")
                pack_block = config.get("pack")
                declared: Mapping[str, Any] = pack_block if isinstance(pack_block, Mapping) else {}
                if str(declared.get("status") or "active") != "active":
                    continue
                result.append(_SelectedPack(
                    entry["pack_id"], root, config, load_contract_descriptors(root, config), pack_identity(entry),
                ))
            except (BuildContextError, OSError) as exc:
                # Assembly reports an unusable pack; nothing unverified stands in for it.
                logger.warning("selected pack %s could not be read: %s", root, exc)
            continue
    return result


def _pack_template_file(root: Path, config: Mapping[str, Any], path: str) -> Path | None:
    """The file a pack's declared templates asset renders to ``path``, if it ships one."""
    for asset in iter_context_assets(config, kind="templates"):
        try:
            base = resolve_context_asset_path(root, asset)
        except BuildContextError:
            continue
        for candidate in (base / path, base / f"{path}.j2"):
            try:
                candidate.resolve().relative_to(base.resolve())
            except ValueError:
                continue
            if candidate.is_file():
                return candidate
    return None


def _is_genesis_build(context_variables: Any) -> bool:
    binding = detach(context_variables.get("run_build_binding")) or {}
    phase = binding.get("phase") if isinstance(binding, Mapping) else None
    return (
        (phase or "genesis") == "genesis"
        and context_variables.get("build_mode") != "revision"
        and not context_variables.get("brownfield_build_path")
    )


class PackOwnedOutput(NamedTuple):
    pack_id: str
    owner: str
    source: str | None


def pack_owned_outputs(context_variables: Any) -> dict[str, PackOwnedOutput]:
    """Exclusive bundle paths selected packs write from templates, by path.

    Pack-owned outputs are never model work: plan review builds no task for
    them, a worker's copy is discarded, and assembly takes them only from the
    templates. A ``required_outputs`` entry with ``owner: templates`` is written
    at every assembly. An ``owner: workspace`` entry the pack ships a template
    for is written at genesis and then belongs to the workspace, so it is
    pack-owned only in a genesis build. Any other owner (``generator``) names
    work the pack asks a model to author. Shared composition roots retain app
    task ownership; their verified pack contributions are merged at assembly.
    """
    if context_variables is None:
        return {}
    genesis = _is_genesis_build(context_variables)
    outputs: dict[str, PackOwnedOutput] = {}
    for pack in _selected_packs(context_variables):
        for contract in pack.contracts:
            for entry in contract.get("required_outputs") or []:
                owner = str(entry.get("owner") or "templates").strip() if isinstance(entry, Mapping) else "templates"
                path = _bundle_relative_path(entry.get("path") if isinstance(entry, Mapping) else entry)
                if not path or path in SHARED_PACK_OUTPUT_PATHS:
                    continue
                if owner == "workspace":
                    if not genesis or pack.root is None or pack.config is None:
                        continue
                    if _pack_template_file(pack.root, pack.config, path) is None:
                        continue
                elif owner != "templates":
                    continue
                outputs.setdefault(path, PackOwnedOutput(pack.pack_id, owner, str(pack.root) if pack.root else None))
    return outputs


def pack_owned_output_paths(context_variables: Any) -> frozenset[str]:
    """The bundle paths of :func:`pack_owned_outputs`."""
    return frozenset(pack_owned_outputs(context_variables))


class PackFacadeDirectory(NamedTuple):
    """A selected pack's facade module directory and the declared files in it a pack leaves to others."""

    pack_id: str
    path: str
    exempt: frozenset[str]

    def owns(self, path: str) -> bool:
        """Owning the directory itself claims every file in it, the pack's included."""
        return path == self.path or (path.startswith(f"{self.path}/") and path not in self.exempt)


def pack_facade_directories(context_variables: Any) -> list[PackFacadeDirectory]:
    """The module directory of every facade a selected pack declares.

    A facade module belongs to its pack: the templates ship the module and the
    contract declares every file the pack wants in it, so a path under
    ``modules/<facade>/`` the contract does not declare is never model work. A
    declared path keeps the owner :func:`pack_owned_outputs` gives it: a
    ``generator`` output, or a ``workspace`` file outside genesis, is exempt.
    """
    if context_variables is None:
        return []
    owned = pack_owned_output_paths(context_variables)
    directories: dict[str, tuple[str, set[str]]] = {}
    for pack in _selected_packs(context_variables):
        for contract in pack.contracts:
            declared = {
                _bundle_relative_path(entry.get("path") if isinstance(entry, Mapping) else entry)
                for entry in contract.get("required_outputs") or []
            }
            for facade in contract.get("facades") or []:
                module_id = facade.get("module_id") if isinstance(facade, Mapping) else None
                if not isinstance(module_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", module_id):
                    continue
                path = f"modules/{module_id}"
                _, exempt = directories.setdefault(path, (pack.pack_id, set()))
                exempt.update(
                    declared_path for declared_path in declared
                    if declared_path and declared_path.startswith(f"{path}/") and declared_path not in owned
                )
    return [
        PackFacadeDirectory(pack_id, path, frozenset(exempt))
        for path, (pack_id, exempt) in sorted(directories.items())
    ]


class PackModuleTemplate(NamedTuple):
    source: str
    # Applies to template-owned generated modules. Managed facades and
    # workspace-owned seeds retain their existing action authority.
    action_source: Literal["template", "compiler"] | None


def pack_module_templates(context_variables: Any) -> dict[str, PackModuleTemplate]:
    """Read selected static module manifests and identify who completes their action contracts."""
    files: dict[str, PackModuleTemplate] = {}
    for pack in _selected_packs(context_variables):
        if pack.root is None or pack.config is None:
            continue
        root, config = pack.root, pack.config
        owned = {
            _bundle_relative_path(entry.get("path"))
            for contract in pack.contracts for entry in contract.get("required_outputs") or []
            if isinstance(entry, Mapping) and (entry.get("owner") or "templates") == "templates"
        }
        for contract in pack.contracts:
            for entry in contract.get("required_outputs") or []:
                path = _bundle_relative_path(entry.get("path") if isinstance(entry, Mapping) else entry)
                if not path or not re.fullmatch(r"modules/[^/]+/module\.yaml", path):
                    continue
                template = _pack_template_file(root, config, path)
                if template is None or template.suffix == ".j2":
                    continue
                action_source: Literal["template", "compiler"] | None = None
                if path in owned and config["pack"].get("capability_source") == "generated_module":
                    prefix = path.removesuffix("module.yaml")
                    complete = all(
                        prefix + name in owned and _pack_template_file(root, config, prefix + name) is not None
                        for name in TEMPLATE_BACKEND_FILES
                    )
                    action_source = "template" if complete else "compiler"
                files[path] = PackModuleTemplate(template.read_text(encoding="utf-8"), action_source)
    return files


def pack_template_module_contracts(context_variables: Any) -> dict[str, str]:
    """Return the selected static manifests used by the design prompts."""
    return {path: template.source for path, template in pack_module_templates(context_variables).items()}


def approved_manifest_action_ids(
    context: Any, design_actions: dict[str, list[str]], *, http_only: bool = False,
    templates: dict[str, PackModuleTemplate] | None = None,
) -> dict[str, list[str]]:
    """Complete backends supply exact IDs. Partial modules still compile actions from approved design and data."""
    if not design_actions:
        return {}
    if templates is None:
        templates = pack_module_templates(context)
    stock = module_action_index({
        path: template.source for path, template in templates.items() if template.action_source == "template"
    }, http_only=http_only)
    return {
        module_id: sorted(stock[module_id]) if module_id in stock else actions
        for module_id, actions in design_actions.items()
    }


def ungated_module_actions(context_variables: Any) -> dict[str, list[str]]:
    """Canonical collection reads and managed facade actions never need paid access."""
    if context_variables is None:
        return {}
    surface_map = detach(context_variables.get("design_surface_map")) or {}
    contract = context_variables.get("data_contract")
    result = {
        surface["surface_id"]: canonical_read_actions_for_surface(surface, contract)
        for surface in surface_map.get("surfaces") or []
        if surface.get("surface_kind") == "module" and surface.get("owner") == "app"
    }
    result.update(managed_facade_actions(context_variables))
    return result
