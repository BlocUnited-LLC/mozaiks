"""Determined ownership repairs survive the real frozen-context save boundary."""

import logging
from copy import deepcopy

import pytest
import yaml

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
def test_live_auth_users_normalizes_and_preserves_auth_page(persistence, surface_kind):
    store, _, summary = persistence
    context = ownership._context(managed=False)
    assert isinstance(context, ContextVariablesBridge)
    bundle = _auth_bundle()
    bundle["surface_map"]["surfaces"][-1]["surface_kind"] = surface_kind
    bundle["data_contract"]["surfaces"][-1]["surface_kind"] = surface_kind
    bundle["data_contract"]["surfaces"][-1]["collections"][0]["ownership"]["surface_kind"] = surface_kind
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


def test_mixed_auth_and_app_fields_are_rejected_without_discarding_data(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    bundle["data_contract"]["surfaces"][-1]["collections"][0]["fields"].append(
        {"name": "favorite_genre", "type": "string", "required": False},
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


@pytest.mark.parametrize("fields", [["user_id"], ["user_id", "favorite_genre"], []])
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
        for name in ("user_id", "favorite_genre")
    ]
    expected_data = deepcopy(bundle["data_contract"]["surfaces"][-1])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert expected_data in detach(context.get("data_contract"))["surfaces"]


def test_unknown_app_collection_on_auth_is_not_silently_removed(persistence, caplog):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = _auth_bundle()
    preference = ownership._collection("auth", "preferences")
    preference["fields"] = [
        {"name": name, "type": "string", "required": True} for name in ("user_id", "theme")
    ]
    bundle["data_contract"]["surfaces"][-1]["collections"].append(preference)
    submitted = deepcopy(bundle)

    with caplog.at_level(logging.INFO):
        result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="platform")
    assert bundle == submitted
    assert "DESIGN_OWNERSHIP_NORMALIZED" not in caplog.text


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
