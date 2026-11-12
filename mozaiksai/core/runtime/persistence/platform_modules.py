"""Compose a workspace data contract with the platform modules mounted into it.

A host can mount platform-provided modules into a workspace whose data contract
it does not own; Studio mounts the packaged ``factory_app/app/modules``. Those
modules declare the collections they own in their own bundle's
``data/contract.json``, in the same contract shape an app uses. Each module
dispatch then gets exactly one allow-list:

* An app module keeps the workspace contract unchanged, and may not address a
  collection a mounted platform module declares, even when the workspace has
  no contract at all.
* A mounted platform module may address only the collections declared for it:
  its own declarations, collections another mounted platform module shares
  with it, and collections the workspace declares for it, either under its own
  surface or in a ``shared_collections`` entry that names it.

A ``shared_collections`` grant names the receiving module in ``shared_with`` or
``read_by``, and identifies the shared collection by its owner's
``owner_module`` and declared ``name``, or by a literal ``mongo_collection``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .intent_loader import DataContract, DataContractLoadError, iter_data_contract_collections
from .naming import safe_identifier
from .ownership import collection_bindings, collection_ownership

CollectionKey = tuple[str, str]
_GRANT_FIELDS = ("shared_with", "read_by")
_LITERAL_FIELDS = ("mongo_collection", "collection")


@dataclass(frozen=True)
class CollectionAccess:
    """The collections one dispatching module may address.

    ``reserved_*`` name collections this module may not reach even where its
    own contract would otherwise allow it. ``literal_names`` is ``None`` when
    literal access follows the contract's existing rules; otherwise it lists
    the declared literal Mongo names, beyond the derived storage names of the
    contract's own collections.
    """

    data_contract: DataContract | None
    reserved_collections: frozenset[CollectionKey] = field(default_factory=frozenset)
    reserved_literals: frozenset[str] = field(default_factory=frozenset)
    literal_names: frozenset[str] | None = None
    read_only_collections: frozenset[CollectionKey] = field(default_factory=frozenset)
    read_only_literals: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def for_workspace(cls, module_id: str | None, contract: DataContract | None) -> CollectionAccess:
        collections, literals = _read_only_targets(_shared_grants(contract), module_id)
        return cls(data_contract=contract, read_only_collections=collections, read_only_literals=literals)


@dataclass(frozen=True)
class _SharedGrant:
    index: int
    grantees: frozenset[str]
    owner: str
    collection: CollectionKey | None
    literals: frozenset[str]
    read_only_grantees: frozenset[str]
    collection_targets: frozenset[CollectionKey]
    literal_targets: frozenset[str]


def _storage_name(collection: Mapping[str, Any]) -> str:
    return str(collection.get("name") or collection.get("entity") or collection.get("entity_name") or "").strip()


def _owned_collections(contract: DataContract | None) -> dict[CollectionKey, tuple[str, dict[str, Any]]]:
    """Collections with a resolved owner, keyed by owner and storage name."""
    rows: dict[CollectionKey, tuple[str, dict[str, Any]]] = {}
    for owner_id, owner_kind, collection in iter_data_contract_collections(contract, require_complete_ownership=False):
        name = _storage_name(collection)
        if owner_id and name:
            rows[(owner_id, name)] = (owner_kind, collection)
    return rows


def _literal_names(collection: Mapping[str, Any]) -> frozenset[str]:
    return frozenset(
        str(collection[key]).strip() for key in _LITERAL_FIELDS if str(collection.get(key) or "").strip()
    )



def _literal_aggregate_owner(entry: Any) -> str | None:
    """An existing runtime aggregate can own a literal without a logical entity.

    Definition metadata distinguishes this declaration from a reference. It
    supplies no logical collection binding or factory generation authority.
    """
    if not isinstance(entry, Mapping) or _storage_name(entry):
        return None
    owner = entry.get("owner_module")
    if not isinstance(owner, str) or not owner.strip() or len(_literal_names(entry)) != 1:
        return None
    if any(key in entry and (not isinstance(entry[key], str) or not entry[key].strip())
           for key in _LITERAL_FIELDS):
        return None
    if not any(isinstance(entry.get(key), list) and entry[key] for key in ("indexes", "owner_fields")):
        return None
    return owner.strip()


def _shared_grants(contract: DataContract | None) -> list[_SharedGrant]:
    grants: list[_SharedGrant] = []
    owned = _owned_collections(contract)
    shared = (contract or {}).get("shared_collections") or []
    literal_aggregates = [(owner, _literal_names(entry)) for entry in shared
                          if (owner := _literal_aggregate_owner(entry)) is not None]
    for index, entry in enumerate(shared):
        if not isinstance(entry, Mapping):
            continue
        declarations: list[Mapping[str, Any]] = []
        for grant_field in _GRANT_FIELDS:
            value = entry.get(grant_field)
            if value is None:
                continue
            if not isinstance(value, list):
                raise DataContractLoadError(f"shared_collections[{index}].{grant_field} must be a list or null")
            for declaration in value:
                if not isinstance(declaration, Mapping):
                    raise DataContractLoadError(f"shared_collections[{index}].{grant_field} entries must be objects")
                receiver = declaration.get("module")
                if not isinstance(receiver, str) or not receiver.strip():
                    raise DataContractLoadError("Shared collection access requires a receiving module")
                declarations.append(declaration)
        grantees = frozenset(
            declaration["module"].strip() for declaration in declarations
        )
        ownership = entry.get("ownership")
        owner_surface = ownership.get("surface_id") if isinstance(ownership, Mapping) else None
        owner = str(entry.get("owner_module") or owner_surface or entry.get("module_id") or "").strip()
        name = _storage_name(entry)
        literals = _literal_names(entry)
        targets = {
            key for key, (_kind, row) in owned.items()
            if key[0] == owner and (not name or key[1] == name)
            and (not literals or bool(literals & _literal_names(row)))
        } if owner and (name or literals) else set()
        target_literals = literals | frozenset(
            literal for key in targets for literal in _literal_names(owned[key][1])
        )
        read_only: set[str] = set()
        for declaration in declarations:
            access = declaration.get("access")
            if access is None:
                continue  # Unmarked grants retain their existing operation behavior.
            if not isinstance(access, str) or access != "read_only":
                raise DataContractLoadError("Shared collection access must be read_only or null when declared")
            literal_only = not targets and _literal_aggregate_owner(entry) == owner
            if len(targets) != 1 and not literal_only:
                raise DataContractLoadError("A read_only grant must resolve to one declared collection owner")
            target = next(iter(targets), None)
            if any(
                key != target and (
                    (target is not None and _physical_identity(key) == _physical_identity(target))
                    or bool(target_literals & _literal_names(row))
                )
                for key, (_kind, row) in owned.items()
            ) or any(other_owner != owner and bool(target_literals & other_literals)
                     for other_owner, other_literals in literal_aggregates):
                raise DataContractLoadError("A read_only target has conflicting physical collection owners")
            read_only.add(declaration["module"].strip())
        grants.append(_SharedGrant(
            index=index, grantees=grantees, owner=owner,
            collection=(owner, name) if owner and name else None, literals=literals,
            read_only_grantees=frozenset(read_only), collection_targets=frozenset(targets),
            literal_targets=target_literals,
        ))
    return grants


def validate_shared_collection_access(contract: DataContract) -> None:
    """Reject ambiguous explicit operation grants before the host loads modules."""
    _shared_grants(contract)


def _read_only_targets(
    grants: Iterable[_SharedGrant], module_id: str | None,
) -> tuple[frozenset[CollectionKey], frozenset[str]]:
    collections: set[CollectionKey] = set()
    literals: set[str] = set()
    for grant in grants:
        if grant.owner != module_id and grant.read_only_grantees and (
            module_id is None or module_id in grant.read_only_grantees
        ):
            collections.update(grant.collection_targets)
            literals.update(grant.literal_targets)
    return frozenset(collections), frozenset(literals)


def _physical_identity(key: CollectionKey) -> CollectionKey:
    """The parts of a storage name that ``collection_name_for`` derives from a declaration."""
    return safe_identifier(key[0]), safe_identifier(key[1])


def _contract(rows: Mapping[CollectionKey, tuple[str, dict[str, Any]]], version: str) -> DataContract:
    surfaces: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for (owner, _name), (owner_kind, collection) in sorted(rows.items()):
        surfaces.setdefault((owner, owner_kind), []).append(dict(collection))
    return {
        "version": version,
        "surfaces": [
            {"surface_id": owner, "surface_kind": owner_kind, "collections": collections}
            for (owner, owner_kind), collections in surfaces.items()
        ],
        "shared_collections": [],
    }


class PlatformModuleDeclarations:
    """Collection declarations of the platform modules mounted into one workspace."""

    def __init__(self, contract: DataContract | None, module_ids: Iterable[str]) -> None:
        self.module_ids = frozenset(str(module_id) for module_id in module_ids)
        self._version = str((contract or {}).get("version") or "1")
        self._collections = {
            key: row for key, row in _owned_collections(contract).items() if key[0] in self.module_ids
        }
        self._grants: dict[str, set[CollectionKey]] = {}
        self._literal_grants: dict[str, set[str]] = {}
        reserved_literals: set[str] = set()
        self._access_grants = tuple(
            grant for grant in _shared_grants(contract) if not grant.owner or grant.owner in self.module_ids
        )
        for grant in self._access_grants:
            if grant.collection is not None and grant.collection not in self._collections:
                raise DataContractLoadError(
                    f"data_contract.shared_collections[{grant.index}] shares "
                    f"{grant.collection[0]}.{grant.collection[1]}, which no mounted platform module declares"
                )
            reserved_literals |= grant.literals
            for grantee in grant.grantees & self.module_ids:
                if grant.collection is not None:
                    self._grants.setdefault(grantee, set()).add(grant.collection)
                self._literal_grants.setdefault(grantee, set()).update(grant.literals)
        for _kind, row in self._collections.values():
            reserved_literals |= _literal_names(row)
        self._reserved_literals = frozenset(reserved_literals)
        self._reserved_identities = {_physical_identity(key) for key in self._collections}

    @property
    def collections(self) -> frozenset[CollectionKey]:
        """Every (owner module, storage name) the mounted platform modules declare."""
        return frozenset(self._collections)

    def access_for(self, module_id: str | None, workspace: DataContract | None) -> CollectionAccess:
        """Resolve the allow-list for one dispatching module."""
        read_only_collections, read_only_literals = _read_only_targets(_shared_grants(workspace), module_id)
        if module_id not in self.module_ids:
            return CollectionAccess(
                data_contract=workspace,
                reserved_collections=frozenset(self._collections),
                reserved_literals=self._reserved_literals,
                read_only_collections=read_only_collections, read_only_literals=read_only_literals,
            )
        rows = {key: row for key, row in self._collections.items() if key[0] == module_id}
        rows.update({key: self._collections[key] for key in self._grants.get(module_id, ())})
        literals = set(self._literal_grants.get(module_id, ()))
        platform_literals = literals | {
            name for _kind, row in rows.values() for name in _literal_names(row)
        }
        workspace_rows = _owned_collections(workspace)
        granted = {key for key in workspace_rows if key[0] == module_id}
        for grant in _shared_grants(workspace):
            if module_id not in grant.grantees:
                continue
            literals |= grant.literals
            if grant.collection is not None and grant.collection in workspace_rows:
                granted.add(grant.collection)
        for key in granted:
            if _physical_identity(key) in self._reserved_identities:
                raise DataContractLoadError(
                    f"Workspace data contract redeclares platform module collection {key[0]}.{key[1]}"
                )
            rows[key] = workspace_rows[key]
        for _kind, row in rows.values():
            literals |= _literal_names(row)
        platform_read_only, platform_read_only_literals = _read_only_targets(self._access_grants, module_id)
        return CollectionAccess(
            data_contract=_contract(rows, self._version), literal_names=frozenset(literals),
            reserved_collections=frozenset(self._collections.keys() - rows.keys()),
            reserved_literals=self._reserved_literals - platform_literals,
            read_only_collections=read_only_collections | platform_read_only,
            read_only_literals=read_only_literals | platform_read_only_literals,
        )

    def validate_workspace(self, workspace: DataContract | None) -> None:
        """Fail app load on an ambiguous composition rather than on a request."""
        if workspace is not None:
            for (owner, _reference), storage in collection_bindings(workspace).items():
                if _physical_identity((owner, storage)) in self._reserved_identities:
                    raise DataContractLoadError(
                        f"Workspace data contract redeclares platform module collection {owner}.{storage}; "
                        "declare it in one place, or override the module to own its data"
                    )
            workspace_literals = {
                name for _kind, row in _owned_collections(workspace).values() for name in _literal_names(row)
            }
            conflicts = sorted(self._reserved_literals & workspace_literals)
            if conflicts:
                raise DataContractLoadError(f"Workspace data contract redeclares platform module collections {conflicts}")
        for module_id in sorted(self.module_ids):
            contract = self.access_for(module_id, workspace).data_contract
            collection_bindings(contract or {})
            collection_ownership(contract, app_id="platform-module-validation", app_slug=None)


__all__ = ["CollectionAccess", "PlatformModuleDeclarations", "validate_shared_collection_access"]
