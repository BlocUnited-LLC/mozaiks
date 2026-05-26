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
        "app_name": "Shared Persistence Demo",
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


def _shared_persistence_contract() -> dict:
    return {
        "version": "1",
        "mode": "app_shared_contracts",
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


def test_file_contracts_define_shared_persistence_as_opt_in_generic_lane() -> None:
    contract_path = ROOT / "factory_app" / "workflows" / "AppGenerator" / "tools" / "file_contracts.yaml"
    contracts = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    persistence_contract = contracts["task_contracts"]["persistence_contract"]
    text = json.dumps(persistence_contract, sort_keys=True)

    assert "config/shared_persistence.json" in persistence_contract["optional_outputs"]
    assert "shared_persistence/persistence.py" in persistence_contract["optional_outputs"]
    assert "shared_persistence/proposals.py" in persistence_contract["optional_outputs"]
    assert "opt-in only" in text
    assert "ctx.persistence.collection(module_id, entity_name)" in text
    assert "app/shared" in text
    assert "platform_persistence" in text
    assert "HostSystemPersistence" in text
    assert "normal generated apps" in text


def test_save_app_schema_omits_shared_persistence_by_default(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)

    result = save_app_schema_module.save_app_schema(
        manifest=_base_manifest(),
        pages=[_base_page()],
        context_variables=_Context(),
    )

    assert not (tmp_path / "config" / "shared_persistence.json").exists()
    assert "Shared persistence: no" in result


def test_save_app_schema_writes_shared_persistence_from_context(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)
    context = _Context({"shared_persistence_contract": _shared_persistence_contract()})

    result = save_app_schema_module.save_app_schema(
        manifest=_base_manifest(),
        pages=[_base_page()],
        context_variables=context,
    )

    contract = json.loads((tmp_path / "config" / "shared_persistence.json").read_text(encoding="utf-8"))
    assert contract["mode"] == "app_shared_contracts"
    assert contract["aliases"][0]["alias"] == "orders.lifecycle"
    assert context.data["app_shared_persistence_contract"]["aliases"][0]["collection"] == "orders"
    assert "config/shared_persistence.json" in result
    assert "Shared persistence: yes" in result


@pytest.mark.parametrize(
    ("contract", "match"),
    [
        ({"version": "1", "mode": "generated_scoped", "aliases": []}, "mode"),
        ({"version": "1", "mode": "app_shared_contracts", "aliases": [{"alias": "x"}]}, "collection"),
        ({"version": "1", "mode": "app_shared_contracts", "aliases": [{"alias": "x", "collection": "c"}]}, "owner_module"),
    ],
)
def test_shared_persistence_contract_validation_rejects_invalid_shapes(contract: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        save_app_schema_module._validate_shared_persistence_contract(contract)


def test_structured_outputs_expose_shared_persistence_contract() -> None:
    structured_outputs = (
        ROOT / "factory_app" / "workflows" / "AppGenerator" / "structured_outputs.yaml"
    ).read_text(encoding="utf-8")

    assert "shared_persistence_contract" in structured_outputs
    assert "config/shared_persistence.json" in structured_outputs
    assert "shared_persistence_json" in structured_outputs
    assert "app/shared" in structured_outputs
    assert "platform_persistence" in structured_outputs


def test_shared_persistence_helpers_are_promotable_app_artifacts() -> None:
    assert "shared_persistence" in save_app_schema_module.PROMOTABLE_APP_ENTRIES


def test_shared_persistence_architecture_doc_exists() -> None:
    doc = (ROOT / "docs" / "architecture" / "app" / "shared-persistence-contracts.md").read_text(
        encoding="utf-8"
    )

    assert "ctx.persistence.collection(module_id, entity_name)" in doc
    assert "config/shared_persistence.json" in doc
    assert "Do not generate hosted-product-specific names" in doc
    assert "shared_persistence/proposals.py" in doc
    assert "- `shared`" in doc
    assert "platform_persistence" in doc
