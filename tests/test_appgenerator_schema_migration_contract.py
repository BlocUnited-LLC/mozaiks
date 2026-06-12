"""Schema migration contract tests.

Verifies that generate_migration() produces a document that satisfies the
runtime _validate_migration() contract so files written to
data/migrations/*.json can be loaded and validated by the
runtime migration loader without raising DatabaseMigrationError.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from factory_app.workflows.AppGenerator.tools.schema_migration import (
    diff_schemas,
    generate_migration,
    inject_migration_into_bundle,
)
from mozaiksai.core.runtime.persistence.migrations import (
    DatabaseMigrationError,
    _validate_migration,
    load_data_migrations,
)


# ── fixtures ───────────────────────────────────────────────────────────────────


def _schema(collections: list[Dict[str, Any]]) -> Dict[str, Any]:
    """Minimal schema wrapper consumed by diff_schemas."""
    return {"collections": collections}


def _col(name: str, *, module_id: str | None = None, entity_name: str | None = None) -> Dict[str, Any]:
    c: Dict[str, Any] = {"name": name, "columns": []}
    if module_id:
        c["module_id"] = module_id
    if entity_name:
        c["entity_name"] = entity_name
    return c


# ── generate_migration output contract ────────────────────────────────────────


def test_generate_migration_has_schema_version():
    diff = diff_schemas(None, _schema([_col("orders")]))
    migration = generate_migration(diff, app_id="app_1", change_class="feature", new_schema=_schema([_col("orders")]))

    assert "schema_version" in migration
    assert isinstance(migration["schema_version"], str)
    assert migration["schema_version"]


def test_generate_migration_has_operations_list():
    diff = diff_schemas(None, _schema([_col("orders")]))
    migration = generate_migration(diff, app_id="app_1", change_class="feature", new_schema=_schema([_col("orders")]))

    assert "operations" in migration
    assert isinstance(migration["operations"], list)


def test_generate_migration_passes_runtime_validate_migration():
    """The output of generate_migration must pass the runtime _validate_migration check."""
    diff = diff_schemas(None, _schema([_col("orders")]))
    migration = generate_migration(
        diff,
        app_id="app_1",
        change_class="feature",
        new_schema=_schema([_col("orders")]),
    )

    # Must not raise DatabaseMigrationError.
    _validate_migration(migration, "test_migration")


def test_generate_migration_emits_ensure_collection_for_new_collections():
    """Each new collection in the diff becomes an ensure_collection operation."""
    schema = _schema([_col("orders", module_id="billing", entity_name="orders")])
    diff = diff_schemas(None, schema)
    migration = generate_migration(diff, app_id="app_1", change_class="feature", new_schema=schema)

    ops = migration["operations"]
    assert len(ops) == 1
    assert ops[0]["type"] == "ensure_collection"
    assert ops[0]["module_id"] == "billing"
    assert ops[0]["entity_name"] == "orders"


def test_generate_migration_falls_back_to_name_when_module_id_missing():
    """When module_id/entity_name are absent, collection name is used for both."""
    schema = _schema([_col("orders")])  # no module_id or entity_name
    diff = diff_schemas(None, schema)
    migration = generate_migration(diff, app_id="app_1", change_class="feature", new_schema=schema)

    ops = migration["operations"]
    assert len(ops) == 1
    assert ops[0]["type"] == "ensure_collection"
    assert ops[0]["module_id"] == "orders"
    assert ops[0]["entity_name"] == "orders"


def test_generate_migration_metadata_carries_human_readable_diff():
    schema = _schema([_col("orders")])
    diff = diff_schemas(None, schema)
    migration = generate_migration(diff, app_id="app_1", change_class="feature", new_schema=schema)

    metadata = migration.get("metadata")
    assert isinstance(metadata, dict)
    assert metadata["app_id"] == "app_1"
    assert metadata["change_class"] == "feature"
    assert "safety" in metadata
    assert "changes" in metadata
    assert "new_collections" in metadata["changes"]


def test_generate_migration_empty_diff_has_empty_operations():
    """No new collections → operations list is empty."""
    old_schema = _schema([_col("orders")])
    new_schema = _schema([_col("orders")])
    diff = diff_schemas(old_schema, new_schema)
    migration = generate_migration(diff, app_id="app_1", change_class="design", new_schema=new_schema)

    assert migration["operations"] == []
    _validate_migration(migration, "test_migration")


# ── roundtrip: generate → inject → load → validate ────────────────────────────


def test_generated_migration_survives_roundtrip_through_runtime_loader(tmp_path: Path):
    """A generated migration written via inject_migration_into_bundle must be
    loadable by the runtime load_data_migrations without raising."""
    schema = _schema([_col("invoices", module_id="billing", entity_name="invoices")])
    diff = diff_schemas(None, schema)
    migration = generate_migration(diff, app_id="app_1", change_class="feature", new_schema=schema)

    files_map: Dict[str, str] = {}
    inject_migration_into_bundle(files_map, migration)

    # Write the file to disk as the runtime loader expects.
    app_root = tmp_path
    for rel_path, content in files_map.items():
        abs_path = app_root / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content, encoding="utf-8")

    loaded = load_data_migrations(app_root)

    assert len(loaded) == 1
    assert loaded[0]["migration_id"] == migration["migration_id"]
    assert isinstance(loaded[0]["operations"], list)
    assert loaded[0]["operations"][0]["type"] == "ensure_collection"


