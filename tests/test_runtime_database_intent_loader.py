from __future__ import annotations

import json
from pathlib import Path

import pytest

import mozaiksai.core.runtime.persistence.mongo as mongo_module
from mozaiksai.core.runtime.app.loader import AppLoader, AppLoadError
from mozaiksai.core.runtime.persistence.intent_loader import (
    index_database_intent_by_entity,
    load_database_intent,
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
                        "scope": "app",
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
    path = root / "config" / "database_intent.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.mark.asyncio
async def test_app_without_database_intent_loads_successfully(tmp_path: Path) -> None:
    _write_app(tmp_path)

    result = await AppLoader.load(str(tmp_path))

    assert result.database_intent is None
    assert result.database_entities_by_key == {}


@pytest.mark.asyncio
async def test_app_with_valid_database_intent_loads_and_exposes_intent(tmp_path: Path) -> None:
    _write_app(tmp_path)
    _write_intent(tmp_path, _valid_intent())

    result = await AppLoader.load(str(tmp_path))

    assert result.database_intent is not None
    assert result.database_intent["version"] == "1"
    assert result.database_intent["surfaces"][0]["surface_id"] == "projects"


@pytest.mark.asyncio
async def test_valid_intent_is_indexed_by_module_and_entity(tmp_path: Path) -> None:
    _write_app(tmp_path)
    _write_intent(tmp_path, _valid_intent())

    result = await AppLoader.load(str(tmp_path))

    assert ("projects", "projects") in result.database_entities_by_key
    assert result.database_entities_by_key[("projects", "projects")]["name"] == "projects"


@pytest.mark.asyncio
async def test_invalid_json_produces_clear_app_load_failure(tmp_path: Path) -> None:
    _write_app(tmp_path)
    path = tmp_path / "config" / "database_intent.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(AppLoadError, match="Invalid config/database_intent.json"):
        await AppLoader.load(str(tmp_path))


@pytest.mark.asyncio
async def test_missing_surfaces_list_fails_validation(tmp_path: Path) -> None:
    _write_app(tmp_path)
    _write_intent(tmp_path, {"version": "1"})

    with pytest.raises(AppLoadError, match="database_intent.surfaces must be a list"):
        await AppLoader.load(str(tmp_path))


@pytest.mark.asyncio
async def test_optional_top_level_entities_list_may_be_absent_when_surfaces_exist(tmp_path: Path) -> None:
    _write_app(tmp_path)
    intent = _valid_intent()
    intent.pop("entities", None)
    _write_intent(tmp_path, intent)

    result = await AppLoader.load(str(tmp_path))

    assert result.database_entities_by_key[("projects", "projects")]["name"] == "projects"


@pytest.mark.asyncio
async def test_entity_missing_module_id_fails_validation(tmp_path: Path) -> None:
    _write_app(tmp_path)
    intent = _valid_intent()
    intent["entities"] = [{"entity_name": "tasks"}]
    _write_intent(tmp_path, intent)

    with pytest.raises(AppLoadError, match=r"database_intent.entities\[0\].module_id is required"):
        await AppLoader.load(str(tmp_path))


@pytest.mark.asyncio
async def test_entity_missing_entity_name_fails_validation(tmp_path: Path) -> None:
    _write_app(tmp_path)
    intent = _valid_intent()
    intent["entities"] = [{"module_id": "tasks"}]
    _write_intent(tmp_path, intent)

    with pytest.raises(AppLoadError, match=r"database_intent.entities\[0\].entity_name is required"):
        await AppLoader.load(str(tmp_path))


def test_load_database_intent_missing_file_returns_none(tmp_path: Path) -> None:
    assert load_database_intent(tmp_path) is None


def test_index_database_intent_by_entity_supports_top_level_entities() -> None:
    intent = {
        "version": "1",
        "surfaces": [],
        "entities": [{"module_id": "tasks", "entity_name": "tasks", "label": "Tasks"}],
    }

    assert index_database_intent_by_entity(intent)[("tasks", "tasks")]["label"] == "Tasks"


@pytest.mark.asyncio
async def test_intent_loading_does_not_call_mongo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fail_get_mongo_client():
        raise AssertionError("database intent loading must not call Mongo")

    monkeypatch.setattr(mongo_module, "get_mongo_client", fail_get_mongo_client)
    _write_app(tmp_path)
    _write_intent(tmp_path, _valid_intent())

    result = await AppLoader.load(str(tmp_path))

    assert result.database_intent is not None
