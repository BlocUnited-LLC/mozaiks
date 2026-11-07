"""Compile module permissions from the approved design's access policy."""

from __future__ import annotations

from collections import defaultdict
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from mozaiksai.core.runtime.persistence.intent_loader import iter_data_contract_collections
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    CANONICAL_WRITE_OPERATIONS,
    canonical_write_action_id,
    collection_has_canonical_writes,
)

_Identifier = Annotated[str, Field(strict=True, pattern=r"^[a-z][a-z0-9_]*$")]
_Text = Annotated[str, Field(strict=True, min_length=1)]


class DesignRole(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role_id: _Identifier
    label: _Text
    description: _Text


class DesignGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role_id: _Identifier
    collection: _Text
    actions: list[_Identifier] = Field(min_length=1)


class DesignAccessPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    roles: list[DesignRole]
    grants: list[DesignGrant]


def validate_access_policy(
    raw: Any, *, data_contract: Any, design_surface_map: Any,
) -> dict[str, Any]:
    """Close role, collection and mutation references before design approval.

    The policy cannot grant platform/facade actions or create identity membership.
    A role-managed collection covers every canonical write explicitly, so an
    omitted update/delete grant can never silently become an open action.
    """
    policy = DesignAccessPolicy.model_validate(
        {"roles": [], "grants": []} if raw is None else detach(raw)
    ).model_dump()
    role_ids = [role["role_id"] for role in policy["roles"]]
    if len(role_ids) != len(set(role_ids)):
        raise ValueError("access_policy repeats a role_id")
    if not policy["grants"]:
        return policy
    contract, surface_map = detach(data_contract), detach(design_surface_map)
    if not isinstance(contract, dict) or not isinstance(surface_map, dict):
        raise ValueError("access_policy grants require the approved data_contract and surface_map")
    surfaces = {
        surface["surface_id"]: surface for surface in surface_map.get("surfaces", [])
        if surface.get("surface_kind") == "module" and surface.get("owner") == "app"
    }
    collections: dict[str, tuple[str, dict[str, Any]]] = {}
    for owner, kind, collection in iter_data_contract_collections(contract):
        if kind != "module" or owner not in surfaces:
            continue
        name = collection["name"]
        if name in collections:
            raise ValueError(f"access_policy collection {name!r} is ambiguous")
        collections[name] = (owner, collection)
    selected: dict[str, set[str]] = defaultdict(set)
    seen: set[tuple[str, str, str]] = set()
    action_collections: dict[tuple[str, str], str] = {}
    for grant in policy["grants"]:
        role, name = grant["role_id"], grant["collection"]
        if role not in role_ids:
            raise ValueError(f"access_policy grant names unknown role {role!r}")
        if name not in collections:
            raise ValueError(f"access_policy grant names unknown app-owned collection {name!r}")
        owner, collection = collections[name]
        canonical = {
            canonical_write_action_id(collection["entity"], operation)
            for operation in CANONICAL_WRITE_OPERATIONS
        } if collection_has_canonical_writes(collection) else set()
        allowed = canonical | set(surfaces[owner].get("owned_mutations") or [])
        for action in grant["actions"]:
            if action not in allowed:
                raise ValueError(f"access_policy action {action!r} is not an approved mutation of {name!r}")
            key = (role, name, action)
            if key in seen:
                raise ValueError(f"access_policy repeats grant {key!r}")
            seen.add(key)
            binding = (owner, action)
            previous = action_collections.setdefault(binding, name)
            if previous != name:
                raise ValueError(f"access_policy action {owner}.{action} names multiple collections")
            selected[name].add(action)
        # Other roles may supply the remaining canonical grants; check after all rows.
    missing_grants: list[str] = []
    for name, actions in sorted(selected.items()):
        _, collection = collections[name]
        required = {
            canonical_write_action_id(collection["entity"], operation)
            for operation in CANONICAL_WRITE_OPERATIONS
        } if collection_has_canonical_writes(collection) else set()
        if missing := required - actions:
            missing_grants.append(f"access_policy for {name!r} omits canonical write grants {sorted(missing)}")
    if missing_grants:
        raise ValueError("; ".join(missing_grants))
    return policy


def compiled_role_grants(policy: dict[str, Any]) -> list[dict[str, Any]]:
    """Render the auth contract's role grants; no caller receives this union."""
    permissions: dict[str, set[str]] = defaultdict(set)
    for grant in policy["grants"]:
        permissions[grant["role_id"]].update(
            f"{grant['collection']}.{action}" for action in grant["actions"]
        )
    return [{"id": role["role_id"], "permissions": sorted(permissions[role["role_id"]])}
            for role in policy["roles"]]


def approved_module_role_permissions(
    raw: Any, *, data_contract: Any, design_surface_map: Any,
) -> dict[str, dict[str, str]]:
    """Map approved action identities to their one code-derived permission id."""
    policy = validate_access_policy(raw, data_contract=data_contract, design_surface_map=design_surface_map)
    if not policy["grants"]:
        return {}
    owners = {collection["name"]: owner
              for owner, kind, collection in iter_data_contract_collections(detach(data_contract))
              if kind == "module"}
    result: dict[str, dict[str, str]] = {}
    for grant in policy["grants"]:
        target = result.setdefault(owners[grant["collection"]], {})
        for action in grant["actions"]:
            target[action] = f"{grant['collection']}.{action}"
    return result
