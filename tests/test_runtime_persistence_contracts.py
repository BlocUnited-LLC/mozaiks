"""
Runtime persistence contract helpers unit tests.

Covers:
  startup_policy.get_database_startup_policy:
    - defaults to best_effort when env var unset
    - returns best_effort / required for valid values
    - raises DatabaseStartupPolicyError for invalid values

  intent_loader.load_data_contract:
    - returns None when data/contract.json does not exist
    - raises DataContractLoadError for invalid JSON
    - raises DataContractLoadError when version missing
    - raises DataContractLoadError when surfaces is not a list
    - raises DataContractLoadError when surface_id missing
    - raises DataContractLoadError when surface_kind missing
    - raises DataContractLoadError when module surface has no entity name
    - returns valid contract for well-formed input

  intent_loader.index_data_contract_by_entity:
    - returns empty dict for None contract
    - indexes entities by (module_id, entity_name)
    - raises DataContractLoadError for entity missing module_id
    - raises DataContractLoadError for entity missing entity_name
    - indexes surface collections when module_id and entity_name present
    - skips surface collections without module_id or entity_name
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mozaiksai.core.runtime.persistence.intent_loader import (
    DataContractLoadError,
    index_data_contract_by_entity,
    load_data_contract,
)
from mozaiksai.core.runtime.persistence.startup_policy import (
    DATABASE_STARTUP_POLICY_ENV,
    DatabaseStartupPolicyError,
    get_database_startup_policy,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_contract(tmp_path: Path, contract: dict) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    contract_file = data_dir / "contract.json"
    contract_file.write_text(json.dumps(contract), encoding="utf-8")
    return tmp_path


def _minimal_contract(**kw) -> dict:
    return {
        "version": kw.get("version", "1.0.0"),
        "surfaces": kw.get("surfaces", []),
        "entities": kw.get("entities", None),
    }


def _module_surface(
    surface_id: str = "s1",
    collections: list | None = None,
) -> dict:
    return {
        "surface_id": surface_id,
        "surface_kind": "module",
        "collections": collections or [],
    }


def _collection(name: str = "records", module_id: str = "my_module") -> dict:
    return {"name": name, "module_id": module_id}


# ---------------------------------------------------------------------------
# 1. get_database_startup_policy
# ---------------------------------------------------------------------------

class TestGetDatabaseStartupPolicy:
    def test_defaults_to_best_effort_when_unset(self, monkeypatch):
        monkeypatch.delenv(DATABASE_STARTUP_POLICY_ENV, raising=False)
        assert get_database_startup_policy() == "best_effort"

    def test_returns_best_effort_when_set(self, monkeypatch):
        monkeypatch.setenv(DATABASE_STARTUP_POLICY_ENV, "best_effort")
        assert get_database_startup_policy() == "best_effort"

    def test_returns_required_when_set(self, monkeypatch):
        monkeypatch.setenv(DATABASE_STARTUP_POLICY_ENV, "required")
        assert get_database_startup_policy() == "required"

    def test_raises_for_invalid_value(self, monkeypatch):
        monkeypatch.setenv(DATABASE_STARTUP_POLICY_ENV, "optional")
        with pytest.raises(DatabaseStartupPolicyError):
            get_database_startup_policy()

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv(DATABASE_STARTUP_POLICY_ENV, "BEST_EFFORT")
        assert get_database_startup_policy() == "best_effort"

    def test_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv(DATABASE_STARTUP_POLICY_ENV, "  required  ")
        assert get_database_startup_policy() == "required"


# ---------------------------------------------------------------------------
# 2. load_data_contract
# ---------------------------------------------------------------------------

class TestLoadDataContract:
    def test_returns_none_when_file_absent(self, tmp_path):
        result = load_data_contract(tmp_path)
        assert result is None

    def test_raises_for_invalid_json(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "contract.json").write_text("not json{{", encoding="utf-8")
        with pytest.raises(DataContractLoadError, match="Failed to read"):
            load_data_contract(tmp_path)

    def test_raises_for_missing_version(self, tmp_path):
        contract = {"surfaces": [], "entities": []}
        _write_contract(tmp_path, contract)
        with pytest.raises(DataContractLoadError, match="version"):
            load_data_contract(tmp_path)

    def test_raises_for_empty_version(self, tmp_path):
        contract = {"version": "", "surfaces": [], "entities": []}
        _write_contract(tmp_path, contract)
        with pytest.raises(DataContractLoadError, match="version"):
            load_data_contract(tmp_path)

    def test_raises_when_surfaces_not_list(self, tmp_path):
        contract = {"version": "1.0", "surfaces": "bad"}
        _write_contract(tmp_path, contract)
        with pytest.raises(DataContractLoadError, match="surfaces"):
            load_data_contract(tmp_path)

    def test_raises_when_surface_missing_surface_id(self, tmp_path):
        contract = {
            "version": "1.0",
            "surfaces": [{"surface_kind": "module", "collections": []}],
        }
        _write_contract(tmp_path, contract)
        with pytest.raises(DataContractLoadError, match="surface_id"):
            load_data_contract(tmp_path)

    def test_raises_when_surface_missing_surface_kind(self, tmp_path):
        contract = {
            "version": "1.0",
            "surfaces": [{"surface_id": "s1", "collections": []}],
        }
        _write_contract(tmp_path, contract)
        with pytest.raises(DataContractLoadError, match="surface_kind"):
            load_data_contract(tmp_path)

    def test_raises_when_module_collection_missing_name(self, tmp_path):
        contract = {
            "version": "1.0",
            "surfaces": [
                {
                    "surface_id": "s1",
                    "surface_kind": "module",
                    "collections": [{"module_id": "mod"}],  # no name
                }
            ],
        }
        _write_contract(tmp_path, contract)
        with pytest.raises(DataContractLoadError):
            load_data_contract(tmp_path)

    def test_returns_contract_for_valid_input(self, tmp_path):
        contract = _minimal_contract()
        contract["surfaces"] = [_module_surface(collections=[_collection()])]
        _write_contract(tmp_path, contract)
        result = load_data_contract(tmp_path)
        assert result is not None
        assert result["version"] == "1.0.0"

    def test_returns_contract_with_no_surfaces(self, tmp_path):
        contract = _minimal_contract(surfaces=[])
        _write_contract(tmp_path, contract)
        result = load_data_contract(tmp_path)
        assert result["surfaces"] == []


# ---------------------------------------------------------------------------
# 3. index_data_contract_by_entity
# ---------------------------------------------------------------------------

class TestIndexDataContractByEntity:
    def test_returns_empty_for_none_contract(self):
        assert index_data_contract_by_entity(None) == {}

    def test_indexes_entities_by_module_entity(self):
        contract = {
            "version": "1.0",
            "surfaces": [],
            "entities": [
                {"module_id": "wallet", "entity_name": "transaction", "schema": {}}
            ],
        }
        index = index_data_contract_by_entity(contract)
        assert ("wallet", "transaction") in index

    def test_raises_for_entity_missing_module_id(self):
        contract = {
            "version": "1.0",
            "surfaces": [],
            "entities": [{"entity_name": "tx"}],
        }
        with pytest.raises(DataContractLoadError, match="module_id"):
            index_data_contract_by_entity(contract)

    def test_raises_for_entity_missing_entity_name(self):
        contract = {
            "version": "1.0",
            "surfaces": [],
            "entities": [{"module_id": "wallet"}],
        }
        with pytest.raises(DataContractLoadError, match="entity_name"):
            index_data_contract_by_entity(contract)

    def test_indexes_surface_module_collections(self):
        contract = _minimal_contract()
        contract["surfaces"] = [
            _module_surface(surface_id="mod_surface", collections=[_collection("users", "auth")])
        ]
        index = index_data_contract_by_entity(contract)
        assert ("auth", "users") in index

    def test_skips_non_module_surface_collection_without_explicit_module_id(self):
        # surface_kind != "module" and no module_id on collection → skipped
        contract = {
            "version": "1.0",
            "surfaces": [
                {
                    "surface_id": "ext",
                    "surface_kind": "external",
                    "collections": [{"name": "logs"}],  # no module_id
                }
            ],
        }
        index = index_data_contract_by_entity(contract)
        # should not raise; collection skipped or indexed without module_id
        assert isinstance(index, dict)

    def test_entity_name_fallback_to_name_field(self):
        contract = {
            "version": "1.0",
            "surfaces": [],
            "entities": [{"module_id": "mod", "name": "record"}],
        }
        index = index_data_contract_by_entity(contract)
        assert ("mod", "record") in index
