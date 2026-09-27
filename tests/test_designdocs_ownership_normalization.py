"""Determined ownership repairs survive the real frozen-context save boundary."""

import logging
from copy import deepcopy

import pytest
import yaml
from pydantic import ValidationError

from factory_app.workflows._shared.surface_ownership import SurfaceOwnershipRule
from mozaiksai.core.runtime.persistence.indexes import _iter_indexed_collections
from mozaiksai.core.runtime.persistence.intent_loader import index_data_contract_by_entity
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from tests import test_designdocs_capability_ownership as ownership
from tests import test_designdocs_monetization_inventory as inventory

persistence = inventory.persistence


def _auth_bundle() -> dict:
    bundle = inventory._bundle(pricing=False)
    ownership._add_surface(
        bundle, surface_id="auth", name="Authentication", route="/auth",
        entities=[], actions=[], collection="users",
    )
    collection = bundle["data_contract"]["surfaces"][-1]["collections"][0]
    collection["fields"] = [
        {"name": name, "type": "string", "required": True}
        for name in ("user_id", "email", "password_hash")
    ]
    return bundle


def _saved_collections(context) -> list[dict]:
    data = detach(context.get("data_contract"))
    return [
        collection
        for surface in data["surfaces"]
        for collection in surface["collections"]
    ] + data.get("shared_collections", [])


@pytest.mark.parametrize("surface_kind", ["module", "ui_only"])
@pytest.mark.parametrize("additional_fields", [
    pytest.param([], id="base"),
    pytest.param(["display_name"], id="display_name"),
    pytest.param(["role"], id="role"),
    pytest.param(["first_name", "last_name"], id="first_name_last_name"),
    pytest.param(["is_active"], id="is_active"),
    pytest.param(["full_name"], id="full_name"),
    pytest.param(["avatar_url"], id="avatar_url"),
])
def test_live_auth_users_normalizes_and_preserves_auth_page(persistence, surface_kind, additional_fields):
    store, _, summary = persistence
    context = ownership._context(managed=False)
    assert isinstance(context, ContextVariablesBridge)
    bundle = _auth_bundle()
    bundle["surface_map"]["surfaces"][-1]["surface_kind"] = surface_kind
    bundle["data_contract"]["surfaces"][-1]["surface_kind"] = surface_kind
    bundle["data_contract"]["surfaces"][-1]["collections"][0]["ownership"]["surface_kind"] = surface_kind
    bundle["data_contract"]["surfaces"][-1]["collections"][0]["fields"].extend(
        {"name": name, "type": "boolean" if name == "is_active" else "string", "required": False}
        for name in additional_fields
    )
    submitted = deepcopy(bundle)

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    surface = next(s for s in context.get("design_surface_map")["surfaces"] if s["surface_id"] == "auth")
    assert surface["owner"] == "platform"
    assert detach(surface["primary_entities"]) == []
    assert detach(surface["owned_pages"]) == ["Authentication"]
    assert not any(c["name"] == "users" for c in _saved_collections(context))
    assert detach(context.get("experience_spec"))["pages"][-1] == submitted["experience_spec"]["pages"][-1]
    assert store.save_data_contract.await_args.kwargs["data_contract"] == detach(context.get("data_contract"))
    assert summary.await_args.kwargs["summary_payload"]["surface_map"] == detach(context.get("design_surface_map"))
    assert bundle == submitted, "Repair a detached copy, not the submitted structured output."


def test_selected_subscription_state_normalizes_to_billing_facade(persistence):
    context = ownership._context(managed=True)
    bundle = inventory._bundle(pricing=False)
    ownership._add_surface(
        bundle, surface_id="subscription_management", name="My Subscription", route="/subscription",
        entities=["Subscription"], actions=["update_subscription"], collection="subscriptions",
    )
    page = deepcopy(bundle["experience_spec"]["pages"][-1])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    surfaces = detach(context.get("design_surface_map"))["surfaces"]
    assert not any(s["surface_id"] == "subscription_management" for s in surfaces)
    facade = next(s for s in surfaces if s["surface_id"] == "billing_portal")
    assert facade["owner"] == "app"
    assert "My Subscription" in facade["owned_pages"]
    assert facade["primary_entities"] == []
    assert "update_subscription" not in facade["owned_mutations"]
    assert page in detach(context.get("experience_spec"))["pages"]
    assert not any(c["name"] == "subscriptions" for c in _saved_collections(context))
    assert not any(s["surface_id"] == "subscription_management" for s in context.get("data_contract")["surfaces"])


@pytest.mark.parametrize("normalize_auth", [False, True])
def test_user_id_preferences_remain_app_owned(persistence, normalize_auth):
    context = ownership._context(managed=False)
    bundle = _auth_bundle() if normalize_auth else inventory._bundle(pricing=False)
    ownership._add_surface(
        bundle, surface_id="reader_preferences", name="Reading Preferences", route="/preferences",
        entities=["ReaderPreferences"], actions=["update_preferences"], collection="preferences",
    )
    collection = bundle["data_contract"]["surfaces"][-1]["collections"][0]
    collection["fields"] = [
        {"name": name, "type": "string", "required": True}
        for name in ("user_id", "theme", "favorite_genre")
    ]
    expected_surface = deepcopy(bundle["surface_map"]["surfaces"][-1])
    expected_data = deepcopy(bundle["data_contract"]["surfaces"][-1])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert expected_surface in detach(context.get("design_surface_map"))["surfaces"]
    assert expected_data in detach(context.get("data_contract"))["surfaces"]
    assert [c["name"] for c in _saved_collections(context)] == ["preferences"]


def test_mixed_auth_and_app_fields_split_to_only_app_module(persistence, caplog):
    store, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    collection = bundle["data_contract"]["surfaces"][-1]["collections"][0]
    collection["fields"].append({"name": "favorite_task_color", "type": "string", "required": False})
    submitted = deepcopy(bundle)

    with caplog.at_level(logging.INFO):
        result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert isinstance(context, ContextVariablesBridge)
    data = detach(context.get("data_contract"))
    residual = next(c for c in _saved_collections(context) if c["name"] == "users")
    assert residual["ownership"] == {"surface_id": "reports", "surface_kind": "module"}
    assert {f["name"] for f in residual["fields"]} == {"user_id", "favorite_task_color"}
    assert next(f for f in residual["fields"] if f["name"] == "user_id")["required"] is True
    assert residual["search_by"] == "user_id"
    assert any(index["keys"] == [{"field": "user_id", "order": 1}] and index["unique"] for index in residual["indexes"])
    assert residual in next(s for s in data["surfaces"] if s["surface_id"] == "reports")["collections"]
    assert not next(s for s in data["surfaces"] if s["surface_id"] == "auth")["collections"]
    assert next(s for s in context.get("design_surface_map")["surfaces"] if s["surface_id"] == "auth")["owner"] == "platform"
    assert store.save_data_contract.await_args.kwargs["data_contract"] == data
    assert index_data_contract_by_entity(data) == {("reports", "users"): residual}
    indexed = _iter_indexed_collections(data)
    assert [(collection.module_id, collection.entity_name) for collection in indexed] == [("reports", "users")]
    assert indexed[0].indexes[0].keys == [("user_id", 1)]
    assert indexed[0].indexes[0].options["unique"] is True
    record = {"surface_id": "auth", "owner": "platform", "removed_collections": [], "split_collections": [{
        "name": "users", "target_surface_id": "reports", "target_collection": "users",
        "removed_fields": ["email", "password_hash"], "retained_fields": ["user_id", "favorite_task_color"],
    }]}
    assert record in summary.await_args.kwargs["summary_payload"]["ownership_normalizations"]
    saved = {call.kwargs["kind"]: call.kwargs for call in store.upsert_design_doc.await_args_list}
    assert record in saved["database"]["extra_fields"]["ownership_normalizations"]
    marker = "split=users->reports/users retained=[user_id,favorite_task_color] stripped=[email,password_hash]"
    assert marker in caplog.text
    assert marker in saved["database"]["content"]
    assert bundle == submitted


@pytest.mark.parametrize("app_modules", [0, 2])
def test_mixed_auth_and_app_fields_reject_undetermined_app_owner(persistence, app_modules):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    bundle["data_contract"]["surfaces"][-1]["collections"][0]["fields"].append(
        {"name": "favorite_task_color", "type": "string", "required": False},
    )
    if app_modules == 0:
        bundle["surface_map"]["surfaces"][0]["surface_kind"] = "ui_only"
        bundle["data_contract"]["surfaces"][0]["surface_kind"] = "ui_only"
    else:
        ownership._add_surface(
            bundle, surface_id="tasks", name="Tasks", route="/tasks",
            entities=["Task"], actions=["update_task"],
        )
    submitted = deepcopy(bundle)

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="platform")
    assert bundle == submitted


def test_normalization_is_saved_for_user_and_logged(persistence, caplog):
    store, _, summary = persistence
    context = ownership._context(managed=False)
    with caplog.at_level(logging.INFO):
        result = inventory._save(context, _auth_bundle())

    assert result["outcome"] == "saved", result
    marker = "DESIGN_OWNERSHIP_NORMALIZED surface=auth owner=platform removed=[users]"
    assert marker in caplog.text
    saved = {call.kwargs["kind"]: call.kwargs for call in store.upsert_design_doc.await_args_list}
    assert marker in saved["backend"]["content"]
    assert marker in saved["database"]["content"]
    assert marker in saved["frontend"]["content"]
    assert marker in context.get("backend_design_document")
    assert marker in summary.await_args.kwargs["summary_payload"]["backend_markdown"]
    record = {"surface_id": "auth", "owner": "platform", "removed_collections": ["users"]}
    assert record in summary.await_args.kwargs["summary_payload"]["ownership_normalizations"]
    assert record in saved["backend"]["extra_fields"]["ownership_normalizations"]
    assert record in saved["database"]["extra_fields"]["ownership_normalizations"]


@pytest.mark.parametrize("fields", [["user_id"], []])
def test_generic_users_collection_on_auth_surface_requires_identity_evidence(persistence, fields):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    bundle["data_contract"]["surfaces"][-1]["collections"][0]["fields"] = [
        {"name": name, "type": "string", "required": True} for name in fields
    ]

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="platform")


def test_users_named_app_profile_collection_is_preserved(persistence):
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    ownership._add_surface(
        bundle, surface_id="reader_profiles", name="Reader Profiles", route="/readers",
        entities=["ReaderProfile"], actions=["update_reader_profile"], collection="users",
    )
    bundle["data_contract"]["surfaces"][-1]["collections"][0]["fields"] = [
        {"name": name, "type": "string", "required": True}
        for name in ("user_id", "favorite_genre", "role")
    ]
    expected_data = deepcopy(bundle["data_contract"]["surfaces"][-1])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert expected_data in detach(context.get("data_contract"))["surfaces"]


@pytest.mark.parametrize("collection_name", ["preferences", "users"])
def test_app_fields_on_auth_move_to_only_module_without_being_removed(persistence, collection_name):
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    preference = ownership._collection("auth", collection_name)
    preference["fields"] = [
        {"name": name, "type": "string", "required": True} for name in ("user_id", "theme")
    ]
    bundle["data_contract"]["surfaces"][-1]["collections"] = [preference]
    submitted = deepcopy(bundle)

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    residual = next(c for c in _saved_collections(context) if c["name"] == collection_name)
    assert residual["ownership"]["surface_id"] == "reports"
    assert {f["name"] for f in residual["fields"]} == {"user_id", "theme"}
    assert bundle == submitted


@pytest.mark.parametrize("collision", ["managed_pack", "platform_hint"])
def test_competing_canonical_owners_are_rejected(persistence, tmp_path, collision):
    _, store_factory, _ = persistence
    context = ownership._context(managed=True)
    if collision == "platform_hint":
        blueprint = detach(context.get("concept_blueprint"))
        blueprint["surface_candidate_hints"] = [{
            "surface_id": "subscription_management", "owner_hint": "platform",
        }]
        context.set("concept_blueprint", blueprint)
    else:
        descriptor, contracts = ownership._search_pack(tmp_path)
        contract = contracts[0]
        contract["surface_ownership"][0].update(
            surface_ids=["subscription_management"], entity_names=["Subscription"],
            collection_names=["subscriptions"], state_field_names=["status"],
        )
        (tmp_path / "managed_search/contract.yaml").write_text(yaml.safe_dump(contract), encoding="utf-8")
        context.set("capability_packs", [*detach(context.get("capability_packs")), descriptor])
    bundle = inventory._bundle(pricing=False)
    ownership._add_surface(
        bundle, surface_id="subscription_management", name="Subscription", route="/subscription",
        entities=["Subscription"], actions=["update_subscription"], collection="subscriptions",
    )
    submitted = deepcopy(bundle)

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="Ambiguous ownership")
    assert "billing_portal" in result["error"]
    assert ("search_portal" if collision == "managed_pack" else "platform") in result["error"]
    assert submitted == bundle


@pytest.mark.parametrize("field,value", [
    ("primary_entities", ["AuthIdentity", "ReaderPreference"]),
    ("owned_mutations", ["login", "update_preferences"]),
])
def test_mixed_app_behavior_on_platform_surface_stays_a_rejection(persistence, field, value):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    bundle["surface_map"]["surfaces"][-1][field] = value
    submitted = deepcopy(bundle)

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="platform")
    assert "Ambiguous surface" in result["error"]
    assert bundle == submitted


def test_two_subscription_surfaces_merge_pages_without_losing_normalization_records(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=True)
    bundle = inventory._bundle(pricing=False)
    for surface_id, name, route in (
        ("subscription_management", "Subscription", "/subscription"),
        ("billing_management", "Invoices", "/invoices"),
    ):
        ownership._add_surface(
            bundle, surface_id=surface_id, name=name, route=route,
            entities=["Subscription"], actions=["update_subscription"], collection="subscriptions",
        )
    approved_pages = deepcopy(bundle["experience_spec"]["pages"])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    surfaces = detach(context.get("design_surface_map"))["surfaces"]
    facades = [surface for surface in surfaces if surface["surface_id"] == "billing_portal"]
    assert len(facades) == 1
    assert {"Subscription", "Invoices"} <= set(facades[0]["owned_pages"])
    assert all(page in detach(context.get("experience_spec"))["pages"] for page in approved_pages)
    assert not any(collection["name"] == "subscriptions" for collection in _saved_collections(context))
    records = summary.await_args.kwargs["summary_payload"]["ownership_normalizations"]
    for surface_id in ("subscription_management", "billing_management"):
        assert {
            "surface_id": surface_id, "owner": "billing_portal", "removed_collections": ["subscriptions"],
        } in records


def test_appointment_session_action_stays_app_owned(persistence):
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    ownership._add_surface(
        bundle, surface_id="appointment_sessions", name="Appointments", route="/appointments",
        entities=["Appointment"], actions=["create_session"], collection="appointment_sessions",
    )
    expected_surface = deepcopy(bundle["surface_map"]["surfaces"][-1])
    expected_data = deepcopy(bundle["data_contract"]["surfaces"][-1])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert expected_surface in detach(context.get("design_surface_map"))["surfaces"]
    assert expected_data in detach(context.get("data_contract"))["surfaces"]


def test_managed_ownership_without_facade_is_rejected(persistence, tmp_path):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    descriptor, contracts = ownership._search_pack(tmp_path)
    contract = contracts[0]
    del contract["surface_ownership"][0]["facade_module"]
    (tmp_path / "managed_search/contract.yaml").write_text(yaml.safe_dump(contract), encoding="utf-8")
    context.set("capability_packs", [descriptor])

    result = inventory._save(context, inventory._bundle(pricing=False))

    ownership._assert_refused(context, result, store_factory, owner="managed_search")
    assert "facade" in result["error"].lower()


def test_auth_collection_with_conflicting_app_owner_is_not_removed(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    ownership._add_surface(
        bundle, surface_id="reader_profiles", name="Readers", route="/readers",
        entities=["ReaderProfile"], actions=["update_reader_profile"],
    )
    collection = bundle["data_contract"]["surfaces"][-2]["collections"][0]
    collection["ownership"]["surface_id"] = "reader_profiles"
    collection["fields"] = [{"name": "email", "type": "string", "required": True}]
    submitted = deepcopy(bundle)

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="platform")
    assert "auth" in result["error"]
    assert "reader_profiles" in result["error"]
    assert bundle == submitted


def test_facade_custom_integration_is_not_discarded(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=True)
    bundle = inventory._bundle(pricing=False)
    ownership._add_surface(
        bundle, surface_id="subscription_management", name="Subscription", route="/subscription",
        entities=["Subscription"], actions=["update_subscription"], collection="subscriptions",
    )
    bundle["surface_map"]["surfaces"][-1]["integrations"] = ["customer_crm"]
    submitted = deepcopy(bundle)

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="MozaiksPay")
    assert "integrations" in result["error"]
    assert bundle == submitted


@pytest.mark.parametrize("field_type", ["object", "array"])
def test_identity_field_name_does_not_authorize_discarding_nested_app_data(persistence, field_type):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    collection = bundle["data_contract"]["surfaces"][-1]["collections"][0]
    collection["fields"] = [{"name": "email", "type": field_type, "required": True}]
    submitted = deepcopy(bundle)

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="platform")
    assert "users" in result["error"]
    assert bundle == submitted


def test_captured_auth_identity_shape_normalizes(persistence):
    # Captured earlier in .local/reports/pr-736/baseline-fixture.json; this is
    # evidence for that identity shape, not the unavailable ef9caec2 payload.
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    bundle["surface_map"]["surfaces"][-1].update(
        primary_entities=["User"], owned_mutations=["login_user"],
    )
    submitted = deepcopy(bundle)

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    surface = next(s for s in detach(context.get("design_surface_map"))["surfaces"] if s["surface_id"] == "auth")
    assert surface["owner"] == "platform"
    assert surface["primary_entities"] == []
    assert surface["owned_mutations"] == []
    assert not any(c["name"] == "users" for c in _saved_collections(context))
    assert submitted["experience_spec"]["pages"][-1] in detach(context.get("experience_spec"))["pages"]
    assert bundle == submitted


def test_openid_standard_claims_normalize_through_real_bridge(persistence):
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    claim_types = {
        "sub": "string", "name": "string", "given_name": "string", "family_name": "string",
        "middle_name": "string", "nickname": "string", "preferred_username": "string",
        "profile": "string", "picture": "string", "website": "string", "email": "string",
        "email_verified": "boolean", "gender": "string", "birthdate": "string",
        "zoneinfo": "string", "locale": "string", "phone_number": "string",
        "phone_number_verified": "boolean", "address": "object", "updated_at": "integer",
    }
    bundle["data_contract"]["surfaces"][-1]["collections"][0]["fields"] = [
        {"name": name, "type": field_type, "required": False} for name, field_type in claim_types.items()
    ]

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert isinstance(context, ContextVariablesBridge)
    assert _saved_collections(context) == []


@pytest.mark.parametrize("claim,field_type", [
    ("roles", "array"), ("scopes", "array"), ("realm_access", "object"), ("resource_access", "object"),
])
def test_platform_role_and_scope_claims_normalize(persistence, claim, field_type):
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    bundle["data_contract"]["surfaces"][-1]["collections"][0]["fields"].append(
        {"name": claim, "type": field_type, "required": False},
    )

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert _saved_collections(context) == []


@pytest.mark.parametrize("has_user_id", [False, True])
def test_auth_split_keeps_residual_constraints_and_rekeys_identity_indexes(persistence, has_user_id):
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    collection = bundle["data_contract"]["surfaces"][-1]["collections"][0]
    if not has_user_id:
        collection["fields"] = [field for field in collection["fields"] if field["name"] != "user_id"]
    residual_field = {"name": "favorite_task_color", "type": "string", "required": False,
                      "enum": ["blue", "green"], "default": "blue", "nullable": False}
    collection["fields"].extend([{"name": "app_id", "type": "string", "required": True}, residual_field])
    collection["search_by"] = "email"
    residual_index = {"keys": [{"field": "favorite_task_color", "order": 1}],
                      "unique": False, "sparse": False, "name": "users_app_id_user_id_unique"}
    collection["indexes"] = [
        {"keys": [{"field": "email", "order": 1}], "unique": True, "sparse": False, "name": "email"},
        residual_index,
    ]

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    residual = _saved_collections(context)[0]
    assert residual_field in residual["fields"]
    assert residual_index in residual["indexes"]
    assert all(key["field"] != "email" for index in residual["indexes"] for key in index["keys"])
    assert any(index["keys"] == [{"field": "app_id", "order": 1}, {"field": "user_id", "order": 1}]
               and index["unique"] for index in residual["indexes"])
    assert residual["search_by"] == "user_id"
    assert next(field for field in residual["fields"] if field["name"] == "user_id")["required"] is True
    runtime_indexes = _iter_indexed_collections(detach(context.get("data_contract")))[0].indexes
    assert len({index.name for index in runtime_indexes}) == len(runtime_indexes) == 2


def test_explicit_app_owner_disambiguates_reserved_identity_collection_split(persistence):
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    ownership._add_surface(bundle, surface_id="tasks", name="Tasks", route="/tasks",
                           entities=["Task"], actions=["update_task"])
    collection = bundle["data_contract"]["surfaces"][-2]["collections"].pop()
    collection.update(name="auth_identities", ownership={"surface_id": "reports", "surface_kind": "module"})
    collection["fields"].append({"name": "favorite_task_color", "type": "string", "required": False})
    bundle["data_contract"]["surfaces"][0]["collections"].append(collection)

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    residual = _saved_collections(context)[0]
    assert residual["name"] == "auth_identities_app_data"
    assert residual["ownership"]["surface_id"] == "reports"
    assert {field["name"] for field in residual["fields"]} == {"user_id", "favorite_task_color"}


def test_auth_split_collision_rejects_atomically(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    collection = bundle["data_contract"]["surfaces"][-1]["collections"][0]
    collection["fields"].append({"name": "favorite_task_color", "type": "string", "required": False})
    existing = ownership._collection("reports", "users")
    existing["fields"] = [{"name": "favorite_report", "type": "string", "required": False}]
    bundle["data_contract"]["surfaces"][0]["collections"].append(existing)
    submitted = deepcopy(bundle)

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="users")
    assert bundle == submitted


def test_mixed_subscription_provider_fields_still_require_revision(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=True)
    bundle = inventory._bundle(pricing=False)
    ownership._add_surface(
        bundle, surface_id="subscription_management", name="Subscription", route="/subscription",
        entities=["Subscription"], actions=["update_subscription"], collection="subscriptions",
    )
    bundle["data_contract"]["surfaces"][-1]["collections"][0]["fields"].append(
        {"name": "favorite_task_color", "type": "string", "required": False},
    )
    submitted = deepcopy(bundle)

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="MozaiksPay")
    assert bundle == submitted


def test_structured_identity_types_must_reference_declared_state_fields():
    with pytest.raises(ValidationError, match="must reference declared state_field_names"):
        SurfaceOwnershipRule(owner="platform", state_field_names=["email"],
                             structured_state_field_types={"invented_claim": ["object"]})
