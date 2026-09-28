"""Project action identifiers from approved design and collection contracts."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mozaiksai.core.runtime.persistence.intent_loader import iter_data_contract_collections
from mozaiksai.core.session.build_context import (
    BuildContextError,
    load_build_context,
    load_contract_descriptors,
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


def selected_pack_contracts(context_variables: Any) -> list[dict[str, Any]]:
    """Contract declarations of every selected pack whose templates assembly applies.

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
    result: list[dict[str, Any]] = []
    for pack in packs:
        if not isinstance(pack, Mapping):
            continue
        pack_id = pack.get("id") or pack.get("pack_id") or pack.get("capability_pack_id")
        source = pack.get("pack_source_path")
        root = Path(str(source)) if source else None
        if root is not None and (root / "context.yaml").is_file():
            try:
                config = load_build_context(root / "context.yaml")
                pack_block = config.get("pack")
                declared: Mapping[str, Any] = pack_block if isinstance(pack_block, Mapping) else {}
                if str(declared.get("status") or "active") != "active":
                    continue
                result.extend(load_contract_descriptors(root, config))
            except (BuildContextError, OSError) as exc:
                # Assembly reports an unusable pack; authoring keeps checking its pages.
                logger.warning("selected pack %s could not be read: %s", root, exc)
            continue
        result.extend(
            dict(contract) for contract in projected
            if isinstance(contract, Mapping) and pack_id and (
                contract.get("contract_id") == pack_id
                or (contract.get("canonical_provider") or {}).get("provider_pack_id") == pack_id
            )
        )
    return result


def managed_pack_output_paths(context_variables: Any) -> frozenset[str]:
    """Bundle paths the selected packs declare as template outputs (``required_outputs``).

    A page a worker authors at one of these paths is a placeholder the pack's
    template replaces at assembly, so authoring-time binding checks defer to
    the template contracts. Only declared outputs count: a facade page route a
    pack ships no template for is authored, and stays checked.
    """
    paths: set[str] = set()
    for contract in selected_pack_contracts(context_variables):
        for entry in contract.get("required_outputs") or []:
            # A workspace-owned output (owner: workspace) is preserved across
            # regeneration rather than written by a template; only template
            # outputs replace an authored placeholder.
            owner = str(entry.get("owner") or "templates").strip() if isinstance(entry, Mapping) else "templates"
            if owner != "templates":
                continue
            path = _bundle_relative_path(entry.get("path") if isinstance(entry, Mapping) else entry)
            if path:
                paths.add(path)
    return frozenset(paths)


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
