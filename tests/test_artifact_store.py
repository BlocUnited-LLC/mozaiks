from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.import_utils import import_module_directly

_artifact_models_mod = import_module_directly("mozaiksai.core.artifacts.models")
_artifact_store_mod = import_module_directly("mozaiksai.core.artifacts.store")

ArtifactLifecycleStatus = _artifact_models_mod.ArtifactLifecycleStatus
ArtifactValidationStatus = _artifact_models_mod.ArtifactValidationStatus
ChangeClassification = _artifact_models_mod.ChangeClassification
RefinementSessionStatus = _artifact_models_mod.RefinementSessionStatus
ArtifactStore = _artifact_store_mod.ArtifactStore


@pytest.mark.asyncio
async def test_create_artifact_version_persists_manifest_and_lineage() -> None:
    store = ArtifactStore.__new__(ArtifactStore)
    versions_coll = MagicMock()
    versions_coll.find_one = AsyncMock(return_value=None)
    versions_coll.update_many = AsyncMock()
    versions_coll.insert_one = AsyncMock()

    counters_coll = MagicMock()
    counters_coll.find_one_and_update = AsyncMock(return_value={"sequence": 3})

    async def _fake_coll(name: str):
        mapping = {
            "ArtifactVersions": versions_coll,
            "ArtifactVersionCounters": counters_coll,
        }
        return mapping[name]

    store._coll = AsyncMock(side_effect=_fake_coll)

    doc = await store.create_artifact_version(
        app_id="app-1",
        artifact_kind="app_bundle",
        artifact_key="primary",
        files_manifest=[{"path": "src/App.tsx", "sha256": "abc", "size_bytes": 42}],
        source_workflow="AppGenerator",
        source_chat_id="chat-1",
        lifecycle_status=ArtifactLifecycleStatus.CURRENT,
        validation_status=ArtifactValidationStatus.PENDING,
        commit_metadata={"message": "Initial compile", "author_user_id": "user-1"},
    )

    assert doc.version_number == 3
    assert doc.lineage_root_id == doc.id
    assert doc.files_manifest[0].path == "src/App.tsx"
    assert doc.commit_metadata.message == "Initial compile"

    versions_coll.update_many.assert_awaited_once()
    inserted = versions_coll.insert_one.await_args.args[0]
    assert inserted["artifact_kind"] == "app_bundle"
    assert inserted["artifact_key"] == "primary"
    assert inserted["version_number"] == 3
    assert inserted["files_manifest"][0]["sha256"] == "abc"


@pytest.mark.asyncio
async def test_invalidate_artifact_family_marks_versions_stale() -> None:
    store = ArtifactStore.__new__(ArtifactStore)
    versions_coll = MagicMock()
    versions_coll.update_many = AsyncMock(return_value=MagicMock(modified_count=2))
    store._coll = AsyncMock(return_value=versions_coll)

    modified = await store.invalidate_artifact_family(
        app_id="app-1",
        artifact_kind="app_bundle",
        artifact_key="primary",
        reason="design changed upstream",
        invalidated_by_version_id="av_new",
        exclude_version_id="av_keep",
    )

    assert modified == 2
    query, update = versions_coll.update_many.await_args.args
    assert query["app_id"] == "app-1"
    assert query["artifact_kind"] == "app_bundle"
    assert query["artifact_key"] == "primary"
    assert query["_id"] == {"$ne": "av_keep"}
    assert update["$set"]["lifecycle_status"] == ArtifactLifecycleStatus.STALE.value
    assert update["$set"]["invalidated_by_version_id"] == "av_new"
    assert update["$set"]["invalidation_reason"] == "design changed upstream"
    assert isinstance(update["$set"]["stale_at"], datetime)


@pytest.mark.asyncio
async def test_create_change_request_and_refinement_session_persist_structured_records() -> None:
    store = ArtifactStore.__new__(ArtifactStore)
    change_coll = MagicMock()
    change_coll.insert_one = AsyncMock()
    session_coll = MagicMock()
    session_coll.insert_one = AsyncMock()

    async def _fake_coll(name: str):
        return {
            "ChangeRequests": change_coll,
            "RefinementSessions": session_coll,
        }[name]

    store._coll = AsyncMock(side_effect=_fake_coll)

    change_request = await store.create_change_request(
        app_id="app-1",
        artifact_kind="app_bundle",
        artifact_key="primary",
        artifact_version_id="av_123",
        raw_user_request="Add export button",
        classification=ChangeClassification.FEATURE,
        scope={"owned_paths": ["src/pages/reports.tsx"]},
        router_decision={"reentry": "feature_refinement"},
        created_by_user_id="user-1",
    )
    refinement_session = await store.create_refinement_session(
        app_id="app-1",
        artifact_version_id="av_123",
        change_request_id=change_request.id,
        provider="e2b",
        sandbox_id="sbx_123",
        status=RefinementSessionStatus.PROVISIONING,
        preview_url="https://preview.example",
        metadata={"branch": "feature/export"},
    )

    assert change_request.classification == ChangeClassification.FEATURE
    assert refinement_session.provider == "e2b"
    assert refinement_session.sandbox_id == "sbx_123"
    assert refinement_session.preview_url == "https://preview.example"

    inserted_change = change_coll.insert_one.await_args.args[0]
    inserted_session = session_coll.insert_one.await_args.args[0]
    assert inserted_change["router_decision"]["reentry"] == "feature_refinement"
    assert inserted_session["status"] == RefinementSessionStatus.PROVISIONING.value