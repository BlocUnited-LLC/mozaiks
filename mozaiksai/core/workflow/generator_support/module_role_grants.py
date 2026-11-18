"""Compile module permissions from the approved design's access policy."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import yaml
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


class ReviewedPackIdentity(BaseModel):
    """Pack bytes whose action requirements were shown at design review."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    pack_id: _Text
    version: _Text
    digest: Annotated[str, Field(strict=True, pattern=r"^sha256:[0-9a-f]{64}$")]


@dataclass(frozen=True)
class PackAuthorization:
    identities: tuple[ReviewedPackIdentity, ...]
    data_contracts: tuple[dict[str, Any], ...]
    actions: dict[str, dict[str, tuple[str, ...]]]
    module_packs: dict[str, str]


def verified_pack_authorization(context: Any, *, selection: list[dict[str, Any]] | None = None) -> PackAuthorization:
    """Read action prerequisites only from selected, verified static pack files."""
    from factory_app.workflows._shared.pack_selection import rows_for_pack_choices
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        declared_output_owners,
        static_pack_templates,
        verified_selected_pack_sources,
    )
    from mozaiksai.core.runtime.app.module_loader import ModuleDefinition

    if selection is None:
        from mozaiksai.core.workflow.generator_support.design_access_policy import (
            resolve_reviewed_pack_selection,
        )

        selection = resolve_reviewed_pack_selection(context)
    selected = rows_for_pack_choices(context, selection)
    if not selected:
        return PackAuthorization((), (), {}, {})
    identities: list[ReviewedPackIdentity] = []
    contracts: list[dict[str, Any]] = []
    actions: dict[str, dict[str, tuple[str, ...]]] = {}
    module_packs: dict[str, str] = {}
    for entry in verified_selected_pack_sources(selected, context_variables=context):
        root = Path(entry["source_path"])
        static, contextual = static_pack_templates(root, entry["pack_id"])
        owners = declared_output_owners(root)
        identities.append(ReviewedPackIdentity.model_validate({
            key: entry[key] for key in ("pack_id", "version", "digest")
        }))
        if "data/contract.json" in contextual:
            raise ValueError("Reviewed pack data contract must be a static verified template")
        pack_contract = None
        if "data/contract.json" in static:
            if owners.get("data/contract.json") != "templates":
                raise ValueError("Reviewed pack data contract must be template-owned")
            data = json.loads(static["data/contract.json"])
            if not isinstance(data, dict):
                raise ValueError("Reviewed pack data contract must be an object")
            pack_contract = data
            contracts.append(data)
        pack_modules: set[str] = set()
        for path, source in static.items():
            parts = path.split("/")
            if len(parts) != 3 or parts[0] != "modules" or parts[2] != "module.yaml":
                continue
            if owners.get(path) != "templates":
                continue
            module = ModuleDefinition.model_validate(yaml.safe_load(source))
            if module.module.id != parts[1] or module.module.id in actions:
                raise ValueError(f"Conflicting verified pack module {parts[1]!r}")
            actions[module.module.id] = {action.id: tuple(action.permissions) for action in module.actions}
            module_packs[module.module.id] = entry["pack_id"]
            pack_modules.add(module.module.id)
        if pack_contract is not None:
            for surface in pack_contract.get("surfaces") or []:
                if surface.get("surface_kind") != "module" or surface.get("surface_id") not in pack_modules:
                    raise ValueError("Reviewed pack data must belong to its verified module")
        if any(path.startswith("modules/") and path.endswith("/module.yaml") for path in contextual):
            raise ValueError("Reviewed pack module contracts must be static verified templates")
    return PackAuthorization(tuple(sorted(identities, key=lambda row: row.pack_id)), tuple(contracts), actions, module_packs)


def validate_reviewed_pack_data(contract: dict[str, Any], authorization: PackAuthorization) -> set[tuple[str, str]]:
    """Require the exact verified contributions, including their owner boundaries."""
    from mozaiksai.core.workflow.generator_support.code_files import compile_data_contract

    if not authorization.data_contracts:
        return set()
    if compile_data_contract(contract, pack_data_contracts=list(authorization.data_contracts)) != contract:
        raise ValueError("access_policy requires the verified pack data in the reviewed data_contract")
    expected = {
        (owner, collection["name"])
        for contribution in authorization.data_contracts
        for owner, kind, collection in iter_data_contract_collections(contribution)
        if kind == "module"
    }
    pack_owners = {owner for owner, _name in expected}
    for owner, kind, collection in iter_data_contract_collections(contract):
        if kind == "module" and owner in pack_owners and (owner, collection["name"]) not in expected:
            raise ValueError(f"Unverified collection {owner}.{collection['name']} cannot belong to a pack module")
    return expected


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
    # Only a singular/plural slip is a misspelling. Live run 7 (mozaiks#1005) put
    # create_borrow_request under 'tools'; calling that a misspelling of create_tool
    # sent the repair the wrong way.
    if candidate == action or candidate not in canonical or candidate.rstrip("s") != action.rstrip("s"):
        return ""
    return f"Use the canonical {candidate!r} instead of {action!r}. "


def _other_collection_owner(action: str, name: str, canonical_owners: dict[tuple[str, str], str]) -> str:
    """Name the collection whose canonical action this is, when it is not this one."""
    owners = sorted({collection for (_, candidate), collection in canonical_owners.items()
                     if candidate == action and collection != name})
    if len(owners) != 1:
        return ""
    return (f"{action!r} is the canonical action of collection {owners[0]!r}; "
            f"grant it in a grant whose collection is {owners[0]!r}, not {name!r}. ")


def _custom_action_remedy(action: str, owner: str, collection: dict[str, Any]) -> str:
    """Say how an undeclared domain action, such as an approval, becomes grantable."""
    generic = (f" or grant the canonical {canonical_write_action_id(collection['entity'], 'update')!r}"
               if collection_has_canonical_writes(collection) else "")
    return (f"To grant {action!r}, declare it in surface_map surface {owner!r} owned_mutations "
            f"(custom_reads for a read) in this same bundle{generic}. ")


def validate_access_policy(
    raw: Any, *, data_contract: Any, design_surface_map: Any, pack_authorization: PackAuthorization | None = None,
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
    pack_authorization = pack_authorization or PackAuthorization((), (), {}, {})
    pack_collections = validate_reviewed_pack_data(contract, pack_authorization)
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
    role_actions: dict[str, set[tuple[str, str]]] = defaultdict(set)
    role_permissions: dict[str, set[str]] = defaultdict(set)
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
        pack_actions = pack_authorization.actions.get(owner)
        if pack_actions is not None and (owner, name) not in pack_collections:
            raise ValueError(f"pack grant collection {owner}.{name} is not owned by its verified pack")
        allowed = set(pack_actions) if pack_actions is not None else canonical | reads | set(
            surfaces[owner].get("owned_mutations") or []
        ) | set(surfaces[owner].get("custom_reads") or [])
        for action in grant["actions"]:
            if action not in allowed:
                choices = sorted(candidate for candidate in allowed
                                 if canonical_owners.get((owner, candidate), name) == name)
                remedy = (_other_collection_owner(action, name, canonical_owners)
                          or _canonical_spelling(action, name, collection, canonical | reads)
                          or _custom_action_remedy(action, owner, collection))
                raise ValueError(
                    f"access_policy action {action!r} is not an approved action of {name!r}. {remedy}"
                    f"Approved actions: {choices}. Canonical reads use the exact collection name; "
                    "canonical writes use the snake_case entity name."
                )
            canonical_owner = canonical_owners.get((owner, action)) if pack_actions is None else None
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
            if pack_actions is not None:
                if not pack_actions[action]:
                    raise ValueError(f"pack action {owner}.{action} has no dispatch permission to grant")
                role_actions[role].add((owner, action))
                role_permissions[role].update(pack_actions[action])
            else:
                role_permissions[role].add(f"{name}.{action}")
        # Other roles may supply the remaining canonical grants; check after all rows.
    errors = [
        f"access_policy action {owner}.{action} names multiple collections {sorted(names)}; "
        "bind this module action to exactly one collection in every role's grants, "
        "even when its implementation changes several collections"
        for (owner, action), names in sorted(action_collections.items()) if len(names) > 1
    ]
    write_gaps = False
    for name, actions in sorted(selected.items()):
        owner, collection = collections[name]
        if owner in pack_authorization.actions:
            continue
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
            "instead of omitting them. For example, when the approved concept has submitter and approver "
            "roles, submitter may receive create_<entity> while approver receives update_<entity> and "
            "delete_<entity> for the same collection; use this split only when it matches the approved roles"
        )
    if errors:
        raise ValueError("; ".join(errors))
    consumers: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for owner, pack_actions_by_name in pack_authorization.actions.items():
        for action, permissions in pack_actions_by_name.items():
            for permission in permissions:
                consumers[permission].add((owner, action))
    for role, role_scopes in role_permissions.items():
        for permission in sorted(role_scopes & consumers.keys()):
            missing_consumers = consumers[permission] - role_actions[role]
            if missing_consumers:
                raise ValueError(
                    f"role {role!r} cannot receive shared pack permission {permission!r} without reviewing "
                    f"every consuming action: {sorted(f'{owner}.{action}' for owner, action in missing_consumers)}"
                )
    return policy


def compiled_role_grants(
    policy: dict[str, Any], *, data_contract: Any = None, pack_authorization: PackAuthorization | None = None,
) -> list[dict[str, Any]]:
    """Render the auth contract's role grants; no caller receives this union."""
    permissions: dict[str, set[str]] = defaultdict(set)
    owners = {
        collection["name"]: owner
        for owner, kind, collection in iter_data_contract_collections(detach(data_contract))
        if kind == "module"
    } if data_contract is not None else {}
    for grant in policy["grants"]:
        owner = owners.get(grant["collection"])
        pack_actions = pack_authorization.actions.get(owner) if pack_authorization and owner is not None else None
        for action in grant["actions"]:
            permissions[grant["role_id"]].update(
                pack_actions[action] if pack_actions is not None else [f"{grant['collection']}.{action}"]
            )
    return [{"id": role["role_id"], "permissions": sorted(permissions[role["role_id"]])}
            for role in policy["roles"]]


def validate_pack_permission_consumers(
    files: dict[str, str], policy: dict[str, Any], *, data_contract: Any, pack_authorization: PackAuthorization,
) -> None:
    """Refuse another module consuming a permission derived from reviewed pack actions."""
    from mozaiksai.core.runtime.app.module_loader import ModuleDefinition

    owners = {
        collection["name"]: owner
        for owner, kind, collection in iter_data_contract_collections(detach(data_contract))
        if kind == "module"
    }
    pack_permissions = {
        permission for actions in pack_authorization.actions.values()
        for permissions in actions.values() for permission in permissions
    }
    compiled = compiled_role_grants(policy, data_contract=data_contract, pack_authorization=pack_authorization)
    granted = {permission for role in compiled for permission in role["permissions"]} & pack_permissions
    authored_actions = {
        (owners[grant["collection"]], action): f"{grant['collection']}.{action}"
        for grant in policy["grants"]
        for action in grant["actions"]
        if owners.get(grant["collection"]) not in pack_authorization.actions
    }
    if not granted:
        return
    for path, source in files.items():
        parts = path.split("/")
        if len(parts) != 3 or parts[0] != "modules" or parts[2] != "module.yaml":
            continue
        module = ModuleDefinition.model_validate(yaml.safe_load(source))
        if module.module.id != parts[1]:
            raise ValueError(f"Module identity does not match {path}")
        for action in module.actions:
            if not granted.intersection(action.permissions):
                continue
            expected = pack_authorization.actions.get(module.module.id, {}).get(action.id)
            if expected is None:
                authored = authored_actions.get((module.module.id, action.id))
                expected = (authored,) if authored else None
            if expected is None or tuple(action.permissions) != expected:
                raise ValueError(f"Unreviewed action {module.module.id}.{action.id} consumes a reviewed pack permission")


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
