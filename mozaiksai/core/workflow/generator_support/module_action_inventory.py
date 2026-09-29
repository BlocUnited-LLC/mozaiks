"""Project action identifiers from approved design and collection contracts."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NamedTuple

from mozaiksai.core.runtime.persistence.intent_loader import iter_data_contract_collections
from mozaiksai.core.session.build_context import (
    BuildContextError,
    iter_context_assets,
    load_build_context,
    load_contract_descriptors,
    resolve_context_asset_path,
)
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.resources import resolve_factory_app_root

logger = logging.getLogger(__name__)


def _bundle_relative_path(raw: Any) -> str | None:
    """Normalize a declared pack output path; reject anything outside the bundle."""
    if not isinstance(raw, str):
        return None
    path = raw.replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    parts = [part for part in path.split("/") if part]
    if not parts or path.startswith("/") or ".." in parts or ":" in parts[0]:
        return None
    return "/".join(parts)


def canonical_read_action_id(collection_name: str, operation: str) -> str:
    """Name a canonical read using the declared storage collection identifier."""
    if operation not in {"list", "get"}:
        raise ValueError(f"Unsupported canonical read operation {operation!r}; choose list or get.")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", collection_name):
        raise ValueError(f"Canonical read requires a valid collection name, got {collection_name!r}.")
    return f"{operation}_{collection_name}"


CANONICAL_WRITE_OPERATIONS = ("create", "update", "delete")


def entity_identifier(entity: str) -> str:
    """Project a declared entity name such as ``ProjectMilestone`` to ``project_milestone``."""
    if not isinstance(entity, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", entity):
        raise ValueError(f"Canonical writes require an identifier-safe entity name, got {entity!r}.")
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", entity).lower()
    return re.sub(r"_+", "_", snake)


def canonical_write_action_id(entity: str, operation: str) -> str:
    """Name a canonical write using the declared entity, matching approved mutation ids."""
    if operation not in CANONICAL_WRITE_OPERATIONS:
        raise ValueError(f"Unsupported canonical write operation {operation!r}; choose create, update or delete.")
    return f"{operation}_{entity_identifier(entity)}"


def collection_has_canonical_writes(collection: Mapping[str, Any]) -> bool:
    """Only collections written by module actions receive code-owned writes."""
    lifecycle = collection.get("lifecycle")
    return isinstance(lifecycle, Mapping) and lifecycle.get("write_mode") == "module_action"


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
            events = {operation: canonical_write_event_type(entity, operation) for operation in CANONICAL_WRITE_EVENT_VERBS}
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
        for operation in CANONICAL_WRITE_OPERATIONS
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
        surface["surface_id"]: facades[surface["surface_id"]] if surface["surface_id"] in facades else sorted(set(
            [*(surface.get("owned_mutations") or []), *(surface.get("custom_reads") or []),
             *canonical_read_actions_for_surface(surface, contract),
             *canonical_write_actions_for_surface(surface, contract)]
        ))
        for surface in surface_map.get("surfaces") or []
        if surface.get("surface_kind") == "module" and surface.get("owner") == "app"
    }


def managed_pack_contracts(context_variables: Any) -> list[dict[str, Any]]:
    """Resolve selected managed contracts through their declared build-context assets."""
    if context_variables is None:
        return []
    packs = detach(context_variables.get("capability_packs")) or []
    contracts = detach(context_variables.get("operator_contracts")) or []
    surface_map = detach(context_variables.get("design_surface_map")) or {}
    referenced = {
        pack_id for surface in surface_map.get("surfaces") or []
        for pack_id in surface.get("source_capability_packs") or []
    }
    selected = {pack.get("id") or pack.get("pack_id") or pack.get("capability_pack_id") for pack in packs}
    factory_root = resolve_factory_app_root()
    for pack_id in referenced - selected:
        if not isinstance(pack_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", pack_id) or factory_root is None:
            continue
        registry = factory_root / "build_context" / pack_id / "context.yaml"
        if registry.is_file():
            config = load_build_context(registry)
            packs.append({**config.get("pack", {}), "pack_source_path": str(registry.parent)})
    result: list[dict[str, Any]] = []
    for pack in packs:
        if pack.get("capability_source") != "managed_capability" or pack.get("status", "active") != "active":
            continue
        pack_id = pack.get("id") or pack.get("pack_id") or pack.get("capability_pack_id")
        source = pack.get("pack_source_path")
        if source:
            root = Path(source)
            config = load_build_context(root / "context.yaml")
            declarations = load_contract_descriptors(root, config)
        else:
            declarations = [
                contract for contract in contracts
                if contract.get("contract_id") == pack_id
                or (contract.get("canonical_provider") or {}).get("provider_pack_id") == pack_id
            ]
        if not declarations and pack.get("facades"):
            declarations = [{**pack, "contract_id": pack_id}]
        result.extend(declarations)
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


def _selected_packs(context_variables: Any) -> list[_SelectedPack]:
    """Every selected pack whose templates assembly applies, with its contract declarations.

    Assembly reads the selected packs from ``capability_packs`` and falls back to
    the approved plan's packs. A pack with a source path is read from disk, and
    its own ``context.yaml`` says whether it is active; a pack selected by id
    alone is read from the ``operator_contracts`` the build context projected,
    the same way the managed facade inventory resolves it.
    """
    if context_variables is None:
        return []
    packs = detach(context_variables.get("capability_packs")) or []
    if not packs:
        plan = detach(context_variables.get("app_build_plan")) or {}
        packs = plan.get("capability_packs") or [] if isinstance(plan, Mapping) else []
    projected = detach(context_variables.get("operator_contracts")) or []
    result: list[_SelectedPack] = []
    for pack in packs:
        if not isinstance(pack, Mapping):
            continue
        pack_id = str(pack.get("id") or pack.get("pack_id") or pack.get("capability_pack_id") or "")
        source = pack.get("pack_source_path")
        root = Path(str(source)) if source else None
        if root is not None and (root / "context.yaml").is_file():
            try:
                config = load_build_context(root / "context.yaml")
                pack_block = config.get("pack")
                declared: Mapping[str, Any] = pack_block if isinstance(pack_block, Mapping) else {}
                if str(declared.get("status") or "active") != "active":
                    continue
                result.append(_SelectedPack(pack_id, root, config, load_contract_descriptors(root, config)))
            except (BuildContextError, OSError) as exc:
                # Assembly reports an unusable pack; authoring keeps checking its pages.
                logger.warning("selected pack %s could not be read: %s", root, exc)
            continue
        result.append(_SelectedPack(pack_id, None, None, [
            dict(contract) for contract in projected
            if isinstance(contract, Mapping) and pack_id and (
                contract.get("contract_id") == pack_id
                or (contract.get("canonical_provider") or {}).get("provider_pack_id") == pack_id
            )
        ]))
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
    """Bundle paths the selected packs write from their templates, by path.

    Pack-owned outputs are never model work: plan review builds no task for
    them, a worker's copy is discarded, and assembly takes them only from the
    templates. A ``required_outputs`` entry with ``owner: templates`` is written
    at every assembly. An ``owner: workspace`` entry the pack ships a template
    for is written at genesis and then belongs to the workspace, so it is
    pack-owned only in a genesis build. Any other owner (``generator``) names
    work the pack asks a model to author.
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
                if not path:
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


def pack_template_module_contracts(context_variables: Any) -> dict[str, str]:
    """``module.yaml`` contracts the selected packs' templates provide, by bundle path.

    No task authors a pack-owned module, so pages that bind its actions compile
    against the template contract, the same one assembly applies.
    """
    files: dict[str, str] = {}
    for pack in _selected_packs(context_variables):
        if pack.root is None or pack.config is None:
            continue
        for contract in pack.contracts:
            for entry in contract.get("required_outputs") or []:
                path = _bundle_relative_path(entry.get("path") if isinstance(entry, Mapping) else entry)
                if not path or not re.fullmatch(r"modules/[^/]+/module\.yaml", path):
                    continue
                template = _pack_template_file(pack.root, pack.config, path)
                if template is not None and template.suffix != ".j2":
                    files[path] = template.read_text(encoding="utf-8")
    return files


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
