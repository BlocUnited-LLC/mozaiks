"""Project action identifiers from approved design and collection contracts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from mozaiksai.core.runtime.persistence.intent_loader import iter_data_contract_collections
from mozaiksai.core.workflow.context.frozen import detach


def canonical_read_action_id(collection_name: str, operation: str) -> str:
    """Name a canonical read using the declared storage collection identifier."""
    if operation not in {"list", "get"}:
        raise ValueError(f"Unsupported canonical read operation {operation!r}; choose list or get.")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", collection_name):
        raise ValueError(f"Canonical read requires a valid collection name, got {collection_name!r}.")
    return f"{operation}_{collection_name}"


def canonical_read_actions_for_surface(surface: Mapping[str, Any], data_contract: Any) -> list[str]:
    """Use explicit collection entity declarations, never inferred singular names."""
    contract = detach(data_contract)
    if not isinstance(contract, Mapping):
        return []
    module_id = surface.get("surface_id")
    entities = set(surface.get("primary_entities") or [])
    actions: set[str] = set()
    for owner_id, owner_kind, collection in iter_data_contract_collections(dict(contract)):
        if owner_id != module_id or owner_kind != "module" or collection.get("entity") not in entities:
            continue
        for operation in ("list", "get"):
            actions.add(canonical_read_action_id(collection["name"], operation))
    return sorted(actions)


def all_module_actions(context_variables: Any) -> dict[str, list[str]]:
    """Return the one approved inventory for page references and gate selection.

    Managed facades are included for page bindings. Subscription gate selection
    applies the managed-ownership exclusion to this projection.
    """
    if context_variables is None:
        return {}
    surface_map = detach(context_variables.get("design_surface_map"))
    if not isinstance(surface_map, Mapping):
        return {}
    contract = context_variables.get("data_contract")
    return {
        surface["surface_id"]: sorted(set(
            [*(surface.get("owned_mutations") or []), *(surface.get("custom_reads") or []),
             *canonical_read_actions_for_surface(surface, contract)]
        ))
        for surface in surface_map.get("surfaces") or []
        if surface.get("surface_kind") == "module" and surface.get("owner") == "app"
    }
