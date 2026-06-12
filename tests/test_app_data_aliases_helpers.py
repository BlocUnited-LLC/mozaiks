"""
App data persistence pure helper unit tests.

Covers:
  aliases_from_data_contract:
    - None / non-Mapping → empty dict
    - empty contract → empty dict
    - contract with surfaces.collections using data_alias+mongo_collection → alias mapped
    - collection uses "collection" fallback if no "mongo_collection" → still mapped
    - surface entry is non-Mapping → skipped
    - collection entry is non-Mapping → skipped
    - missing alias or collection_name → entry skipped
    - contract with top-level "aliases" entries → alias mapped
    - alias entry uses "data_alias" key → still mapped
    - alias entry uses "mongo_collection" → still mapped
    - alias entry is non-Mapping → skipped
    - contract with "shared_collections" → primary_alias mapped
    - shared_collections entry with "shared_with" → each alias mapped
    - shared_collections entry with "read_by" → each alias mapped
    - shared entry with non-Mapping in shared_with → skipped
    - shared entry missing primary_alias → skipped for primary
    - all three sources combined in one contract

  collection_name_for_alias:
    - alias exists in provided aliases → returns collection name
    - alias not found → AppDataAliasError raised with alias in message
    - empty alias string → raises AppDataAliasError
    - alias resolved from explicit contract dict

  known_data_aliases:
    - aliases=None, contract=None, no app_root → falls back (returns tuple from DATA_COLLECTIONS or empty)
    - explicit aliases mapping → sorted tuple returned
    - explicit contract → sorted aliases returned
    - empty contract → empty tuple
"""
from __future__ import annotations

import pytest

from mozaiksai.core.runtime.persistence.app_data import (
    AppDataAliasError,
    aliases_from_data_contract,
    collection_name_for_alias,
    known_data_aliases,
)

# ---------------------------------------------------------------------------
# 1. aliases_from_data_contract
# ---------------------------------------------------------------------------

class TestAliasesFromDataContract:
    def test_none_returns_empty(self):
        assert aliases_from_data_contract(None) == {}

    def test_non_mapping_returns_empty(self):
        assert aliases_from_data_contract("bad") == {}  # type: ignore[arg-type]
        assert aliases_from_data_contract(42) == {}  # type: ignore[arg-type]
        assert aliases_from_data_contract([]) == {}  # type: ignore[arg-type]

    def test_empty_contract_returns_empty(self):
        assert aliases_from_data_contract({}) == {}

    # -- surfaces path --

    def test_surfaces_collection_mapped(self):
        contract = {
            "surfaces": [{
                "collections": [{
                    "data_alias": "users",
                    "mongo_collection": "app_users",
                }]
            }]
        }
        result = aliases_from_data_contract(contract)
        assert result == {"users": "app_users"}

    def test_surfaces_collection_fallback_to_collection_key(self):
        contract = {
            "surfaces": [{
                "collections": [{
                    "data_alias": "orders",
                    "collection": "app_orders",
                }]
            }]
        }
        result = aliases_from_data_contract(contract)
        assert result == {"orders": "app_orders"}

    def test_surfaces_non_mapping_surface_skipped(self):
        contract = {
            "surfaces": ["not_a_dict", {"collections": [{"data_alias": "a", "mongo_collection": "b"}]}]
        }
        result = aliases_from_data_contract(contract)
        assert result == {"a": "b"}

    def test_surfaces_non_mapping_collection_skipped(self):
        contract = {
            "surfaces": [{
                "collections": ["not_a_dict", {"data_alias": "x", "mongo_collection": "y"}]
            }]
        }
        result = aliases_from_data_contract(contract)
        assert result == {"x": "y"}

    def test_surfaces_missing_alias_skipped(self):
        contract = {
            "surfaces": [{
                "collections": [{"mongo_collection": "no_alias"}]
            }]
        }
        assert aliases_from_data_contract(contract) == {}

    def test_surfaces_missing_collection_name_skipped(self):
        contract = {
            "surfaces": [{
                "collections": [{"data_alias": "has_alias"}]
            }]
        }
        assert aliases_from_data_contract(contract) == {}

    # -- top-level aliases path --

    def test_aliases_list_mapped(self):
        contract = {
            "aliases": [{"alias": "profiles", "collection": "app_profiles"}]
        }
        result = aliases_from_data_contract(contract)
        assert result == {"profiles": "app_profiles"}

    def test_aliases_uses_data_alias_key(self):
        contract = {
            "aliases": [{"data_alias": "my_alias", "collection": "my_col"}]
        }
        result = aliases_from_data_contract(contract)
        assert result == {"my_alias": "my_col"}

    def test_aliases_uses_mongo_collection_key(self):
        contract = {
            "aliases": [{"alias": "things", "mongo_collection": "mongo_things"}]
        }
        result = aliases_from_data_contract(contract)
        assert result == {"things": "mongo_things"}

    def test_aliases_non_mapping_entry_skipped(self):
        contract = {
            "aliases": ["not_a_dict", {"alias": "a", "collection": "b"}]
        }
        result = aliases_from_data_contract(contract)
        assert result == {"a": "b"}

    def test_aliases_missing_alias_key_skipped(self):
        contract = {"aliases": [{"collection": "no_alias"}]}
        assert aliases_from_data_contract(contract) == {}

    def test_aliases_missing_collection_skipped(self):
        contract = {"aliases": [{"alias": "no_col"}]}
        assert aliases_from_data_contract(contract) == {}

    # -- shared_collections path --

    def test_shared_collections_primary_alias_mapped(self):
        contract = {
            "shared_collections": [{
                "primary_alias": "shared_data",
                "mongo_collection": "shared_col",
            }]
        }
        result = aliases_from_data_contract(contract)
        assert result == {"shared_data": "shared_col"}

    def test_shared_collections_shared_with_entries_mapped(self):
        contract = {
            "shared_collections": [{
                "mongo_collection": "shared_col",
                "primary_alias": "primary",
                "shared_with": [
                    {"data_alias": "alias_a"},
                    {"alias": "alias_b"},
                ],
            }]
        }
        result = aliases_from_data_contract(contract)
        assert result["alias_a"] == "shared_col"
        assert result["alias_b"] == "shared_col"

    def test_shared_collections_read_by_entries_mapped(self):
        contract = {
            "shared_collections": [{
                "mongo_collection": "read_col",
                "primary_alias": "primary",
                "read_by": [
                    {"data_alias": "reader_a"},
                ],
            }]
        }
        result = aliases_from_data_contract(contract)
        assert result["reader_a"] == "read_col"

    def test_shared_collections_missing_primary_alias_skipped(self):
        contract = {
            "shared_collections": [{"mongo_collection": "col"}]
        }
        result = aliases_from_data_contract(contract)
        assert result == {}

    def test_shared_collections_non_mapping_shared_with_entry_skipped(self):
        contract = {
            "shared_collections": [{
                "mongo_collection": "col",
                "primary_alias": "p",
                "shared_with": ["not_a_dict", {"alias": "valid"}],
            }]
        }
        result = aliases_from_data_contract(contract)
        assert result["valid"] == "col"

    # -- combined --

    def test_all_three_sources_combined(self):
        contract = {
            "surfaces": [{"collections": [{"data_alias": "surf_alias", "mongo_collection": "surf_col"}]}],
            "aliases": [{"alias": "top_alias", "collection": "top_col"}],
            "shared_collections": [{"primary_alias": "shared_alias", "mongo_collection": "shared_col"}],
        }
        result = aliases_from_data_contract(contract)
        assert result["surf_alias"] == "surf_col"
        assert result["top_alias"] == "top_col"
        assert result["shared_alias"] == "shared_col"


# ---------------------------------------------------------------------------
# 2. collection_name_for_alias
# ---------------------------------------------------------------------------

class TestCollectionNameForAlias:
    def test_alias_found_in_explicit_map(self):
        result = collection_name_for_alias("users", aliases={"users": "app_users"})
        assert result == "app_users"

    def test_alias_not_found_raises(self):
        with pytest.raises(AppDataAliasError, match="unknown_alias"):
            collection_name_for_alias("unknown_alias", aliases={"a": "b"})

    def test_alias_from_contract(self):
        contract = {"aliases": [{"alias": "items", "collection": "app_items"}]}
        result = collection_name_for_alias("items", contract=contract)
        assert result == "app_items"

    def test_empty_alias_not_in_dict_raises(self):
        with pytest.raises(AppDataAliasError):
            collection_name_for_alias("", aliases={"users": "app_users"})


# ---------------------------------------------------------------------------
# 3. known_data_aliases
# ---------------------------------------------------------------------------

class TestKnownDataAliases:
    def test_explicit_aliases_sorted(self):
        result = known_data_aliases(aliases={"b_alias": "b", "a_alias": "a"})
        assert result == ("a_alias", "b_alias")

    def test_explicit_contract(self):
        contract = {
            "aliases": [
                {"alias": "z_alias", "collection": "z_col"},
                {"alias": "a_alias", "collection": "a_col"},
            ]
        }
        result = known_data_aliases(contract=contract)
        assert result == ("a_alias", "z_alias")

    def test_empty_contract_returns_empty_tuple(self):
        result = known_data_aliases(contract={})
        assert result == ()

    def test_returns_tuple(self):
        result = known_data_aliases(aliases={"x": "y"})
        assert isinstance(result, tuple)
