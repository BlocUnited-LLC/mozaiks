"""The real structured model/save/export path retains shared references and aliases."""

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from mozaiksai.core.runtime.persistence.app_data import aliases_from_data_contract
from mozaiksai.core.runtime.persistence.intent_loader import (
    index_data_contract_by_entity,
    load_data_contract,
)
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.code_files import (
    compile_data_contract,
    materialize_data_contract,
)
from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs
from tests import test_designdocs_data_ownership as design

persistence = design.persistence


def bundle():
    value = design._bundle()
    collection = value["data_contract"]["surfaces"][0]["collections"][0]
    collection.update(mongo_collection="review_documents", data_alias="reports.documents", owner_module="reports")
    value["data_contract"]["aliases"] = [{
        "alias": "reports.documents", "collection": "review_documents", "owner_module": "reports",
    }]
    value["data_contract"]["shared_collections"] = [{
        "owner_module": "reports", "mongo_collection": "review_documents", "primary_alias": "reports.documents",
        "read_by": [{"module": "audit_reader", "access": "read_only", "purpose": "correlation"}],
    }]
    return value


def test_typed_design_save_export_and_runtime_load_preserve_reference(persistence, tmp_path):
    context = design.ownership._context(managed=False)
    models, _ = load_workflow_structured_outputs("DesignDocs")
    validated = models["DesignDocsBundle"].model_validate(bundle()).model_dump(mode="json")
    result = design.inventory._save(context, validated)
    assert result["outcome"] == "saved", result
    saved = detach(context.get("data_contract"))
    assert saved["aliases"] == validated["data_contract"]["aliases"]
    assert saved["shared_collections"] == validated["data_contract"]["shared_collections"]
    assert set(index_data_contract_by_entity(saved)) == {("reports", "Report"), ("reports", "ReportNote")}
    exported = materialize_data_contract({}, data_contract=saved)
    (tmp_path / "data").mkdir()
    (tmp_path / "data/contract.json").write_text(exported["data/contract.json"], encoding="utf-8")
    loaded = load_data_contract(tmp_path)
    assert loaded == json.loads(exported["data/contract.json"])
    assert aliases_from_data_contract(loaded) == {"reports.documents": "review_documents"}
    assert loaded["shared_collections"] == saved["shared_collections"]


@pytest.mark.parametrize("field,value", [("fields", []), ("tenancy", "app_wide"), ("ownership", {}), ("unknown", True)])
def test_structured_reference_cannot_hide_partial_collection_metadata(field, value):
    models, _ = load_workflow_structured_outputs("DesignDocs")
    candidate = bundle()
    candidate["data_contract"]["shared_collections"][0][field] = value
    with pytest.raises(ValidationError):
        models["DesignDocsBundle"].model_validate(candidate)


@pytest.mark.parametrize("grant_field", ["read_by", "shared_with"])
@pytest.mark.parametrize("access", ["read_olny", "read_write", "read", "support_thread_lifecycle", "", True])
def test_typed_design_rejects_unknown_shared_access(grant_field, access):
    models, _ = load_workflow_structured_outputs("DesignDocs")
    candidate = bundle()
    entry = candidate["data_contract"]["shared_collections"][0]
    entry[grant_field] = entry.pop("read_by")
    entry[grant_field][0]["access"] = access
    with pytest.raises(ValidationError):
        models["DesignDocsBundle"].model_validate(candidate)


@pytest.mark.parametrize("grant_field", ["read_by", "shared_with"])
@pytest.mark.parametrize("access", [None, "absent", "read_only"])
def test_typed_design_preserves_supported_shared_access(grant_field, access):
    models, _ = load_workflow_structured_outputs("DesignDocs")
    candidate = bundle()
    entry = candidate["data_contract"]["shared_collections"][0]
    entry[grant_field] = entry.pop("read_by")
    if access == "absent":
        entry[grant_field][0].pop("access")
    else:
        entry[grant_field][0]["access"] = access
    parsed = models["DesignDocsBundle"].model_validate(candidate).model_dump(mode="json")
    assert parsed["data_contract"]["shared_collections"][0][grant_field][0]["access"] == (
        None if access == "absent" else access
    )


@pytest.mark.parametrize("grant_field", ["read_by", "shared_with"])
@pytest.mark.parametrize("declarations", [
    {"module": "reader", "access": "read_only"}, "reader", True,
    [None], ["reader"], [{}], [{"module": None}], [{"module": 3}],
])
def test_typed_design_rejects_malformed_grant_structure(grant_field, declarations):
    models, _ = load_workflow_structured_outputs("DesignDocs")
    candidate = bundle()
    entry = candidate["data_contract"]["shared_collections"][0]
    entry.pop("read_by")
    entry[grant_field] = declarations
    with pytest.raises(ValidationError):
        models["DesignDocsBundle"].model_validate(candidate)


@pytest.mark.parametrize("grant_field", ["read_by", "shared_with"])
@pytest.mark.parametrize("module", ["", " "])
def test_design_compilation_rejects_blank_receiver_after_structural_validation(grant_field, module):
    models, _ = load_workflow_structured_outputs("DesignDocs")
    candidate = bundle()
    entry = candidate["data_contract"]["shared_collections"][0]
    entry[grant_field] = entry.pop("read_by")
    entry[grant_field][0]["module"] = module
    parsed = models["DesignDocsBundle"].model_validate(candidate).model_dump(mode="json")
    with pytest.raises(ValueError, match="module"):
        compile_data_contract(parsed["data_contract"])


def test_unresolved_reference_does_not_persist_or_infer_a_new_owner(persistence):
    _, store_factory, _ = persistence
    context = design.ownership._context(managed=False)
    candidate = bundle()
    candidate["data_contract"]["shared_collections"][0]["owner_module"] = "foreign"
    before = deepcopy(candidate)
    result = design.inventory._save(context, candidate)
    assert result["outcome"] == "revise"
    assert "exactly one collection" in result["error"]
    assert context.get("data_contract") is None
    store_factory.assert_not_called()
    assert candidate == before


@pytest.mark.parametrize("pack_nulls", [False, True])
def test_typed_save_then_equivalent_pack_preserves_storage_decisions(persistence, pack_nulls):
    context = design.ownership._context(managed=False)
    models, _ = load_workflow_structured_outputs("DesignDocs")
    candidate = bundle()
    candidate["data_contract"]["surfaces"][0]["collections"][1].update(tenancy="app_wide", owner_field=None)
    candidate["data_contract"]["aliases"][0].pop("owner_module")
    validated = models["DesignDocsBundle"].model_validate(candidate).model_dump(mode="json")
    result = design.inventory._save(context, validated)
    assert result["outcome"] == "saved", result
    saved = detach(context.get("data_contract"))
    pack = deepcopy({key: saved[key] for key in ("version", "surfaces", "aliases", "shared_collections")})
    collection = pack["surfaces"][0]["collections"][1]
    for field in ("mongo_collection", "data_alias", "owner_module"):
        if pack_nulls:
            collection[field] = None
        else:
            collection.pop(field, None)
    if pack_nulls:
        pack["aliases"][0]["owner_module"] = None
    else:
        pack["aliases"][0].pop("owner_module", None)
    before = deepcopy(saved)
    compiled = compile_data_contract(saved, pack_data_contracts=[pack])
    assert compiled == saved == before
    assert compile_data_contract(compiled, pack_data_contracts=[pack]) == compiled
    first, second = saved["surfaces"][0]["collections"]
    assert {field: first[field] for field in ("mongo_collection", "data_alias", "owner_module")} == {
        "mongo_collection": "review_documents", "data_alias": "reports.documents", "owner_module": "reports",
    }
    assert second["owner_field"] is None
    assert not {"mongo_collection", "data_alias", "owner_module"} & second.keys()
    assert "owner_module" not in saved["aliases"][0]


@pytest.mark.parametrize("field", ["mongo_collection", "data_alias"])
def test_optional_null_cleanup_does_not_hide_unknown_root_alias_fields(persistence, field):
    _, store_factory, _ = persistence
    context = design.ownership._context(managed=False)
    candidate = bundle()
    candidate["data_contract"]["aliases"][0][field] = None
    result = design.inventory._save(context, candidate)
    assert result["outcome"] == "revise"
    assert "permits only alias, collection, and owner_module" in result["error"]
    store_factory.assert_not_called()
