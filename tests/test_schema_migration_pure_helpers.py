"""
Pure helper unit tests for:
  factory_app/workflows/AppGenerator/tools/schema_migration.py

Covers:
  _collection_field_map:
    - empty columns → {}
    - single field → {name: field_def}
    - multiple fields → all mapped
    - collection with no "columns" key → {}

  _index_set:
    - empty list → empty set
    - values converted to strings
    - key not present → empty set
    - multiple values → all included

  _build_ensure_collection_ops:
    - diff with no new collections → []
    - collection in new_collections with explicit module_id and entity_name
    - collection in new_collections falling back to name for both
    - collection not in new_collections → skipped
    - missing module_id after fallback → skipped

  _new_collection_definitions:
    - empty new_collections → []
    - new collection in diff → returned
    - collection not in diff → excluded
    - multiple new collections → all returned
"""
from __future__ import annotations

from typing import Any

from factory_app.workflows.AppGenerator.tools.schema_migration import (
    _build_ensure_collection_ops,
    _collection_field_map,
    _index_set,
    _new_collection_definitions,
)

# ---------------------------------------------------------------------------
# 1. _collection_field_map
# ---------------------------------------------------------------------------

class TestCollectionFieldMap:
    def test_empty_columns_returns_empty(self):
        assert _collection_field_map({"columns": []}) == {}

    def test_no_columns_key_returns_empty(self):
        assert _collection_field_map({}) == {}

    def test_single_field_mapped_by_name(self):
        col = {"columns": [{"name": "user_id", "type": "string"}]}
        result = _collection_field_map(col)
        assert "user_id" in result
        assert result["user_id"]["type"] == "string"

    def test_multiple_fields_all_mapped(self):
        col = {
            "columns": [
                {"name": "id", "type": "string"},
                {"name": "email", "type": "string"},
                {"name": "created_at", "type": "datetime"},
            ]
        }
        result = _collection_field_map(col)
        assert set(result.keys()) == {"id", "email", "created_at"}

    def test_field_def_preserved_fully(self):
        field_def = {"name": "score", "type": "float", "required": True, "default": 0.0}
        col = {"columns": [field_def]}
        result = _collection_field_map(col)
        assert result["score"] == field_def


# ---------------------------------------------------------------------------
# 2. _index_set
# ---------------------------------------------------------------------------

class TestIndexSet:
    def test_empty_list_returns_empty_set(self):
        assert _index_set({"indices": []}, "indices") == set()

    def test_key_not_present_returns_empty_set(self):
        assert _index_set({}, "indices") == set()

    def test_values_returned_as_strings(self):
        result = _index_set({"indices": ["idx_email", "idx_created"]}, "indices")
        assert result == {"idx_email", "idx_created"}

    def test_integers_converted_to_strings(self):
        result = _index_set({"indices": [1, 2, 3]}, "indices")
        assert result == {"1", "2", "3"}

    def test_constraints_key(self):
        result = _index_set({"constraints": ["unique_email"]}, "constraints")
        assert "unique_email" in result

    def test_multiple_values_all_included(self):
        result = _index_set({"indices": ["a", "b", "c"]}, "indices")
        assert len(result) == 3


# ---------------------------------------------------------------------------
# 3. _build_ensure_collection_ops
# ---------------------------------------------------------------------------

def _make_diff(new_collections: list[str]) -> dict[str, Any]:
    return {
        "has_changes": True,
        "is_additive_only": True,
        "new_collections": new_collections,
        "removed_collections": [],
        "modified_collections": [],
        "destructive_warnings": [],
    }


class TestBuildEnsureCollectionOps:
    def test_no_new_collections_returns_empty(self):
        diff = _make_diff([])
        schema = {"collections": [{"name": "existing", "module_id": "billing", "entity_name": "invoice"}]}
        assert _build_ensure_collection_ops(diff, schema) == []

    def test_new_collection_with_explicit_fields(self):
        diff = _make_diff(["orders"])
        schema = {
            "collections": [
                {"name": "orders", "module_id": "sales", "entity_name": "order"}
            ]
        }
        ops = _build_ensure_collection_ops(diff, schema)
        assert len(ops) == 1
        assert ops[0]["type"] == "ensure_collection"
        assert ops[0]["module_id"] == "sales"
        assert ops[0]["entity_name"] == "order"

    def test_fallback_to_name_when_module_id_missing(self):
        diff = _make_diff(["products"])
        schema = {"collections": [{"name": "products"}]}
        ops = _build_ensure_collection_ops(diff, schema)
        assert len(ops) == 1
        assert ops[0]["module_id"] == "products"
        assert ops[0]["entity_name"] == "products"

    def test_collection_not_in_new_collections_skipped(self):
        diff = _make_diff(["orders"])
        schema = {
            "collections": [
                {"name": "orders", "module_id": "sales", "entity_name": "order"},
                {"name": "users", "module_id": "auth", "entity_name": "user"},
            ]
        }
        ops = _build_ensure_collection_ops(diff, schema)
        names_in_ops = {op["entity_name"] for op in ops}
        assert "user" not in names_in_ops
        assert "order" in names_in_ops

    def test_empty_name_after_fallback_skipped(self):
        # name is empty → empty string → skipped
        diff = _make_diff([""])
        schema = {"collections": [{"name": ""}]}
        ops = _build_ensure_collection_ops(diff, schema)
        assert ops == []

    def test_whitespace_name_after_fallback_skipped(self):
        diff = _make_diff(["   "])
        schema = {"collections": [{"name": "   "}]}
        ops = _build_ensure_collection_ops(diff, schema)
        assert ops == []

    def test_multiple_new_collections(self):
        diff = _make_diff(["orders", "products"])
        schema = {
            "collections": [
                {"name": "orders", "module_id": "sales", "entity_name": "order"},
                {"name": "products", "module_id": "catalog", "entity_name": "product"},
            ]
        }
        ops = _build_ensure_collection_ops(diff, schema)
        assert len(ops) == 2
        entity_names = {op["entity_name"] for op in ops}
        assert entity_names == {"order", "product"}


# ---------------------------------------------------------------------------
# 4. _new_collection_definitions
# ---------------------------------------------------------------------------

class TestNewCollectionDefinitions:
    def test_empty_new_collections_returns_empty(self):
        diff = _make_diff([])
        schema = {"collections": [{"name": "existing"}]}
        assert _new_collection_definitions(diff, schema) == []

    def test_new_collection_returned(self):
        diff = _make_diff(["invoices"])
        schema = {
            "collections": [
                {"name": "invoices", "module_id": "billing", "entity_name": "invoice"}
            ]
        }
        result = _new_collection_definitions(diff, schema)
        assert len(result) == 1
        assert result[0]["name"] == "invoices"

    def test_collection_not_in_new_collections_excluded(self):
        diff = _make_diff(["invoices"])
        schema = {
            "collections": [
                {"name": "invoices", "module_id": "billing"},
                {"name": "users", "module_id": "auth"},
            ]
        }
        result = _new_collection_definitions(diff, schema)
        names = {c["name"] for c in result}
        assert "invoices" in names
        assert "users" not in names

    def test_multiple_new_collections_all_returned(self):
        diff = _make_diff(["a", "b"])
        schema = {
            "collections": [
                {"name": "a"},
                {"name": "b"},
                {"name": "c"},
            ]
        }
        result = _new_collection_definitions(diff, schema)
        assert len(result) == 2
        names = {c["name"] for c in result}
        assert names == {"a", "b"}

    def test_empty_schema_returns_empty(self):
        diff = _make_diff(["x"])
        schema = {}
        assert _new_collection_definitions(diff, schema) == []
