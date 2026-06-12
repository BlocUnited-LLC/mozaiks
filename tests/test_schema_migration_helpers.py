"""
schema_migration.py pure helper unit tests.

Covers:
  _collection_field_map:
    - empty columns → {}
    - columns with names → {name: col_def}
    - multiple columns → all mapped

  _index_set:
    - missing key → empty set
    - list of index entries → set of str entries
    - integer entries → str-coerced

  _build_ensure_collection_ops:
    - no new collections → []
    - new collection with module_id and entity_name → op added
    - new collection using name fallback for both → op added
    - collection with empty module_id/entity_name → skipped
    - collection not in diff new_collections → skipped

  _new_collection_definitions:
    - no new collections → []
    - new collection in diff → returned
    - existing collections not in diff → excluded

  migration_file_path:
    - returns data/migrations/{migration_id}.json
"""
from __future__ import annotations

from factory_app.workflows.AppGenerator.tools.schema_migration import (
    _build_ensure_collection_ops,
    _collection_field_map,
    _index_set,
    _new_collection_definitions,
    migration_file_path,
)

# ---------------------------------------------------------------------------
# 1. _collection_field_map
# ---------------------------------------------------------------------------

class TestCollectionFieldMap:
    def test_empty_columns_returns_empty_dict(self):
        assert _collection_field_map({"columns": []}) == {}

    def test_no_columns_key_returns_empty_dict(self):
        assert _collection_field_map({}) == {}

    def test_single_column_mapped(self):
        col = {"name": "id", "type": "string"}
        result = _collection_field_map({"columns": [col]})
        assert result == {"id": col}

    def test_multiple_columns_all_mapped(self):
        cols = [{"name": "id", "type": "string"}, {"name": "title", "type": "string"}]
        result = _collection_field_map({"columns": cols})
        assert "id" in result
        assert "title" in result
        assert result["id"] == cols[0]


# ---------------------------------------------------------------------------
# 2. _index_set
# ---------------------------------------------------------------------------

class TestIndexSet:
    def test_missing_key_returns_empty_set(self):
        assert _index_set({}, "indices") == set()

    def test_empty_list_returns_empty_set(self):
        assert _index_set({"indices": []}, "indices") == set()

    def test_list_of_strings_returned_as_set(self):
        result = _index_set({"indices": ["idx_a", "idx_b"]}, "indices")
        assert result == {"idx_a", "idx_b"}

    def test_integer_entries_coerced_to_str(self):
        result = _index_set({"constraints": [1, 2]}, "constraints")
        assert "1" in result
        assert "2" in result

    def test_constraints_key(self):
        result = _index_set({"constraints": ["unique_email"]}, "constraints")
        assert result == {"unique_email"}


# ---------------------------------------------------------------------------
# 3. _build_ensure_collection_ops
# ---------------------------------------------------------------------------

def _diff(new_collection_names: list[str]) -> dict:
    return {"new_collections": new_collection_names}


class TestBuildEnsureCollectionOps:
    def test_no_new_collections_returns_empty(self):
        result = _build_ensure_collection_ops(
            _diff([]),
            {"collections": [{"name": "tasks"}]},
        )
        assert result == []

    def test_new_collection_with_module_and_entity(self):
        diff = _diff(["tasks"])
        schema = {"collections": [{"name": "tasks", "module_id": "tasks_module", "entity_name": "Task"}]}
        result = _build_ensure_collection_ops(diff, schema)
        assert len(result) == 1
        assert result[0]["type"] == "ensure_collection"
        assert result[0]["module_id"] == "tasks_module"
        assert result[0]["entity_name"] == "Task"

    def test_new_collection_name_fallback(self):
        diff = _diff(["tickets"])
        schema = {"collections": [{"name": "tickets"}]}
        result = _build_ensure_collection_ops(diff, schema)
        assert len(result) == 1
        assert result[0]["module_id"] == "tickets"
        assert result[0]["entity_name"] == "tickets"

    def test_collection_empty_module_id_falls_back_to_name(self):
        # module_id="" falls back to name via `module_id or col.get("name")`
        diff = _diff(["bad"])
        schema = {"collections": [{"name": "bad", "module_id": "", "entity_name": ""}]}
        result = _build_ensure_collection_ops(diff, schema)
        assert len(result) == 1
        assert result[0]["module_id"] == "bad"
        assert result[0]["entity_name"] == "bad"

    def test_collection_with_no_name_and_empty_module_id_skipped(self):
        # When name, module_id, and entity_name are all empty/missing → skipped
        diff = _diff([""])
        schema = {"collections": [{"name": "", "module_id": "", "entity_name": ""}]}
        result = _build_ensure_collection_ops(diff, schema)
        assert result == []

    def test_collection_not_in_new_collections_skipped(self):
        diff = _diff(["tasks"])
        schema = {"collections": [
            {"name": "tasks"},
            {"name": "existing"},  # not in diff
        ]}
        result = _build_ensure_collection_ops(diff, schema)
        names = [op["entity_name"] for op in result]
        assert "existing" not in names


# ---------------------------------------------------------------------------
# 4. _new_collection_definitions
# ---------------------------------------------------------------------------

class TestNewCollectionDefinitions:
    def test_no_new_collections_returns_empty(self):
        result = _new_collection_definitions(
            _diff([]),
            {"collections": [{"name": "tasks"}]},
        )
        assert result == []

    def test_new_collection_returned(self):
        col = {"name": "tickets", "columns": []}
        diff = _diff(["tickets"])
        result = _new_collection_definitions(diff, {"collections": [col]})
        assert result == [col]

    def test_existing_collections_excluded(self):
        existing = {"name": "old_col", "columns": []}
        new_col = {"name": "new_col", "columns": []}
        diff = _diff(["new_col"])
        result = _new_collection_definitions(diff, {"collections": [existing, new_col]})
        assert len(result) == 1
        assert result[0]["name"] == "new_col"

    def test_empty_schema_returns_empty(self):
        diff = _diff(["tasks"])
        result = _new_collection_definitions(diff, {"collections": []})
        assert result == []


# ---------------------------------------------------------------------------
# 5. migration_file_path
# ---------------------------------------------------------------------------

class TestMigrationFilePath:
    def test_migration_path_format(self):
        result = migration_file_path("2024-01-01-add-tasks")
        assert result == "data/migrations/2024-01-01-add-tasks.json"

    def test_simple_migration_id(self):
        result = migration_file_path("v2")
        assert result == "data/migrations/v2.json"

    def test_path_starts_with_data_migrations(self):
        result = migration_file_path("some_migration")
        assert result.startswith("data/migrations/")
        assert result.endswith(".json")
