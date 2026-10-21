"""Resolve declared collection ownership and validate bounded Mongo operations."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .adapter import PersistencePrincipal, PersistenceScopeError
from .intent_loader import (
    DataContract,
    DataContractLoadError,
    index_data_contract_by_entity,
    validate_collection_ownership,
)
from .naming import collection_name_for


@dataclass(frozen=True)
class CollectionOwnership:
    tenancy: str
    owner_field: str

    def scope(self, principal: PersistencePrincipal | None) -> dict[str, str]:
        if principal is None or not isinstance(principal.user_id, str) or not principal.user_id.strip():
            raise PersistenceScopeError("Owned collections require an authenticated persistence principal")
        identity = principal.user_id if self.tenancy == "per_user" else principal.workspace_id
        if not isinstance(identity, str) or not identity.strip():
            raise PersistenceScopeError(f"{self.tenancy} collection requires authenticated workspace identity")
        return {self.owner_field: identity}


def collection_ownership(
    contract: DataContract | None, *, app_id: str, app_slug: str | None,
) -> dict[str, CollectionOwnership]:
    """Index by actual storage name, including naming normalization collisions."""
    by_name: dict[str, CollectionOwnership | None] = {}
    for (module_id, entity), collection in index_data_contract_by_entity(contract).items():
        validate_collection_ownership(collection, f"data_contract {module_id}.{entity}", required=False)
        name = str(collection.get("name") or entity)
        tenancy, owner_field = collection.get("tenancy"), collection.get("owner_field")
        policy = (
            CollectionOwnership(tenancy, owner_field)
            if tenancy in {"per_user", "per_workspace"} and owner_field
            else None
        )
        names = {collection_name_for(app_id=app_id, app_slug=app_slug, module_id=module_id, entity_name=name)}
        literal = collection.get("mongo_collection") or collection.get("collection")
        if literal:
            names.add(str(literal))
        for physical_name in names:
            if physical_name in by_name and by_name[physical_name] != policy:
                raise DataContractLoadError(f"Conflicting ownership for physical collection {physical_name!r}")
            by_name[physical_name] = policy
    return {name: policy for name, policy in by_name.items() if policy is not None}


def collection_bindings(contract: DataContract) -> dict[tuple[str, str], str]:
    """Resolve a module's declared collection name or entity to its storage name."""
    bindings: dict[tuple[str, str], str] = {}
    for (module_id, entity), collection in index_data_contract_by_entity(contract).items():
        name = str(collection.get("name") or entity)
        for reference in {name, entity}:
            key = (module_id, reference)
            if key in bindings and bindings[key] != name:
                raise DataContractLoadError(f"Ambiguous collection reference {module_id}.{reference}")
            bindings[key] = name
    return bindings


_READ_STAGES = frozenset({
    "$match", "$project", "$group", "$sort", "$limit", "$skip", "$unwind",
    "$addFields", "$set", "$unset", "$replaceRoot", "$replaceWith", "$count",
    "$bucket", "$bucketAuto", "$sortByCount", "$sample", "$facet", "$setWindowFields",
})


def _plain_copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        document: dict[str, Any] = {}
        for key, item in list(value.items()):
            if not isinstance(key, str):
                raise PersistenceScopeError("Aggregation documents require string keys")
            # A str subclass can compare and hash as one name while its encoded
            # content is another; keep only the content.
            document[str.__str__(key)] = _plain_copy(item)
        return document
    if isinstance(value, (list, tuple)):
        return [_plain_copy(item) for item in value]
    return value


def canonical_pipeline(pipeline: Iterable[Any]) -> list[dict[str, Any]]:
    """Read a caller's pipeline exactly once into plain lists and dicts.

    The validator and the driver must see the same content. A caller's mapping
    or sequence can answer differently each time it is read, and a lazy cursor
    reads it again long after validation, so every mapping becomes a ``dict``
    and every list or tuple a ``list`` in a single pass. Validate the returned
    copy and send that same copy. Any other value is passed through untouched,
    which keeps ObjectId, datetime, Decimal128, Regex, Binary and key order as
    the caller gave them.
    """
    stages: list[dict[str, Any]] = []
    for stage in list(pipeline):
        if not isinstance(stage, Mapping):
            raise PersistenceScopeError("Aggregation stages must be documents")
        stages.append(_plain_copy(stage))
    return stages


def validate_owned_pipeline(pipeline: Sequence[Mapping[str, Any]]) -> None:
    """Allow transformations of scoped input only, including nested facets.

    Collection sources, sinks and unknown stages fail closed. In particular, a
    leading owner match cannot make lookup/union/graph traversal or writes safe.
    """
    for stage in pipeline:
        if not isinstance(stage, Mapping) or len(stage) != 1:
            raise PersistenceScopeError("Owned aggregation requires single-operator stages")
        operator, value = next(iter(stage.items()))
        if operator not in _READ_STAGES:
            raise PersistenceScopeError(f"Aggregation stage {operator!r} is not allowed by collection ownership")
        if operator == "$facet":
            if not isinstance(value, Mapping):
                raise PersistenceScopeError("Owned aggregation facets must be pipelines")
            for nested in value.values():
                if not isinstance(nested, (list, tuple)):
                    raise PersistenceScopeError("Owned aggregation facets must be pipelines")
                validate_owned_pipeline(nested)


_UPDATE_OPERATORS = frozenset({
    "$set", "$setOnInsert", "$unset", "$inc", "$mul", "$min", "$max", "$rename",
    "$currentDate", "$addToSet", "$pop", "$pull", "$push", "$pullAll", "$bit",
})


def owned_update(
    update: Mapping[str, Any], *, scope: Mapping[str, str], upsert: bool,
) -> dict[str, Any]:
    """Make ownership immutable and stamp every possible upsert insertion."""
    if not isinstance(update, Mapping) or not update:
        raise PersistenceScopeError("Owned updates require Mongo update operators")
    result: dict[str, Any] = {}
    for operator, changes in update.items():
        if operator not in _UPDATE_OPERATORS or not isinstance(changes, Mapping):
            raise PersistenceScopeError("Owned updates require supported Mongo update operators")
        result[operator] = dict(changes)
        for field, value in changes.items():
            # Owner fields are top-level identifiers. Dotted paths must not
            # mutate their children; rename destinations require the same check.
            protected = str(field).split(".", 1)[0] in scope
            target_protected = operator == "$rename" and str(value).split(".", 1)[0] in scope
            if protected:
                if operator not in {"$set", "$setOnInsert"} or field not in scope or value != scope[field]:
                    raise PersistenceScopeError(f"Updates cannot change ownership field {field!r}")
            if target_protected:
                raise PersistenceScopeError("Updates cannot rename an ownership field")
    if upsert:
        inserted = result.setdefault("$setOnInsert", {})
        for field, identity in scope.items():
            if field not in result.get("$set", {}):
                inserted[field] = identity
    return result
