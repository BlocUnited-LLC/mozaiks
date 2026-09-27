from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from tests.factory_context import factory_context

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
        "schema_version": "mozaiks.app_page.v1",
        "name": "Dashboard",
        "route": "/dashboard",
        "title": "Dashboard",
        "page_type": "record_list",
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


def test_file_contracts_require_data_contract_for_persistent_generated_modules() -> None:
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
    assert "Generate data/contract.json for persistent generated modules" in text
    assert "owner_field" in text
    assert "tenancy" in text
    assert "deterministic policy/read construction" in text
    assert "opt-in only" not in text
    assert "ctx.persistence.collection(module_id, entity_name)" in text
    assert "app/data is declarative only" in text
    assert "documented alias exclusions" in text
    assert "data/contract.json" in text


def test_save_app_schema_omits_data_contract_by_default(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)

    result = save_app_schema_module.save_app_schema(
        manifest=_base_manifest(),
        pages=[_base_page()],
        context_variables=ContextVariablesBridge(factory_context()),
    )

    assert not (tmp_path / "config" / "data.json").exists()
    assert "Data contract: no" in result


def test_save_app_schema_writes_data_contract_from_context(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)
    context = ContextVariablesBridge(factory_context({"data_contract": _data_contract()}))

    result = save_app_schema_module.save_app_schema(
        manifest=_base_manifest(),
        pages=[_base_page()],
        context_variables=context,
    )

    contract = json.loads((tmp_path / "data" / "contract.json").read_text(encoding="utf-8"))
    assert contract["mode"] == "app_data_contract"
    assert contract["aliases"][0]["alias"] == "orders.lifecycle"
    assert context.get("data_contract")["aliases"][0]["collection"] == "orders"
    assert "data/contract.json" in result
    assert "Data contract: yes" in result


@pytest.mark.parametrize(
    ("contract", "match"),
    [
        ({"version": "1", "aliases": []}, "surfaces"),
        ({"version": "1", "surfaces": [], "mode": "app_data_contract", "aliases": [{"alias": "x"}]}, "collection"),
        ({"version": "1", "surfaces": [], "mode": "app_data_contract", "aliases": [{"alias": "x", "collection": "c"}]}, "owner_module"),
        ({"version": "1", "surfaces": [], "mode": "app_data_contract", "aliases": [], "shared_collections": "orders"}, "shared_collections"),
        ({"version": "1", "surfaces": [], "shared_collections": [{"name": "orders", "entity": "Order"}]}, "scope"),
        ({"version": "1", "surfaces": [], "shared_collections": [{
            "name": "orders", "entity": "Order", "scope": "app", "tenancy": "app_wide", "owner_field": None, "fields": [],
        }]}, "ownership"),
    ],
)
def test_data_contract_validation_rejects_invalid_shapes(contract: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        save_app_schema_module._validate_data_contract(contract)


def test_only_design_docs_exposes_data_contract_model() -> None:
    app_models = yaml.safe_load((ROOT / "factory_app/workflows/AppGenerator/structured_outputs.yaml").read_text(encoding="utf-8"))["models"]
    design_models = yaml.safe_load((ROOT / "factory_app/workflows/DesignDocs/structured_outputs.yaml").read_text(encoding="utf-8"))["models"]
    assert "DataContractCollection" in design_models
    assert "DataContractCollection" not in app_models
    assert "data_contract" not in app_models["AppBuildPlan"]["fields"]
    assert "data_contract" not in app_models["AppSchemaOutput"]["fields"]


def test_config_is_the_promotable_data_contract_entry() -> None:
    assert "provenance.yaml" in save_app_schema_module.PROMOTABLE_APP_ENTRIES
    assert "config" in save_app_schema_module.PROMOTABLE_APP_ENTRIES
    assert "data" in save_app_schema_module.PROMOTABLE_APP_ENTRIES
    assert "security" in save_app_schema_module.PROMOTABLE_APP_ENTRIES
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


