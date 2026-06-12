from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def _load_save_app_schema_module():
    file_path = ROOT / "factory_app" / "workflows" / "AppGenerator" / "tools" / "save_app_schema.py"
    spec = importlib.util.spec_from_file_location("tests.appgenerator_save_app_schema_shared", file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


save_app_schema_module = _load_save_app_schema_module()


class _Context:
    def __init__(self, initial=None) -> None:
        self.data = dict(initial or {})

    def set(self, key, value) -> None:
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)


def _base_manifest() -> dict:
    return {
        "app_name": "Data Contract Demo",
        "version": "1.0.0",
        "default_route": "/dashboard",
        "pages": ["Dashboard"],
        "custom_routes": [],
    }


def _base_page() -> dict:
    return {
        "name": "Dashboard",
        "route": "/dashboard",
        "title": "Dashboard",
        "layout": "grid",
        "sections": [{"id": "overview", "primitive": "Panel", "config": {"title": "Overview"}}],
    }


def _data_contract() -> dict:
    return {
        "version": "1",
        "mode": "app_data_contract",
        "surfaces": [],
        "aliases": [
            {
                "alias": "orders.lifecycle",
                "collection": "orders",
                "owner_module": "orders",
                "access": "lifecycle_update",
            }
        ],
        "shared_collections": [],
    }


def test_file_contracts_define_data_contract_as_opt_in_generic_lane() -> None:
    contract_path = (
        ROOT
        / "factory_app"
        / "build_context"
        / "AppGenerator"
        / "file_contracts.yaml"
    )
    contracts = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    persistence_contract = contracts["task_contracts"]["persistence_contract"]
    text = json.dumps(persistence_contract, sort_keys=True)

    assert "data/contract.json" in persistence_contract["required_outputs"]
    assert "data/migrations/{migration_id}.json" in persistence_contract["optional_outputs"]
    assert "opt-in only" in text
    assert "ctx.persistence.collection(module_id, entity_name)" in text
    assert "app/data is declarative only" in text
    assert "documented alias exclusions" in text
    assert "data/contract.json" in text


def test_save_app_schema_omits_data_contract_by_default(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)

    result = save_app_schema_module.save_app_schema(
        manifest=_base_manifest(),
        pages=[_base_page()],
        context_variables=_Context(),
    )

    assert not (tmp_path / "config" / "data.json").exists()
    assert "Data contract: no" in result


def test_save_app_schema_writes_data_contract_from_context(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)
    context = _Context({"data_contract": _data_contract()})

    result = save_app_schema_module.save_app_schema(
        manifest=_base_manifest(),
        pages=[_base_page()],
        context_variables=context,
    )

    contract = json.loads((tmp_path / "data" / "contract.json").read_text(encoding="utf-8"))
    assert contract["mode"] == "app_data_contract"
    assert contract["aliases"][0]["alias"] == "orders.lifecycle"
    assert context.data["app_data_contract"]["aliases"][0]["collection"] == "orders"
    assert "data/contract.json" in result
    assert "Data contract: yes" in result


@pytest.mark.parametrize(
    ("contract", "match"),
    [
        ({"version": "1", "aliases": []}, "surfaces"),
        ({"version": "1", "surfaces": [], "mode": "app_data_contract", "aliases": [{"alias": "x"}]}, "collection"),
        ({"version": "1", "surfaces": [], "mode": "app_data_contract", "aliases": [{"alias": "x", "collection": "c"}]}, "owner_module"),
        ({"version": "1", "surfaces": [], "mode": "app_data_contract", "aliases": [], "shared_collections": "orders"}, "shared_collections"),
    ],
)
def test_data_contract_validation_rejects_invalid_shapes(contract: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        save_app_schema_module._validate_data_contract(contract)


def test_structured_outputs_expose_data_contract() -> None:
    structured_outputs = (
        ROOT / "factory_app" / "workflows" / "AppGenerator" / "structured_outputs.yaml"
    ).read_text(encoding="utf-8")

    assert "data_contract" in structured_outputs
    assert "shared_collections" in structured_outputs
    assert "data/contract.json" in structured_outputs
    assert "data_contract_json" in structured_outputs
    assert "services/data/" in structured_outputs


def test_config_is_the_promotable_data_contract_entry() -> None:
    assert "config" in save_app_schema_module.PROMOTABLE_APP_ENTRIES
    assert "services" in save_app_schema_module.PROMOTABLE_APP_ENTRIES
    assert "services/data" not in save_app_schema_module.PROMOTABLE_APP_ENTRIES


def test_data_contract_architecture_doc_exists() -> None:
    doc = (ROOT / "docs" / "architecture" / "app" / "data-contracts.md").read_text(
        encoding="utf-8"
    )

    assert "ctx.persistence.collection(module_id, entity_name)" in doc
    assert "app/data/contract.json" in doc
    assert "documented_alias_exclusions" in doc
    assert "strict structured outputs" in doc
    assert "data/migrations/{migration_id}.json" in doc


