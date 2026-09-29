"""The DesignDocs save constructs corrections the contract determines, and names the change otherwise.

The live model changes a rejected design at most once and then resubmits it
unchanged until the run is blocked, so a rejection is only right when what to
change is a design decision. Each test here pins one determined correction (and
its record) or one judgment message whose offered change saves. The recorded
live outputs are replayed in test_designdocs_corpus_replay.py; these name each
behavior so a regression reads as what broke.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from factory_app.workflows._shared.surface_ownership import SurfaceOwnershipRule
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.data_contract_fields import (
    DataContractFieldError,
    normalize_structured_defaults,
    validate_collection_fields,
)
from tests import test_designdocs_capability_ownership as ownership
from tests import test_designdocs_monetization_inventory as inventory
from tests import test_designdocs_ownership_normalization as normalization

persistence = inventory.persistence
ROOT = Path(__file__).resolve().parents[1]


def _field(name: str, kind: str, *, default=None, required: bool = False) -> dict:
    return {"name": name, "type": kind, "required": required, "default": default, "enum": None, "nullable": not required}


def _records(summary) -> list[dict]:
    return summary.await_args.kwargs["summary_payload"]["ownership_normalizations"]


def _surface(context, surface_id: str) -> dict:
    return next(s for s in detach(context.get("design_surface_map"))["surfaces"] if s["surface_id"] == surface_id)


def _collection_names(context) -> list[str]:
    contract = detach(context.get("data_contract"))
    return [c["name"] for g in contract["surfaces"] for c in g["collections"]] + [
        c["name"] for c in contract.get("shared_collections") or []
    ]


def _module(bundle: dict, surface_id: str, *, entities, actions, collections=(), page: dict | None = None,
            reads=(), events=(), triggers=()) -> dict:
    """Add an app module surface, its data group and optionally a page it owns."""
    surface = {
        "surface_id": surface_id, "label": surface_id.title(), "surface_kind": "module", "owner": "app",
        "primary_entities": list(entities), "owned_pages": [page["name"]] if page else [],
        "owned_mutations": list(actions), "custom_reads": list(reads), "events_emitted": list(events),
        "workflow_triggers": list(triggers), "source_capability_packs": [], "notes": None,
    }
    bundle["surface_map"]["surfaces"].append(surface)
    bundle["data_contract"]["surfaces"].append({"surface_id": surface_id, "surface_kind": "module", "collections": [
        _records_of(surface_id, *spec[:3], **(spec[3] if len(spec) > 3 else {})) for spec in collections
    ]})
    if page:
        bundle["experience_spec"]["pages"].append(page)
    return surface


def _records_of(surface_id: str, name: str, entity: str, fields, *, unique=(), tenancy="per_user") -> dict:
    collection = ownership._collection(surface_id, name)
    collection.update(
        entity=entity, fields=fields, tenancy=tenancy,
        owner_field="user_id" if tenancy == "per_user" else ("workspace_id" if tenancy == "per_workspace" else None),
        indexes=[{"keys": [{"field": key, "order": 1} for key in unique], "unique": True, "sparse": None,
                  "name": None}] if unique else [],
    )
    return collection


def _page(name: str, route: str, *sections: tuple[str, str, dict | None]) -> dict:
    return {
        "name": name, "route": route, "layout": "full-width", "intent": f"{name} page",
        "sections": [
            {"id": sid, "primitive": primitive, "intent": sid, "config_hint": json.dumps(config) if config else None}
            for sid, primitive, config in sections
        ],
    }


# ---------------------------------------------------------------------------
# Field defaults (data_contract_fields.normalize_structured_defaults)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field,expected", [
    pytest.param(_field("created_at", "datetime", default="now", required=True), None, id="managed_timestamp"),
    pytest.param(_field("updated_at", "date", default="current_timestamp"), None, id="managed_timestamp_date"),
    pytest.param(_field("streak", "number", default=""), None, id="empty_optional"),
    pytest.param(_field("streak", "number", default="", required=True), None, id="empty_required"),
    pytest.param(_field("streak", "integer", default=", "), None, id="undecodable_optional"),
    pytest.param(_field("notify", "boolean", default="True", required=True), "true", id="python_boolean"),
    pytest.param(_field("count", "integer", default="3.0", required=True), "3", id="integral_number"),
    pytest.param(_field("due", "date", default="2026-01-01", required=True), '"2026-01-01"', id="bare_date"),
    pytest.param(_field("at", "datetime", default="2026-01-01T09:00:00"), '"2026-01-01T09:00:00"', id="bare_datetime"),
])
def test_determined_default_corrections(field, expected):
    collection = {"fields": [field]}

    messages = normalize_structured_defaults(collection, "collection 'habits'")

    assert collection["fields"][0]["default"] == expected
    assert len(messages) == 1 and field["name"] in messages[0]
    validate_collection_fields(collection, "collection 'habits'")


def test_a_required_scalar_bad_default_is_still_a_decision_and_null_is_offered():
    collection = {"fields": [_field("streak", "number", default=", ", required=True)]}

    assert normalize_structured_defaults(collection, "c") == []
    with pytest.raises(DataContractFieldError, match=re.escape("valid examples=['0', '1.5'] or null")):
        validate_collection_fields(collection, "c")


def test_string_defaults_and_string_timestamps_are_kept_as_written():
    collection = {"fields": [_field("title", "string", default="now"), _field("created_at", "string", default="now")]}

    assert normalize_structured_defaults(collection, "c") == []
    assert [field["default"] for field in collection["fields"]] == ["now", "now"]


def test_field_corrections_are_recorded_on_the_saved_design(persistence):
    store, _, summary = persistence
    context = inventory._context(monetized=False)
    bundle = inventory._bundle(pricing=False)
    bundle["data_contract"]["surfaces"][0]["collections"] = [_records_of("reports", "reports", "Report", [
        _field("user_id", "string", required=True), _field("created_at", "datetime", default="now", required=True),
    ])]

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    corrections = summary.await_args.kwargs["summary_payload"]["field_normalizations"]
    assert corrections == [
        "data_contract collection 'reports' field 'created_at': managed timestamp default 'now' -> null "
        "(canonical writes stamp it)"
    ]
    database = next(call.kwargs for call in store.upsert_design_doc.await_args_list if call.kwargs["kind"] == "database")
    assert database["extra_fields"]["field_normalizations"] == corrections
    assert "## Data contract field normalizations" in database["content"]


# ---------------------------------------------------------------------------
# MozaiksPay subscription state
# ---------------------------------------------------------------------------


SUBSCRIPTION_FIELDS = [
    _field("subscription_id", "string", required=True), _field("user_id", "string", required=True),
    _field("plan_type", "string", required=True), _field("status", "string", required=True),
    _field("expiration_date", "datetime"),
]


def test_provider_state_filed_on_an_app_module_is_removed_and_the_module_kept(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=True)
    bundle = inventory._bundle(pricing=False)
    bundle["data_contract"]["surfaces"][0]["collections"] = [
        _records_of("reports", "subscriptions", "Subscription", SUBSCRIPTION_FIELDS),
    ]

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert {"surface_id": "reports", "owner": "billing_portal", "removed_collections": ["subscriptions"]} in _records(summary)
    assert _surface(context, "reports")["owner"] == "app"
    assert "subscriptions" not in _collection_names(context)


def test_provider_state_declared_with_a_structured_type_is_still_provider_state(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=True)
    bundle = inventory._bundle(pricing=False)
    _module(bundle, "subscription_management", entities=["Subscription"], actions=[], collections=[
        ("subscriptions", "Subscription", [
            _field("subscription_id", "string", required=True), _field("user_id", "string", required=True),
            _field("plan", "string"), _field("status", "object"),
        ]),
    ])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    record = next(r for r in _records(summary) if r["surface_id"] == "subscription_management")
    assert record["removed_collections"] == ["subscriptions"]


def test_provider_state_beside_an_app_field_names_what_to_remove_and_keep(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=True)
    bundle = inventory._bundle(pricing=False)
    _module(bundle, "subscription_management", entities=["Subscription"], actions=[], collections=[
        ("subscriptions", "Subscription", [*SUBSCRIPTION_FIELDS, _field("referral_code", "string")]),
    ])

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="MozaiksPay")
    assert "remove collection 'subscriptions'" in result["error"]
    assert "billing_portal.get_subscription_status serves plan_id, plan_name, status" in result["error"]
    assert "Its fields ['referral_code'] are not provider state" in result["error"]


def test_a_subscription_surface_alias_maps_to_the_facade_action_and_page_bindings_follow(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=True)
    bundle = inventory._bundle(pricing=False)
    upgrade = _page("Upgrade", "/upgrade", ("cta", "ActionButton", {
        "label": "Upgrade", "data_source": {"module_id": "subscription_management", "action_id": "subscribe_user"},
    }))
    _module(bundle, "subscription_management", entities=[], actions=["subscribe_user"], page=upgrade)

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    record = next(r for r in _records(summary) if r["surface_id"] == "subscription_management")
    assert record["mapped_actions"] == [{"from": "subscribe_user", "to": "start_subscription_checkout"}]
    assert record["rebound_sections"] == [{
        "page": "Upgrade", "section": "cta", "from": "subscription_management.subscribe_user",
        "to": "billing_portal.start_subscription_checkout",
    }]
    page = next(p for p in detach(context.get("experience_spec"))["pages"] if p["name"] == "Upgrade")
    assert json.loads(page["sections"][0]["config_hint"])["data_source"] == {
        "module_id": "billing_portal", "action_id": "start_subscription_checkout",
    }


def test_an_app_surface_subscribe_action_is_not_billing(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=True)
    bundle = inventory._bundle(pricing=False)
    _module(bundle, "newsletter", entities=["NewsletterSignup"], actions=["subscribe_user", "unsubscribe_user"],
            collections=[("newsletter_signups", "NewsletterSignup", [
                _field("newsletter_signup_id", "string", required=True), _field("user_id", "string", required=True),
            ])])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    newsletter = _surface(context, "newsletter")
    assert (newsletter["owner"], newsletter["owned_mutations"]) == ("app", ["subscribe_user", "unsubscribe_user"])
    assert not any(r["surface_id"] == "newsletter" for r in _records(summary))


def test_a_page_an_app_surface_also_owns_stays_the_apps(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=True)
    bundle = inventory._bundle(pricing=False)
    _module(bundle, "subscriptions", entities=["Subscription"], actions=["subscribe_user"])
    _surface_map = bundle["surface_map"]["surfaces"]
    _surface_map[-1]["owned_pages"] = ["Reports"]

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    record = next(r for r in _records(summary) if r["surface_id"] == "subscriptions")
    assert record["released_pages"] == [{"name": "Reports", "owners": ["reports"]}]
    assert "Reports" not in _surface(context, "billing_portal")["owned_pages"]
    assert _surface(context, "reports")["owned_pages"] == ["Reports"]


# ---------------------------------------------------------------------------
# Platform identity (#756 follow-ups D3)
# ---------------------------------------------------------------------------


def test_subscription_status_on_a_users_collection_is_the_account_profile(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    fields = (*normalization.IDENTITY_FIELDS, ("subscription_status", "string"))
    bundle = normalization._identity_bundle(collections=(("users", "User", fields),))

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    record = normalization._record(summary, "user_management")
    assert record["removed_collections"] == ["users"] and "split_collections" not in record


def test_a_crm_account_without_a_password_is_app_data(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    _module(bundle, "crm", entities=["Account"], actions=["create_account", "update_account", "delete_account"],
            page=_page("Accounts", "/accounts", ("list", "DataTable", {"columns": ["name", "email", "website"]})),
            collections=[("accounts", "Account", [
                _field("account_id", "string", required=True), _field("workspace_id", "string", required=True),
                _field("name", "string", required=True), _field("email", "string", required=True),
                _field("website", "string"),
            ], {"unique": ("workspace_id", "email"), "tenancy": "per_workspace"})])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    crm = _surface(context, "crm")
    assert (crm["owner"], crm["primary_entities"], crm["owned_pages"]) == ("app", ["Account"], ["Accounts"])
    assert "accounts" in _collection_names(context)
    assert not any(r["surface_id"] == "crm" for r in _records(summary))


def test_an_account_holding_a_password_is_platform_identity(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    _module(bundle, "accounts", entities=["Account"], actions=["create_account", "delete_account"],
            page=_page("Accounts", "/accounts", ("list", "DataTable", {"columns": ["account_id", "email"]})),
            collections=[("accounts", "Account", [
                _field("account_id", "string", required=True), _field("email", "string", required=True),
                _field("password_hash", "string", required=True), _field("created_at", "datetime"),
            ], {"unique": ("email",), "tenancy": "app_wide"})])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    record = normalization._record(summary, "accounts")
    assert record["removed_collections"] == ["accounts"]
    assert record["removed_mutations"] == ["create_account", "delete_account"]
    assert record["removed_pages"] == [{"name": "Accounts", "route": "/accounts", **normalization.ADMIN_USERS}]


def test_a_session_store_paired_with_login_is_platform_identity(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    _module(bundle, "access", entities=["Session"], actions=["login_user", "logout_user"], collections=[
        ("sessions", "Session", [
            _field("session_id", "string", required=True), _field("user_id", "string", required=True),
            _field("created_at", "datetime"),
        ]),
    ])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    record = normalization._record(summary, "access")
    assert (record["removed_collections"], record["removed_mutations"]) == (["sessions"], ["login_user", "logout_user"])
    assert _surface(context, "access")["owner"] == "platform"


def _gamification(bundle: dict, *, actions, events=(), triggers=(), page=None) -> None:
    _module(bundle, "gamification", entities=["User"], actions=actions, events=events, triggers=triggers, page=page,
            collections=[("users", "User", [
                _field("user_id", "string", required=True), _field("xp", "integer"), _field("level", "integer"),
            ], {"unique": ("user_id",)})])


def test_sign_in_leaves_a_surface_that_keeps_app_data_about_users(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    _gamification(bundle, actions=["login_user", "refresh_user", "award_xp"],
                  events=["domain.users.user_logged_in", "domain.users.xp_awarded"])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    surface = _surface(context, "gamification")
    assert surface["owned_mutations"] == ["refresh_user", "award_xp"], "refresh is not sign-in"
    assert surface["events_emitted"] == ["domain.users.xp_awarded"]
    record = normalization._record(summary, "gamification")
    assert (record["removed_mutations"], record["removed_events"]) == (["login_user"], ["domain.users.user_logged_in"])


def test_a_section_bound_to_a_removed_sign_in_action_goes_with_it(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    page = _page(
        "Leaderboard", "/leaderboard",
        ("board", "DataTable", {"columns": ["xp", "level"]}),
        ("sign-in", "Form", {"data_source": {"module_id": "gamification", "action_id": "login_user"}}),
    )
    _gamification(bundle, actions=["login_user", "award_xp"], page=page)

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    saved = next(p for p in detach(context.get("experience_spec"))["pages"] if p["name"] == "Leaderboard")
    assert [section["id"] for section in saved["sections"]] == ["board"]
    assert normalization._record(summary, "gamification")["removed_sections"] == [{
        "page": "Leaderboard", "section": "sign-in", "binding": "gamification.login_user", "removed": "section",
    }]


def test_a_trigger_on_a_surfaces_own_removed_sign_in_event_is_a_decision(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    _gamification(bundle, actions=["login_user"], events=["domain.users.user_logged_in"],
                  triggers=["domain.users.user_logged_in"])

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="sign-in events the app cannot emit")
    assert "surface 'gamification' lists ['domain.users.user_logged_in'] in workflow_triggers" in result["error"]


def test_an_account_surface_is_the_platforms_while_another_keeps_user_app_data(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = normalization._identity_bundle()
    _module(bundle, "profiles", entities=["User"], actions=["update_user_theme"], collections=[
        ("user_profiles", "User", [_field("user_id", "string", required=True), _field("theme", "string")]),
    ])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    record = normalization._record(summary, "user_management")
    assert record["removed_mutations"] == ["create_user", "delete_user"]
    assert _surface(context, "user_management")["owner"] == "platform"
    profiles = _surface(context, "profiles")
    assert (profiles["owner"], profiles["owned_mutations"]) == ("app", ["update_user_theme"])


# ---------------------------------------------------------------------------
# Users records no surface declares
# ---------------------------------------------------------------------------


def _unclaimed(bundle: dict, fields, *, unique=()) -> None:
    bundle["data_contract"]["surfaces"][0]["collections"].append(
        _records_of("reports", "users", "User", fields, unique=unique, tenancy="app_wide"),
    )


def test_unclaimed_identity_only_users_are_removed(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    _unclaimed(bundle, [_field("user_id", "string", required=True), _field("email", "string", required=True)],
               unique=("email",))

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert normalization._record(summary, "reports")["removed_collections"] == ["users"]
    assert _surface(context, "reports")["primary_entities"] == ["Report"]


def test_unclaimed_user_app_data_one_row_per_user_splits(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    _unclaimed(bundle, [
        _field("user_id", "string", required=True), _field("email", "string", required=True),
        _field("streak_goal", "integer"),
    ], unique=("email",))

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    split = normalization._record(summary, "reports")["split_collections"][0]
    assert (split["removed_fields"], split["retained_fields"]) == (["email"], ["user_id", "streak_goal"])


def test_unclaimed_user_records_many_per_user_are_left_to_the_design(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    _unclaimed(bundle, [_field("user_id", "string", required=True), _field("device_token", "string", required=True)])

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="names platform identity")
    assert "keep only app data in 'users' under an app entity of its own" in result["error"]


# ---------------------------------------------------------------------------
# Entities of the collections a module owns
# ---------------------------------------------------------------------------


def _notes(bundle: dict, surface_id: str, name: str, entity: str = "Note") -> None:
    group = next(g for g in bundle["data_contract"]["surfaces"] if g["surface_id"] == surface_id)
    group["collections"].append(_records_of(surface_id, name, entity, [
        _field(f"{name[:-1]}_id", "string", required=True), _field("user_id", "string", required=True),
    ]))


def test_a_module_lists_the_entity_of_a_collection_it_owns(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    _notes(bundle, "reports", "report_notes", "ReportNote")

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert _surface(context, "reports")["primary_entities"] == ["Report", "ReportNote"]
    assert {"surface_id": "reports", "owner": "app", "removed_collections": [], "added_entities": ["ReportNote"]} in (
        _records(summary)
    )


def test_an_entity_two_modules_hold_is_named_on_both(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    _module(bundle, "journal", entities=[], actions=[])
    _notes(bundle, "reports", "report_notes")
    _notes(bundle, "journal", "journal_notes")

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="no module owns it alone")
    assert "Give each of these collections an entity of its own" in result["error"]


@pytest.mark.parametrize("target_has_records,offers_move", [(False, True), (True, False)])
def test_an_entity_another_module_declares_offers_the_move_only_when_it_saves(persistence, target_has_records,
                                                                              offers_move):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    _module(bundle, "notes", entities=["Note"], actions=[])
    if target_has_records:
        _notes(bundle, "notes", "notes")
    _notes(bundle, "reports", "report_notes")

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="is declared by ['notes']")
    assert ("Move 'report_notes' into the data_contract group of 'notes'" in result["error"]) is offers_move
    assert "entity of its own" in result["error"]


def test_a_differently_spelled_entity_offers_the_declared_spelling(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    _notes(bundle, "reports", "reports", "Reports")

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="valid choices=['Report']")
    assert "Use the declared spelling 'Report'" in result["error"]


def test_a_collection_follows_its_owners_kind(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    bundle["data_contract"]["surfaces"][0]["collections"] = [_records_of("reports", "reports", "Report", [
        _field("user_id", "string", required=True),
    ])]
    bundle["data_contract"]["surfaces"][0]["collections"][0]["ownership"]["surface_kind"] = "ui_only"

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert {"surface_id": "reports", "owner": "app", "removed_collections": [],
            "mirrored_collection_kinds": ["reports"]} in _records(summary)


# ---------------------------------------------------------------------------
# Catalog validation and the prompt's own example
# ---------------------------------------------------------------------------


def _auth_rule() -> dict:
    catalog = yaml.safe_load((ROOT / "factory_app/build_context/AppGenerator/capability_routing.yaml").read_text(
        encoding="utf-8",
    ))
    return dict(catalog["layers"]["runtime_provided"]["surface_ownership"][0])


@pytest.mark.parametrize("change,message", [
    ({"sign_in_verbs": ["teleport"]}, "sign_in_verbs must be declared identity_lifecycle_verbs"),
    ({"sign_in_facts": ["teleported"]}, "sign_in_facts must be declared identity_lifecycle_facts"),
    ({"sign_in_credential_fields": ["api_key"]}, "sign_in_credential_fields must be declared identity_evidence_fields"),
    ({"credential_entity_names": []}, "declared together"),
    ({"action_aliases": {"subscribe_user": "login"}}, "map onto a managed facade's actions"),
])
def test_the_identity_catalog_rejects_inconsistent_declarations(change, message):
    with pytest.raises(ValidationError, match=re.escape(message)):
        SurfaceOwnershipRule.model_validate({**_auth_rule(), **change})


def test_the_prompt_example_saves_without_corrections(persistence):
    _, _, summary = persistence
    agent = yaml.safe_load((ROOT / "factory_app/workflows/DesignDocs/agents.yaml").read_text(encoding="utf-8"))
    sections = {section["id"]: section["content"] for section in agent["agents"][0]["prompt_sections"]}
    bundle = json.loads(re.search(r"```json\n(.*?)\n```", sections["output_format"], re.S).group(1))
    context = inventory._context(monetized=False)

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert _records(summary) == []
    assert "field_normalizations" not in summary.await_args.kwargs["summary_payload"]
    owned = {page for surface in detach(context.get("design_surface_map"))["surfaces"] for page in surface["owned_pages"]}
    assert owned == {page["name"] for page in detach(context.get("experience_spec"))["pages"]}


def _facade_rule() -> dict:
    contract = yaml.safe_load((ROOT / "factory_app/build_context/mozaikspay/contract.yaml").read_text(encoding="utf-8"))
    return dict(contract["surface_ownership"][0])


@pytest.mark.parametrize("change,message", [
    ({"state_readers": {"get_subscription_status": ["portal_url"]}}, "state_readers must serve declared"),
    ({"sign_in_facts": ["logged_in"], "identity_lifecycle_facts": ["logged_in"]},
     "sign-in verbs and lifecycle facts belong to the platform authentication capability"),
    ({"credential_entity_names": ["Wallet"], "sign_in_credential_fields": ["token_balance"]},
     "credential-gated identity entities belong to the platform authentication capability"),
])
def test_the_facade_catalog_rejects_identity_declarations(change, message):
    with pytest.raises(ValidationError, match=re.escape(message)):
        SurfaceOwnershipRule.model_validate({**_facade_rule(), **change})


def test_an_alias_must_name_a_facade_action(monkeypatch):
    from factory_app.workflows._shared import surface_ownership

    context = ownership._context(managed=True)
    original = surface_ownership.managed_pack_contracts

    def contracts(context_variables):
        declared = original(context_variables)
        for contract in declared:
            for rule in contract.get("surface_ownership") or []:
                rule["action_aliases"] = {"subscribe_user": "teleport_subscription"}
        return declared

    monkeypatch.setattr(surface_ownership, "managed_pack_contracts", contracts)
    with pytest.raises(ValueError, match="must name actions of facade 'billing_portal'"):
        surface_ownership._ownership_rules(context, include_default_subscription=True)


# ---------------------------------------------------------------------------
# Credential-gated Account, facade matching and page bindings (review rounds 2-3)
# ---------------------------------------------------------------------------


def test_a_password_managers_saved_logins_are_app_data(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    _module(bundle, "vault", entities=["Account"], actions=["create_account", "update_account", "delete_account"],
            collections=[("saved_accounts", "Account", [
                _field("account_id", "string", required=True), _field("user_id", "string", required=True),
                _field("website", "string"), _field("username", "string"), _field("password", "string"),
            ])])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    vault = _surface(context, "vault")
    assert (vault["owner"], vault["owned_mutations"]) == ("app", ["create_account", "update_account", "delete_account"])
    assert not any(r["surface_id"] == "vault" for r in _records(summary))


def test_an_undeclared_account_holding_a_password_hash_is_removed(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    bundle["data_contract"]["surfaces"][0]["collections"].append(_records_of("reports", "logins", "Account", [
        _field("account_id", "string", required=True), _field("email", "string", required=True),
        _field("password_hash", "string", required=True),
    ], unique=("email",), tenancy="app_wide"))

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert normalization._record(summary, "reports")["removed_collections"] == ["logins"]


def test_an_undeclared_crm_account_is_declared_on_its_module(persistence):
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    _module(bundle, "crm", entities=["Contact"], actions=[], collections=[
        ("accounts", "Account", [
            _field("account_id", "string", required=True), _field("user_id", "string", required=True),
            _field("name", "string"), _field("email", "string"),
        ]),
    ])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert _surface(context, "crm")["primary_entities"] == ["Contact", "Account"]


def test_sign_in_leaves_an_account_surface_that_keeps_app_data(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    _module(bundle, "members", entities=["Account"], actions=["login_account", "award_badge"], collections=[
        ("accounts", "Account", [
            _field("account_id", "string", required=True), _field("user_id", "string", required=True),
            _field("email", "string", required=True), _field("password_hash", "string", required=True),
            _field("badge_count", "integer"),
        ], {"unique": ("email",)}),
    ])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert _surface(context, "members")["owned_mutations"] == ["award_badge"]
    assert normalization._record(summary, "members")["removed_mutations"] == ["login_account"]


def test_a_provider_named_entity_on_an_app_surface_is_the_designs_call(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=True)
    bundle = inventory._bundle(pricing=False)
    bundle["data_contract"]["surfaces"][0]["collections"] = [_records_of("reports", "channel_follows", "Subscription", [
        _field("subscription_id", "string", required=True), _field("user_id", "string", required=True),
        _field("status", "string"),
    ])]

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="is named for MozaiksPay")
    assert "remove it: billing_portal.get_subscription_status serves" in result["error"]
    assert "give it an app entity of its own (for example ReportsSubscription)" in result["error"]


def test_a_reserved_collection_name_beside_an_app_entity_is_the_designs_call(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=True)
    bundle = inventory._bundle(pricing=False)
    _module(bundle, "newsletter", entities=["NewsletterSubscription"], actions=[], collections=[
        ("subscriptions", "NewsletterSubscription", [
            _field("subscription_id", "string", required=True), _field("user_id", "string", required=True),
            _field("type", "string"), _field("status", "string"),
        ]),
    ])

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="is named for MozaiksPay")
    assert "rename the collection (for example newsletter_subscriptions)" in result["error"]


def test_a_provider_record_with_its_own_id_on_a_billing_surface_is_removed(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=True)
    bundle = inventory._bundle(pricing=False)
    _module(bundle, "subscription_management", entities=["Subscription"], actions=[], collections=[
        ("user_plans", "Subscription", [
            _field("user_plan_id", "string", required=True), _field("user_id", "string", required=True),
            _field("plan", "string"), _field("status", "string"), _field("expires_at", "datetime"),
        ]),
    ])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    record = next(r for r in _records(summary) if r["surface_id"] == "subscription_management")
    assert record["removed_collections"] == ["user_plans"]


@pytest.mark.parametrize("name,fields", [
    pytest.param("users", [
        _field("user_id", "string", required=True), _field("email", "string", required=True),
        _field("password_hash", "string", required=True),
    ], id="credentials"),
    pytest.param("subscriptions", SUBSCRIPTION_FIELDS, id="reserved_name"),
])
def test_records_that_are_the_rules_wherever_filed_are_removed_despite_an_app_owner(persistence, name, fields):
    _, _, summary = persistence
    context = ownership._context(managed=True)
    bundle = inventory._bundle(pricing=False)
    _module(bundle, "journal", entities=["Entry"], actions=[])
    collection = _records_of("journal", name, "User" if name == "users" else "Subscription", fields)
    bundle["data_contract"]["surfaces"][0]["collections"] = [collection]  # filed under reports, owned by journal

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert any(r["surface_id"] == "journal" and name in r["removed_collections"] for r in _records(summary))


def test_a_bare_type_in_a_billing_subscription_is_provider_state(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=True)
    bundle = inventory._bundle(pricing=False)
    _module(bundle, "subscription", entities=["Subscription"], actions=[], collections=[
        ("subscriptions", "Subscription", [
            _field("subscription_id", "string", required=True), _field("user_id", "string", required=True),
            _field("status", "string"), _field("type", "string"),
        ]),
    ])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    record = next(r for r in _records(summary) if r["surface_id"] == "subscription")
    assert record["removed_collections"] == ["subscriptions"]


def test_an_endpoint_binding_follows_the_alias(persistence):
    context = ownership._context(managed=True)
    bundle = inventory._bundle(pricing=False)
    upgrade = _page("Upgrade", "/upgrade", ("cta", "ActionButton", {
        "label": "Upgrade", "api_endpoint": "/api/modules/subscription_management/subscribe_user",
    }))
    _module(bundle, "subscription_management", entities=[], actions=["subscribe_user"], page=upgrade)

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    page = next(p for p in detach(context.get("experience_spec"))["pages"] if p["name"] == "Upgrade")
    assert json.loads(page["sections"][0]["config_hint"])["api_endpoint"] == (
        "/api/modules/billing_portal/start_subscription_checkout"
    )


def test_a_binding_to_an_action_the_facade_does_not_serve_is_named(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=True)
    bundle = inventory._bundle(pricing=False)
    compare = _page("Compare", "/compare", ("cta", "ActionButton", {
        "label": "Compare", "data_source": {"module_id": "subscription_management", "action_id": "compare_plans"},
    }))
    _module(bundle, "subscription_management", entities=[], actions=[], page=compare)

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="does not serve 'compare_plans'")


def test_a_page_that_only_binds_a_removed_sign_in_action_goes_with_it(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    welcome = _page("Welcome", "/welcome", ("go", "ActionButton", {
        "label": "Go", "data_source": {"module_id": "gamification", "action_id": "login_user"},
    }))
    _gamification(bundle, actions=["login_user"], page=welcome)

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert normalization._record(summary, "gamification")["removed_pages"] == [{"name": "Welcome", "route": "/welcome"}]


def test_another_surfaces_page_left_only_with_platform_sign_in_is_a_decision(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    bundle["experience_spec"]["pages"][0]["sections"] = [{
        "id": "go", "primitive": "ActionButton", "intent": "Sign in",
        "config_hint": json.dumps({"data_source": {"module_id": "gamification", "action_id": "login_user"}}),
    }]
    _gamification(bundle, actions=["login_user"])

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="only binds 'gamification' actions")


def test_an_app_page_with_a_password_field_stays_the_apps(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    settings = _page(
        "Settings", "/settings",
        ("prefs", "Form", {"fields": [{"name": "level"}], "data_source": {"module_id": "gamification",
                                                                             "action_id": "award_xp"}}),
        ("password", "Form", {"fields": [{"name": "password"}]}),
    )
    _gamification(bundle, actions=["login_user", "award_xp"], page=settings)

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert "Settings" in {page["name"] for page in detach(context.get("experience_spec"))["pages"]}
    assert "removed_pages" not in normalization._record(summary, "gamification")


def test_a_sign_in_page_on_a_surface_keeping_app_data_is_the_auth_contracts(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    sign_in = _page("Sign In", "/account/login", ("form", "Form", {"fields": [{"name": "email"}, {"name": "password"}]}))
    _gamification(bundle, actions=["login_user"], page=sign_in)
    bundle["experience_spec"]["pages"][0]["sections"][0]["config_hint"] = json.dumps({"href": "/account/login"})

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    record = normalization._record(summary, "gamification")
    assert record["removed_pages"] == [{"name": "Sign In", "route": "/account/login"}]
    assert record["redirected_navigation"][0]["from"] == "/account/login"
    assert _surface(context, "gamification")["owned_pages"] == []


def test_sign_in_leaves_a_surface_whose_identity_store_was_removed(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    _module(bundle, "profiles", entities=["Profile"], actions=["login_user", "update_profile"], collections=[
        ("users", "User", [
            _field("user_id", "string", required=True), _field("email", "string", required=True),
            _field("password_hash", "string", required=True),
        ], {"unique": ("email",)}),
    ])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    record = normalization._record(summary, "profiles")
    assert (record["removed_collections"], record["removed_mutations"]) == (["users"], ["login_user"])


def test_memberships_filed_under_user_are_left_to_the_design(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    _module(bundle, "teams", entities=["Team"], actions=["add_team_member"], collections=[
        ("team_members", "User", [
            _field("user_id", "string", required=True), _field("workspace_id", "string", required=True),
            _field("role", "string"), _field("name", "string"), _field("email", "string"),
        ], {"unique": ("workspace_id", "user_id"), "tenancy": "per_workspace"}),
    ])

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="names platform identity")


def test_an_identity_surface_with_a_provider_collection_is_still_the_platforms(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=True)
    bundle = normalization._identity_bundle()
    group = next(g for g in bundle["data_contract"]["surfaces"] if g["surface_id"] == "user_management")
    group["collections"].append(_records_of("user_management", "subscriptions", "Subscription", SUBSCRIPTION_FIELDS))

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert _surface(context, "user_management")["owner"] == "platform"
    records = [r for r in _records(summary) if r["surface_id"] == "user_management"]
    assert {name for r in records for name in r["removed_collections"]} == {"subscriptions", "users"}
    assert "Users" not in {page["name"] for page in detach(context.get("experience_spec"))["pages"]}


def test_the_mixed_identity_message_names_a_listing_page_with_app_columns(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = normalization._identity_bundle(
        entities=["User", "Team"], mutations=["create_user", "create_team"],
        page=normalization._users_page(columns=("user_id", "email", "favorite_genre")),
        collections=(("users", "User", normalization.IDENTITY_FIELDS),
                     ("teams", "Team", (("id", "string"), ("name", "string")))),
    )

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="platform")
    assert "Page 'Users' lists 'user_management''s user accounts but also names fields" in result["error"]
    assert "['favorite_genre']" in result["error"]


def test_a_conflicting_owner_is_told_to_keep_the_records_with_the_identity_surface_first(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = normalization._identity_bundle(collections=(
        ("users", "User", normalization.IDENTITY_FIELDS),
        ("user_profiles", "User", (("user_id", "string"), ("display_name", "string"))),
    ))
    group = next(g for g in bundle["data_contract"]["surfaces"] if g["surface_id"] == "user_management")
    group["collections"][1]["ownership"]["surface_id"] = "reports"

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="platform")
    assert "Declare it once: set its ownership.surface_id to 'user_management'" in result["error"]
    assert "names platform identity" in result["error"]


def test_a_declared_spelling_is_offered_only_when_it_saves(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    bundle["data_contract"]["surfaces"][0]["collections"] = [
        _records_of("reports", "reports", "Report", [_field("user_id", "string", required=True)]),
        _records_of("reports", "report_archive", "Reports", [_field("user_id", "string", required=True)]),
    ]

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="report_archive")
    assert "Use the declared spelling" not in result["error"]


def test_a_ui_only_move_asks_for_an_own_entity_when_a_module_declares_it(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    bundle["surface_map"]["surfaces"].append({
        "surface_id": "stats_view", "label": "Stats", "surface_kind": "ui_only", "owner": "app",
        "primary_entities": [], "owned_pages": [], "owned_mutations": [], "custom_reads": [],
        "source_capability_packs": [], "notes": None,
    })
    stats = _records_of("stats_view", "report_stats", "Report", [_field("user_id", "string", required=True)])
    stats["ownership"]["surface_kind"] = "ui_only"
    bundle["data_contract"]["surfaces"].append({"surface_id": "stats_view", "surface_kind": "ui_only",
                                               "collections": [stats]})

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="holds no durable records")
    assert "ownership.surface_kind 'module' and an entity of its own" in result["error"]


# ---------------------------------------------------------------------------
# Review round 3: records one per user, provider record ids, sign-in pages
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("placement", ["grouped", "shared"])
@pytest.mark.parametrize("with_workflow", [False, True])
def test_with_no_ai_a_module_collection_declared_workflow_write_is_module_written(persistence, placement,
                                                                                  with_workflow):
    _, _, summary = persistence
    context = inventory._context(monetized=False)
    bundle = inventory._bundle(pricing=False)
    collection = _records_of("reports", "reports", "Report", [_field("user_id", "string", required=True)])
    collection["lifecycle"]["write_mode"] = "workflow_write"
    if placement == "grouped":
        bundle["data_contract"]["surfaces"][0]["collections"] = [collection]
    else:
        bundle["data_contract"]["shared_collections"] = [collection]
    if with_workflow:
        bundle["surface_map"]["surfaces"].append({
            "surface_id": "suggestion_flow", "label": "Suggestions", "surface_kind": "workflow", "owner": "app",
            "primary_entities": [], "owned_pages": [], "owned_mutations": [], "custom_reads": [],
            "source_capability_packs": [], "notes": None,
        })

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    contract = detach(context.get("data_contract"))
    saved = next(c for g in [*contract["surfaces"], {"collections": contract["shared_collections"]}]
                 for c in g["collections"] if c["name"] == "reports")
    assert saved["lifecycle"]["write_mode"] == "module_action"
    assert any(r.get("module_written_collections") == ["reports"] for r in _records(summary))


def test_unclaimed_user_accounts_with_a_password_are_one_per_user_and_removed(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    _unclaimed(bundle, [
        _field("user_id", "string", required=True), _field("email", "string", required=True),
        _field("password_hash", "string", required=True),
    ])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert normalization._record(summary, "reports")["removed_collections"] == ["users"]


@pytest.mark.parametrize("name,fields,unique,tenancy", [
    pytest.param("user_integrations", [("user_id", True), ("service", False), ("access_token", False),
                                       ("refresh_token", False)], (), "per_user", id="integration_tokens"),
    pytest.param("calendar_tokens", [("user_id", True), ("provider", False), ("access_token", False),
                                     ("expires_at", False)], ("user_id", "provider"), "per_user",
                 id="tokens_per_provider"),
    pytest.param("team_members", [("user_id", True), ("workspace_id", True), ("role", False), ("email", False)],
                 ("workspace_id", "email"), "per_workspace", id="membership_by_email"),
])
def test_unclaimed_user_records_many_per_user_get_the_entity_message(persistence, name, fields, unique, tenancy):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    bundle["data_contract"]["surfaces"][0]["collections"].append(_records_of(
        "reports", name, "User", [_field(field, "string", required=required) for field, required in fields],
        unique=unique, tenancy=tenancy,
    ))

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="names platform identity")


def test_an_account_holding_a_hashed_password_is_platform_identity(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    _module(bundle, "accounts", entities=["Account"], actions=["create_account"], collections=[
        ("accounts", "Account", [
            _field("account_id", "string", required=True), _field("email", "string", required=True),
            _field("hashed_password", "string", required=True),
        ], {"unique": ("email",), "tenancy": "app_wide"}),
    ])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert normalization._record(summary, "accounts")["removed_collections"] == ["accounts"]


def test_a_provider_record_keyed_by_its_entity_id_is_removed(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=True)
    bundle = inventory._bundle(pricing=False)
    _module(bundle, "subscription_management", entities=[], actions=[], collections=[
        ("billing_state", "SubscriptionAssignment", [
            _field("subscription_assignment_id", "string", required=True), _field("user_id", "string", required=True),
            _field("plan_id", "string"), _field("status", "string"),
        ]),
    ])

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    record = next(r for r in _records(summary) if r["surface_id"] == "subscription_management")
    assert record["removed_collections"] == ["billing_state"]


def test_an_app_foreign_key_in_a_provider_collection_is_named_as_app_data(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=True)
    bundle = inventory._bundle(pricing=False)
    subscriptions = _records_of("reports", "subscriptions", "Subscription", [
        _field("subscription_id", "string", required=True), _field("user_id", "string", required=True),
        _field("report_id", "string", required=True), _field("status", "string"),
    ])
    subscriptions["search_by"] = "report_id"
    bundle["data_contract"]["surfaces"][0]["collections"] = [subscriptions]

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="remove collection 'subscriptions'")
    assert "Its fields ['report_id'] are not provider state" in result["error"]


def test_provider_state_under_a_reserved_name_is_removed_whatever_entity_names_it(persistence):
    _, _, summary = persistence
    context = ownership._context(managed=True)
    bundle = inventory._bundle(pricing=False)
    bundle["data_contract"]["surfaces"][0]["collections"] = [
        _records_of("reports", "subscriptions", "UserPlan", SUBSCRIPTION_FIELDS),
    ]

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert {"surface_id": "reports", "owner": "billing_portal", "removed_collections": ["subscriptions"]} in (
        _records(summary)
    )
    assert _surface(context, "reports")["primary_entities"] == ["Report"]


def test_a_sign_in_page_another_surface_owns_is_left_to_it(persistence):
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    sign_in = _page("Sign In", "/account/login", ("form", "Form", {"fields": [{"name": "email"}, {"name": "password"}]}))
    _gamification(bundle, actions=["login_user"], page=sign_in)
    bundle["surface_map"]["surfaces"][0]["owned_pages"] = ["Reports", "Sign In"]

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert "Sign In" in {page["name"] for page in detach(context.get("experience_spec"))["pages"]}


def test_removing_the_only_page_as_sign_in_is_a_decision(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    bundle["experience_spec"]["pages"] = []
    bundle["surface_map"]["surfaces"][0]["owned_pages"] = []
    sign_in = _page("Sign In", "/account/login", ("form", "Form", {"fields": [{"name": "email"}, {"name": "password"}]}))
    _gamification(bundle, actions=["login_user"], page=sign_in)

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="leaves no approved pages")


def test_a_valid_choice_another_collection_holds_is_not_offered(persistence):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = inventory._bundle(pricing=False)
    bundle["data_contract"]["surfaces"][0]["collections"] = [
        _records_of("reports", "reports", "Report", [_field("user_id", "string", required=True)]),
        _records_of("reports", "report_archive", "Reports", [_field("user_id", "string", required=True)]),
    ]

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="report_archive")
    assert "use one of" not in result["error"]
    assert "each collection holds its own entity's records" in result["error"]


def test_account_credentials_must_be_declared_evidence():
    with pytest.raises(ValidationError, match="account_credential_fields must be declared identity_evidence_fields"):
        SurfaceOwnershipRule.model_validate({**_auth_rule(), "account_credential_fields": ["api_key"]})
