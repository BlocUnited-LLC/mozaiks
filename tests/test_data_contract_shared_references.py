"""Shared references preserve one owned declaration across generation and runtime."""

from copy import deepcopy

import pytest

from factory_app.workflows.AppGenerator.tools.assembly_phase import _carried_contract_conflict
from factory_app.workflows.DesignDocs.tools.save_design_doc import _canonical_data_contract
from mozaiksai.core.runtime.persistence.app_data import aliases_from_data_contract
from mozaiksai.core.runtime.persistence.intent_loader import (
    index_data_contract_by_entity,
    validate_complete_data_contract_ownership,
)
from mozaiksai.core.runtime.persistence.platform_modules import PlatformModuleDeclarations
from mozaiksai.core.workflow.generator_support.code_files import compile_data_contract


def contract():
    surfaces = []
    for owner, name in (("catalog", "record"), ("work_queue", "job")):
        surfaces.append({
            "surface_id": owner, "surface_kind": "module", "collections": [{
                "name": name, "entity": name, "mongo_collection": f"{owner}_{name}s",
                "data_alias": f"{owner}.{name}s", "owner_module": owner,
                "scope": "app", "tenancy": "app_wide", "owner_field": None,
                "ownership": {"surface_id": owner, "surface_kind": "module"},
                "fields": [{"name": "record_id", "type": "string", "required": True}],
                "indexes": [{"name": "record_id_unique", "keys": [{"field": "record_id", "order": 1}], "unique": True}],
                "lifecycle": {"write_mode": "module_action", "migration_policy": "additive_only"},
            }],
        })
    return {
        "version": "1", "surfaces": surfaces,
        "aliases": [{"alias": f"{owner}.{name}s", "collection": f"{owner}_{name}s", "owner_module": owner}
                    for owner, name in (("catalog", "record"), ("work_queue", "job"))],
        "shared_collections": [{
            "owner_module": "catalog", "mongo_collection": "catalog_records", "primary_alias": "catalog.records",
            "read_by": [{"module": "audit_reader", "access": "read_only", "purpose": "record correlation"}],
            "notes": "Catalog owns these records; audit reads them.",
        }],
    }


def test_reference_is_not_a_collection_definition():
    value = contract()
    before = deepcopy(value)
    validate_complete_data_contract_ownership(value)
    assert set(index_data_contract_by_entity(value)) == {("catalog", "record"), ("work_queue", "job")}
    assert value == before


def test_compilation_preserves_aliases_grant_and_idempotence():
    contribution = contract()
    empty = {"version": "1", "surfaces": [], "shared_collections": []}
    compiled = compile_data_contract(empty, pack_data_contracts=[contribution])
    assert compiled == contribution
    assert compile_data_contract(compiled, pack_data_contracts=[contribution]) == compiled


@pytest.mark.parametrize("grant_field", ["read_by", "shared_with"])
@pytest.mark.parametrize("access", ["read_olny", "read_write", "read", "support_thread_lifecycle", "", True])
def test_compiler_rejects_unknown_shared_access_without_changing_input(grant_field, access):
    contribution = contract()
    entry = contribution["shared_collections"][0]
    entry[grant_field] = entry.pop("read_by")
    entry[grant_field][0]["access"] = access
    before = deepcopy(contribution)
    with pytest.raises(ValueError):
        compile_data_contract({"version": "1", "surfaces": [], "shared_collections": []},
                              pack_data_contracts=[contribution])
    assert contribution == before


@pytest.mark.parametrize("grant_field", ["read_by", "shared_with"])
@pytest.mark.parametrize("declarations", [
    {"module": "reader", "access": "read_only"}, "reader", True,
    [None], ["reader"], [{}], [{"module": None}], [{"module": 3}], [{"module": ""}], [{"module": " "}],
])
def test_compiler_rejects_malformed_grants_without_changing_input(grant_field, declarations):
    contribution = contract()
    entry = contribution["shared_collections"][0]
    entry.pop("read_by")
    entry[grant_field] = declarations
    before = deepcopy(contribution)
    with pytest.raises(ValueError):
        compile_data_contract({"version": "1", "surfaces": [], "shared_collections": []},
                              pack_data_contracts=[contribution])
    assert contribution == before


def test_design_normalization_preserves_root_aliases():
    value = contract()
    surface_map = {"surfaces": [{"surface_id": row["surface_id"], "surface_kind": "module"}
                                for row in value["surfaces"]]}
    saved = _canonical_data_contract(value, app_id="test-app", artifact_version_id=None, surface_map=surface_map)
    assert saved["aliases"] == value["aliases"]
    assert saved["shared_collections"] == value["shared_collections"]


def test_equal_carried_grant_is_not_dropped_app_data():
    import json
    value = contract()
    assert _carried_contract_conflict(json.dumps(value), value) is None


def test_existing_runtime_grantee_and_alias_behavior():
    value = contract()
    mounted = PlatformModuleDeclarations(value, ["catalog", "work_queue", "audit_reader"])
    reader = mounted.access_for("audit_reader", None)
    assert reader.literal_names == frozenset({"catalog_records"})
    assert "catalog_records" not in mounted.access_for("work_queue", None).literal_names
    assert aliases_from_data_contract(value) == {"catalog.records": "catalog_records", "work_queue.jobs": "work_queue_jobs"}


def test_real_partial_definition_still_fails():
    value = contract()
    value["shared_collections"][0]["fields"] = []
    with pytest.raises(ValueError):
        validate_complete_data_contract_ownership(value)


@pytest.mark.parametrize("change", [
    {"owner_module": None}, {"owner_module": "work_queue"}, {"mongo_collection": "unknown"},
    {"name": "job"}, {"unexpected": True}, {"fields": []}, {"indexes": []},
    {"scope": "app"}, {"tenancy": "app_wide"}, {"ownership": {"surface_id": "catalog"}},
    {"read_by": []}, {"read_by": "audit_reader"}, {"read_by": False},
    {"read_by": [{"module": "audit_reader", "access": "read_write"}]},
    {"read_by": [{"module": "audit_reader", "permissions": ["write"]}]},
    {"read_by": [{"module": ""}]}, {"read_by": [{"module": 3}]},
    {"primary_alias": "work_queue.jobs"},
    {"mongo_collection": None, "name": "record"},
])
def test_malformed_or_conflicting_reference_is_rejected(change):
    value = contract()
    value["shared_collections"][0].update(change)
    with pytest.raises(ValueError):
        validate_complete_data_contract_ownership(value)


def test_logical_and_literal_selectors_must_identify_one_declared_owner():
    value = contract()
    reference = value["shared_collections"][0]
    reference["name"] = "record"
    validate_complete_data_contract_ownership(value)
    reference.pop("mongo_collection")
    reference.pop("primary_alias")
    validate_complete_data_contract_ownership(value)
    duplicate = deepcopy(value["surfaces"][0])
    duplicate["surface_id"] = "foreign"
    duplicate["collections"][0].update(ownership={"surface_id": "foreign", "surface_kind": "module"}, owner_module="foreign", data_alias=None)
    value["surfaces"].append(duplicate)
    with pytest.raises(ValueError):
        validate_complete_data_contract_ownership(value)


@pytest.mark.parametrize("change", [
    {"collection": "work_queue_jobs"}, {"owner_module": "work_queue"},
    {"collection": "unknown"}, {"alias": ""}, {"hidden_target": "other"},
])
def test_alias_must_not_redirect_or_impersonate_an_owner(change):
    value = contract()
    value["aliases"][0].update(change)
    with pytest.raises(ValueError):
        validate_complete_data_contract_ownership(value)


def test_full_shared_definition_keeps_all_ownership_requirements():
    value = contract()
    value["shared_collections"] = [value["surfaces"][1]["collections"].pop()]
    validate_complete_data_contract_ownership(value)
    assert ("work_queue", "job") in index_data_contract_by_entity(value)
    value["shared_collections"][0].pop("entity")
    value["shared_collections"][0]["read_by"] = [{"module": "audit_reader"}]
    with pytest.raises(ValueError):
        validate_complete_data_contract_ownership(value)


@pytest.mark.parametrize("field", ["read_by", "mongo_collection", "primary_alias"])
def test_conflicting_pack_reference_cannot_replace_existing_grant(field):
    value = contract()
    changed = deepcopy(value)
    if field == "read_by":
        changed["shared_collections"][0][field] = [{"module": "another_reader", "access": "read_only"}]
    elif field == "mongo_collection":
        changed["shared_collections"][0][field] = "work_queue_jobs"
    else:
        changed["shared_collections"][0][field] = "work_queue.jobs"
    before = deepcopy(value)
    with pytest.raises(ValueError):
        compile_data_contract(value, pack_data_contracts=[changed])
    assert value == before


def test_pack_cannot_grant_or_alias_a_foreign_collection_from_the_final_contract():
    value = contract()
    reader_only = {"version": "1", "surfaces": [], "shared_collections": value["shared_collections"]}
    with pytest.raises(ValueError):
        compile_data_contract(value, pack_data_contracts=[reader_only])
    alias_only = {"version": "1", "surfaces": [], "aliases": [{"alias": "foreign", "collection": "catalog_records"}]}
    with pytest.raises(ValueError):
        compile_data_contract(value, pack_data_contracts=[alias_only])


@pytest.mark.parametrize("alteration", ["dropped", "broadened", "retargeted"])
def test_carried_reader_access_requires_exact_reconciliation(alteration):
    import json
    carried = contract()
    composed = deepcopy(carried)
    if alteration == "dropped":
        composed["shared_collections"] = []
    elif alteration == "broadened":
        composed["shared_collections"][0]["read_by"].append({"module": "another_reader", "access": "read_only"})
    else:
        composed["shared_collections"][0]["mongo_collection"] = "work_queue_jobs"
    assert _carried_contract_conflict(json.dumps(carried), composed) is not None


def test_schema_optional_nulls_do_not_duplicate_the_same_pack_reference():
    import json

    from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs

    contribution = contract()
    approved = deepcopy(contribution)
    models, _ = load_workflow_structured_outputs("DesignDocs")
    approved["shared_collections"] = [models["DataContractSharedReference"].model_validate(
        approved["shared_collections"][0],
    ).model_dump(mode="json")]
    compiled = compile_data_contract(approved, pack_data_contracts=[contribution])
    assert len(compiled["shared_collections"]) == 1
    assert _carried_contract_conflict(json.dumps(approved), contribution) is None


def test_reader_reference_does_not_count_as_a_second_assignment_collection():
    from tests.test_subscription_assignment_data_contract import _subscription_contract

    value = contract()
    compiled = compile_data_contract(value, subscription_contract=_subscription_contract("catalog.records"))
    assert compiled == value


def test_reference_alias_cannot_be_repointed_to_a_new_assignment_store():
    from tests.test_subscription_assignment_data_contract import _subscription_contract

    value = contract()
    value["shared_collections"][0]["primary_alias"] = "reader.lookup"
    with pytest.raises(ValueError, match="conflicts with an existing alias"):
        compile_data_contract(value, subscription_contract=_subscription_contract("reader.lookup"))


def test_logical_reference_cannot_invent_a_runtime_alias_without_a_literal_target():
    value = contract()
    reference = value["shared_collections"][0]
    reference.pop("mongo_collection")
    reference.pop("primary_alias")
    reference["name"] = "record"
    reference["read_by"][0]["data_alias"] = "reader.records"
    with pytest.raises(ValueError, match="explicit literal collection"):
        validate_complete_data_contract_ownership(value)


def test_alias_collision_uses_the_runtime_trimmed_identity():
    value = contract()
    value["aliases"].append({"alias": " catalog.records ", "collection": "work_queue_jobs"})
    assert aliases_from_data_contract(value)["catalog.records"] == "catalog_records"
    # Without the reference's last alias write, runtime resolves the conflicting
    # padded root alias to the other collection. Validation must reject either order.
    value["shared_collections"][0].pop("primary_alias")
    assert aliases_from_data_contract(value)["catalog.records"] == "work_queue_jobs"
    with pytest.raises(ValueError, match="Conflicting data alias"):
        validate_complete_data_contract_ownership(value)


def test_padded_literal_cannot_hide_a_second_physical_owner():
    value = contract()
    duplicate = value["surfaces"][1]["collections"][0]
    duplicate.update(mongo_collection=" catalog_records ", data_alias=None)
    value["aliases"] = []
    with pytest.raises(ValueError, match="conflicting collection owners"):
        validate_complete_data_contract_ownership(value)


@pytest.mark.parametrize("alias_source", ["primary_alias", "read_by", "shared_with"])
def test_full_shared_definition_cannot_redirect_a_reference_alias(alias_source):
    value = contract()
    full_definition = value["surfaces"][1]["collections"].pop()
    if alias_source == "primary_alias":
        full_definition[alias_source] = "catalog.records"
    else:
        full_definition[alias_source] = [{"module": "audit_reader", "data_alias": "catalog.records"}]
    value["shared_collections"].append(full_definition)
    assert aliases_from_data_contract(value)["catalog.records"] == "work_queue_jobs"
    with pytest.raises(ValueError, match="Conflicting data alias"):
        validate_complete_data_contract_ownership(value)
