"""Reserved platform identifiers require explicit repair, never a domain-data move."""

from copy import deepcopy

import pytest

from mozaiksai.core.workflow.context.frozen import detach
from tests import test_designdocs_capability_ownership as ownership
from tests import test_designdocs_monetization_inventory as inventory

persistence = inventory.persistence


def _counter_bundle(other_modules: int) -> dict:
    bundle = inventory._bundle(pricing=False)
    if other_modules == 2:
        ownership._add_surface(
            bundle, surface_id="reading_list", name="Reading List", route="/reading",
            entities=["ReadingItem"], actions=["add_reading_item"],
        )
    ownership._add_surface(
        bundle, surface_id="user_sessions", name="Practice Counter", route="/practice",
        entities=["PracticeStats"], actions=["increment_practice_count"], collection="practice_stats",
    )
    surface = bundle["surface_map"]["surfaces"][-1]
    surface["custom_reads"] = ["get_practice_count"]
    collection = bundle["data_contract"]["surfaces"][-1]["collections"][0]
    collection.update(tenancy="per_user", owner_field="user_id", search_by="user_id")
    collection["fields"] = [
        {"name": name, "type": kind, "required": True}
        for name, kind in (
            ("user_id", "string"), ("app_id", "string"),
            ("completed_practices", "integer"), ("last_completed_at", "datetime"),
        )
    ]
    collection["indexes"] = [{
        "keys": [{"field": "app_id", "order": 1}, {"field": "user_id", "order": 1}],
        "unique": True, "name": "practice_stats_owner_unique",
    }]
    return bundle


@pytest.mark.parametrize("other_modules", [1, 2])
def test_reserved_domain_surface_feedback_can_be_repaired_without_losing_app_state(persistence, other_modules):
    store, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = _counter_bundle(other_modules)
    original = deepcopy(bundle)

    refused = inventory._save(context, bundle)

    ownership._assert_refused(context, refused, store_factory, owner="platform")
    assert "Choose an app-specific surface_id" in refused["error"]
    assert "user_sessions" in refused["error"]
    assert "collection ownership.surface_id" in refused["error"]
    assert "page module/action bindings" in refused["error"]
    assert "Preserve" in refused["error"]
    assert bundle == original

    repaired = deepcopy(bundle)
    repaired["surface_map"]["surfaces"][-1]["surface_id"] = "practice_totals"
    group = repaired["data_contract"]["surfaces"][-1]
    group["surface_id"] = "practice_totals"
    group["collections"][0]["ownership"]["surface_id"] = "practice_totals"
    page = repaired["experience_spec"]["pages"][-1]
    page["intent"] = "Review personal practice totals."
    page["sections"][0]["intent"] = "Review personal practice totals."

    accepted = inventory._save(context, repaired)

    assert accepted["outcome"] == "saved", accepted
    assert context.get("design_docs_save_attempts") == 2
    saved_surface = detach(context.get("design_surface_map"))["surfaces"][-1]
    assert saved_surface == repaired["surface_map"]["surfaces"][-1]
    saved_group = detach(context.get("data_contract"))["surfaces"][-1]
    assert saved_group["surface_id"] == "practice_totals"
    saved_collection = saved_group["collections"][0]
    assert saved_collection["entity"] == "PracticeStats"
    assert saved_collection["ownership"]["surface_id"] == "practice_totals"
    assert saved_collection["fields"] == group["collections"][0]["fields"]
    assert saved_collection["indexes"] == group["collections"][0]["indexes"]
    assert detach(context.get("experience_spec"))["pages"][-1] == page
    store.save_data_contract.assert_awaited_once()


@pytest.mark.parametrize("identity", ["credential", "platform_entity", "identity_field"])
def test_reserved_surface_with_identity_evidence_does_not_receive_domain_rename_guidance(persistence, identity):
    _, store_factory, _ = persistence
    context = ownership._context(managed=False)
    bundle = _counter_bundle(2)
    collection = bundle["data_contract"]["surfaces"][-1]["collections"][0]
    if identity == "platform_entity":
        collection["entity"] = "AuthSession"
        bundle["surface_map"]["surfaces"][-1]["primary_entities"] = ["AuthSession"]
    else:
        collection["fields"].append({
            "name": "password_hash" if identity == "credential" else "email",
            "type": "string", "required": True,
        })
    original = deepcopy(bundle)

    result = inventory._save(context, bundle)

    ownership._assert_refused(context, result, store_factory, owner="platform")
    assert "Choose an app-specific surface_id" not in result["error"]
    assert bundle == original
