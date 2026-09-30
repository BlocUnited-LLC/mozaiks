"""The real frozen save boundary owns typed collection and custom read decisions."""

from copy import deepcopy

import pytest

from mozaiksai.core.runtime.persistence.intent_loader import index_data_contract_by_entity
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs
from tests import test_designdocs_capability_ownership as ownership
from tests import test_designdocs_monetization_inventory as inventory

persistence = inventory.persistence


def _bundle():
    bundle = inventory._bundle(pricing=False)
    surface = bundle["surface_map"]["surfaces"][0]
    surface.update(primary_entities=["Report", "ReportNote"], owned_mutations=["edit_report"], custom_reads=["summarize_reports"])
    bundle["data_contract"]["surfaces"][0]["collections"] = [{
        **ownership._collection("reports", "report_documents"),
        "entity": "Report", "tenancy": "per_user", "owner_field": "author_id",
        "fields": [{"name": "author_id", "type": "string", "required": True}],
    }, {
        **ownership._collection("reports", "report_notes"),
        "entity": "ReportNote", "tenancy": "per_workspace", "owner_field": "team_id",
        "fields": [{"name": "team_id", "type": "string", "required": True}],
    }]
    return bundle


def test_approved_contract_preserves_explicit_entities_owners_and_custom_reads(persistence):
    context = ownership._context(managed=False)
    bundle = _bundle()
    models, _ = load_workflow_structured_outputs("DesignDocs")
    validated = models["DesignDocsBundle"].model_validate(bundle).model_dump(mode="json")
    result = inventory._save(context, validated)
    assert result["outcome"] == "saved", result
    contract = detach(context.get("data_contract"))
    index = index_data_contract_by_entity(contract)
    assert index[("reports", "Report")]["name"] == "report_documents"
    assert index[("reports", "ReportNote")]["owner_field"] == "team_id"
    assert detach(context.get("design_surface_map"))["surfaces"][0]["custom_reads"] == ["summarize_reports"]


@pytest.mark.parametrize("changes,expected", [
    ({"tenancy": None}, "['per_user', 'per_workspace', 'app_wide']"),
    ({"owner_field": "user_id"}, "['author_id']"),
    ({"entity": "Reports"}, "['Report', 'ReportNote']"),
    ({"tenancy": "app_wide"}, "owner_field must be null"),
])
def test_ambiguous_or_invalid_ownership_returns_exact_choices_without_persisting(persistence, changes, expected):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = _bundle()
    bundle["data_contract"]["surfaces"][0]["collections"][0].update(changes)
    result = inventory._save(context, bundle)
    assert result["outcome"] == "revise"
    assert expected in result["error"]
    assert context.get("data_contract") is None
    store_factory.assert_not_called()


@pytest.mark.parametrize("missing", ["entity", "tenancy", "owner_field"])
def test_factory_save_still_requires_complete_ownership_metadata(persistence, missing):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = _bundle()
    bundle["data_contract"]["surfaces"][0]["collections"][0].pop(missing)
    result = inventory._save(context, bundle)
    assert result["outcome"] == "revise", result
    assert missing in result["error"]
    assert context.get("data_contract") is None
    store_factory.assert_not_called()


def test_identity_storage_under_business_surface_normalizes_before_new_fields(persistence):
    context = ownership._context(managed=False)
    bundle = _bundle()
    identity = ownership._collection("reports", "users")
    for field in ("entity", "tenancy", "owner_field"):
        identity.pop(field, None)
    identity["fields"] = [{"name": name, "type": "string", "required": True}
                          for name in ("user_id", "email", "password_hash")]
    bundle["data_contract"]["surfaces"][0]["collections"].insert(0, identity)
    original = deepcopy(bundle)
    result = inventory._save(context, bundle)
    assert result["outcome"] == "saved", result
    assert [collection["name"] for collection in context.get("data_contract")["surfaces"][0]["collections"]] == [
        "report_documents", "report_notes",
    ]
    assert bundle == original


def test_generic_users_without_identity_evidence_remains_app_owned(persistence):
    context = ownership._context(managed=False)
    bundle = _bundle()
    collection = bundle["data_contract"]["surfaces"][0]["collections"][0]
    collection["name"] = "users"
    result = inventory._save(context, bundle)
    assert result["outcome"] == "saved", result
    assert context.get("data_contract")["surfaces"][0]["collections"][0]["name"] == "users"


@pytest.mark.parametrize("reads", [
    ["edit_report"], ["summarize_reports", "summarize_reports"], ["Bad Name"],
])
def test_custom_reads_are_distinct_valid_action_ids(persistence, reads):
    bundle = _bundle()
    bundle["surface_map"]["surfaces"][0]["custom_reads"] = reads
    result = inventory._save(ownership._context(managed=False), bundle)
    assert result["outcome"] == "revise"
    assert "custom_reads" in result["error"]


def test_a_canonical_read_in_custom_reads_is_dropped_not_refused(persistence):
    context = ownership._context(managed=False)
    bundle = _bundle()
    bundle["surface_map"]["surfaces"][0]["custom_reads"] = ["list_report_documents"]
    result = inventory._save(context, bundle)
    assert result["outcome"] == "saved", result
    assert detach(context.get("design_surface_map"))["surfaces"][0]["custom_reads"] == []


def test_shared_collection_keeps_declared_owner_and_materializes_known_null(persistence):
    context = ownership._context(managed=False)
    bundle = _bundle()
    shared = bundle["data_contract"]["surfaces"][0]["collections"].pop()
    shared["tenancy"] = "app_wide"
    shared.pop("owner_field")
    bundle["data_contract"]["shared_collections"] = [shared]
    result = inventory._save(context, bundle)
    assert result["outcome"] == "saved", result
    saved = detach(context.get("data_contract"))
    assert saved["shared_collections"][0]["owner_field"] is None
    assert index_data_contract_by_entity(saved)[("reports", "ReportNote")] == saved["shared_collections"][0]


def test_factory_save_rejects_shared_collection_without_surface_ownership(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = _bundle()
    shared = bundle["data_contract"]["surfaces"][0]["collections"].pop()
    shared.pop("ownership")
    bundle["data_contract"]["shared_collections"] = [shared]
    result = inventory._save(context, bundle)
    assert result["outcome"] == "revise", result
    assert "ownership" in result["error"]
    assert context.get("data_contract") is None
    store_factory.assert_not_called()


@pytest.mark.parametrize("duplicate", ["entity", "name"])
def test_duplicate_surface_and_shared_identity_is_rejected(persistence, duplicate):
    _, store_factory, _ = persistence
    bundle = _bundle()
    surface_collections = bundle["data_contract"]["surfaces"][0]["collections"]
    shared = surface_collections.pop()
    shared[duplicate] = surface_collections[0][duplicate]
    bundle["data_contract"]["shared_collections"] = [shared]
    result = inventory._save(ownership._context(managed=False), bundle)
    assert result["outcome"] == "revise"
    if duplicate == "entity":
        # The design message names both collections and what to change.
        assert "'report_documents' and 'report_notes' on 'reports' both declare entity 'Report'" in result["error"]
        assert "Give 'report_notes' an entity of its own" in result["error"]
    else:
        assert f".{duplicate} duplicates" in result["error"]
    store_factory.assert_not_called()
