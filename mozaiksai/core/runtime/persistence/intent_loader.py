from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

DataContract = dict[str, Any]
DataEntityIndex = dict[tuple[str, str], dict[str, Any]]
_OWNERSHIP_FIELDS = frozenset({"scope", "entity", "tenancy", "owner_field", "fields"})
_COLLECTION_DEFINITION_FIELDS = _OWNERSHIP_FIELDS | {
    "ownership", "indexes", "lifecycle", "search_by", "module_id", "entity_name", "scope_field",
    "owner_fields", "record_key_fields",
}
_SHARED_REFERENCE_FIELDS = frozenset({
    "owner_module", "name", "mongo_collection", "primary_alias", "read_by", "shared_with", "notes",
})


class DataContractLoadError(ValueError):
    """Raised when data/contract.json is present but invalid."""


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _require_object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DataContractLoadError(f"{path} must be an object")
    return value


def _require_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise DataContractLoadError(f"{path} must be a list")
    return value


def is_shared_collection_reference(value: Any) -> bool:
    """A grant references existing storage; collection metadata cannot disguise itself as a grant."""
    return (
        isinstance(value, dict) and bool({"read_by", "shared_with"} & value.keys())
        and not (_COLLECTION_DEFINITION_FIELDS & value.keys())
    )


def shared_reference_content(reference: dict[str, Any]) -> dict[str, Any]:
    """Compare one reference across typed optional-null and hand-authored omission.

    Only optional metadata is omitted. Collection definitions never pass through
    this helper: their explicit null owner_field remains an ownership decision.
    """
    return {
        key: ([{field: item for field, item in grant.items() if item is not None} if isinstance(grant, dict) else grant
               for grant in value] if key in {"read_by", "shared_with"} and isinstance(value, list) else value)
        for key, value in reference.items() if value is not None
    }


def storage_metadata_content(value: dict[str, Any], *, alias: bool = False) -> dict[str, Any]:
    """Omit optional storage defaults, retaining explicit owner_field null and all values."""
    optional = {"owner_module"} if alias else {"mongo_collection", "data_alias", "owner_module"}
    return {key: item for key, item in value.items()
            if item is not None or key not in optional}


def resolve_shared_collection_references(contract: DataContract) -> list[tuple[str, str, dict[str, Any]]]:
    """Validate factory reference closure without changing storage identity.

    References resolve against declared collection owners, never alias order or
    inferred entity/tenancy. Runtime's collection allow-list remains the
    enforcement owner, including explicit read_only operation restrictions.
    """
    rows = list(iter_data_contract_collections(contract, require_complete_ownership=False))
    aliases: dict[str, str] = {}

    def optional_string(value: dict[str, Any], key: str, path: str) -> str | None:
        item = value.get(key)
        if item is not None and not _is_non_empty_string(item):
            raise DataContractLoadError(f"{path}.{key} must be a non-empty string or null")
        return item

    def declare_alias(alias: str | None, target: str | None, path: str, owner: str | None = None) -> None:
        if alias is None:
            return
        if not isinstance(target, str) or not target.strip():
            raise DataContractLoadError(f"{path} requires an explicit literal collection for alias {alias!r}")
        # Runtime alias lookup strips surrounding whitespace before resolving.
        alias, target = alias.strip(), target.strip()
        if alias in aliases and aliases[alias] != target:
            raise DataContractLoadError(f"Conflicting data alias {alias!r}: {aliases[alias]!r} and {target!r}")
        matches = [(row_owner, kind) for row_owner, kind, row in rows
                   if target == str(row.get("mongo_collection") or row.get("collection") or "").strip()]
        if len(matches) > 1:
            raise DataContractLoadError(f"{path} alias {alias!r} has conflicting collection owners")
        if owner is not None and matches != [(owner, "module")]:
            raise DataContractLoadError(f"{path}.owner_module must uniquely own literal collection {target!r}")
        aliases[alias] = target

    for collection_owner, kind, row in rows:
        declared_owner = optional_string(row, "owner_module", f"collection {row.get('name')!r}")
        if declared_owner is not None and (declared_owner, "module") != (collection_owner, kind):
            raise DataContractLoadError(f"Collection {row.get('name')!r}.owner_module conflicts with its declared owner")
        declare_alias(
            optional_string(row, "data_alias", "collection"), row.get("mongo_collection") or row.get("collection"),
            f"collection {row.get('name')!r}",
        )
    for index, value in enumerate(_require_list(contract.get("aliases", []), "data_contract.aliases")):
        path = f"data_contract.aliases[{index}]"
        alias_entry = _require_object(value, path)
        if set(alias_entry) - {"alias", "collection", "owner_module"}:
            raise DataContractLoadError(f"{path} permits only alias, collection, and owner_module")
        if not _is_non_empty_string(alias_entry.get("alias")) or not _is_non_empty_string(alias_entry.get("collection")):
            raise DataContractLoadError(f"{path} requires alias and literal collection")
        declare_alias(alias_entry["alias"], alias_entry["collection"], path, optional_string(alias_entry, "owner_module", path))

    # Runtime also reads primary/per-grantee aliases from full shared definitions.
    # Their complete ownership requirements do not exempt their aliases from closure.
    for index, value in enumerate(contract.get("shared_collections", [])):
        if is_shared_collection_reference(value):
            continue
        path = f"data_contract.shared_collections[{index}]"
        literal = optional_string(value, "mongo_collection", path) or optional_string(value, "collection", path)
        declare_alias(optional_string(value, "primary_alias", path), literal, path)
        for field in ("read_by", "shared_with"):
            grants = value.get(field)
            for ordinal, entry in enumerate(_require_list([] if grants is None else grants, f"{path}.{field}")):
                grant_path = f"{path}.{field}[{ordinal}]"
                grant = _require_object(entry, grant_path)
                alias = optional_string(grant, "data_alias", grant_path) or optional_string(grant, "alias", grant_path)
                declare_alias(alias, literal, grant_path)

    resolved: list[tuple[str, str, dict[str, Any]]] = []
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for index, value in enumerate(_require_list(contract.get("shared_collections", []), "data_contract.shared_collections")):
        if not is_shared_collection_reference(value):
            continue
        path = f"data_contract.shared_collections[{index}]"
        if set(value) - _SHARED_REFERENCE_FIELDS:
            raise DataContractLoadError(f"{path} contains unsupported reference fields {sorted(set(value) - _SHARED_REFERENCE_FIELDS)}")
        owner = optional_string(value, "owner_module", path)
        name = optional_string(value, "name", path)
        literal = optional_string(value, "mongo_collection", path)
        primary_alias = optional_string(value, "primary_alias", path)
        optional_string(value, "notes", path)
        if owner is None or not (name or literal):
            raise DataContractLoadError(f"{path} requires owner_module and a declared name or mongo_collection")
        matches = [(row_owner, row) for row_owner, kind, row in rows
                   if row_owner == owner and kind == "module"
                   and (name is None or row.get("name") == name)
                   and (literal is None or literal == (row.get("mongo_collection") or row.get("collection")))]
        if len(matches) != 1:
            raise DataContractLoadError(f"{path} must resolve to exactly one collection declared by {owner!r}")
        target = matches[0][1]
        target_literal = target.get("mongo_collection") or target.get("collection")
        # A literal shared by multiple owners is not an unambiguous reference.
        if target_literal and sum(str(target_literal).strip() == str(row.get("mongo_collection") or row.get("collection") or "").strip() for _, _, row in rows) != 1:
            raise DataContractLoadError(f"{path}.mongo_collection has conflicting collection owners")
        if primary_alias is not None and literal is None:
            raise DataContractLoadError(f"{path}.primary_alias requires explicit mongo_collection")
        declare_alias(primary_alias, literal, path)
        modules: set[str] = set()
        for field in ("read_by", "shared_with"):
            grants = value.get(field)
            for ordinal, entry in enumerate(_require_list([] if grants is None else grants, f"{path}.{field}")):
                grant_path = f"{path}.{field}[{ordinal}]"
                grant = _require_object(entry, grant_path)
                if set(grant) - {"module", "access", "purpose", "data_alias"}:
                    raise DataContractLoadError(f"{grant_path} contains unsupported grant fields")
                module = optional_string(grant, "module", grant_path)
                access = optional_string(grant, "access", grant_path)
                optional_string(grant, "purpose", grant_path)
                if module is None or module in modules:
                    raise DataContractLoadError(f"{grant_path}.module must name one distinct receiving module")
                if access not in (None, "read_only"):
                    raise DataContractLoadError(f"{grant_path}.access must be read_only when declared")
                modules.add(module)
                declare_alias(optional_string(grant, "data_alias", grant_path), literal, grant_path)
        if not modules:
            raise DataContractLoadError(f"{path} requires a non-empty read_by or shared_with grant")
        key = (owner, target["name"])
        if key in seen and shared_reference_content(seen[key]) != shared_reference_content(value):
            raise DataContractLoadError(f"Conflicting shared collection grants for {owner}.{target['name']}")
        seen[key] = value
        resolved.append((*key, value))
    from .platform_modules import validate_shared_collection_access

    validate_shared_collection_access(contract)
    return resolved


def validate_collection_ownership(collection: dict[str, Any], path: str, *, required: bool = True) -> None:
    """Require factory ownership decisions, or validate supplied runtime metadata."""
    scope = collection.get("scope")
    if required and (not isinstance(scope, str) or scope not in {"app", "platform", "hosted"}):
        raise DataContractLoadError(f"{path}.scope must be one of ['app', 'platform', 'hosted']")
    if (required or "entity" in collection) and not _is_non_empty_string(collection.get("entity")):
        raise DataContractLoadError(f"{path}.entity must name an explicit surface primary_entities entry")
    tenancy = collection.get("tenancy")
    if (required or "tenancy" in collection) and (not isinstance(tenancy, str) or tenancy not in {"per_user", "per_workspace", "app_wide"}):
        raise DataContractLoadError(
            f"{path}.tenancy must be one of ['per_user', 'per_workspace', 'app_wide']; "
            "declare owner_field from the collection fields for scoped records, or null for app_wide"
        )
    fields = _require_list(collection.get("fields"), f"{path}.fields") if required or "owner_field" in collection and "fields" in collection else []
    names = sorted({
        field["name"] for field in fields
        if isinstance(field, dict) and _is_non_empty_string(field.get("name"))
    })
    owner_field = collection.get("owner_field")
    if required and "owner_field" not in collection:
        raise DataContractLoadError(f"{path}.owner_field is required; use null for app_wide tenancy")
    if "owner_field" not in collection:
        return
    if tenancy == "app_wide":
        if owner_field is not None:
            raise DataContractLoadError(f"{path}.owner_field must be null for app_wide tenancy")
    elif owner_field is None and tenancy in {"per_user", "per_workspace"}:
        raise DataContractLoadError(f"{path}.owner_field must be a declared field for {tenancy} tenancy")
    elif owner_field is not None and not _is_non_empty_string(owner_field):
        raise DataContractLoadError(f"{path}.owner_field must be a document field identifier or null")
    elif owner_field is not None and "fields" in collection and owner_field not in names:
        raise DataContractLoadError(
            f"{path}.owner_field must be one of its declared fields {names} for {tenancy} tenancy"
        )
    elif owner_field is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(owner_field)):
        raise DataContractLoadError(f"{path}.owner_field must be a document field identifier")
    elif tenancy in {"per_user", "per_workspace"} and owner_field in {"app_id", "user_id", "tenant_id", "workspace_id"}:
        expected = {"per_user": "user_id", "per_workspace": "workspace_id"}[tenancy]
        if owner_field != expected:
            raise DataContractLoadError(f"{path}.owner_field {owner_field!r} conflicts with {tenancy}; use {expected!r} or a custom field")
    if required and ("scope_field" in collection or "entity_name" in collection):
        raise DataContractLoadError(f"{path} uses obsolete scope_field/entity_name; declare tenancy, owner_field, and entity")


def has_complete_collection_ownership(collection: dict[str, Any], path: str) -> bool:
    """Only complete explicit ownership declarations authorize code generation."""
    validate_collection_ownership(collection, path, required=False)
    if not _OWNERSHIP_FIELDS.issubset(collection):
        return False
    validate_collection_ownership(collection, path)
    return True


def load_data_contract(app_root: Path) -> DataContract | None:
    """Load the app data contract as runtime metadata only.

    Loads from the canonical path ``data/contract.json``. Does not apply
    migrations, create indexes, or connect to Mongo.
    """

    root = Path(app_root)
    contract_path = root / "data" / "contract.json"
    if not contract_path.exists():
        return None

    try:
        raw = json.loads(contract_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DataContractLoadError(f"Failed to read data/contract.json: {exc}") from exc

    contract = _require_object(raw, "data_contract")
    _validate_data_contract(contract)
    return contract


def iter_data_contract_collections(
    contract: DataContract | None,
    *, require_complete_ownership: bool = True,
) -> Iterator[tuple[str, str, dict[str, Any]]]:
    """Yield generation-eligible collections, or runtime metadata when requested."""
    if contract is None:
        return
    groups: list[tuple[str, str, str, list[Any]]] = []
    kinds: dict[str, str] = {}
    for index, surface in enumerate(_require_list(contract.get("surfaces", []), "data_contract.surfaces")):
        path = f"data_contract.surfaces[{index}]"
        surface = _require_object(surface, path)
        for field in ("surface_id", "surface_kind"):
            if not _is_non_empty_string(surface.get(field)):
                raise DataContractLoadError(f"{path}.{field} is required")
        surface_id, surface_kind = surface["surface_id"], surface["surface_kind"]
        if surface_id in kinds and kinds[surface_id] != surface_kind:
            raise DataContractLoadError(f"{path}.surface_kind conflicts with owner {surface_id!r}")
        kinds[surface_id] = surface_kind
        groups.append((surface_id, surface_kind, f"{path}.collections", _require_list(surface.get("collections"), f"{path}.collections")))
    groups.append(("", "", "data_contract.shared_collections", _require_list(
        contract.get("shared_collections", []), "data_contract.shared_collections",
    )))
    entities: set[tuple[str, str]] = set()
    names: set[tuple[str, str]] = set()
    for group_id, group_kind, group_path, collections in groups:
        for index, value in enumerate(collections):
            path = f"{group_path}[{index}]"
            collection = _require_object(value, path)
            if not group_id and is_shared_collection_reference(collection):
                continue
            if require_complete_ownership:
                if not has_complete_collection_ownership(collection, path):
                    continue
            else:
                validate_collection_ownership(collection, path, required=False)
            explicit = any(field in collection for field in ("entity", "tenancy", "owner_field"))
            ownership_value = collection.get("ownership", {})
            if ownership_value is None and not explicit:
                ownership_value = {}
            ownership = _require_object(ownership_value, f"{path}.ownership")
            owner_id = ownership.get("surface_id", group_id) if explicit else collection.get("module_id") or ownership.get("surface_id", group_id)
            owner_kind = ownership.get("surface_kind", group_kind)
            if not group_id and "ownership" not in collection:
                if collection.get("tenancy") in {"per_user", "per_workspace"} and collection.get("owner_field"):
                    raise DataContractLoadError(f"{path}.ownership is required for scoped shared collections")
                # Retain unowned shared metadata for runtime loading and strict factory validation.
                # Missing ownership cannot authorize indexing or generated actions/policies.
                if not require_complete_ownership:
                    yield "", "", collection
                continue
            if explicit and (not _is_non_empty_string(owner_id) or not _is_non_empty_string(owner_kind)):
                raise DataContractLoadError(f"{path}.ownership requires surface_id and surface_kind")
            if explicit and group_id and (owner_id, owner_kind) != (group_id, group_kind):
                raise DataContractLoadError(f"{path}.ownership must match enclosing surface {group_id!r}/{group_kind!r}")
            if explicit and owner_id in kinds and kinds[owner_id] != owner_kind:
                raise DataContractLoadError(f"{path}.ownership.surface_kind conflicts with owner {owner_id!r}")
            kinds[owner_id] = owner_kind
            if explicit and collection.get("module_id", owner_id) != owner_id:
                raise DataContractLoadError(f"{path}.module_id conflicts with declared owner {owner_id!r}")
            for field, seen in (("entity", entities), ("name", names)):
                if not require_complete_ownership and field == "entity" and field not in collection:
                    continue
                if not explicit and owner_kind != "module":
                    continue
                if not _is_non_empty_string(collection.get(field)):
                    raise DataContractLoadError(f"{path}.{field} is required")
                key = (str(owner_id), str(collection[field]))
                if explicit and key in seen:
                    raise DataContractLoadError(
                        f"{path}.{field} duplicates {owner_id}.{collection[field]}: each collection of an owner "
                        f"needs its own {field}"
                    )
                seen.add(key)
            yield str(owner_id), str(owner_kind), collection


def validate_complete_data_contract_ownership(contract: DataContract) -> None:
    """Factory artifacts require complete row metadata and a resolved collection owner."""
    if contract.get("entities"):
        raise DataContractLoadError("Factory data_contract requires explicit collection ownership under surfaces")
    for owner_id, owner_kind, collection in iter_data_contract_collections(contract, require_complete_ownership=False):
        path = f"data_contract collection {collection.get('name', '')!r}"
        validate_collection_ownership(collection, path)
        if not owner_id or not owner_kind:
            raise DataContractLoadError(f"{path}.ownership requires surface_id and surface_kind")
    resolve_shared_collection_references(contract)


def index_data_contract_by_entity(contract: DataContract | None) -> DataEntityIndex:
    """Index runtime metadata; absent ownership metadata retains physical-name keys."""
    if contract is None:
        return {}
    index: DataEntityIndex = {}

    def add(key: tuple[str, str], collection: dict[str, Any]) -> None:
        previous = index.get(key)
        if previous is not None and any(
            row.get("tenancy") in {"per_user", "per_workspace"} and row.get("owner_field")
            for row in (previous, collection)
        ):
            raise DataContractLoadError(f"Duplicate collection identity {key!r} cannot replace ownership metadata")
        index[key] = collection

    for ordinal, value in enumerate(_require_list(contract.get("entities", []), "data_contract.entities")):
        path = f"data_contract.entities[{ordinal}]"
        entity = _require_object(value, path)
        module_id = str(entity.get("module_id") or "").strip()
        entity_name = str(entity.get("entity_name") or entity.get("name") or "").strip()
        if not module_id or not entity_name:
            raise DataContractLoadError(f"{path} requires module_id and entity_name")
        add((module_id, entity_name), entity)
    for owner_id, _owner_kind, collection in iter_data_contract_collections(contract, require_complete_ownership=False):
        name = collection.get("entity") or collection.get("entity_name") or collection.get("name")
        if owner_id and name:
            add((owner_id, str(name)), collection)
    return index


def _validate_data_contract(contract: DataContract) -> None:
    if not _is_non_empty_string(contract.get("version")):
        raise DataContractLoadError("data_contract.version is required")
    _require_list(contract.get("surfaces"), "data_contract.surfaces")
    index_data_contract_by_entity(contract)
    from .platform_modules import validate_shared_collection_access

    validate_shared_collection_access(contract)


__all__ = [
    "DataContract",
    "DataContractLoadError",
    "DataEntityIndex",
    "has_complete_collection_ownership",
    "index_data_contract_by_entity",
    "iter_data_contract_collections",
    "is_shared_collection_reference",
    "resolve_shared_collection_references",
    "shared_reference_content",
    "storage_metadata_content",
    "validate_collection_ownership",
    "validate_complete_data_contract_ownership",
    "load_data_contract",
]
