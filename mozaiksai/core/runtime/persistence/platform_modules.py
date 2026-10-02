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


@dataclass(frozen=True)
class _SharedGrant:
    index: int
    grantees: frozenset[str]
    owner: str
    collection: CollectionKey | None
    literals: frozenset[str]


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


def _shared_grants(contract: DataContract | None) -> list[_SharedGrant]:
    grants: list[_SharedGrant] = []
    for index, entry in enumerate((contract or {}).get("shared_collections") or []):
        if not isinstance(entry, Mapping):
            continue
        grantees = frozenset(
            str(grant.get("module")).strip()
            for grant_field in _GRANT_FIELDS
            for grant in entry.get(grant_field) or []
            if isinstance(grant, Mapping) and str(grant.get("module") or "").strip()
        )
        ownership = entry.get("ownership")
        owner_surface = ownership.get("surface_id") if isinstance(ownership, Mapping) else None
        owner = str(entry.get("owner_module") or owner_surface or entry.get("module_id") or "").strip()
        name = _storage_name(entry)
        grants.append(_SharedGrant(
            index=index, grantees=grantees, owner=owner,
            collection=(owner, name) if owner and name else None, literals=_literal_names(entry),
        ))
    return grants


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
        for grant in _shared_grants(contract):
            if grant.owner and grant.owner not in self.module_ids:
                continue  # The workspace overrides the owner; that data is the app's.
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
        if module_id not in self.module_ids:
            return CollectionAccess(
                data_contract=workspace,
                reserved_collections=frozenset(self._collections),
                reserved_literals=self._reserved_literals,
            )
        rows = {key: row for key, row in self._collections.items() if key[0] == module_id}
        rows.update({key: self._collections[key] for key in self._grants.get(module_id, ())})
        literals = set(self._literal_grants.get(module_id, ()))
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
        return CollectionAccess(data_contract=_contract(rows, self._version), literal_names=frozenset(literals))

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


__all__ = ["CollectionAccess", "PlatformModuleDeclarations"]
