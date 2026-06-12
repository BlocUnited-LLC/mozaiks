from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Mapping, Sequence

from .app_data import AppData, app_data_from_context, collection_name_for_alias, load_app_data_contract
from .intent_loader import DataContract
from .mongo import MongoPersistenceContext


class DatabaseIndexApplyError(ValueError):
    """Raised when data contract index metadata cannot be applied."""


_SPEC_COMPARISON_OPTION_EXCLUSIONS = {"background"}


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
    options = {
        key: value
        for key, value in raw_spec.items()
        if key not in {"name", "keys"}
        and key not in _SPEC_COMPARISON_OPTION_EXCLUSIONS
        and value is not None
    }
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
            module_id = str(collection.get("module_id") or ownership.get("surface_id") or surface_id).strip()
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


def _existing_option_value(index_doc: dict[str, Any], key: str) -> Any:
    if key in {"unique", "sparse"}:
        return bool(index_doc.get(key, False))
    return index_doc.get(key)


def _index_matches_spec(index_doc: dict[str, Any], spec: _NormalizedIndexSpec) -> bool:
    try:
        if _normalize_existing_keys(index_doc.get("key", {})) != spec.keys:
            return False
    except DatabaseIndexApplyError:
        return False
    for key, expected in spec.options.items():
        if _existing_option_value(index_doc, key) != expected:
            return False
    return True


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
) -> int:
    """Ensure indexes declared in the app data contract exist.

    This is index-only. It does not mutate documents, apply migrations, or
    write migration history.
    """

    if contract is None:
        return 0

    resolved_app_id = str(app_id or contract.get("app_id") or "").strip()
    if not resolved_app_id:
        raise DatabaseIndexApplyError("app_id is required to apply database indexes")

    context = persistence or MongoPersistenceContext(app_id=resolved_app_id)
    applied_specs = 0
    for indexed_collection in _iter_indexed_collections(contract):
        if not indexed_collection.indexes:
            continue
        if indexed_collection.collection_name:
            collection = context.literal_collection(indexed_collection.collection_name)
        else:
            collection = context.collection(indexed_collection.module_id, indexed_collection.entity_name)
        await collection.ensure_indexes([_index_spec_dict(spec) for spec in indexed_collection.indexes])
        applied_specs += len(indexed_collection.indexes)
    return applied_specs


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
    fail_on_conflict: bool,
) -> DataContractIndexRunResult:
    planned = sum(1 for item in items if item.action == "create")
    skipped = sum(1 for item in items if item.action in {"exists", "skipped"})
    conflicts = sum(1 for item in items if item.action == "conflict")
    success = not (fail_on_conflict and conflicts > 0)
    return DataContractIndexRunResult(
        items=items,
        planned=planned,
        created=created,
        skipped=skipped,
        conflicts=conflicts,
        dry_run=dry_run,
        success=success,
    )


async def run_data_index_application(
    *,
    dry_run: bool = True,
    module_id: str | None = None,
    fail_on_conflict: bool = False,
    metadata: dict[str, Any] | None = None,
    persistence: Any | None = None,
) -> DataContractIndexRunResult:
    contract = metadata or load_app_data_contract()
    indexed_collections = _iter_indexed_collections(contract)
    if module_id is not None:
        indexed_collections = [
            collection for collection in indexed_collections if collection.surface_id == module_id
        ]
    surfaced_collection_names = {
        collection.collection_name
        for collection in indexed_collections
        if collection.collection_name
    }
    items = _build_skip_items(
        contract,
        surfaced_collection_names=surfaced_collection_names,
        module_id=module_id,
    )

    if not indexed_collections:
        return _summarize(items, created=0, dry_run=dry_run, fail_on_conflict=fail_on_conflict)

    resolved_persistence = persistence or _default_app_data()
    pending_creates: list[tuple[Any, _NormalizedIndexSpec]] = []

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
        existing_rows = await collection.list_indexes().to_list(length=None)
        existing_docs = [row for row in existing_rows if isinstance(row, dict)]
        existing_by_name = {
            str(row.get("name")): row
            for row in existing_docs
            if _is_non_empty_string(row.get("name"))
        }

        for spec in indexed_collection.indexes:
            named_match = existing_by_name.get(spec.name)
            if named_match is not None:
                if _index_matches_spec(named_match, spec):
                    action = "exists"
                    reason = "matching index already exists"
                else:
                    action = "conflict"
                    reason = "index name exists with different keys or options"
                items.append(
                    DataContractIndexPlanItem(
                        surface_id=indexed_collection.surface_id,
                        alias=indexed_collection.alias,
                        collection_name=indexed_collection.collection_name,
                        index_name=spec.name,
                        keys=list(spec.keys),
                        options=dict(spec.options),
                        action=action,
                        reason=reason,
                    )
                )
                continue

            same_shape = next((row for row in existing_docs if _index_matches_spec(row, spec)), None)
            if same_shape is not None:
                items.append(
                    DataContractIndexPlanItem(
                        surface_id=indexed_collection.surface_id,
                        alias=indexed_collection.alias,
                        collection_name=indexed_collection.collection_name,
                        index_name=spec.name,
                        keys=list(spec.keys),
                        options=dict(spec.options),
                        action="exists",
                        reason=(
                            "matching key shape already exists"
                            if not same_shape.get("name")
                            else f"matching key shape already exists as {same_shape['name']}"
                        ),
                    )
                )
                continue

            items.append(
                DataContractIndexPlanItem(
                    surface_id=indexed_collection.surface_id,
                    alias=indexed_collection.alias,
                    collection_name=indexed_collection.collection_name,
                    index_name=spec.name,
                    keys=list(spec.keys),
                    options=dict(spec.options),
                    action="create",
                    reason="missing index",
                )
            )
            pending_creates.append((collection, spec))

    conflicts = sum(1 for item in items if item.action == "conflict")
    created = 0
    if not dry_run and (conflicts == 0 or not fail_on_conflict):
        for collection, spec in pending_creates:
            await collection.create_index(spec.keys, name=spec.name, **spec.options)
            created += 1

    return _summarize(items, created=created, dry_run=dry_run, fail_on_conflict=fail_on_conflict)


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
    parser.add_argument(
        "--fail-on-conflict",
        action="store_true",
        help="Return a failure status when an incompatible existing index is detected.",
    )
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
    result = asyncio.run(
        run_data_index_application(
            dry_run=not args.apply,
            module_id=args.module_id,
            fail_on_conflict=args.fail_on_conflict,
            metadata=metadata,
            persistence=persistence,
        )
    )
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
