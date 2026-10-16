"""Default AppData snapshots stay fresh without re-reading per alias."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from mozaiksai.core.runtime.persistence import app_data


def _workspace(root: Path, aliases: dict[str, str]) -> Path:
    app_root = root / "app"
    (app_root / "data").mkdir(parents=True, exist_ok=True)
    (app_root / "app.json").write_text('{"app_id": "snapshot-test"}', encoding="utf-8")
    contract_path = app_root / "data" / "contract.json"
    contract_path.write_text(json.dumps({"aliases": [
        {"alias": key, "collection": value} for key, value in aliases.items()
    ]}), encoding="utf-8")
    return contract_path


@pytest.mark.parametrize("alias_count", [1, 133])
def test_default_snapshot_reads_contract_once(monkeypatch, tmp_path, alias_count):
    aliases = {f"module.items_{index}": f"items_{index}" for index in range(alias_count)}
    _workspace(tmp_path, aliases)
    monkeypatch.setenv("PLATFORM_PATH", str(tmp_path))
    load = Mock(wraps=app_data.load_app_data_contract)
    monkeypatch.setattr(app_data, "load_app_data_contract", load)

    view = app_data.AppData(collection_resolver=lambda name: name)

    assert dict(view.aliases) == aliases
    for key, value in aliases.items():
        assert view.collection(key) == value
    assert load.call_count == 1
    with pytest.raises(TypeError):
        view.aliases["injected"] = "other"


def test_new_views_follow_contract_edits_and_workspace_switches(monkeypatch, tmp_path):
    first_root, second_root = tmp_path / "first", tmp_path / "second"
    _workspace(first_root, {"items": "first_items"})
    _workspace(second_root, {"items": "second_items"})
    monkeypatch.setenv("PLATFORM_PATH", str(first_root))
    original = app_data.AppData()

    _workspace(first_root, {"items": "updated_items", "extra": "new_items"})
    updated = app_data.AppData()
    monkeypatch.setenv("PLATFORM_PATH", str(second_root))
    switched = app_data.AppData()

    assert dict(original.aliases) == {"items": "first_items"}
    assert dict(updated.aliases) == {"items": "updated_items", "extra": "new_items"}
    assert dict(switched.aliases) == {"items": "second_items"}
    assert app_data.DATA_COLLECTIONS["items"] == "second_items"


def test_explicit_aliases_and_contract_do_not_read_defaults(monkeypatch):
    load = Mock(side_effect=AssertionError("default contract must not be read"))
    monkeypatch.setattr(app_data, "load_app_data_contract", load)
    aliases = {"items": "explicit_items"}
    contract = {"aliases": [{"alias": "items", "collection": "contract_items"}]}
    explicit = app_data.AppData(aliases=aliases, contract=contract)
    from_contract = app_data.AppData(contract=contract)
    aliases["items"] = "changed"
    contract["aliases"][0]["collection"] = "changed"

    assert explicit.collection_name_for_alias("items") == "explicit_items"
    assert from_contract.collection_name_for_alias("items") == "contract_items"
    load.assert_not_called()


def test_empty_explicit_inputs_keep_default_resolution(monkeypatch, tmp_path):
    _workspace(tmp_path, {"items": "default_items"})
    monkeypatch.setenv("PLATFORM_PATH", str(tmp_path))
    view = app_data.AppData(aliases={}, contract={})
    assert view.collection_name_for_alias("items") == "default_items"


@pytest.mark.parametrize("invalid", [False, True])
def test_missing_or_invalid_default_remains_empty_and_next_view_can_recover(monkeypatch, tmp_path, invalid):
    contract_path = tmp_path / "contract.json"
    if invalid:
        contract_path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(app_data, "default_data_contract_path", lambda app_root=None: contract_path)
    empty = app_data.AppData()
    assert dict(empty.aliases) == {}
    with pytest.raises(app_data.AppDataAliasError):
        empty.collection_name_for_alias("items")

    contract_path.write_text('{"aliases": [{"alias": "items", "collection": "repaired_items"}]}', encoding="utf-8")
    repaired = app_data.AppData()
    assert repaired.collection_name_for_alias("items") == "repaired_items"
    assert dict(empty.aliases) == {}
