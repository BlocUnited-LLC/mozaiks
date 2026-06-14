from __future__ import annotations

"""App data helpers backed by the app data contract.

This module is runtime/platform infrastructure. App bundles declare aliases and
literal collection names in ``app/data/contract.json``; app bundles should not
carry Python helper code under ``app/data``.
"""

import json
import os
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.runtime.app.paths import APP_DATA_CONTRACT_PATH
from mozaiksai.core.workflow.paths import resolve_active_app_root

from .mongo import DEFAULT_APP_DATABASE_NAME, MongoPersistenceContext

DataAliasMap = Mapping[str, str]


class AppDataAliasError(KeyError):
    """Raised when an app data alias is missing from the data contract."""


def _candidate_app_roots(app_root: str | os.PathLike[str] | None = None) -> list[Path]:
    roots: list[Path] = []
    if app_root is not None:
        roots.append(Path(app_root))

    for env_name in ("PLATFORM_PATH", "MOZAIKS_APP_WORKSPACE_PATH"):
        raw = str(os.getenv(env_name) or "").strip()
        if raw:
            roots.append(Path(raw))

    try:
        active = resolve_active_app_root()
        if active.name != "__no_active_app__":
            roots.append(active)
    except Exception:
        pass

    roots.append(Path.cwd())
    roots.append(Path.cwd() / "app")

    normalized: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        candidate = root.expanduser()
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve()
        if (candidate / "app.json").exists():
            resolved = candidate.resolve()
        elif (candidate / "app" / "app.json").exists():
            resolved = (candidate / "app").resolve()
        else:
            resolved = candidate.resolve()
        if resolved not in seen:
            seen.add(resolved)
            normalized.append(resolved)
    return normalized


def default_data_contract_path(app_root: str | os.PathLike[str] | None = None) -> Path:
    for root in _candidate_app_roots(app_root):
        candidate = root / APP_DATA_CONTRACT_PATH
        if candidate.exists():
            return candidate
    return _candidate_app_roots(app_root)[0] / APP_DATA_CONTRACT_PATH


def load_app_data_contract(
    app_root: str | os.PathLike[str] | None = None,
    *,
    contract_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    path = Path(contract_path).expanduser().resolve() if contract_path else default_data_contract_path(app_root)
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def aliases_from_data_contract(contract: Mapping[str, Any] | None) -> dict[str, str]:
    """Return ``data_alias``/``alias`` to literal Mongo collection mappings."""

    if not isinstance(contract, Mapping):
        return {}

    aliases: dict[str, str] = {}

    for surface in contract.get("surfaces") or []:
        if not isinstance(surface, Mapping):
            continue
        for collection in surface.get("collections") or []:
            if not isinstance(collection, Mapping):
                continue
            alias = str(collection.get("data_alias") or "").strip()
            collection_name = str(
                collection.get("mongo_collection")
                or collection.get("collection")
                or ""
            ).strip()
            if alias and collection_name:
                aliases[alias] = collection_name

    for alias_entry in contract.get("aliases") or []:
        if not isinstance(alias_entry, Mapping):
            continue
        alias = str(alias_entry.get("alias") or alias_entry.get("data_alias") or "").strip()
        collection_name = str(
            alias_entry.get("collection")
            or alias_entry.get("mongo_collection")
            or ""
        ).strip()
        if alias and collection_name:
            aliases[alias] = collection_name

    for shared in contract.get("shared_collections") or []:
        if not isinstance(shared, Mapping):
            continue
        collection_name = str(shared.get("mongo_collection") or shared.get("collection") or "").strip()
        primary_alias = str(shared.get("primary_alias") or "").strip()
        if primary_alias and collection_name:
            aliases[primary_alias] = collection_name
        for field in ("shared_with", "read_by"):
            for entry in shared.get(field) or []:
                if not isinstance(entry, Mapping):
                    continue
                alias = str(entry.get("data_alias") or entry.get("alias") or "").strip()
                if alias and collection_name:
                    aliases[alias] = collection_name

    return aliases


class _LazyDataAliasMap(Mapping[str, str]):
    def _data(self) -> dict[str, str]:
        try:
            return aliases_from_data_contract(load_app_data_contract())
        except Exception:
            return {}

    def __getitem__(self, key: str) -> str:
        return self._data()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data())

    def __len__(self) -> int:
        return len(self._data())


DATA_COLLECTIONS: Mapping[str, str] = _LazyDataAliasMap()


def collection_name_for_alias(
    alias: str,
    *,
    aliases: DataAliasMap | None = None,
    contract: Mapping[str, Any] | None = None,
    app_root: str | os.PathLike[str] | None = None,
) -> str:
    alias_key = str(alias or "").strip()
    resolved_aliases = (
        aliases
        if aliases is not None
        else aliases_from_data_contract(contract)
        if contract is not None
        else aliases_from_data_contract(load_app_data_contract(app_root))
    )
    try:
        return resolved_aliases[alias_key]
    except KeyError as exc:
        known = ", ".join(sorted(resolved_aliases))
        raise AppDataAliasError(
            f"Unknown app data alias {alias_key!r}. Known aliases: {known}"
        ) from exc


def known_data_aliases(
    *,
    aliases: DataAliasMap | None = None,
    contract: Mapping[str, Any] | None = None,
    app_root: str | os.PathLike[str] | None = None,
) -> tuple[str, ...]:
    resolved_aliases = (
        aliases
        if aliases is not None
        else aliases_from_data_contract(contract)
        if contract is not None
        else aliases_from_data_contract(load_app_data_contract(app_root))
    )
    return tuple(sorted(resolved_aliases))


class AppData:
    """App-owned persistence for explicit data-contract aliases."""

    def __init__(
        self,
        db: Any | None = None,
        *,
        aliases: DataAliasMap | None = None,
        contract: Mapping[str, Any] | None = None,
        collection_resolver: Callable[[str], Any] | None = None,
    ) -> None:
        self.db = db
        self.aliases = dict(aliases or aliases_from_data_contract(contract) or DATA_COLLECTIONS)
        self._collection_resolver = collection_resolver

    def collection_name_for_alias(self, alias: str) -> str:
        return collection_name_for_alias(alias, aliases=self.aliases)

    def collection(self, alias: str) -> Any:
        collection_name = self.collection_name_for_alias(alias)
        if self._collection_resolver is not None:
            return self._collection_resolver(collection_name)
        if self.db is None:
            raise RuntimeError("AppData requires a database or collection_resolver")
        return self.db[collection_name]


def _default_database_name() -> str:
    return (
        os.getenv("MOZAIKS_APP_DATA_DATABASE_NAME")
        or os.getenv("MOZAIKS_APP_DATABASE_NAME")
        or os.getenv("MOZAIKS_APPS_DATABASE")
        or DEFAULT_APP_DATABASE_NAME
    ).strip() or DEFAULT_APP_DATABASE_NAME


def app_data_from_context(
    ctx: Any,
    *,
    contract: Mapping[str, Any] | None = None,
    app_root: str | os.PathLike[str] | None = None,
) -> AppData:
    existing = getattr(ctx, "app_data", None)
    if existing is not None:
        return existing  # type: ignore[no-any-return]

    resolved_contract = contract if contract is not None else load_app_data_contract(app_root)
    aliases = aliases_from_data_contract(resolved_contract)

    db = getattr(ctx, "db", None)
    if db is not None:
        return AppData(db, aliases=aliases)

    persistence = getattr(ctx, "persistence", None)
    if isinstance(persistence, MongoPersistenceContext) or hasattr(persistence, "literal_collection"):
        return AppData(
            aliases=aliases,
            collection_resolver=persistence.literal_collection,  # type: ignore[union-attr]
        )

    client = get_mongo_client()
    return AppData(client[_default_database_name()], aliases=aliases)


__all__ = [
    "AppData",
    "AppDataAliasError",
    "DATA_COLLECTIONS",
    "aliases_from_data_contract",
    "app_data_from_context",
    "collection_name_for_alias",
    "default_data_contract_path",
    "known_data_aliases",
    "load_app_data_contract",
]
