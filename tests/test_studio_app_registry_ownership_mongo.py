"""Signed-user Studio requests exercise real registry persistence boundaries."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import jwt
import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from factory_app.app.modules.app_registry.backend.handler import AppRegistryModule
from factory_app.app.modules.app_registry.backend.repo import AppRegistryRepo
from factory_app.app.modules.app_registry.backend.schemas import (
    GenesisAcceptanceReceipt,
    GenesisImportClaim,
)
from factory_app.app.modules.app_registry.backend.service import AppRegistryService
from mozaiksai.core.artifacts.models import (
    BuildRecord,
    BuildRecordStatus,
    BuildRecordValidationStatus,
)
from mozaiksai.core.artifacts.store import BuildRecordStore
from mozaiksai.core.auth import reset_auth_adapter
from mozaiksai.core.session.build_binding import RunBuildBinding


@pytest_asyncio.fixture
async def registry_host(monkeypatch):
    database_name = f"registry_ownership_{uuid4().hex}"
    mongo = AsyncIOMotorClient(os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017"), serverSelectionTimeoutMS=1500)
    try:
        await mongo.admin.command("ping")
    except Exception:
        mongo.close()
        if os.getenv("MOZAIKS_REQUIRE_REAL_MONGO"):
            pytest.fail("Real Mongo is required for app registry ownership acceptance")
        pytest.skip("Mongo is unavailable")
    collection = mongo[database_name]["AppRegistryRecords"]
    repo = AppRegistryRepo.__new__(AppRegistryRepo)

    async def get_collection():
        return collection

    monkeypatch.setattr(repo, "_collection", get_collection)
    service = AppRegistryService(repo=repo)
    monkeypatch.setenv("PLATFORM_PATH", str(Path(__file__).resolve().parents[1] / "factory_app" / "app"))
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    signing_key = uuid4().hex + uuid4().hex
    monkeypatch.setenv("SUPABASE_JWT_SECRET", signing_key)
    reset_auth_adapter()
    import mozaiksai.core.usage as usage
    from mozaiksai.hosts import studio

    purged = []

    async def purge(*, app_id):
        purged.append(app_id)

    monkeypatch.setattr(usage, "get_runtime_usage_ledger", lambda: SimpleNamespace(purge_usage_for_app=purge))
    monkeypatch.setattr(studio, "_get_app_registry_service", lambda: service)

    def headers(user):
        now = datetime.now(UTC)
        token = jwt.encode(
            {"sub": user, "aud": "authenticated", "role": "authenticated", "iat": now,
             "exp": now + timedelta(minutes=5)},
            signing_key, algorithm="HS256",
        )
        return {"Authorization": f"Bearer {token}"}

    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio.app), base_url="http://test") as http:
            yield SimpleNamespace(http=http, service=service, collection=collection, headers=headers, purged=purged)
    finally:
        reset_auth_adapter()
        assert database_name.startswith("registry_ownership_")
        await mongo.drop_database(database_name)
        mongo.close()


async def _create(host, *, owner="alice"):
    response = await host.http.post(
        "/api/studio/apps", headers=host.headers(owner),
        json={"name": "Owner's app"},
    )
    assert response.status_code == 200, response.text
    return response.json()["app"]


async def test_real_mongo_genesis_claim_blocks_generic_build_start(registry_host):
    host = registry_host
    target = await host.service.register_existing_app_record(
        owner_user_id="alice", app_id="existing-app", chat_app_id="existing-app", name="Existing App",
    )
    claim = GenesisImportClaim(
        build_record_id="av_" + "1" * 24, bundle_name="ExistingApp",
        bundle_sha256="a" * 64, manifest_sha256="b" * 64, content_backend="local",
        source_id="managed/existing", revision_id="c" * 40, tree_id="d" * 40,
    )
    args = {
        "build_registry_id": target["build_registry_id"], "owner_user_id": "alice",
        "app_id": "existing-app", "chat_app_id": "existing-app",
    }
    saved = await host.service.reserve_genesis_import(**args, claim=claim)
    assert saved["genesis_import"] == claim.model_dump(mode="json")
    assert (await host.service.reserve_genesis_import(**args, claim=claim))["build_registry_id"] == target["build_registry_id"]
    changed = claim.model_copy(update={"bundle_sha256": "e" * 64})
    assert await host.service.reserve_genesis_import(**args, claim=changed) is None
    assert (await host.service.update_build_status(
        owner_user_id="alice", build_registry_id=target["build_registry_id"],
        status="building", expected_lifecycle_state="draft",
        current_build_run={"build_id": "build_new", "phase": "genesis"},
    ))["success"] is False
    with pytest.raises(ValueError, match="reserved Genesis import"):
        await host.service.resolve_build_binding(
            owner_user_id="alice", app_id="existing-app", chat_id="chat_new",
            workflow_name="ValueEngine", build_registry_id=target["build_registry_id"],
            allow_create=True,
        )
    current = await host.collection.find_one({"_id": target["build_registry_id"]})
    assert current["lifecycle_state"] == "draft"
    assert current.get("current_build_run") is None


async def test_real_mongo_genesis_review_cas_is_single_and_survives_refinement(registry_host):
    host = registry_host
    target = await host.service.register_existing_app_record(
        owner_user_id="alice", app_id="existing-app", chat_app_id="existing-app", name="Existing App",
    )
    claim = GenesisImportClaim(
        build_record_id="av_" + "1" * 24, bundle_name="ExistingApp",
        bundle_sha256="a" * 64, manifest_sha256="b" * 64, content_backend="local",
        source_id="managed/existing", revision_id="c" * 40, tree_id="d" * 40,
    )
    args = {
        "build_registry_id": target["build_registry_id"], "owner_user_id": "alice",
        "app_id": "existing-app", "chat_app_id": "existing-app", "claim": claim,
    }
    assert await host.service.reserve_genesis_import(**args)
    first = GenesisAcceptanceReceipt(
        accepted_by="alice", accepted_at=datetime.now(UTC), validation_sha256="e" * 64,
    )
    competing = first.model_copy(update={"validation_sha256": "f" * 64})
    outcomes = await asyncio.gather(
        host.service.accept_genesis_import(**args, receipt=first),
        host.service.accept_genesis_import(**args, receipt=competing),
    )
    assert sum(outcome is not None for outcome in outcomes) == 1
    winner = first if outcomes[0] is not None else competing
    loser = competing if winner is first else first
    assert await host.service.accept_genesis_import(**args, receipt=loser) is None
    assert (await host.service.accept_genesis_import(**args, receipt=winner))["genesis_import"]["status"] == "accepted"
    assert await host.service.accept_genesis_import(**{**args, "owner_user_id": "mallory"}, receipt=winner) is None
    started = await host.service.update_build_status(
        owner_user_id="alice", build_registry_id=target["build_registry_id"],
        status="building", expected_lifecycle_state="draft",
        current_build_run={"build_id": "build_later", "phase": "refinement"},
    )
    assert started["success"] is True
    persisted = await host.collection.find_one({"_id": target["build_registry_id"]})
    assert persisted["genesis_import"]["acceptance"]["validation_sha256"] == winner.validation_sha256
    assert persisted["current_build_run"]["build_id"] == "build_later"


async def test_real_mongo_genesis_artifact_projection_requires_matching_validation(registry_host):
    versions = registry_host.collection.database["ArtifactVersions"]
    store = BuildRecordStore.__new__(BuildRecordStore)
    store._coll = AsyncMock(return_value=versions)
    record = BuildRecord(
        _id="av_" + "1" * 24, app_id="existing-app", build_family="app_bundle",
        build_key="app_bundle", version_number=1, lineage_root_id="av_" + "1" * 24,
        lifecycle_status=BuildRecordStatus.DRAFT,
        validation_status=BuildRecordValidationStatus.PENDING,
        commit_metadata={"author_user_id": "alice", "metadata": {
            "bundle_mode": "brownfield_genesis_import", "bundle_sha256": "a" * 64,
        }},
    )
    await versions.insert_one(record.model_dump(by_alias=True, mode="python"))
    unvalidated = await store.accept_genesis_build_record(
        app_id="existing-app", build_record_id=record.id, validation_sha256="e" * 64,
    )
    assert unvalidated.lifecycle_status == BuildRecordStatus.DRAFT
    validation = {"bundle_sha256": "a" * 64, "sha256": "e" * 64}
    checked = await store.mark_genesis_build_record_validated(
        app_id="existing-app", build_record_id=record.id, validation=validation,
    )
    assert checked.validation_status == BuildRecordValidationStatus.PASSED
    assert checked.app_validation_status == "passed"
    wrong = await store.accept_genesis_build_record(
        app_id="existing-app", build_record_id=record.id, validation_sha256="f" * 64,
    )
    assert wrong.lifecycle_status == BuildRecordStatus.DRAFT
    accepted = await store.accept_genesis_build_record(
        app_id="existing-app", build_record_id=record.id, validation_sha256="e" * 64,
    )
    assert accepted.lifecycle_status == BuildRecordStatus.CURRENT
    assert (await store.accept_genesis_build_record(
        app_id="existing-app", build_record_id=record.id, validation_sha256="e" * 64,
    )).lifecycle_status == BuildRecordStatus.CURRENT


async def _concept_target(host, *, name=None, name_source=None, description=None):
    record = (await host.service.create_app_record(
        owner_user_id="alice", name=name, name_source=name_source, description=description,
        status="building", chat_app_id="factory-host", active_chat_id="review-chat",
        active_workflow_id="ValueEngine", current_build_run={"build_id": "build-current", "phase": "genesis"},
    ))["app"]
    binding = RunBuildBinding(build_registry_id=record["build_registry_id"], target_app_id=record["app_id"],
                              build_id="build-current", phase="genesis")
    return record, binding


async def test_approved_name_updates_same_record_without_changing_lifecycle_or_build(registry_host):
    host = registry_host
    record, binding = await _concept_target(host)
    before = await host.collection.find_one({"_id": record["build_registry_id"]})
    result = await host.service.apply_approved_concept_identity(
        owner_user_id="alice", execution_app_id="factory-host", binding=binding, name="FocusSprint",
    )
    assert result["success"] is True
    after = await host.collection.find_one({"_id": record["build_registry_id"]})
    assert after["name"] == "FocusSprint"
    assert after["name_status"] == "named"
    assert after["name_source"] == "value_engine_concept"
    assert {key: value for key, value in after.items() if key not in
            {"name", "name_status", "name_source", "named_at", "updated_at"}} == {
        key: value for key, value in before.items() if key not in
        {"name", "name_status", "name_source", "named_at", "updated_at"}}
    response = await host.http.get("/api/studio/apps", headers=host.headers("alice"))
    assert response.status_code == 200, response.text
    row = next(app for app in response.json()["apps"] if app["app_id"] == binding.target_app_id)
    assert row["name"] == "FocusSprint"
    assert row["name_source"] == "value_engine_concept"

    assert row["name_status"] == "named"


async def test_persisted_value_engine_approval_names_the_directory_record(registry_host, monkeypatch):
    from factory_app.workflows.ValueEngine.tools import manifest
    from mozaiksai.core.data.persistence.artifact_store import BuilderArtifactStore

    host = registry_host
    record, binding = await _concept_target(host)
    before = await host.collection.find_one({"_id": record["build_registry_id"]})
    store = BuilderArtifactStore()
    monkeypatch.setattr(store, "_collection", AsyncMock(return_value=host.collection.database["Concepts"]))
    monkeypatch.setattr(manifest, "BuilderArtifactStore", lambda: store)
    monkeypatch.setattr(manifest, "persist_summary_artifact", AsyncMock())
    monkeypatch.setattr(manifest, "AppRegistryService", lambda: host.service)

    async def approve(_tool, payload, **kwargs):
        assert (await host.collection.find_one({"_id": record["build_registry_id"]}))["name"] is None
        return {"action": "approve", "approved": True, "review_id": payload["review_id"]}

    monkeypatch.setattr(manifest, "use_ui_tool", approve)
    context = {
        "app_id": "factory-host", "user_id": "alice", "chat_id": "review-chat", "workflow_name": "ValueEngine",
        "run_build_binding": binding.model_dump(), "structured_output": {
            "app_name": "FocusSprint", "concept_overview": "A focus timer.", "core_features": ["Save focus sessions"],
        },
    }
    assert (await manifest.save_value_manifest(context))["outcome"] == "approved"
    assert (await store.get_concept(app_id=binding.target_app_id))["status"] == "approved"
    after = await host.collection.find_one({"_id": record["build_registry_id"]})
    assert after["current_build_run"] == before["current_build_run"]
    assert after["build_runs"] == before["build_runs"]
    response = await host.http.get("/api/studio/apps", headers=host.headers("alice"))
    row = next(app for app in response.json()["apps"] if app["build_registry_id"] == binding.build_registry_id)
    assert row["name"] == "FocusSprint"
    assert row["name_source"] == "value_engine_concept"
    assert after["description"] == "A focus timer."
    assert row["description"] == "A focus timer."
    overview = await host.http.get(
        "/api/studio/overview?app_id=factory-host&build_registry_id=" + binding.build_registry_id,
        headers=host.headers("alice"),
    )
    assert overview.status_code == 200, overview.text
    projected = overview.json()["app"]
    assert projected["description"] == "A focus timer."
    assert projected["current_build_run"] == row["current_build_run"]
    assert projected["active_chat_id"] == "review-chat"
    assert projected["chat_app_id"] == "factory-host"
    assert projected["destination"] == (
        "/chat?workflow=ValueEngine&mode=workflow&chat_id=review-chat&app_id=factory-host"
    )


@pytest.mark.parametrize("name", [None, "", "  ", "Untitled app", "My app"])
async def test_concept_name_requires_specific_product_name(registry_host, name):
    host = registry_host
    record, binding = await _concept_target(host)
    before = await host.collection.find_one({"_id": record["build_registry_id"]})
    with pytest.raises(ValueError, match="specific product name"):
        await host.service.apply_approved_concept_identity(
            owner_user_id="alice", execution_app_id="factory-host", binding=binding, name=name,
        )
    assert await host.collection.find_one({"_id": record["build_registry_id"]}) == before


@pytest.mark.parametrize("field,value", [
    ("owner_user_id", "bob"), ("execution_app_id", "foreign-host"),
    ("build_registry_id", "foreign-registry"), ("target_app_id", "foreign-app"), ("build_id", "old-build"),
])
async def test_concept_name_rejects_foreign_or_stale_binding(registry_host, field, value):
    host = registry_host
    record, binding = await _concept_target(host)
    before = await host.collection.find_one({"_id": record["build_registry_id"]})
    args = {"owner_user_id": "alice", "execution_app_id": "factory-host", "binding": binding, "name": "FocusSprint"}
    if field in {"owner_user_id", "execution_app_id"}:
        args[field] = value
    else:
        args["binding"] = binding.model_copy(update={field: value})
    assert (await host.service.apply_approved_concept_identity(**args))["success"] is False
    assert await host.collection.find_one({"_id": record["build_registry_id"]}) == before
    assert await host.collection.count_documents({}) == 1


@pytest.mark.parametrize("source", ["manual", "imported_app"])
async def test_concept_name_preserves_explicit_name_precedence(registry_host, source):
    host = registry_host
    record, binding = await _concept_target(host, name="Owner's chosen name", name_source=source)
    before = await host.collection.find_one({"_id": record["build_registry_id"]})
    assert (await host.service.apply_approved_concept_identity(
        owner_user_id="alice", execution_app_id="factory-host", binding=binding, name="FocusSprint",
    ))["success"] is True
    assert await host.collection.find_one({"_id": record["build_registry_id"]}) == before


@pytest.mark.parametrize("source", ["provisional", "manual", "imported_app"])
async def test_approved_concept_preserves_existing_description(registry_host, source):
    host = registry_host
    record, binding = await _concept_target(
        host, name=None if source == "provisional" else "Owner name", name_source=source,
        description="Owner's explicit product description.",
    )
    result = await host.service.apply_approved_concept_identity(
        owner_user_id="alice", execution_app_id="factory-host", binding=binding,
        name="FocusSprint", description="An approved but different description.",
    )
    assert result["success"] is True
    saved = await host.collection.find_one({"_id": record["build_registry_id"]})
    assert saved["description"] == "Owner's explicit product description."
    assert saved["name"] == ("FocusSprint" if source == "provisional" else "Owner name")


@pytest.mark.parametrize("source", ["manual", "imported_app"])
async def test_approved_concept_fills_empty_description_without_renaming(registry_host, source):
    host = registry_host
    record, binding = await _concept_target(host, name="Owner name", name_source=source)
    before = await host.collection.find_one({"_id": record["build_registry_id"]})
    result = await host.service.apply_approved_concept_identity(
        owner_user_id="alice", execution_app_id="factory-host", binding=binding,
        name="FocusSprint", description="  An approved focus timer.  ",
    )
    assert result["success"] is True
    after = await host.collection.find_one({"_id": record["build_registry_id"]})
    assert after["description"] == "An approved focus timer."
    assert {key: value for key, value in after.items() if key not in {"description", "updated_at"}} == {
        key: value for key, value in before.items() if key not in {"description", "updated_at"}
    }


async def test_concept_identity_cannot_overwrite_concurrent_description(registry_host, monkeypatch):
    host = registry_host
    record, binding = await _concept_target(host)
    original = host.collection.find_one_and_update

    async def describe_before_write(query, update, **kwargs):
        await host.collection.update_one({"_id": record["build_registry_id"]}, {
            "$set": {"description": "Concurrent owner description."},
        })
        return await original(query, update, **kwargs)

    monkeypatch.setattr(host.collection, "find_one_and_update", describe_before_write)
    result = await host.service.apply_approved_concept_identity(
        owner_user_id="alice", execution_app_id="factory-host", binding=binding,
        name="FocusSprint", description="Approved focus timer.",
    )
    assert result["success"] is False
    saved = await host.collection.find_one({"_id": record["build_registry_id"]})
    assert saved["description"] == "Concurrent owner description."
    assert saved["name"] is None


async def test_name_update_cannot_overwrite_concurrent_manual_rename(registry_host, monkeypatch):
    host = registry_host
    record, binding = await _concept_target(host)
    original = host.collection.find_one_and_update

    async def rename_before_write(query, update, **kwargs):
        await host.collection.update_one({"_id": record["build_registry_id"]}, {"$set": {
            "name": "Manual winner", "name_status": "named", "name_source": "manual",
        }})
        return await original(query, update, **kwargs)

    monkeypatch.setattr(host.collection, "find_one_and_update", rename_before_write)
    result = await host.service.apply_approved_concept_identity(
        owner_user_id="alice", execution_app_id="factory-host", binding=binding, name="FocusSprint",
    )
    assert result["success"] is False
    after = await host.collection.find_one({"_id": record["build_registry_id"]})
    assert after["name"] == "Manual winner"
    assert after["current_build_run"]["build_id"] == "build-current"


async def test_name_update_cannot_touch_a_build_superseded_during_approval(registry_host, monkeypatch):
    host = registry_host
    record, binding = await _concept_target(host)
    original = host.collection.find_one_and_update

    async def supersede_before_write(query, update, **kwargs):
        await host.collection.update_one({"_id": record["build_registry_id"]}, {
            "$set": {"current_build_run.build_id": "new-build"},
        })
        return await original(query, update, **kwargs)

    monkeypatch.setattr(host.collection, "find_one_and_update", supersede_before_write)
    result = await host.service.apply_approved_concept_identity(
        owner_user_id="alice", execution_app_id="factory-host", binding=binding, name="FocusSprint",
    )
    assert result["success"] is False
    after = await host.collection.find_one({"_id": record["build_registry_id"]})
    assert after["name"] is None
    assert after["current_build_run"]["build_id"] == "new-build"


async def test_another_user_cannot_take_over_existing_app_id(registry_host):
    host = registry_host
    record = await _create(host)
    before = await host.collection.find_one({"_id": record["build_registry_id"]})
    response = await host.http.post(
        "/api/studio/apps", headers=host.headers("bob"),
        json={"app_id": "owned-app", "name": "Replacement", "owner_user_id": "alice"},
    )
    assert response.status_code == 422
    assert await host.collection.find_one({"_id": record["build_registry_id"]}) == before


@pytest.mark.parametrize("path", ["/api/studio/overview", "/api/studio/build"])
async def test_studio_reads_hide_other_owners_record(registry_host, path):
    host = registry_host
    record = await _create(host)
    response = await host.http.get(path + "?app_id=" + record["app_id"], headers=host.headers("bob"))
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("method", ["DELETE"])
async def test_other_owner_and_anonymous_cannot_mutate_or_purge(registry_host, method):
    host = registry_host
    record = await _create(host)
    before = await host.collection.find_one({"_id": record["build_registry_id"]})
    path = f"/api/studio/apps/{record['build_registry_id']}" + ("/status" if method == "PUT" else "")
    payload = {"status": "active"} if method == "PUT" else None
    assert (await host.http.request(method, path, json=payload)).status_code == 401
    response = await host.http.request(method, path, json=payload, headers=host.headers("bob"))
    assert response.status_code == 404
    assert await host.collection.find_one({"_id": record["build_registry_id"]}) == before
    assert host.purged == []


async def test_owner_can_create_distinct_apps_and_delete_record(registry_host):
    host = registry_host
    original = await _create(host)
    reopened = await _create(host)
    assert original["build_registry_id"] != reopened["build_registry_id"]
    assert original["app_id"] != reopened["app_id"]
    path = f"/api/studio/apps/{original['build_registry_id']}"
    updated = await host.http.put(path + "/status", headers=host.headers("alice"), json={"status": "active"})
    assert updated.status_code == 404, updated.text
    deleted = await host.http.delete(path, headers=host.headers("alice"))
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["success"] is True
    assert await host.collection.count_documents({}) == 1
    assert host.purged == []


async def test_concurrent_new_app_requests_allocate_distinct_targets(registry_host):
    host = registry_host
    results = await asyncio.gather(*[_create(host) for _ in range(12)])
    assert len({item["build_registry_id"] for item in results}) == 12
    assert len({item["app_id"] for item in results}) == 12
    assert await host.collection.count_documents({}) == 12


async def test_service_reads_and_promotions_require_owner(registry_host):
    host = registry_host
    record = (await host.service.create_app_record(
        owner_user_id="alice", app_id="owned-app", status="review",
        current_build_run={"build_id": "build_1", "phase": "genesis", "artifact_version_id": "av_1"},
    ))["app"]
    for reference in ({"app_id": "owned-app"}, {"build_registry_id": record["build_registry_id"]}):
        assert (await host.service.get_app_record(owner_user_id="bob", **reference))["app"] is None
        assert (await host.service.get_app_record(owner_user_id="alice", **reference))["app"]["app_id"] == "owned-app"
    with pytest.raises(ValueError, match="not found"):
        await host.service.promote_build(
            build_registry_id=record["build_registry_id"], promoted_by="bob",
            expected_build_id="build_1", expected_artifact_version_id="av_1",
        )
    assert (await host.collection.find_one({"app_id": "owned-app"}))["lifecycle_state"] == "review"
    result = await host.service.promote_build(
        build_registry_id=record["build_registry_id"], promoted_by="alice",
        expected_build_id="build_1", expected_artifact_version_id="av_1",
    )
    assert result["app"]["lifecycle_state"] == "active"


async def test_promotion_cannot_activate_a_superseded_build(registry_host):
    host = registry_host
    record = (await host.service.create_app_record(
        owner_user_id="alice", app_id="owned-app", status="review",
        current_build_run={"build_id": "new-build", "phase": "refinement", "artifact_version_id": "new-artifact"},
    ))["app"]
    before = await host.collection.find_one({"app_id": "owned-app"})
    result = await host.service.promote_build(
        build_registry_id=record["build_registry_id"], promoted_by="alice",
        expected_build_id="old-build", expected_artifact_version_id="old-artifact",
    )
    assert result == {"success": False, "app": None}
    assert await host.collection.find_one({"app_id": "owned-app"}) == before


async def test_inline_refinement_does_not_inherit_previous_chat(registry_host):
    host = registry_host
    record = (await host.service.create_app_record(
        owner_user_id="alice", app_id="owned-app", status="active",
        active_chat_id="genesis-chat", active_workflow_id="AppGenerator",
        current_build_run={"build_id": "genesis-build", "phase": "genesis"},
    ))["app"]
    updated = await host.service.repo.update_lifecycle_state(
        build_registry_id=record["build_registry_id"], owner_user_id="alice",
        lifecycle_state="building", expected_build_id="genesis-build",
        current_build_run={"build_id": "inline-build", "phase": "refinement"},
    )
    assert updated["current_build_run"]["build_id"] == "inline-build"
    assert updated["active_chat_id"] is None
    assert updated["active_workflow_id"] is None
    assert updated["build_runs"][0]["active_chat_id"] == "genesis-chat"


async def test_ensure_status_cannot_reopen_someone_elses_record(registry_host):
    host = registry_host
    await host.service.create_app_record(owner_user_id="alice", app_id="owned-app")
    before = await host.collection.find_one({"app_id": "owned-app"})
    with pytest.raises(ValueError):
        await host.service.ensure_status_for_app(app_id="owned-app", owner_user_id="bob", status="draft")
    assert await host.collection.find_one({"app_id": "owned-app"}) == before


@pytest.mark.parametrize("owner", [None, "", " "])
async def test_missing_owner_cannot_create_a_record(registry_host, owner):
    with pytest.raises(ValueError, match="owner_user_id"):
        await registry_host.service.create_app_record(owner_user_id=owner, app_id="new-app")
    assert await registry_host.collection.count_documents({}) == 0


async def test_concurrent_different_owners_cannot_share_an_app_id(registry_host):
    host = registry_host
    owners = ["alice", "bob"] * 6
    responses = await asyncio.gather(*[
        host.http.post("/api/studio/apps", headers=host.headers(owner), json={"app_id": "shared-target"})
        for owner in owners
    ])
    assert await host.collection.count_documents({}) == 0
    for response in responses:
        assert response.status_code == 422


async def test_existing_self_target_claim_is_atomic_and_does_not_reset_build(registry_host):
    host = registry_host
    claims = await asyncio.gather(*[
        host.service.register_existing_app_record(
            owner_user_id=owner, app_id="fixed-existing-app",
            chat_app_id="fixed-existing-app", name="Existing App",
        ) for owner in ("alice", "bob")
    ], return_exceptions=True)
    winners = [claim for claim in claims if isinstance(claim, dict)]
    denials = [claim for claim in claims if isinstance(claim, ValueError)]
    assert len(winners) == 1
    assert len(denials) == 1
    winner = winners[0]
    owner = winner["owner_user_id"]
    await host.collection.update_one(
        {"app_id": "fixed-existing-app"},
        {"$set": {
            "lifecycle_state": "active",
            "current_build_run": {"build_id": "build_1", "phase": "refinement"},
        }},
    )
    before = await host.collection.find_one({"app_id": "fixed-existing-app"})
    repeat = await host.service.register_existing_app_record(
        owner_user_id=owner, app_id="fixed-existing-app",
        chat_app_id="fixed-existing-app", name="Changed Name",
    )
    assert repeat["build_registry_id"] == winner["build_registry_id"]
    assert await host.collection.find_one({"app_id": "fixed-existing-app"}) == before


async def test_interrupted_creation_keeps_ownership_and_allows_owner_retry(registry_host, monkeypatch):
    host = registry_host
    original = host.collection.find_one_and_update

    async def interrupted(query, update, **kwargs):
        if "$set" in update:
            raise RuntimeError("interrupted metadata write")
        return await original(query, update, **kwargs)

    monkeypatch.setattr(host.collection, "find_one_and_update", interrupted)
    with pytest.raises(RuntimeError, match="interrupted metadata"):
        await host.service.create_app_record(owner_user_id="alice", app_id="owned-app")
    draft = await host.collection.find_one({"app_id": "owned-app"})
    assert draft["owner_user_id"] == "alice"
    assert draft["lifecycle_state"] == "draft"
    monkeypatch.setattr(host.collection, "find_one_and_update", original)
    with pytest.raises(ValueError, match="not available"):
        await host.service.create_app_record(owner_user_id="bob", app_id="owned-app")
    reopened = (await host.service.create_app_record(owner_user_id="alice", app_id="owned-app"))["app"]
    assert reopened["build_registry_id"] == draft["_id"]


async def test_module_actions_use_context_owner_and_do_not_emit_on_denial(registry_host):
    host = registry_host
    record = await _create(host)
    events = []

    async def emit(*args):
        events.append(args)

    module = AppRegistryModule(service=host.service)
    context = SimpleNamespace(user_id="bob", app_id="studio-host", emit=emit)
    reference = {"build_registry_id": record["build_registry_id"]}
    assert (await module.get_app_record(context, **reference))["app"] is None
    assert not hasattr(module, "update_build_status")
    assert (await module.delete_app(context, **reference))["success"] is False
    assert not hasattr(module, "promote_build")
    assert events == []
    created = await module.create_app_record(context, name="Independent app")
    assert created["app"]["app_id"] != "studio-host"
    assert created["app"]["owner_user_id"] == "bob"
