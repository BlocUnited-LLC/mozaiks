from __future__ import annotations

import json
from pathlib import Path

import pytest

import mozaiksai.core.runtime.persistence.mongo as mongo_module
from mozaiksai.core.runtime.app.loader import AppLoader, AppLoadError
from mozaiksai.core.runtime.persistence.intent_loader import (
    index_data_contract_by_entity,
    iter_data_contract_collections,
    load_data_contract,
)


def _write_app(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.json").write_text('{"appName": "Intent Test", "version": "1.0.0"}', encoding="utf-8")


def _valid_intent() -> dict:
    return {
        "version": "1",
        "app_id": "app_1",
        "surfaces": [
            {
                "surface_id": "projects",
                "surface_kind": "module",
                "collections": [
                    {
                        "name": "projects",
                        "scope": "app", "entity": "Project", "tenancy": "app_wide", "owner_field": None,
                        "ownership": {"surface_id": "projects", "surface_kind": "module"},
                        "fields": [{"name": "app_id", "type": "string", "required": True}],
                        "indexes": [{"keys": [["app_id", 1], ["project_id", 1]], "unique": True}],
                    }
                ],
            }
        ],
        "shared_collections": [],
        "policies": {"default_scope_field": "app_id"},
    }


def _write_intent(root: Path, value: dict) -> None:
    path = root / "data" / "contract.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.mark.asyncio
async def test_app_without_data_contract_loads_successfully(tmp_path: Path) -> None:
    _write_app(tmp_path)

    result = await AppLoader.load(str(tmp_path))

    assert result.data_contract is None
    assert result.data_entities_by_key == {}


@pytest.mark.asyncio
async def test_app_with_valid_data_contract_loads_and_exposes_intent(tmp_path: Path) -> None:
    _write_app(tmp_path)
    _write_intent(tmp_path, _valid_intent())

    result = await AppLoader.load(str(tmp_path))

    assert result.data_contract is not None
    assert result.data_contract["version"] == "1"
    assert result.data_contract["surfaces"][0]["surface_id"] == "projects"


@pytest.mark.asyncio
async def test_valid_intent_is_indexed_by_module_and_entity(tmp_path: Path) -> None:
    _write_app(tmp_path)
    _write_intent(tmp_path, _valid_intent())

    result = await AppLoader.load(str(tmp_path))

    assert ("projects", "Project") in result.data_entities_by_key
    assert result.data_entities_by_key[("projects", "Project")]["name"] == "projects"


@pytest.mark.asyncio
async def test_invalid_json_produces_clear_app_load_failure(tmp_path: Path) -> None:
    _write_app(tmp_path)
    path = tmp_path / "data" / "contract.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(AppLoadError, match="Invalid data/contract.json"):
        await AppLoader.load(str(tmp_path))


@pytest.mark.asyncio
async def test_missing_surfaces_list_fails_validation(tmp_path: Path) -> None:
    _write_app(tmp_path)
    _write_intent(tmp_path, {"version": "1"})

    with pytest.raises(AppLoadError, match="data_contract.surfaces must be a list"):
        await AppLoader.load(str(tmp_path))


@pytest.mark.asyncio
async def test_optional_top_level_entities_list_may_be_absent_when_surfaces_exist(tmp_path: Path) -> None:
    _write_app(tmp_path)
    intent = _valid_intent()
    intent.pop("entities", None)
    _write_intent(tmp_path, intent)

    result = await AppLoader.load(str(tmp_path))

    assert result.data_entities_by_key[("projects", "Project")]["name"] == "projects"


@pytest.mark.parametrize("changes,message", [
    ({"entity": None}, "entity"),
    ({"tenancy": "user"}, "tenancy"),
    ({"tenancy": "per_user", "owner_field": "user_id"}, "declared fields"),
    ({"owner_field": "app_id"}, "must be null"),
])
def test_collection_ownership_is_required(tmp_path, changes, message):
    intent = _valid_intent()
    intent["surfaces"][0]["collections"][0].update(changes)
    _write_intent(tmp_path, intent)
    with pytest.raises(ValueError, match=message):
        load_data_contract(tmp_path)


def test_top_level_entities_are_rejected(tmp_path):
    intent = _valid_intent()
    intent["entities"] = [{"module_id": "tasks", "entity_name": "tasks"}]
    _write_intent(tmp_path, intent)
    with pytest.raises(ValueError, match="entities is obsolete"):
        load_data_contract(tmp_path)


def test_load_data_contract_missing_file_returns_none(tmp_path):
    assert load_data_contract(tmp_path) is None


def test_entity_mapping_has_no_collection_name_fallback():
    intent = _valid_intent()
    intent["surfaces"][0]["collections"][0].pop("entity")
    with pytest.raises(ValueError, match="entity is required"):
        index_data_contract_by_entity(intent)


def test_shared_collections_are_indexed_by_their_declared_owner(tmp_path):
    intent = _valid_intent()
    shared = {
        "name": "project_notes", "entity": "ProjectNote", "scope": "app",
        "tenancy": "per_workspace", "owner_field": "workspace_id",
        "ownership": {"surface_id": "projects", "surface_kind": "module"},
        "fields": [{"name": "workspace_id", "type": "string", "required": True}],
    }
    intent["shared_collections"] = [shared]
    _write_intent(tmp_path, intent)
    loaded = load_data_contract(tmp_path)
    assert index_data_contract_by_entity(loaded)[("projects", "ProjectNote")] == shared
    assert [(owner, kind, collection["entity"]) for owner, kind, collection in iter_data_contract_collections(loaded)] == [
        ("projects", "module", "Project"), ("projects", "module", "ProjectNote"),
    ]


def test_shared_collection_requires_explicit_ownership(tmp_path):
    intent = _valid_intent()
    shared = dict(intent["surfaces"][0]["collections"].pop())
    shared.pop("ownership")
    intent["shared_collections"] = [shared]
    _write_intent(tmp_path, intent)
    with pytest.raises(ValueError, match="ownership requires surface_id and surface_kind"):
        load_data_contract(tmp_path)


def test_shared_collection_owners_cannot_disagree_on_kind(tmp_path):
    intent = _valid_intent()
    shared = intent["surfaces"][0]["collections"].pop()
    other = {
        **shared, "name": "project_notes", "entity": "ProjectNote",
        "ownership": {"surface_id": "projects", "surface_kind": "workflow"},
    }
    intent["surfaces"] = []
    intent["shared_collections"] = [shared, other]
    _write_intent(tmp_path, intent)
    with pytest.raises(ValueError, match="surface_kind conflicts with owner"):
        load_data_contract(tmp_path)


def test_shared_collection_validates_its_owner_field(tmp_path):
    intent = _valid_intent()
    shared = intent["surfaces"][0]["collections"].pop()
    shared.update(tenancy="per_user", owner_field="undeclared_user_id")
    intent["shared_collections"] = [shared]
    _write_intent(tmp_path, intent)
    with pytest.raises(ValueError, match="declared fields"):
        load_data_contract(tmp_path)


@pytest.mark.parametrize("ownership", [
    {"surface_id": "tasks", "surface_kind": "module"},
    {"surface_id": "projects", "surface_kind": "workflow"},
])
def test_collection_cannot_override_its_enclosing_owner(tmp_path, ownership):
    intent = _valid_intent()
    intent["surfaces"][0]["collections"][0]["ownership"] = ownership
    _write_intent(tmp_path, intent)
    with pytest.raises(ValueError, match="ownership must match enclosing surface"):
        load_data_contract(tmp_path)


@pytest.mark.asyncio
async def test_intent_loading_does_not_call_mongo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fail_get_mongo_client():
        raise AssertionError("data contract loading must not call Mongo")

    monkeypatch.setattr(mongo_module, "get_mongo_client", fail_get_mongo_client)
    _write_app(tmp_path)
    _write_intent(tmp_path, _valid_intent())

    result = await AppLoader.load(str(tmp_path))

    assert result.data_contract is not None

