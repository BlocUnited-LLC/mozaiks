"""Determined ownership repairs survive the real frozen-context save boundary."""

import json
import logging
from copy import deepcopy

import pytest
import yaml
from pydantic import ValidationError

from factory_app.workflows._shared import surface_ownership
from factory_app.workflows._shared.hook_utils import workflow_context_path
from factory_app.workflows._shared.surface_ownership import (
    SurfaceOwnershipRule,
    UserAdministration,
    auth_contract_routes,
)
from mozaiksai.core.runtime.persistence.indexes import _iter_indexed_collections
from mozaiksai.core.runtime.persistence.intent_loader import index_data_contract_by_entity
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support import (
    data_contract_fields,
    module_read_actions,
    module_write_actions,
)
from mozaiksai.core.workflow.generator_support.data_contract_fields import (
    CANONICAL_FIELD_TYPES,
    DATE_FIELD_TYPES,
    STRUCTURED_FIELD_TYPES,
)
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
def test_live_auth_users_normalizes_and_removes_platform_sign_in_page(persistence, surface_kind, additional_fields):
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
    # The auth contract already serves sign-in; the app builds no page for it.
    assert detach(surface["owned_pages"]) == []
    assert not any(c["name"] == "users" for c in _saved_collections(context))
    assert detach(context.get("experience_spec"))["pages"] == submitted["experience_spec"]["pages"][:-1]
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
    assert index_data_contract_by_entity(data) == {("reports", "users_app_data"): residual}
    indexed = _iter_indexed_collections(data)
    assert [(collection.module_id, collection.entity_name) for collection in indexed] == [("reports", "users")]
    assert indexed[0].indexes[0].keys == [("user_id", 1)]
    assert indexed[0].indexes[0].options["unique"] is True
    record = {"surface_id": "auth", "owner": "platform", "removed_collections": [], "split_collections": [{
        "name": "users", "target_surface_id": "reports", "target_collection": "users",
        "removed_fields": ["email", "password_hash"], "retained_fields": ["user_id", "favorite_task_color"],
    }], "removed_pages": [{"name": "Authentication", "route": "/auth"}]}
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
    marker = (
        "DESIGN_OWNERSHIP_NORMALIZED surface=auth owner=platform removed=[users] "
        "removed_pages=[Authentication@/auth]"
    )
    assert marker in caplog.text
    saved = {call.kwargs["kind"]: call.kwargs for call in store.upsert_design_doc.await_args_list}
    assert marker in saved["backend"]["content"]
    assert marker in saved["database"]["content"]
    assert marker in saved["frontend"]["content"]
    assert marker in context.get("backend_design_document")
    assert marker in summary.await_args.kwargs["summary_payload"]["backend_markdown"]
    record = {
        "surface_id": "auth", "owner": "platform", "removed_collections": ["users"],
        "removed_pages": [{"name": "Authentication", "route": "/auth"}],
    }
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
    split = "entities ['ReaderPreference']" if field == "primary_entities" else "actions ['update_preferences']"
    assert f"Split out the app-owned behavior: move {split} to an app-owned module surface" in result["error"]
    assert "keep 'auth' as a platform reference (owner=platform)" in result["error"]
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
    assert surface["owned_pages"] == []
    assert not any(c["name"] == "users" for c in _saved_collections(context))
    assert submitted["experience_spec"]["pages"][-1] not in detach(context.get("experience_spec"))["pages"]
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


def test_platform_capability_is_not_a_facade_property():
    with pytest.raises(ValidationError, match="platform ownership, not a managed facade"):
        SurfaceOwnershipRule(owner="MozaiksPay", facade_module="billing_portal", platform_capability="authentication")


def test_login_route_comes_from_the_auth_contract_template():
    template = workflow_context_path("webapp_builder", "templates", "config", "auth.yaml")
    declared = yaml.safe_load(
        template.read_text(encoding="utf-8").replace("{{AUTH_DEFAULT_ROUTE}}", "/"),
    )["routes"]
    routes = auth_contract_routes()
    assert routes.login == declared["login"] == "/login"
    assert routes.callback == declared["callback"] == "/auth/callback"


@pytest.mark.parametrize("route,fields,target", [
    pytest.param("/profile", ["display_name", "avatar_url"], "move 'Profile Settings' to owned_pages of 'reports'", id="profile_form"),
    pytest.param("/profile", ["current_password", "new_password"], "move 'Profile Settings' to owned_pages of 'reports'", id="password_change_is_not_sign_in"),
    pytest.param("/", [], "move 'Profile Settings' to owned_pages of 'reports'", id="root_is_not_a_sign_in_ancestor"),
])
def test_non_sign_in_page_on_platform_auth_surface_is_rejected_naming_the_app_owner(persistence, route, fields, target):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    bundle["experience_spec"]["pages"].append({
        "name": "Profile Settings", "route": route, "layout": "full-width", "intent": "Edit profile details.",
        "sections": [{
            "id": "profile-form", "primitive": "Form" if fields else "PageHeader", "intent": "Edit the profile.",
            "config_hint": json.dumps({"fields": [{"name": name, "type": "string"} for name in fields]}) if fields else None,
        }],
    })
    bundle["surface_map"]["surfaces"][-1]["owned_pages"] = ["Authentication", "Profile Settings"]
    submitted = deepcopy(bundle)

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="platform")
    assert f"Page 'Profile Settings' ({route}) is owned by 'auth'" in result["error"]
    assert "not a sign-in page" in result["error"]
    assert target in result["error"]
    assert bundle == submitted


def test_non_sign_in_page_names_every_candidate_or_the_existing_app_owner(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    ownership._add_surface(bundle, surface_id="tasks", name="Tasks", route="/tasks", entities=["Task"], actions=["update_task"])
    bundle["experience_spec"]["pages"].append({
        "name": "Profile Settings", "route": "/profile", "layout": "full-width", "intent": "Edit profile details.",
        "sections": [{"id": "profile", "primitive": "PageHeader", "intent": "Profile.", "config_hint": None}],
    })
    auth = next(s for s in bundle["surface_map"]["surfaces"] if s["surface_id"] == "auth")
    auth["owned_pages"] = ["Authentication", "Profile Settings"]

    result = inventory._save(context, deepcopy(bundle))
    ownership._assert_refused(context, result, store_factory, owner="platform")
    assert "move 'Profile Settings' to owned_pages of one of ['reports', 'tasks']" in result["error"]

    bundle["surface_map"]["surfaces"][0]["owned_pages"] = ["Reports", "Profile Settings"]
    context = ownership._context(managed=False)
    result = inventory._save(context, bundle)
    ownership._assert_refused(context, result, store_factory, owner="platform")
    assert "it is already listed by ['reports']: remove 'Profile Settings' from 'auth'.owned_pages" in result["error"]


@pytest.mark.parametrize("name,route,section", [
    pytest.param("Login", "/login", {"primitive": "PageHeader", "config_hint": None}, id="login_route"),
    pytest.param("Auth Callback", "/auth/callback", {"primitive": "PageHeader", "config_hint": None}, id="callback_route"),
    pytest.param("Account Access", "/auth", {"primitive": "PageHeader", "config_hint": None}, id="route_the_callback_nests_under"),
    pytest.param("Sign In", "/account/sign-in", {
        "primitive": "Form",
        "config_hint": json.dumps({"fields": [{"name": "email", "type": "string"}, {"name": "password", "type": "string"}]}),
    }, id="credential_form"),
])
def test_sign_in_pages_are_recognized_by_auth_route_or_credential_form(persistence, name, route, section):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    bundle["experience_spec"]["pages"][-1].update(name=name, route=route)
    bundle["experience_spec"]["pages"][-1]["sections"] = [{"id": "sign-in", "intent": "Sign in.", **section}]
    bundle["surface_map"]["surfaces"][-1]["owned_pages"] = [name]

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert [page["route"] for page in detach(context.get("experience_spec"))["pages"]] == ["/reports"]
    record = next(
        entry for entry in summary.await_args.kwargs["summary_payload"]["ownership_normalizations"]
        if entry["surface_id"] == "auth"
    )
    assert record["removed_pages"] == [{"name": name, "route": route}]


def test_platform_auth_events_are_removed_and_logged(persistence, caplog):
    store, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    bundle["surface_map"]["surfaces"][-1].update(
        primary_entities=["User"], owned_mutations=["login_user"],
        events_emitted=["domain.auth.user_logged_in", "domain.auth.user_logged_out"],
    )
    submitted = deepcopy(bundle)

    with caplog.at_level(logging.INFO):
        result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert isinstance(context, ContextVariablesBridge)
    surface = next(s for s in detach(context.get("design_surface_map"))["surfaces"] if s["surface_id"] == "auth")
    assert surface["owner"] == "platform"
    assert surface["events_emitted"] == []
    assert surface["owned_pages"] == []
    assert [page["route"] for page in detach(context.get("experience_spec"))["pages"]] == ["/reports"]
    record = {
        "surface_id": "auth", "owner": "platform", "removed_collections": ["users"],
        "removed_events": ["domain.auth.user_logged_in", "domain.auth.user_logged_out"],
        "removed_pages": [{"name": "Authentication", "route": "/auth"}],
    }
    assert record in summary.await_args.kwargs["summary_payload"]["ownership_normalizations"]
    marker = (
        "DESIGN_OWNERSHIP_NORMALIZED surface=auth owner=platform removed=[users] "
        "removed_events=[domain.auth.user_logged_in,domain.auth.user_logged_out] "
        "removed_pages=[Authentication@/auth]"
    )
    assert marker in caplog.text
    saved = {call.kwargs["kind"]: call.kwargs for call in store.upsert_design_doc.await_args_list}
    assert marker in saved["backend"]["content"]
    assert record in saved["ui_schema"]["extra_fields"]["ownership_normalizations"]
    assert "/auth" not in yaml.safe_dump(saved["ui_schema"]["extra_fields"]["experience_spec"])
    assert bundle == submitted


def test_platform_auth_event_consumed_by_workflow_trigger_is_rejected(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    bundle["surface_map"]["surfaces"][-1]["events_emitted"] = ["domain.auth.user_logged_in"]
    bundle["surface_map"]["surfaces"][0]["workflow_triggers"] = ["domain.auth.user_logged_in"]
    submitted = deepcopy(bundle)

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="platform")
    assert "surface 'reports' lists ['domain.auth.user_logged_in'] in workflow_triggers" in result["error"]
    assert "app-owned surface's action or domain event" in result["error"]
    assert bundle == submitted


def test_sign_in_page_co_owned_by_app_surface_is_rejected(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    bundle["surface_map"]["surfaces"][0]["owned_pages"] = ["Reports", "Authentication"]
    submitted = deepcopy(bundle)

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="platform")
    assert "also by app-owned ['reports']" in result["error"]
    assert "remove 'Authentication' from owned_pages of ['reports']" in result["error"]
    assert bundle == submitted


def test_sign_in_page_co_owned_by_sibling_platform_surfaces_is_removed_for_each(persistence, caplog):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    bundle["surface_map"]["surfaces"].append({
        "surface_id": "session_management", "label": "Sessions", "surface_kind": "module", "owner": "app",
        "primary_entities": [], "owned_pages": ["Authentication"], "owned_mutations": [],
        "source_capability_packs": [], "notes": None,
    })
    submitted = deepcopy(bundle)

    with caplog.at_level(logging.INFO):
        result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    surfaces = {s["surface_id"]: s for s in detach(context.get("design_surface_map"))["surfaces"]}
    assert surfaces["auth"]["owner"] == surfaces["session_management"]["owner"] == "platform"
    assert surfaces["auth"]["owned_pages"] == surfaces["session_management"]["owned_pages"] == []
    assert [page["route"] for page in detach(context.get("experience_spec"))["pages"]] == ["/reports"]
    records = summary.await_args.kwargs["summary_payload"]["ownership_normalizations"]
    page = {"name": "Authentication", "route": "/auth"}
    assert {"surface_id": "auth", "owner": "platform", "removed_collections": ["users"], "removed_pages": [page]} in records
    assert {"surface_id": "session_management", "owner": "platform", "removed_collections": [], "removed_pages": [page]} in records
    assert "surface=session_management owner=platform removed=[] removed_pages=[Authentication@/auth]" in caplog.text
    assert bundle == submitted


def test_sign_in_page_at_the_login_route_records_no_self_redirect(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    bundle["experience_spec"]["pages"][-1].update(name="Login", route="/login")
    bundle["surface_map"]["surfaces"][-1]["owned_pages"] = ["Login"]
    bundle["experience_spec"]["pages"][0]["sections"].append({
        "id": "sign-in-cta", "primitive": "ActionButton", "intent": "Go to sign in",
        "config_hint": json.dumps({"label": "Sign in", "href": "/login"}),
    })

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    pages = detach(context.get("experience_spec"))["pages"]
    assert [page["route"] for page in pages] == ["/reports"]
    cta = next(section for section in pages[0]["sections"] if section["id"] == "sign-in-cta")
    assert json.loads(cta["config_hint"]) == {"label": "Sign in", "href": "/login"}
    record = next(
        entry for entry in summary.await_args.kwargs["summary_payload"]["ownership_normalizations"]
        if entry["surface_id"] == "auth"
    )
    assert record["removed_pages"] == [{"name": "Login", "route": "/login"}]
    assert "redirected_navigation" not in record


def test_only_typed_navigation_references_are_redirected(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    bundle["experience_spec"]["pages"][0]["sections"].append({
        "id": "sign-in-cta", "primitive": "ActionButton", "intent": "Go to sign in",
        "config_hint": json.dumps({
            "label": "/auth", "columns": ["/auth"], "href": "/auth",
            "links": [{"path": "/auth", "title": "/auth"}],
        }),
    })

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    pages = detach(context.get("experience_spec"))["pages"]
    cta = next(section for section in pages[0]["sections"] if section["id"] == "sign-in-cta")
    assert json.loads(cta["config_hint"]) == {
        "label": "/auth", "columns": ["/auth"], "href": "/login",
        "links": [{"path": "/login", "title": "/auth"}],
    }
    record = next(
        entry for entry in summary.await_args.kwargs["summary_payload"]["ownership_normalizations"]
        if entry["surface_id"] == "auth"
    )
    assert record["redirected_navigation"] == [
        {"page": "Reports", "section": "sign-in-cta", "from": "/auth", "to": "/login"},
    ]


@pytest.mark.parametrize("managed", [False, True], ids=["platform_entity", "provider_entity"])
def test_app_named_surface_with_reserved_claim_is_told_to_drop_it(persistence, managed):
    _, store_factory, _ = persistence
    context = ownership._context(managed=managed)
    bundle = inventory._bundle(pricing=False)
    reserved = "Subscription" if managed else "AuthSession"
    ownership._add_surface(
        bundle, surface_id="member_directory", name="Members", route="/members",
        entities=["Member", reserved], actions=["invite_member"],
    )
    submitted = deepcopy(bundle)

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="MozaiksPay" if managed else "platform")
    assert "App surface 'member_directory' claims" in result["error"]
    assert f"entities ['{reserved}']" in result["error"]
    assert "keep it app-owned with its entities ['Member'], actions ['invite_member']" in result["error"]
    assert "platform reference" not in result["error"]
    assert ("bind that UI to billing_portal" in result["error"]) is managed
    assert bundle == submitted


@pytest.mark.parametrize("binding", [
    pytest.param('{"data_source": {"module_id": "reports", "action_id": "list_reports"}}', id="data_source"),
    pytest.param('{"items": [{"api_endpoint": "/api/modules/reports/list_reports"}]}', id="api_endpoint"),
])
def test_sign_in_page_bound_to_app_data_is_rejected(persistence, binding):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    bundle["experience_spec"]["pages"][-1]["sections"].append({
        "id": "recent-reports", "primitive": "DataTable", "intent": "Recent reports", "config_hint": binding,
    })
    submitted = deepcopy(bundle)

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="platform")
    assert "sections ['recent-reports'] bind app-owned modules ['reports']" in result["error"]
    assert bundle == submitted


def test_navigation_to_removed_sign_in_page_points_at_auth_contract_login(persistence, caplog):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    bundle["experience_spec"]["pages"][0]["sections"].append({
        "id": "sign-in-cta", "primitive": "ActionButton", "intent": "Go to sign in",
        "config_hint": json.dumps({
            "label": "Sign in", "href": "/auth", "links": [{"href": "/auth"}, {"href": "/reports"}],
        }),
    })

    with caplog.at_level(logging.INFO):
        result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    pages = detach(context.get("experience_spec"))["pages"]
    assert [page["route"] for page in pages] == ["/reports"]
    cta = next(section for section in pages[0]["sections"] if section["id"] == "sign-in-cta")
    assert json.loads(cta["config_hint"]) == {
        "label": "Sign in", "href": "/login", "links": [{"href": "/login"}, {"href": "/reports"}],
    }
    record = next(
        entry for entry in summary.await_args.kwargs["summary_payload"]["ownership_normalizations"]
        if entry["surface_id"] == "auth"
    )
    assert record["redirected_navigation"] == [
        {"page": "Reports", "section": "sign-in-cta", "from": "/auth", "to": "/login"},
    ]
    assert "removed_pages=[Authentication@/auth] redirected=[Reports/sign-in-cta:/auth->/login]" in caplog.text


def test_sign_in_page_as_only_page_is_rejected(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    del bundle["experience_spec"]["pages"][0]
    bundle["surface_map"]["surfaces"][0]["owned_pages"] = []
    submitted = deepcopy(bundle)

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="platform")
    assert "leaves no approved pages" in result["error"]
    assert bundle == submitted


def test_provider_lifecycle_events_on_subscription_surface_are_removed(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=True)
    bundle = inventory._bundle(pricing=False)
    ownership._add_surface(
        bundle, surface_id="subscription_management", name="My Subscription", route="/subscription",
        entities=["Subscription"], actions=["update_subscription"], collection="subscriptions",
    )
    bundle["surface_map"]["surfaces"][-1]["events_emitted"] = ["domain.billing.subscription_updated"]
    page = deepcopy(bundle["experience_spec"]["pages"][-1])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    surfaces = detach(context.get("design_surface_map"))["surfaces"]
    facade = next(surface for surface in surfaces if surface["surface_id"] == "billing_portal")
    assert facade["events_emitted"] == []
    # Facade pages are app pages the pack renders; only the provider events go.
    assert "My Subscription" in facade["owned_pages"]
    assert page in detach(context.get("experience_spec"))["pages"]
    record = {
        "surface_id": "subscription_management", "owner": "billing_portal",
        "removed_collections": ["subscriptions"], "removed_events": ["domain.billing.subscription_updated"],
    }
    assert record in summary.await_args.kwargs["summary_payload"]["ownership_normalizations"]


def test_workflow_trigger_on_subscription_surface_still_requires_revision(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=True)
    bundle = inventory._bundle(pricing=False)
    ownership._add_surface(
        bundle, surface_id="subscription_management", name="My Subscription", route="/subscription",
        entities=["Subscription"], actions=["update_subscription"], collection="subscriptions",
    )
    bundle["surface_map"]["surfaces"][-1]["workflow_triggers"] = ["subscription-renewal-workflow"]
    submitted = deepcopy(bundle)

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="MozaiksPay")
    assert (
        "declare workflow_triggers ['subscription-renewal-workflow'] on the app-owned surface" in result["error"]
    )
    assert "surface_id=billing_portal" in result["error"]
    assert bundle == submitted


@pytest.mark.parametrize("page_route,href", [
    pytest.param("/auth/", "/auth", id="page_trailing_slash"),
    pytest.param("/auth", "/auth/", id="link_trailing_slash"),
])
def test_removal_and_redirect_share_one_route_identity(persistence, page_route, href):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    bundle["experience_spec"]["pages"][-1]["route"] = page_route
    bundle["experience_spec"]["pages"][0]["sections"].append({
        "id": "sign-in-cta", "primitive": "ActionButton", "intent": "Go to sign in",
        "config_hint": json.dumps({"label": "Sign in", "href": href}),
    })

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    pages = detach(context.get("experience_spec"))["pages"]
    assert [page["route"] for page in pages] == ["/reports"]
    cta = next(section for section in pages[0]["sections"] if section["id"] == "sign-in-cta")
    assert json.loads(cta["config_hint"]) == {"label": "Sign in", "href": "/login"}
    record = next(
        entry for entry in summary.await_args.kwargs["summary_payload"]["ownership_normalizations"]
        if entry["surface_id"] == "auth"
    )
    assert record["removed_pages"] == [{"name": "Authentication", "route": page_route}]
    assert record["redirected_navigation"] == [
        {"page": "Reports", "section": "sign-in-cta", "from": href, "to": "/login"},
    ]


def test_sign_in_page_at_login_route_with_trailing_slash_records_no_self_redirect(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    bundle["experience_spec"]["pages"][-1].update(name="Login", route="/login/")
    bundle["surface_map"]["surfaces"][-1]["owned_pages"] = ["Login"]
    bundle["experience_spec"]["pages"][0]["sections"].append({
        "id": "sign-in-cta", "primitive": "ActionButton", "intent": "Go to sign in",
        "config_hint": json.dumps({"label": "Sign in", "href": "/login"}),
    })

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    record = next(
        entry for entry in summary.await_args.kwargs["summary_payload"]["ownership_normalizations"]
        if entry["surface_id"] == "auth"
    )
    assert record["removed_pages"] == [{"name": "Login", "route": "/login/"}]
    assert "redirected_navigation" not in record


@pytest.mark.parametrize("surface_id,entities,actions", [
    pytest.param("accounts", ["AuthSession"], [], id="reserved_entity"),
    pytest.param("account_menu", [], ["logout"], id="reserved_action"),
])
def test_app_named_surface_with_only_a_reserved_claim_and_an_app_page_is_told_to_drop_it(
    persistence, surface_id, entities, actions,
):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    ownership._add_surface(
        bundle, surface_id=surface_id, name="Accounts", route="/accounts", entities=entities, actions=actions,
    )
    submitted = deepcopy(bundle)

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="platform")
    assert f"App surface {surface_id!r} claims" in result["error"]
    assert f"entities {entities}, actions {actions}" in result["error"]
    assert "keep it app-owned with its entities [], actions [], collections [], pages ['Accounts']" in result["error"]
    assert "move 'Accounts' to owned_pages" not in result["error"]
    assert "serves only sign-in" not in result["error"]
    assert bundle == submitted


def test_app_named_reserved_claim_also_names_its_sign_in_pages(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    ownership._add_surface(
        bundle, surface_id="accounts", name="Accounts", route="/accounts", entities=["AuthSession"], actions=[],
    )
    bundle["experience_spec"]["pages"].append({
        "name": "Login", "route": "/login", "layout": "full-width", "intent": "Sign in.",
        "sections": [{"id": "sign-in", "primitive": "PageHeader", "intent": "Sign in.", "config_hint": None}],
    })
    bundle["surface_map"]["surfaces"][-1]["owned_pages"] = ["Accounts", "Login"]

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="platform")
    assert "pages ['Accounts']" in result["error"]
    assert "Also remove its sign-in pages ['Login']: the platform serves sign-in." in result["error"]


@pytest.mark.parametrize("app_first", [False, True], ids=["auth_first", "app_surface_first"])
def test_reserved_claim_rejection_does_not_depend_on_surface_order(persistence, app_first):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    ownership._add_surface(
        bundle, surface_id="member_directory", name="Members", route="/members",
        entities=["Member", "AuthSession"], actions=["invite_member"],
    )
    bundle["experience_spec"]["pages"].append({
        "name": "Profile Settings", "route": "/profile", "layout": "full-width", "intent": "Edit the profile.",
        "sections": [{"id": "profile", "primitive": "PageHeader", "intent": "Profile.", "config_hint": None}],
    })
    surfaces = {surface["surface_id"]: surface for surface in bundle["surface_map"]["surfaces"]}
    surfaces["auth"]["owned_pages"] = ["Authentication", "Profile Settings"]
    surfaces["member_directory"]["owned_pages"] = ["Members", "Profile Settings"]
    if app_first:
        bundle["surface_map"]["surfaces"] = [
            surfaces["reports"], surfaces["member_directory"], surfaces["auth"],
        ]

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="platform")
    assert result["error"].startswith("App surface 'member_directory' claims")
    assert "entities ['AuthSession']" in result["error"]
    assert "pages ['Members', 'Profile Settings']" in result["error"]


@pytest.mark.parametrize("sign_in_page", [False, True], ids=["no_pages", "sign_in_page_only"])
def test_app_named_surface_whose_every_claim_is_reserved_normalizes_to_platform(persistence, sign_in_page):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    bundle["surface_map"]["surfaces"].append({
        "surface_id": "account_access", "label": "Account Access", "surface_kind": "module", "owner": "app",
        "primary_entities": ["AuthSession"], "owned_pages": ["Login"] if sign_in_page else [],
        "owned_mutations": ["logout"], "source_capability_packs": [], "notes": None,
    })
    if sign_in_page:
        bundle["experience_spec"]["pages"].append({
            "name": "Login", "route": "/login", "layout": "full-width", "intent": "Sign in.",
            "sections": [{"id": "sign-in", "primitive": "PageHeader", "intent": "Sign in.", "config_hint": None}],
        })

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    surface = next(
        s for s in detach(context.get("design_surface_map"))["surfaces"] if s["surface_id"] == "account_access"
    )
    assert surface["owner"] == "platform"
    assert surface["primary_entities"] == surface["owned_mutations"] == surface["owned_pages"] == []
    assert [page["route"] for page in detach(context.get("experience_spec"))["pages"]] == ["/reports"]
    record = next(
        entry for entry in summary.await_args.kwargs["summary_payload"]["ownership_normalizations"]
        if entry["surface_id"] == "account_access"
    )
    assert record.get("removed_pages", []) == ([{"name": "Login", "route": "/login"}] if sign_in_page else [])


def test_sibling_removal_records_are_independent_copies_in_the_saved_yaml(persistence):
    store, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    bundle["surface_map"]["surfaces"].append({
        "surface_id": "session_management", "label": "Sessions", "surface_kind": "module", "owner": "app",
        "primary_entities": [], "owned_pages": ["Authentication"], "owned_mutations": [],
        "source_capability_packs": [], "notes": None,
    })

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    records = summary.await_args.kwargs["summary_payload"]["ownership_normalizations"]
    pages = [entry["removed_pages"][0] for entry in records if entry.get("removed_pages")]
    assert len(pages) == 2 and pages[0] == pages[1] and pages[0] is not pages[1]
    saved = {call.kwargs["kind"]: call.kwargs for call in store.upsert_design_doc.await_args_list}
    assert "&id" not in saved["ui_schema"]["content"]
    assert "*id" not in saved["ui_schema"]["content"]


ADMIN_USERS = {"builtin_panel": "users", "admin_page": "access"}
IDENTITY_FIELDS = (("user_id", "string"), ("email", "string"), ("password_hash", "string"), ("created_at", "date"))


def _fields(fields) -> list[dict]:
    return [
        {"name": name, "type": kind, "required": True, "default": None, "enum": None, "nullable": False}
        for name, kind in fields
    ]


def _users_page(columns=("user_id", "email", "created_at"), primitive="DataTable", form=None) -> dict:
    listing = {"columns": list(columns), "selection": "single"} if primitive == "DataTable" else {
        "fields": [{"name": name, "type": "text", "label": name} for name in columns],
    }
    create = {"title": "User Management", "size": "medium"} if form is None else {
        "fields": [{"name": name, "type": "text", "label": name} for name in form],
    }
    return {
        "name": "Users", "route": "/users", "layout": "full-width", "intent": "Administer users.",
        "sections": [
            {"id": "user-table", "primitive": primitive, "intent": "Users.", "config_hint": json.dumps(listing)},
            {"id": "create-user", "primitive": "Modal" if form is None else "Form", "intent": "Create a user.",
             "config_hint": json.dumps(create)},
        ],
    }


def _identity_bundle(
    *, surface_id="user_management", entities=("User",), mutations=("create_user", "delete_user"),
    collections=(("users", "User", IDENTITY_FIELDS),), surface_kind="module", page: dict | None = None,
    indexes=(),
) -> dict:
    """The live c0d1e58d shape: the platform's user system designed as an app surface."""
    bundle = inventory._bundle(pricing=False)
    page = _users_page() if page is None else page
    bundle["experience_spec"]["pages"].append(page)
    bundle["surface_map"]["surfaces"].append({
        "surface_id": surface_id, "label": "Users", "surface_kind": surface_kind, "owner": "app",
        "primary_entities": list(entities), "owned_pages": [page["name"]], "owned_mutations": list(mutations),
        "custom_reads": [], "events_emitted": ["domain.users.user_created"], "source_capability_packs": [],
        "notes": None,
    })
    declared = []
    for name, entity, fields in collections:
        collection = ownership._collection(surface_id, name)
        collection.update(entity=entity, fields=_fields(fields), indexes=list(indexes))
        collection["ownership"]["surface_kind"] = surface_kind
        declared.append(collection)
    bundle["data_contract"]["surfaces"].append({
        "surface_id": surface_id, "surface_kind": surface_kind, "collections": declared,
    })
    return bundle


def _record(summary, surface_id: str) -> dict:
    return next(
        entry for entry in summary.await_args.kwargs["summary_payload"]["ownership_normalizations"]
        if entry["surface_id"] == surface_id
    )


EMAIL_KEYED = {"keys": [{"field": "email", "order": 1}], "unique": True, "sparse": None, "name": None}


@pytest.mark.parametrize("surface_id,entity,surface_kind,collections,indexes", [
    pytest.param("user_management", "User", "module", (("users", "User", IDENTITY_FIELDS),), (),
                 id="live_user_management"),
    pytest.param("auth_module", "user", "module", (("accounts", "user", IDENTITY_FIELDS),), (),
                 id="any_casing_any_collection_name"),
    pytest.param("user_auth", "User", "app_policy", (("users", "User", IDENTITY_FIELDS),), (),
                 id="app_policy_surface"),
    pytest.param("members", "User", "module",
                 (("people", "User", (("user_id", "string"), ("email", "string"), ("display_name", "string"))),),
                 (EMAIL_KEYED,), id="email_identity_without_credentials"),
])
def test_platform_identity_is_recognized_by_what_the_surface_declares(
    persistence, caplog, surface_id, entity, surface_kind, collections, indexes,
):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = _identity_bundle(
        surface_id=surface_id, entities=[entity], surface_kind=surface_kind, collections=collections,
        indexes=indexes,
    )
    submitted = deepcopy(bundle)

    with caplog.at_level(logging.INFO):
        result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    surface = next(s for s in detach(context.get("design_surface_map"))["surfaces"] if s["surface_id"] == surface_id)
    assert surface["owner"] == "platform"
    assert surface["primary_entities"] == surface["owned_mutations"] == surface["events_emitted"] == []
    assert surface["owned_pages"] == []
    assert [c["name"] for c in _saved_collections(context)] == []
    assert [page["route"] for page in detach(context.get("experience_spec"))["pages"]] == ["/reports"]
    assert _record(summary, surface_id) == {
        "surface_id": surface_id, "owner": "platform",
        "removed_collections": [name for name, _, _ in collections],
        "removed_mutations": ["create_user", "delete_user"],
        "removed_events": ["domain.users.user_created"],
        "removed_pages": [{"name": "Users", "route": "/users", **ADMIN_USERS}],
    }
    assert "removed_pages=[Users@/users->admin:users]" in caplog.text
    assert "/admin" not in json.dumps(detach(context.get("experience_spec")))
    assert bundle == submitted


@pytest.mark.parametrize("surface_id,surface_kind,entities,collections,page", [
    pytest.param("community_directory", "ui_only", ["User"], (), {
        "name": "Member Directory", "route": "/members", "layout": "full-width", "intent": "Browse members.",
        "sections": [{"id": "members", "primitive": "DataTable", "intent": "Members.",
                      "config_hint": json.dumps({"columns": ["display_name", "avatar_url", "bio"]})}],
    }, id="user_entity_without_data_or_evidence"),
    pytest.param("focus_timer", "module", ["Session"], (("sessions", "Session", (
        ("session_id", "string"), ("user_id", "string"), ("name", "string"), ("active", "boolean"),
        ("expires_at", "datetime"), ("created_at", "datetime"),
    )),), {
        "name": "Focus", "route": "/focus", "layout": "full-width", "intent": "Focus sessions.",
        "sections": [{"id": "sessions", "primitive": "DataTable", "intent": "Sessions.",
                      "config_hint": json.dumps({"columns": ["name", "active", "expires_at"]})}],
    }, id="app_session_entity_of_claim_named_fields"),
])
def test_identity_named_entities_without_account_evidence_stay_app_owned(
    persistence, surface_id, surface_kind, entities, collections, page,
):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    bundle["experience_spec"]["pages"].append(page)
    bundle["surface_map"]["surfaces"].append({
        "surface_id": surface_id, "label": page["name"], "surface_kind": surface_kind, "owner": "app",
        "primary_entities": entities, "owned_pages": [page["name"]], "owned_mutations": [],
        "custom_reads": [], "source_capability_packs": [], "notes": None,
    })
    declared = []
    for name, entity, fields in collections:
        collection = ownership._collection(surface_id, name)
        collection.update(entity=entity, fields=_fields(fields))
        declared.append(collection)
    bundle["data_contract"]["surfaces"].append({"surface_id": surface_id, "surface_kind": surface_kind, "collections": declared})
    expected_surface = deepcopy(bundle["surface_map"]["surfaces"][-1])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert expected_surface in detach(context.get("design_surface_map"))["surfaces"]
    assert page in detach(context.get("experience_spec"))["pages"]
    assert summary.await_args.kwargs["summary_payload"]["ownership_normalizations"] == []


def test_named_auth_surface_records_only_actions_the_catalog_does_not_reserve(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    auth = bundle["surface_map"]["surfaces"][-1]
    auth.update(primary_entities=["UserSession"], owned_mutations=["login_user", "logout_user"])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert _record(summary, "auth")["removed_mutations"] == ["logout_user"]


@pytest.mark.parametrize("surface_id", ["auth_module", "login_sessions"])
def test_session_surface_normalizes_whatever_it_is_called(persistence, surface_id):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    bundle["surface_map"]["surfaces"].append({
        "surface_id": surface_id, "label": "Sessions", "surface_kind": "module", "owner": "app",
        "primary_entities": ["UserSession"], "owned_pages": [], "owned_mutations": ["login_user", "logout_user"],
        "custom_reads": [], "source_capability_packs": [], "notes": None,
    })
    collection = ownership._collection(surface_id, "user_sessions")
    collection.update(entity="UserSession", fields=_fields(
        (("session_id", "string"), ("user_id", "string"), ("expires_at", "datetime")),
    ))
    bundle["data_contract"]["surfaces"].append({"surface_id": surface_id, "surface_kind": "module", "collections": [collection]})

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert _record(summary, surface_id) == {
        "surface_id": surface_id, "owner": "platform", "removed_collections": ["user_sessions"],
        "removed_mutations": ["login_user", "logout_user"],
    }


@pytest.mark.parametrize("kind", sorted(surface_ownership._BOUNDED_FIELD_TYPES))
def test_every_canonical_scalar_type_is_a_bounded_identity_claim(persistence, kind):
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    collection = bundle["data_contract"]["surfaces"][-1]["collections"][0]
    collection["fields"] = _fields([("user_id", "string"), ("email", "string"), ("password_hash", "string"),
                                    ("created_at", kind)])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert not any(c["name"] == "users" for c in _saved_collections(context))


def test_camel_case_credential_fields_are_identity_state(persistence):
    context = ownership._context(managed=False)
    bundle = _identity_bundle(
        surface_id="auth_management", mutations=["authenticate_user"],
        collections=(("users", "User", (("userId", "string"), ("passwordHash", "string"), ("email", "string"))),),
    )

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert [c["name"] for c in _saved_collections(context)] == []


def test_camel_case_residual_split_keeps_indexes_on_declared_fields(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    collection = bundle["data_contract"]["surfaces"][-1]["collections"][0]
    collection["fields"] = _fields([
        ("appId", "string"), ("userId", "string"), ("email", "string"), ("passwordHash", "string"),
        ("favoriteColor", "string"),
    ])
    collection["indexes"] = [{"keys": [{"field": "userId", "order": 1}], "unique": True, "sparse": None, "name": None}]

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    residual = next(c for c in _saved_collections(context) if c["name"] == "users")
    declared = {field["name"] for field in residual["fields"]}
    assert declared == {"user_id", "appId", "favoriteColor"}
    assert all(key["field"] in declared for index in residual["indexes"] for key in index["keys"])
    assert [key["field"] for key in residual["indexes"][-1]["keys"]] == ["appId", "user_id"]
    split = _record(summary, "auth")["split_collections"][0]
    assert split["removed_fields"] == ["email", "passwordHash"]


@pytest.mark.parametrize("surface_id,entities,actions,collection,fields", [
    pytest.param("team_members", ["TeamMember"], ["add_team_member"], "team_members",
                 (("user_id", "string"), ("team_id", "string"), ("role", "string")), id="team_member_references_users"),
    pytest.param("tasks", ["Task"], ["assign_task_to_user"], "tasks",
                 (("task_id", "string"), ("user_id", "string"), ("title", "string")), id="task_with_user_id"),
    pytest.param("members", ["User"], ["update_member"], "users",
                 (("user_id", "string"), ("display_name", "string"), ("favorite_genre", "string")),
                 id="user_entity_with_app_data"),
])
def test_app_entities_that_reference_users_stay_app_owned(persistence, surface_id, entities, actions, collection, fields):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    ownership._add_surface(
        bundle, surface_id=surface_id, name="Members", route="/members",
        entities=entities, actions=actions, collection=collection,
    )
    bundle["data_contract"]["surfaces"][-1]["collections"][0]["fields"] = _fields(fields)
    expected_surface = deepcopy(bundle["surface_map"]["surfaces"][-1])
    expected_data = deepcopy(bundle["data_contract"]["surfaces"][-1])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert expected_surface in detach(context.get("design_surface_map"))["surfaces"]
    assert expected_data in detach(context.get("data_contract"))["surfaces"]
    assert summary.await_args.kwargs["summary_payload"]["ownership_normalizations"] == []


def test_app_fields_beside_a_password_keep_their_module(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    ownership._add_surface(
        bundle, surface_id="members", name="Members", route="/members",
        entities=["User"], actions=["update_user"], collection="users",
    )
    bundle["data_contract"]["surfaces"][-1]["collections"][0]["fields"] = _fields(
        (("user_id", "string"), ("email", "string"), ("password_hash", "string"), ("favorite_genre", "string")),
    )

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    members = next(s for s in detach(context.get("design_surface_map"))["surfaces"] if s["surface_id"] == "members")
    assert (members["owner"], members["owned_mutations"]) == ("app", ["update_user"])
    assert _record(summary, "members")["split_collections"][0]["target_surface_id"] == "members"


def test_single_app_module_listing_user_still_receives_the_residual_split(persistence):
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    bundle["data_contract"]["surfaces"][-1]["collections"][0]["fields"].append(
        {"name": "favorite_task_color", "type": "string", "required": False},
    )
    bundle["surface_map"]["surfaces"][0]["primary_entities"] = ["Report", "User"]

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    residual = next(c for c in _saved_collections(context) if c["name"] == "users")
    assert residual["ownership"]["surface_id"] == "reports"


@pytest.mark.parametrize("mutations,app_actions", [
    pytest.param(["create_user", "follow_user", "award_user_badge"], ["follow_user", "award_user_badge"],
                 id="social_actions_naming_users"),
    pytest.param(["create_user", "create_team"], ["create_team"], id="app_entity_action"),
])
def test_identity_surface_with_app_behavior_names_what_stays_app_owned(persistence, mutations, app_actions):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = _identity_bundle(
        entities=["User", "Team"], mutations=mutations,
        collections=(
            ("users", "User", IDENTITY_FIELDS),
            ("teams", "Team", (("id", "string"), ("name", "string"), ("created_at", "datetime"))),
        ),
    )
    submitted = deepcopy(bundle)

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="platform")
    assert result["error"].startswith(
        "App surface 'user_management' claims Mozaiks platform authentication and sessions: "
        "entities ['User'], actions ['create_user']."
    )
    assert (
        f"keep it app-owned with its entities ['Team'], actions {app_actions}, collections ['teams'], pages []"
    ) in result["error"]
    assert "Also remove its user-administration pages ['Users']" in result["error"]
    assert "/admin" not in result["error"]
    assert bundle == submitted


def test_named_auth_surface_action_naming_a_user_is_still_split_out(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    bundle["surface_map"]["surfaces"][-1]["owned_mutations"] = ["login", "update_user_theme"]

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="platform")
    assert "move actions ['update_user_theme'] to an app-owned module surface" in result["error"]


def test_navigation_to_a_user_admin_page_is_removed_not_redirected(persistence, caplog):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = _identity_bundle()
    reports = bundle["experience_spec"]["pages"][0]
    reports["sections"].extend([
        {"id": "manage-users", "primitive": "ActionButton", "intent": "Manage users",
         "config_hint": json.dumps({"label": "Users", "href": "/users/"})},
        {"id": "shortcuts", "primitive": "SurfaceCard", "intent": "Shortcuts",
         "config_hint": json.dumps({
             "links": [{"label": "Users", "href": "/users"}, {"label": "Reports", "href": "/reports"}],
             "action": {"type": "navigate", "href": "/users"}, "title": "Shortcuts",
         })},
    ])

    with caplog.at_level(logging.INFO):
        result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    saved = detach(context.get("experience_spec"))["pages"][0]
    assert [section["id"] for section in saved["sections"]] == ["reports", "shortcuts"]
    assert json.loads(saved["sections"][1]["config_hint"]) == {
        "links": [{"label": "Reports", "href": "/reports"}], "title": "Shortcuts",
    }
    record = _record(summary, "user_management")
    assert record["removed_navigation"] == [
        {"page": "Reports", "section": "manage-users", "route": "/users/", "removed": "section"},
        {"page": "Reports", "section": "shortcuts", "route": "/users", "removed": "links[0]"},
        {"page": "Reports", "section": "shortcuts", "route": "/users", "removed": "action"},
    ]
    assert "redirected_navigation" not in record
    assert "removed_navigation=[Reports/manage-users:/users/(section)" in caplog.text


def test_page_that_only_links_to_a_user_admin_page_is_a_design_decision(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = _identity_bundle()
    bundle["experience_spec"]["pages"][0]["sections"] = [{
        "id": "manage-users", "primitive": "ActionButton", "intent": "Manage users",
        "config_hint": json.dumps({"label": "Users", "href": "/users"}),
    }]

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="platform")
    assert "Page 'Reports' (/reports) only links to ['/users']" in result["error"]


def test_create_user_form_with_a_password_does_not_make_an_admin_page_sign_in(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = _identity_bundle(page=_users_page(form=("email", "password")))
    bundle["experience_spec"]["pages"][0]["sections"].append({
        "id": "manage-users", "primitive": "SurfaceCard", "intent": "Manage users",
        "config_hint": json.dumps({"title": "Admin", "links": [{"href": "/users"}, {"href": "/reports"}]}),
    })

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    record = _record(summary, "user_management")
    assert record["removed_pages"] == [{"name": "Users", "route": "/users", **ADMIN_USERS}]
    assert [item["removed"] for item in record["removed_navigation"]] == ["links[0]"]
    assert "redirected_navigation" not in record


@pytest.mark.parametrize("page", [
    pytest.param(_users_page(columns=("user_id", "email", "favorite_genre")), id="lists_app_fields"),
    pytest.param(_users_page(columns=("display_name", "avatar_url"), primitive="Form"), id="profile_form_lists_nobody"),
    pytest.param(_users_page(columns=("session_id", "created_at", "expires_at")), id="lists_sessions"),
    pytest.param(_users_page(columns=("id", "name", "email", "role", "created_at")), id="fields_the_accounts_do_not_declare"),
])
def test_identity_page_that_is_not_user_administration_stays_an_app_page(persistence, page):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = _identity_bundle(page=page)

    result = inventory._save(context, bundle)

    # The page is app evidence: the surface keeps it and drops only its identity claims.
    ownership._assert_refused(context, result, store_factory, owner="platform")
    assert result["error"].startswith(
        "App surface 'user_management' claims Mozaiks platform authentication and sessions: "
        "entities ['User'], actions ['create_user', 'delete_user']."
    )
    assert "keep it app-owned with its entities [], actions [], collections [], pages ['Users']" in result["error"]
    assert "user-administration" not in result["error"]


def _named_auth_with_users_page(page: dict) -> dict:
    bundle = _auth_bundle()
    auth = bundle["surface_map"]["surfaces"][-1]
    auth.update(primary_entities=["User"], owned_pages=["Authentication", "Users"])
    bundle["data_contract"]["surfaces"][-1]["collections"][0]["entity"] = "User"
    bundle["experience_spec"]["pages"].append(page)
    return bundle


@pytest.mark.parametrize("page", [
    pytest.param(_users_page(columns=("user_id", "email", "favorite_genre")), id="list_with_an_app_field"),
    pytest.param(_users_page(form=("email", "password", "department")), id="create_form_with_an_app_field"),
])
def test_page_listing_accounts_with_app_fields_is_rejected_never_sign_in(persistence, page):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)

    result = inventory._save(context, _named_auth_with_users_page(page))

    ownership._assert_refused(context, result, store_factory, owner="platform")
    assert "Page 'Users' (/users) is owned by 'auth', which normalizes to" in result["error"]
    assert "lists its user accounts, but it also names fields the accounts do not declare" in result["error"]
    assert "move 'Users' to owned_pages of 'reports'" in result["error"]
    assert "/login" not in result["error"] and "/admin" not in result["error"]


@pytest.mark.parametrize("page", [
    pytest.param(_users_page(columns=("display_name", "avatar_url"), primitive="Form"), id="profile_form_lists_nobody"),
    pytest.param(_users_page(columns=("id", "name", "created_at")), id="workspaces_like_list"),
])
def test_named_auth_surface_page_that_is_not_user_administration_names_its_app_owner(persistence, page):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)

    result = inventory._save(context, _named_auth_with_users_page(page))

    ownership._assert_refused(context, result, store_factory, owner="platform")
    assert "Page 'Users' (/users) is owned by 'auth'" in result["error"]
    assert "not a sign-in page" in result["error"] and "user accounts" in result["error"]
    assert "move 'Users' to owned_pages of 'reports'" in result["error"]


def test_named_auth_surface_page_listing_its_accounts_is_user_administration(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)

    result = inventory._save(context, _named_auth_with_users_page(_users_page(columns=("user_id", "email"))))

    assert result["outcome"] == "saved", result
    assert [page["route"] for page in detach(context.get("experience_spec"))["pages"]] == ["/reports"]
    assert _record(summary, "auth")["removed_pages"] == [
        {"name": "Authentication", "route": "/auth"}, {"name": "Users", "route": "/users", **ADMIN_USERS},
    ]


def test_user_administration_names_the_admin_panel_but_no_route():
    catalog = yaml.safe_load(
        workflow_context_path("AppGenerator", "capability_routing.yaml").read_text(encoding="utf-8"),
    )
    declared = catalog["layers"]["runtime_provided"]["surface_ownership"][0]
    rule = SurfaceOwnershipRule.model_validate(declared)
    administration = rule.user_administration
    assert rule.platform_capability == "authentication" and administration is not None
    assert (administration.builtin_panel, administration.admin_page) == ("users", "access")
    # The panel is not served end to end yet: nothing may point a generated app at it.
    assert "route" not in declared["user_administration"]
    assert "/admin" not in json.dumps(declared)
    # AdminRegistryAgent declares the Access page, where the built-in users panel renders.
    agents = (workflow_context_path("AppGenerator", "capability_routing.yaml").parents[2]
              / "workflows" / "AppGenerator" / "agents.yaml").read_text(encoding="utf-8")
    assert any(line.strip().startswith(f"- `{administration.admin_page}` —") for line in agents.splitlines())
    for invalid in ({"builtin_panel": "people"}, {"admin_page": "people"}, {"entity_names": []}):
        with pytest.raises(ValidationError):
            UserAdministration.model_validate({**administration.model_dump(), **invalid})
    with pytest.raises(ValidationError, match="belong to the platform authentication capability"):
        SurfaceOwnershipRule(owner="platform", user_administration=administration.model_dump())
    with pytest.raises(ValidationError, match="must be declared identity entities"):
        SurfaceOwnershipRule.model_validate({
            **declared, "user_administration": {**declared["user_administration"], "entity_names": ["Team"]},
        })
    with pytest.raises(ValidationError, match="must include the canonical writes"):
        SurfaceOwnershipRule.model_validate({**declared, "identity_lifecycle_verbs": ["login"]})


def test_ownership_field_types_derive_from_the_canonical_contract():
    assert surface_ownership._BOUNDED_FIELD_TYPES == set(CANONICAL_FIELD_TYPES) - STRUCTURED_FIELD_TYPES
    assert "date" in surface_ownership._BOUNDED_FIELD_TYPES
    assert module_read_actions._JSON_RECORD_TYPES == set(CANONICAL_FIELD_TYPES) - DATE_FIELD_TYPES
    assert set(module_write_actions._FIELD_TYPES) == set(CANONICAL_FIELD_TYPES)
    assert set(data_contract_fields._PYTHON_KINDS) == set(CANONICAL_FIELD_TYPES)
    SurfaceOwnershipRule(owner="platform", state_field_names=["address"],
                         structured_state_field_types={"address": ["object"]})
    with pytest.raises(ValidationError, match="structured_state_field_types must use"):
        SurfaceOwnershipRule(owner="platform", state_field_names=["address"],
                             structured_state_field_types={"address": ["string"]})


@pytest.mark.parametrize("surface_id,entity,collection,fields,actions", [
    pytest.param("session_management", "Session", "sessions", (
        ("session_id", "string"), ("therapist_id", "string"), ("scheduled_at", "datetime"), ("notes", "string"),
    ), ["update_session", "delete_session"], id="therapy_sessions_on_a_reserved_surface_id"),
    pytest.param("auth", "User", "users", (
        ("user_id", "string"), ("email", "string"), ("password_hash", "string"), ("favorite_genre", "string"),
    ), ["update_user"], id="user_with_app_fields_beside_a_password"),
])
def test_actions_on_an_identity_named_entity_with_app_data_stay_app_behavior(
    persistence, surface_id, entity, collection, fields, actions,
):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    ownership._add_surface(
        bundle, surface_id=surface_id, name="Sessions", route="/sessions",
        entities=[entity], actions=actions, collection=collection,
    )
    bundle["data_contract"]["surfaces"][-1]["collections"][0]["fields"] = _fields(fields)

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="platform")
    assert f"move actions {actions} to an app-owned module surface" in result["error"]


@pytest.mark.parametrize("form", [("email", "password"), ("email", "password", "role")], ids=["credentials", "with_role_claim"])
def test_page_listing_accounts_with_a_create_user_form_is_user_administration(persistence, form):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = _identity_bundle(page=_users_page(form=form))

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert _record(summary, "user_management")["removed_pages"] == [{"name": "Users", "route": "/users", **ADMIN_USERS}]


@pytest.mark.parametrize("auth_first", [True, False], ids=["session_surface_first", "account_surface_first"])
def test_user_admin_page_co_owned_by_platform_surfaces_does_not_depend_on_order(persistence, auth_first):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = _identity_bundle()
    sessions = {
        "surface_id": "auth", "label": "Sessions", "surface_kind": "module", "owner": "app",
        "primary_entities": [], "owned_pages": ["Users"], "owned_mutations": [],
        "source_capability_packs": [], "notes": None,
    }
    surfaces = bundle["surface_map"]["surfaces"]
    surfaces.insert(1 if auth_first else len(surfaces), sessions)

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    removed = [entry.get("removed_pages") for entry in summary.await_args.kwargs["summary_payload"]["ownership_normalizations"]]
    assert removed.count([{"name": "Users", "route": "/users", **ADMIN_USERS}]) == 2


@pytest.mark.parametrize("section,expected", [
    pytest.param(
        {"id": "open-users", "primitive": "ActionButton",
         "config_hint": {"actions": [{"label": "Users", "action_type": "navigate", "href": "/users"}]}},
        None, id="canonical_action_button",
    ),
    pytest.param(
        {"id": "admin", "primitive": "Grid", "config_hint": {"columns": 2, "children": [
            {"primitive": "ActionButton", "config": {"label": "Users", "href": "/users"}},
            {"primitive": "Metric", "config": {"label": "Reports", "value_key": "total_reports"}},
        ]}},
        {"columns": 2, "children": [{"primitive": "Metric", "config": {"label": "Reports", "value_key": "total_reports"}}]},
        id="nested_child_link",
    ),
    pytest.param(
        {"id": "links", "primitive": "SurfaceCard", "config_hint": {"links": [
            {"href": "/users?role=admin"}, {"href": "/users#list"}, {"href": "/reports"},
        ]}},
        {"links": [{"href": "/reports"}]}, id="query_and_fragment",
    ),
])
def test_navigation_to_a_removed_user_admin_page_leaves_nothing_behind(persistence, section, expected):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = _identity_bundle()
    section = {**section, "intent": "Navigate.", "config_hint": json.dumps(section["config_hint"])}
    bundle["experience_spec"]["pages"][0]["sections"].append(section)

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    saved = {s["id"]: s for s in detach(context.get("experience_spec"))["pages"][0]["sections"]}
    if expected is None:
        assert section["id"] not in saved
    else:
        assert json.loads(saved[section["id"]]["config_hint"]) == expected
    assert "/users" not in json.dumps(detach(context.get("experience_spec")))
    assert _record(summary, "user_management")["removed_navigation"]


def test_page_whose_only_button_links_to_a_removed_user_admin_page_is_a_design_decision(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = _identity_bundle()
    bundle["experience_spec"]["pages"][0]["sections"] = [{
        "id": "open-users", "primitive": "ActionButton", "intent": "Open user management",
        "config_hint": json.dumps({"actions": [{"label": "Users", "action_type": "navigate", "href": "/users"}]}),
    }]

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="platform")
    assert "Page 'Reports' (/reports) only links to ['/users']" in result["error"]


@pytest.mark.parametrize("keys", [["email"], ["app_id", "email"]], ids=["email", "app_scoped_email"])
def test_per_user_account_table_unique_on_email_is_recognized(persistence, keys):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = _identity_bundle(
        collections=(("users", "User", (("user_id", "string"), ("email", "string"), ("display_name", "string"))),),
        indexes=({"keys": [{"field": key, "order": 1} for key in keys], "unique": True, "sparse": None, "name": None},),
    )
    collection = bundle["data_contract"]["surfaces"][-1]["collections"][0]
    collection.update(tenancy="per_user", owner_field="user_id")

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert _record(summary, "user_management")["removed_collections"] == ["users"]


@pytest.mark.parametrize("section,expected", [
    pytest.param(
        {"id": "actions", "primitive": "ActionButton", "config_hint": {"actions": [
            {"label": "Users", "action_type": "navigate", "href": "/users"},
            {"label": "Export", "action_type": "workflow", "workflow": "export_reports"},
        ]}},
        {"actions": [{"label": "Export", "action_type": "workflow", "workflow": "export_reports"}]},
        id="action_button_keeps_its_other_action",
    ),
    pytest.param(
        {"id": "grid", "primitive": "Grid", "config_hint": {"columns": 2, "children": [
            {"primitive": "ActionButton", "config": {"label": "Users", "href": "/users"}},
        ]}},
        None, id="layout_whose_children_all_went",
    ),
])
def test_link_removal_keeps_other_actions_and_drops_empty_layouts(persistence, section, expected):
    context = ownership._context(managed=False)
    bundle = _identity_bundle()
    section = {**section, "intent": "Navigate.", "config_hint": json.dumps(section["config_hint"])}
    bundle["experience_spec"]["pages"][0]["sections"].append(section)

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    saved = {s["id"]: s for s in detach(context.get("experience_spec"))["pages"][0]["sections"]}
    if expected is None:
        assert section["id"] not in saved
    else:
        assert json.loads(saved[section["id"]]["config_hint"]) == expected


@pytest.mark.parametrize("entity,where", [
    ("Session", "shared"), ("Sessions", "own_surface"),
], ids=["app_data_in_shared_collections", "plural_entity_spelling"])
def test_identity_named_entity_with_app_data_anywhere_keeps_its_actions(persistence, entity, where):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    ownership._add_surface(
        bundle, surface_id="session_management", name="Sessions", route="/sessions",
        entities=["Session"], actions=["update_session"],
    )
    collection = ownership._collection("session_management", "sessions")
    collection.update(entity=entity, fields=_fields(
        (("session_id", "string"), ("therapist_id", "string"), ("scheduled_at", "datetime")),
    ))
    if where == "shared":
        collection["ownership"] = {"surface_id": "session_management", "surface_kind": "module"}
        bundle["data_contract"]["shared_collections"] = [collection]
    else:
        bundle["data_contract"]["surfaces"][-1]["collections"] = [collection]

    result = inventory._save(context, bundle)

    assert result["outcome"] == "revise", result
    assert "update_session" in result["error"]
    store_factory.assert_not_called()


def test_named_auth_surface_lists_accounts_from_its_identity_records(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    auth = bundle["surface_map"]["surfaces"][-1]
    auth.update(primary_entities=["User"], owned_pages=["Authentication", "Users"])
    collection = bundle["data_contract"]["surfaces"][-1]["collections"][0]
    collection.update(entity="User", fields=_fields((("user_id", "string"), ("email", "string"), ("display_name", "string"))))
    bundle["experience_spec"]["pages"].append(_users_page(columns=("user_id", "email"), form=("email", "password")))

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert _record(summary, "auth")["removed_pages"] == [
        {"name": "Authentication", "route": "/auth"}, {"name": "Users", "route": "/users", **ADMIN_USERS},
    ]


def test_user_admin_page_on_a_session_surface_uses_the_platform_accounts(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = _identity_bundle()
    bundle["surface_map"]["surfaces"][-1]["owned_pages"] = []
    bundle["surface_map"]["surfaces"].append({
        "surface_id": "auth", "label": "Sessions", "surface_kind": "module", "owner": "app",
        "primary_entities": [], "owned_pages": ["Users"], "owned_mutations": [],
        "source_capability_packs": [], "notes": None,
    })

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert _record(summary, "auth")["removed_pages"] == [{"name": "Users", "route": "/users", **ADMIN_USERS}]


def test_account_key_scoped_by_a_claim_owner_field_is_recognized(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = _identity_bundle(
        collections=(("people", "User", (("sub", "string"), ("email", "string"), ("display_name", "string"))),),
        indexes=({"keys": [{"field": "email", "order": 1}], "unique": True, "sparse": None, "name": None},),
    )
    bundle["data_contract"]["surfaces"][-1]["collections"][0].update(tenancy="per_user", owner_field="sub")

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert _record(summary, "user_management")["removed_collections"] == ["people"]


@pytest.mark.parametrize("auth_first", [True, False], ids=["session_surface_first", "account_surface_first"])
def test_removed_navigation_attribution_does_not_depend_on_order(persistence, auth_first):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = _identity_bundle()
    bundle["experience_spec"]["pages"][0]["sections"].append({
        "id": "shortcuts", "primitive": "SurfaceCard", "intent": "Shortcuts",
        "config_hint": json.dumps({"links": [{"href": "/users"}, {"href": "/reports"}]}),
    })
    sibling = {
        "surface_id": "auth", "label": "Sessions", "surface_kind": "module", "owner": "app",
        "primary_entities": [], "owned_pages": ["Users"], "owned_mutations": [],
        "source_capability_packs": [], "notes": None,
    }
    surfaces = bundle["surface_map"]["surfaces"]
    surfaces.insert(1 if auth_first else len(surfaces), sibling)

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert "removed_navigation" in _record(summary, "auth")
    assert "removed_navigation" not in _record(summary, "user_management")


@pytest.mark.parametrize("section,expected,removed", [
    pytest.param(
        {"id": "modal", "primitive": "Modal", "config_hint": {"title": "Team", "description": "Invite teammates.",
                                                              "children": [{"primitive": "ActionButton", "config": {"label": "Users", "href": "/users"}}]}},
        {"title": "Team", "description": "Invite teammates."}, "children", id="modal_keeps_its_own_content",
    ),
    pytest.param(
        {"id": "actions", "primitive": "ActionButton", "config_hint": {"actions": [
            {"label": "Users", "action": {"action_type": "navigate", "href": "/users"}},
            {"label": "Export", "action": {"action_type": "workflow", "workflow": "export_reports"}},
        ]}},
        {"actions": [{"label": "Export", "action": {"action_type": "workflow", "workflow": "export_reports"}}]},
        "actions[0]", id="action_item_goes_with_its_action",
    ),
    pytest.param(
        {"id": "grid", "primitive": "Grid", "config_hint": {"columns": 2, "children": [
            {"primitive": "ActionButton", "config": {"label": "Users", "href": "/users"}},
            {"primitive": "Metric", "config": {"label": "Reports", "value_key": "total_reports"}},
        ]}},
        {"columns": 2, "children": [{"primitive": "Metric", "config": {"label": "Reports", "value_key": "total_reports"}}]},
        "children[0]", id="records_the_outermost_removed_object",
    ),
])
def test_link_removal_records_what_went_and_keeps_content(persistence, section, expected, removed):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = _identity_bundle()
    section = {**section, "intent": "Navigate.", "config_hint": json.dumps(section["config_hint"])}
    bundle["experience_spec"]["pages"][0]["sections"].append(section)

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    saved = {s["id"]: s for s in detach(context.get("experience_spec"))["pages"][0]["sections"]}
    assert json.loads(saved[section["id"]]["config_hint"]) == expected
    assert [item["removed"] for item in _record(summary, "user_management")["removed_navigation"]] == [removed]


def test_app_data_under_an_undeclared_entity_keeps_the_surface_actions(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    ownership._add_surface(
        bundle, surface_id="session_management", name="Sessions", route="/sessions",
        entities=["Session"], actions=["update_session"], collection="appointments",
    )
    collection = bundle["data_contract"]["surfaces"][-1]["collections"][0]
    collection.update(entity="Appointment", fields=_fields(
        (("appointment_id", "string"), ("therapist_id", "string"), ("scheduled_at", "datetime")),
    ))

    result = inventory._save(context, bundle)

    assert result["outcome"] == "revise", result
    assert "update_session" in result["error"]
    store_factory.assert_not_called()


def test_identity_state_on_a_surface_declaring_a_reserved_entity_is_removed(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    bundle["surface_map"]["surfaces"].append({
        "surface_id": "account_menu", "label": "Account", "surface_kind": "module", "owner": "app",
        "primary_entities": ["AuthSession"], "owned_pages": [], "owned_mutations": ["logout"],
        "custom_reads": [], "source_capability_packs": [], "notes": None,
    })
    collection = ownership._collection("account_menu", "login_records")
    collection.update(entity="AuthSession", fields=_fields(
        (("session_id", "string"), ("user_id", "string"), ("expires_at", "datetime")),
    ))
    bundle["data_contract"]["surfaces"].append({"surface_id": "account_menu", "surface_kind": "module", "collections": [collection]})

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert _record(summary, "account_menu")["removed_collections"] == ["login_records"]


def test_surface_holding_identity_and_billing_state_names_each_owner(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=True)
    bundle = _identity_bundle(surface_id="account", entities=["User", "Subscription"])

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="Ambiguous ownership")
    assert "competing owners ['billing_portal', 'platform']" in result["error"]
    assert "entities ['Subscription']" in result["error"]
    assert "entities ['User'], actions ['create_user', 'delete_user']" in result["error"]
