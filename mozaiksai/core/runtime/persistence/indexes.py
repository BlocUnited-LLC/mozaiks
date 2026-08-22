from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from .app_data import (
    AppData,
    app_data_from_context,
    collection_name_for_alias,
    load_app_data_contract,
)
from .intent_loader import DataContract
from .mongo import MongoPersistenceContext


class DatabaseIndexApplyError(ValueError):
    """Raised when data contract index metadata cannot be applied."""


_IGNORED_INDEX_OPTIONS = {"background"}
_SUPPORTED_INDEX_OPTIONS = frozenset(
    {
        "collation",
        "expireAfterSeconds",
        "hidden",
        "partialFilterExpression",
        "sparse",
        "unique",
        "wildcardProjection",
    }
)
_BOOLEAN_INDEX_OPTIONS = frozenset({"hidden", "sparse", "unique"})
_COLLATION_REVERSE_SECONDARY_OPTION = "back" + "wards"
_COLLATION_DEFAULTS: dict[str, Any] = {
    "alternate": "non-ignorable",
    _COLLATION_REVERSE_SECONDARY_OPTION: False,
    "caseFirst": "off",
    "caseLevel": False,
    "maxVariable": "punct",
    "normalization": False,
    "numericOrdering": False,
    "strength": 3,
}


@dataclass
class DataContractIndexPlanItem:
    surface_id: str
    alias: str
    collection_name: str
    index_name: str | None
    keys: list[tuple[str, int]] | None
    options: dict[str, Any] = field(default_factory=dict)
    action: str = "skipped"
    reason: str | None = None


@dataclass
class DataContractIndexRunResult:
    items: list[DataContractIndexPlanItem]
    planned: int
    created: int
    skipped: int
    conflicts: int
    verified: int
    dry_run: bool
    success: bool


@dataclass
class _NormalizedIndexSpec:
    name: str
    keys: list[tuple[str, int]]
    options: dict[str, Any]


@dataclass
class _IndexedCollection:
    surface_id: str
    module_id: str
    entity_name: str
    alias: str
    collection_name: str
    indexes: list[_NormalizedIndexSpec]


@dataclass(frozen=True)
class _IndexInspection:
    spec: _NormalizedIndexSpec
    action: str
    reason: str
    materialized_name: str | None = None
    mismatch: str | None = None


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _normalize_index_keys(raw_keys: Any, path: str) -> list[tuple[str, int]]:
    if not isinstance(raw_keys, list) or not raw_keys:
        raise DatabaseIndexApplyError(f"{path}.keys must be a non-empty list")

    normalized: list[tuple[str, int]] = []
    for index, item in enumerate(raw_keys):
        item_path = f"{path}.keys[{index}]"
        if isinstance(item, dict):
            f = item.get("field")
            order = item.get("order", 1)
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            f, order = item
        else:
            raise DatabaseIndexApplyError(f"{item_path} must be an object or [field, order] pair")

        if not _is_non_empty_string(f):
            raise DatabaseIndexApplyError(f"{item_path}.field is required")
        try:
            order_int = int(order)
        except Exception as exc:
            raise DatabaseIndexApplyError(f"{item_path}.order must be an integer") from exc
        if order_int not in {-1, 1}:
            raise DatabaseIndexApplyError(f"{item_path}.order must be 1 or -1")
        normalized.append((str(f).strip(), order_int))

    return normalized


def _normalize_index_spec(raw_spec: Any, path: str) -> _NormalizedIndexSpec:
    if not isinstance(raw_spec, dict):
        raise DatabaseIndexApplyError(f"{path} must be an object")
    name = str(raw_spec.get("name") or "").strip()
    if not name:
        raise DatabaseIndexApplyError(f"{path}.name is required")
    keys = _normalize_index_keys(raw_spec.get("keys"), path)
    options: dict[str, Any] = {}
    for key, value in raw_spec.items():
        if key in {"name", "keys"} or key in _IGNORED_INDEX_OPTIONS:
            continue
        if str(key).startswith("_") or value is None:
            continue
        if key not in _SUPPORTED_INDEX_OPTIONS:
            raise DatabaseIndexApplyError(f"{path}.{key} is not a supported canonical index option")
        if key in _BOOLEAN_INDEX_OPTIONS:
            if not isinstance(value, bool):
                raise DatabaseIndexApplyError(f"{path}.{key} must be a boolean")
        elif key == "expireAfterSeconds":
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise DatabaseIndexApplyError(f"{path}.expireAfterSeconds must be a non-negative integer")
        elif key in {"collation", "partialFilterExpression", "wildcardProjection"}:
            if not isinstance(value, Mapping):
                raise DatabaseIndexApplyError(f"{path}.{key} must be an object")
            if key == "collation" and not _is_non_empty_string(value.get("locale")):
                raise DatabaseIndexApplyError(f"{path}.collation.locale is required")
            value = dict(value)
        options[key] = value
    return _NormalizedIndexSpec(name=name, keys=keys, options=options)


def _iter_indexed_collections(contract: DataContract) -> list[_IndexedCollection]:
    indexed: list[_IndexedCollection] = []
    surfaces = contract.get("surfaces") or []
    for surface_index, surface in enumerate(surfaces):
        surface_path = f"data_contract.surfaces[{surface_index}]"
        if not isinstance(surface, dict):
            raise DatabaseIndexApplyError(f"{surface_path} must be an object")
        surface_id = str(surface.get("surface_id") or "").strip()
        surface_kind = str(surface.get("surface_kind") or "").strip()
        collections = surface.get("collections") or []
        for collection_index, collection in enumerate(collections):
            collection_path = f"{surface_path}.collections[{collection_index}]"
            if not isinstance(collection, dict):
                raise DatabaseIndexApplyError(f"{collection_path} must be an object")
            indexes = collection.get("indexes") or []
            if not indexes:
                continue

            ownership = collection.get("ownership") if isinstance(collection.get("ownership"), dict) else {}
            module_id = str(collection.get("module_id") or ownership.get("surface_id") or surface_id).strip()  # type: ignore[union-attr]
            entity_name = str(collection.get("entity_name") or collection.get("name") or "").strip()
            if surface_kind == "module" and not module_id:
                raise DatabaseIndexApplyError(f"{collection_path}.module_id is required")
            if surface_kind == "module" and not entity_name:
                raise DatabaseIndexApplyError(f"{collection_path}.entity_name is required")
            if not isinstance(indexes, list):
                raise DatabaseIndexApplyError(f"{collection_path}.indexes must be a list")
            collection_name = str(collection.get("mongo_collection") or collection.get("collection") or "").strip()
            alias = str(collection.get("data_alias") or "").strip()
            indexed.append(
                _IndexedCollection(
                    surface_id=surface_id,
                    module_id=module_id,
                    entity_name=entity_name,
                    alias=alias,
                    collection_name=collection_name,
                    indexes=[
                        _normalize_index_spec(
                            index_spec,
                            f"{collection_path}.indexes[{index}]",
                        )
                        for index, index_spec in enumerate(indexes)
                    ],
                )
            )
    return indexed


def _index_spec_dict(spec: _NormalizedIndexSpec) -> dict[str, Any]:
    return {"name": spec.name, "keys": list(spec.keys), **dict(spec.options)}


def _canonical_document(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_document(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_document(item) for item in value]
    return value


def _canonical_collation(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    locale = str(value.get("locale") or "").strip()
    if not locale or locale == "simple":
        return None
    normalized = {**_COLLATION_DEFAULTS, **dict(value), "locale": locale}
    normalized.pop("version", None)
    return cast(dict[str, Any], _canonical_document(normalized))


def _canonical_option_value(options: Mapping[str, Any], key: str) -> Any:
    if key in _BOOLEAN_INDEX_OPTIONS:
        return bool(options.get(key, False))
    if key == "collation":
        return _canonical_collation(options.get(key))
    value = options.get(key)
    if value is None:
        return None
    return _canonical_document(value)


def _index_mismatches(index_doc: Mapping[str, Any], spec: _NormalizedIndexSpec) -> list[str]:
    mismatches: list[str] = []
    actual_name = str(index_doc.get("name") or "")
    if actual_name != spec.name:
        mismatches.append(f"name expected={spec.name!r} actual={actual_name!r}")
    try:
        actual_keys = _normalize_existing_keys(index_doc.get("key", {}))
    except DatabaseIndexApplyError as exc:
        return [f"keys unreadable ({exc})"]
    if actual_keys != spec.keys:
        mismatches.append(f"keys expected={spec.keys!r} actual={actual_keys!r}")
    for key in sorted(_SUPPORTED_INDEX_OPTIONS):
        expected = _canonical_option_value(spec.options, key)
        actual = _canonical_option_value(index_doc, key)
        if actual != expected:
            mismatches.append(f"{key} expected={expected!r} actual={actual!r}")
    return mismatches


def _index_matches_spec(index_doc: Mapping[str, Any], spec: _NormalizedIndexSpec) -> bool:
    return not _index_mismatches(index_doc, spec)


async def _read_materialized_indexes(collection: Any) -> list[dict[str, Any]]:
    list_indexes = getattr(collection, "list_indexes", None)
    if not callable(list_indexes):
        raise DatabaseIndexApplyError("collection does not support materialized index inspection")
    try:
        rows = await list_indexes().to_list(length=None)
    except Exception as exc:
        raise DatabaseIndexApplyError(f"could not read materialized indexes: {exc}") from exc
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _inspect_index(existing_docs: Sequence[Mapping[str, Any]], spec: _NormalizedIndexSpec) -> _IndexInspection:
    named_match = next((row for row in existing_docs if str(row.get("name") or "") == spec.name), None)
    if named_match is not None:
        mismatch = "; ".join(_index_mismatches(named_match, spec))
        if mismatch:
            return _IndexInspection(
                spec=spec,
                action="conflict",
                reason="requested index name exists with an incompatible definition",
                materialized_name=spec.name,
                mismatch=mismatch,
            )
        for row in existing_docs:
            if row is named_match:
                continue
            try:
                row_has_same_keys = _normalize_existing_keys(row.get("key", {})) == spec.keys
            except DatabaseIndexApplyError:
                continue
            if row_has_same_keys and not _index_matches_spec(row, spec):
                return _IndexInspection(
                    spec=spec,
                    action="conflict",
                    reason="equivalent key pattern exists under another name with incompatible options",
                    materialized_name=str(row.get("name") or "") or None,
                    mismatch="; ".join(_index_mismatches(row, spec)),
                )
        return _IndexInspection(
            spec=spec,
            action="exists",
            reason="matching index already exists",
            materialized_name=spec.name,
        )

    same_keys: list[Mapping[str, Any]] = []
    for row in existing_docs:
        try:
            if _normalize_existing_keys(row.get("key", {})) == spec.keys:
                same_keys.append(row)
        except DatabaseIndexApplyError:
            continue
    if same_keys:
        row = same_keys[0]
        return _IndexInspection(
            spec=spec,
            action="conflict",
            reason="equivalent key pattern exists under another name",
            materialized_name=str(row.get("name") or "") or None,
            mismatch="; ".join(_index_mismatches(row, spec)),
        )
    return _IndexInspection(spec=spec, action="create", reason="missing index")


def _conflict_error(collection_label: str, inspection: _IndexInspection) -> DatabaseIndexApplyError:
    materialized = inspection.materialized_name or "<unnamed>"
    return DatabaseIndexApplyError(
        f"Index readiness conflict for collection={collection_label!r} "
        f"declared_name={inspection.spec.name!r} materialized_name={materialized!r}: "
        f"{inspection.reason}; {inspection.mismatch or 'definition mismatch'}"
    )


def _validate_declared_index_set(
    specs: Sequence[_NormalizedIndexSpec],
    *,
    collection_label: str,
) -> None:
    for index, spec in enumerate(specs):
        for prior in specs[:index]:
            prior_doc = {"name": prior.name, "key": dict(prior.keys), **prior.options}
            same_name = prior.name == spec.name
            same_keys = prior.keys == spec.keys
            if not same_name and not same_keys:
                continue
            mismatch = "; ".join(_index_mismatches(prior_doc, spec))
            if mismatch:
                relationship = "name" if same_name else "ordered key pattern"
                raise DatabaseIndexApplyError(
                    f"Conflicting declared indexes for collection={collection_label!r}: "
                    f"{relationship} is reused by {prior.name!r} and {spec.name!r}; {mismatch}"
                )


async def _ensure_raw_collection_indexes(
    collection: Any,
    indexes: Sequence[dict[str, Any]],
    *,
    collection_label: str = "collection",
) -> list[_IndexInspection]:
    specs = [_normalize_index_spec(dict(spec), f"indexes[{index}]") for index, spec in enumerate(indexes)]
    _validate_declared_index_set(specs, collection_label=collection_label)
    existing_docs = await _read_materialized_indexes(collection)
    inspections = [_inspect_index(existing_docs, spec) for spec in specs]
    conflict = next((item for item in inspections if item.action == "conflict"), None)
    if conflict is not None:
        raise _conflict_error(collection_label, conflict)

    create_index = getattr(collection, "create_index", None)
    if not callable(create_index):
        if any(item.action == "create" for item in inspections):
            raise DatabaseIndexApplyError("collection does not support index creation")
        return inspections

    verified: list[_IndexInspection] = []
    for prior in inspections:
        current_docs = await _read_materialized_indexes(collection)
        current = _inspect_index(current_docs, prior.spec)
        if current.action == "conflict":
            raise _conflict_error(collection_label, current)
        if current.action == "create":
            spec = prior.spec
            try:
                await create_index(spec.keys, name=spec.name, **spec.options)
            except Exception as exc:
                raise DatabaseIndexApplyError(
                    f"Could not create index collection={collection_label!r} name={spec.name!r}: {exc}"
                ) from exc
        materialized_docs = await _read_materialized_indexes(collection)
        post = _inspect_index(materialized_docs, prior.spec)
        if post.action == "conflict":
            raise _conflict_error(collection_label, post)
        if post.action == "create":
            raise DatabaseIndexApplyError(
                f"Index verification failed for collection={collection_label!r} "
                f"name={prior.spec.name!r}: materialized index is missing after creation"
            )
        verified.append(
            _IndexInspection(
                spec=prior.spec,
                action="created" if current.action == "create" else "exists",
                reason="created and materialized definition verified" if current.action == "create" else post.reason,
                materialized_name=post.materialized_name,
            )
        )
    return verified


async def _ensure_collection_indexes(
    collection: Any,
    indexes: Sequence[dict[str, Any]],
    *,
    collection_label: str = "collection",
) -> list[_IndexInspection]:
    if callable(getattr(collection, "list_indexes", None)):
        return await _ensure_raw_collection_indexes(collection, indexes, collection_label=collection_label)
    ensure_indexes = getattr(collection, "ensure_indexes", None)
    if not callable(ensure_indexes):
        raise DatabaseIndexApplyError("collection does not support verified index materialization")
    result = await ensure_indexes(indexes)
    if not isinstance(result, list) or not all(isinstance(item, _IndexInspection) for item in result):
        raise DatabaseIndexApplyError("collection index adapter did not return verified materialization results")
    return result


def _normalize_existing_keys(raw_keys: Any) -> list[tuple[str, int]]:
    if isinstance(raw_keys, Mapping):
        items = list(raw_keys.items())
    elif isinstance(raw_keys, list):
        items = []
        for item in raw_keys:
            if isinstance(item, Mapping):
                items.append((item.get("field"), item.get("order", 1)))
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                items.append((item[0], item[1]))
            else:
                raise DatabaseIndexApplyError(
                    f"existing index key entry must be an object or [field, order] pair: {item!r}"
                )
    else:
        raise DatabaseIndexApplyError(
            f"existing index key specification must be a dict or list, got {type(raw_keys).__name__}"
        )

    normalized: list[tuple[str, int]] = []
    for f, order in items:
        if not _is_non_empty_string(f):
            raise DatabaseIndexApplyError("existing index field must be non-empty")
        order_int = int(order)
        if order_int not in {-1, 1}:
            raise DatabaseIndexApplyError("existing index order must be 1 or -1")
        normalized.append((str(f).strip(), order_int))
    return normalized


def _shared_entry_matches_module(entry: dict[str, Any], module_id: str | None) -> bool:
    if module_id is None:
        return True
    if entry.get("owner_module") == module_id:
        return True
    if any(shared.get("module") == module_id for shared in entry.get("shared_with", [])):
        return True
    return any(reader.get("module") == module_id for reader in entry.get("read_by", []))


async def apply_database_indexes(
    contract: DataContract | None,
    *,
    app_id: str | None = None,
    persistence: MongoPersistenceContext | None = None,
) -> DataContractIndexRunResult:
    """Ensure and verify indexes declared in the app data contract."""

    if contract is None:
        return DataContractIndexRunResult(
            items=[], planned=0, created=0, skipped=0, conflicts=0,
            verified=0, dry_run=False, success=True,
        )

    resolved_app_id = str(app_id or contract.get("app_id") or "").strip()
    if not resolved_app_id:
        raise DatabaseIndexApplyError("app_id is required to apply database indexes")

    context = persistence or MongoPersistenceContext(app_id=resolved_app_id)
    items: list[DataContractIndexPlanItem] = []
    for indexed_collection in _iter_indexed_collections(contract):
        if not indexed_collection.indexes:
            continue
        if indexed_collection.collection_name:
            collection = context.literal_collection(indexed_collection.collection_name)  # type: ignore[attr-defined]
        else:
            collection = context.collection(indexed_collection.module_id, indexed_collection.entity_name)
        collection_label = indexed_collection.collection_name or f"{indexed_collection.module_id}.{indexed_collection.entity_name}"
        outcomes = await _ensure_collection_indexes(
            collection,
            [_index_spec_dict(spec) for spec in indexed_collection.indexes],
            collection_label=collection_label,
        )
        for outcome in outcomes:
            items.append(
                DataContractIndexPlanItem(
                    surface_id=indexed_collection.surface_id,
                    alias=indexed_collection.alias,
                    collection_name=collection_label,
                    index_name=outcome.spec.name,
                    keys=list(outcome.spec.keys),
                    options=dict(outcome.spec.options),
                    action=outcome.action,
                    reason=outcome.reason,
                )
            )
    created = sum(1 for item in items if item.action == "created")
    return DataContractIndexRunResult(
        items=items,
        planned=created,
        created=created,
        skipped=sum(1 for item in items if item.action == "exists"),
        conflicts=0,
        verified=len(items),
        dry_run=False,
        success=True,
    )


def _default_app_data() -> AppData:
    return app_data_from_context(object())


def _build_skip_items(
    contract: dict[str, Any],
    *,
    surfaced_collection_names: set[str],
    module_id: str | None,
) -> list[DataContractIndexPlanItem]:
    skipped: list[DataContractIndexPlanItem] = []

    for surface in contract.get("surfaces", []):
        if not isinstance(surface, dict):
            continue
        surface_id = str(surface.get("surface_id") or "").strip()
        if module_id is not None and surface_id != module_id:
            continue
        for collection in surface.get("collections", []):
            if not isinstance(collection, dict):
                continue
            if collection.get("indexes"):
                continue
            alias = str(collection.get("data_alias") or "").strip()
            collection_name = str(
                collection.get("mongo_collection")
                or collection.get("collection")
                or ""
            ).strip()
            if not alias and not collection_name:
                continue
            skipped.append(
                DataContractIndexPlanItem(
                    surface_id=surface_id,
                    alias=alias,
                    collection_name=collection_name,
                    index_name=None,
                    keys=None,
                    action="skipped",
                    reason="surfaced collection has no index specs",
                )
            )

    for entry in contract.get("shared_collections", []):
        if not isinstance(entry, dict):
            continue
        if not _shared_entry_matches_module(entry, module_id):
            continue

        collection_name = str(entry.get("mongo_collection") or entry.get("collection") or "").strip()
        if collection_name in surfaced_collection_names:
            continue

        alias = str(entry.get("primary_alias") or "").strip()
        skipped.append(
            DataContractIndexPlanItem(
                surface_id=str(entry.get("owner_module") or "shared_collections").strip(),
                alias=alias,
                collection_name=collection_name,
                index_name=None,
                keys=None,
                action="skipped",
                reason="shared collection is not surfaced with explicit index specs",
            )
        )

    for entry in contract.get("documented_alias_exclusions", []):
        if not isinstance(entry, dict):
            continue
        owner_module = str(entry.get("module") or "").strip()
        if module_id is not None and owner_module != module_id:
            continue
        reason = str(entry.get("reason") or "documented alias exclusion").strip()
        for alias in entry.get("aliases", []):
            alias_str = str(alias)
            try:
                resolved_name = collection_name_for_alias(alias_str, contract=contract)
                alias_reason = reason
            except KeyError:
                resolved_name = ""
                alias_reason = f"{reason}; alias is not declared in data_contract.aliases"
            skipped.append(
                DataContractIndexPlanItem(
                    surface_id=owner_module,
                    alias=alias_str,
                    collection_name=resolved_name,
                    index_name=None,
                    keys=None,
                    action="skipped",
                    reason=alias_reason,
                )
            )
    return skipped


def _summarize(
    items: list[DataContractIndexPlanItem],
    *,
    created: int,
    dry_run: bool,
) -> DataContractIndexRunResult:
    planned = sum(1 for item in items if item.action == "create")
    skipped = sum(1 for item in items if item.action in {"exists", "skipped"})
    conflicts = sum(1 for item in items if item.action == "conflict")
    verified = sum(1 for item in items if item.action in {"created", "exists"})
    return DataContractIndexRunResult(
        items=items,
        planned=planned,
        created=created,
        skipped=skipped,
        conflicts=conflicts,
        verified=verified,
        dry_run=dry_run,
        success=conflicts == 0,
    )


async def run_data_index_application(
    *,
    dry_run: bool = True,
    module_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    persistence: Any | None = None,
) -> DataContractIndexRunResult:
    contract = metadata or load_app_data_contract()
    indexed_collections = _iter_indexed_collections(contract)
    if module_id is not None:
        indexed_collections = [collection for collection in indexed_collections if collection.surface_id == module_id]
    surfaced_collection_names = {
        collection.collection_name for collection in indexed_collections if collection.collection_name
    }
    items = _build_skip_items(
        contract,
        surfaced_collection_names=surfaced_collection_names,
        module_id=module_id,
    )
    if not indexed_collections:
        return _summarize(items, created=0, dry_run=dry_run)

    resolved_persistence = persistence or _default_app_data()
    created = 0
    for indexed_collection in indexed_collections:
        if indexed_collection.alias:
            collection = resolved_persistence.collection(indexed_collection.alias)
        elif indexed_collection.collection_name and hasattr(resolved_persistence, "literal_collection"):
            collection = resolved_persistence.literal_collection(indexed_collection.collection_name)
        elif indexed_collection.collection_name:
            collection = resolved_persistence.collection(indexed_collection.collection_name)
        else:
            raise DatabaseIndexApplyError(
                f"surface {indexed_collection.surface_id}/{indexed_collection.entity_name} has no data_alias or mongo_collection"
            )
        collection_label = indexed_collection.collection_name or indexed_collection.alias or f"{indexed_collection.module_id}.{indexed_collection.entity_name}"
        if dry_run:
            existing_docs = await _read_materialized_indexes(collection)
            outcomes = [_inspect_index(existing_docs, spec) for spec in indexed_collection.indexes]
        else:
            outcomes = await _ensure_collection_indexes(
                collection,
                [_index_spec_dict(spec) for spec in indexed_collection.indexes],
                collection_label=collection_label,
            )
        for outcome in outcomes:
            if outcome.action == "created":
                created += 1
            reason = outcome.reason
            if outcome.mismatch:
                reason = f"{reason}; {outcome.mismatch}"
            items.append(
                DataContractIndexPlanItem(
                    surface_id=indexed_collection.surface_id,
                    alias=indexed_collection.alias,
                    collection_name=collection_label,
                    index_name=outcome.spec.name,
                    keys=list(outcome.spec.keys),
                    options=dict(outcome.spec.options),
                    action=outcome.action,
                    reason=reason,
                )
            )
    return _summarize(items, created=created, dry_run=dry_run)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan or apply indexes declared in app/data/contract.json."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        dest="apply",
        action="store_false",
        default=False,
        help="Plan app data index creation without creating indexes (default).",
    )
    mode.add_argument(
        "--apply",
        dest="apply",
        action="store_true",
        help="Create missing app data indexes for surfaced collections.",
    )
    parser.add_argument("--module", dest="module_id", help="Limit to one surface_id.")
    return parser


def _print_result(result: DataContractIndexRunResult) -> None:
    for item in result.items:
        index_label = item.index_name or "-"
        suffix = f" ({item.reason})" if item.reason else ""
        print(
            f"[{item.action}] {item.surface_id} {item.alias or '-'} {index_label} -> {item.collection_name or '-'}{suffix}"
        )
    print(
        "Summary: "
        f"planned={result.planned} "
        f"created={result.created} "
        f"verified={result.verified} "
        f"skipped={result.skipped} "
        f"conflicts={result.conflicts} "
        f"dry_run={result.dry_run}"
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    metadata: dict[str, Any] | None = None,
    persistence: Any | None = None,
) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(
            run_data_index_application(
                dry_run=not args.apply,
                module_id=args.module_id,
                metadata=metadata,
                persistence=persistence,
            )
        )
    except DatabaseIndexApplyError as exc:
        print(f"Index readiness failed: {exc}")
        return 1
    _print_result(result)
    return 0 if result.success else 1


__all__ = [
    "DataContractIndexPlanItem",
    "DataContractIndexRunResult",
    "DatabaseIndexApplyError",
    "apply_database_indexes",
    "main",
    "run_data_index_application",
]
