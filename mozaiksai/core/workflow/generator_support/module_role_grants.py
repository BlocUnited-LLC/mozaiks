"""Compile module permissions from the approved design's access policy."""

from __future__ import annotations

from collections import defaultdict
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from mozaiksai.core.runtime.persistence.intent_loader import iter_data_contract_collections
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    CANONICAL_WRITE_OPERATIONS,
    canonical_read_action_id,
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


def _canonical_spelling(action: str, name: str, collection: dict[str, Any], canonical: set[str]) -> str:
    """Name the canonical id an unknown action misspelled, without accepting the alias.

    Reads take the collection name and writes the entity name, so a design writes
    get_tool for the canonical get_tools. A live repair that was only told the
    action was not approved dropped the read instead of renaming it.
    """
    operation = action.split("_", 1)[0]
    if operation in ("get", "list"):
        candidate = canonical_read_action_id(name, operation)
    elif operation in CANONICAL_WRITE_OPERATIONS and collection_has_canonical_writes(collection):
        candidate = canonical_write_action_id(collection["entity"], operation)
    else:
        return ""
    if candidate == action or candidate not in canonical:
        return ""
    return f"Use the canonical {candidate!r} instead of {action!r}. "


def validate_access_policy(
    raw: Any, *, data_contract: Any, design_surface_map: Any,
) -> dict[str, Any]:
    """Close role, collection and action references before design approval.

    The policy cannot grant platform/facade actions or create identity membership.
    A role-managed collection covers every canonical write explicitly, so an
    omitted update/delete grant can never silently become an open action.
    Selecting either canonical read requires both; custom reads do not select
    canonical reads, and write grants never imply read grants.
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
    canonical_owners: dict[tuple[str, str], str] = {}
    for name, (owner, collection) in collections.items():
        actions = {canonical_read_action_id(name, operation) for operation in ("get", "list")}
        if collection_has_canonical_writes(collection):
            actions.update(canonical_write_action_id(collection["entity"], operation)
                           for operation in CANONICAL_WRITE_OPERATIONS)
        canonical_owners.update({(owner, action): name for action in actions})
    selected: dict[str, set[str]] = defaultdict(set)
    seen: set[tuple[str, str, str]] = set()
    action_collections: dict[tuple[str, str], set[str]] = defaultdict(set)
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
        reads = {canonical_read_action_id(name, operation) for operation in ("get", "list")}
        allowed = canonical | reads | set(surfaces[owner].get("owned_mutations") or []) | set(
            surfaces[owner].get("custom_reads") or []
        )
        for action in grant["actions"]:
            if action not in allowed:
                choices = sorted(candidate for candidate in allowed
                                 if canonical_owners.get((owner, candidate), name) == name)
                raise ValueError(
                    f"access_policy action {action!r} is not an approved action of {name!r}. "
                    f"{_canonical_spelling(action, name, collection, canonical | reads)}"
                    f"Approved actions: {choices}. Canonical reads use the exact collection name; "
                    "canonical writes use the snake_case entity name."
                )
            canonical_owner = canonical_owners.get((owner, action))
            if canonical_owner is not None and canonical_owner != name:
                raise ValueError(
                    f"access_policy action {owner}.{action} belongs to collection {canonical_owner!r}, "
                    f"not {name!r}; custom action declarations cannot change canonical ownership"
                )
            key = (role, name, action)
            if key in seen:
                raise ValueError(f"access_policy repeats grant {key!r}")
            seen.add(key)
            action_collections[owner, action].add(name)
            selected[name].add(action)
        # Other roles may supply the remaining canonical grants; check after all rows.
    errors = [
        f"access_policy action {owner}.{action} names multiple collections {sorted(names)}; "
        "bind this module action to exactly one collection in every role's grants, "
        "even when its implementation changes several collections"
        for (owner, action), names in sorted(action_collections.items()) if len(names) > 1
    ]
    write_gaps = False
    for name, actions in sorted(selected.items()):
        _, collection = collections[name]
        required = {
            canonical_write_action_id(collection["entity"], operation)
            for operation in CANONICAL_WRITE_OPERATIONS
        } if collection_has_canonical_writes(collection) else set()
        if missing := required - actions:
            write_gaps = True
            errors.append(f"access_policy for {name!r} omits canonical write grants {sorted(missing)}")
        reads = {canonical_read_action_id(name, operation) for operation in ("get", "list")}
        if actions & reads and (missing_reads := reads - actions):
            errors.append(
                f"access_policy for {name!r} omits canonical read grants {sorted(missing_reads)}; "
                "canonical get and list both use the collection name, so grant "
                f"{sorted(missing_reads)} wherever {sorted(actions & reads)} is granted"
            )
    if write_gaps:
        # The rejection must name the remedy: a design that keeps members from editing
        # or deleting records repeated the same omission until its repairs ran out.
        errors.append(
            "every canonical create, update and delete of a collection in access_policy must be granted "
            "to at least one role, because an ungranted canonical write would be open to every signed-in "
            "user; when members should not change or delete these records directly, grant the missing "
            "canonical writes to the role that administers them (for example an admin or approver role) "
            "instead of omitting them"
        )
    if errors:
        raise ValueError("; ".join(errors))
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


def apply_module_role_permissions(manifest: dict[str, Any], permissions: dict[str, str]) -> None:
    """Attach only the exact action permissions resolved from an approved policy."""
    if not permissions:
        return
    declarations = manifest.setdefault("permissions", [])
    known = {entry["id"] for entry in declarations}
    for action_id, permission in sorted(permissions.items()):
        if permission not in known:
            declarations.append({"id": permission, "description": f"Approved role access to {action_id}."})
            known.add(permission)
    for action in manifest.get("actions") or []:
        if action.get("id") in permissions:
            action["permissions"] = [permissions[action["id"]]]


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
