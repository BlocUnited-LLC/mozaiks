from __future__ import annotations

import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.import_utils import import_module_directly

# Save the real mozaiksai.core.artifacts before import_module_directly creates a
# fake parent stub for it.  Restoring it afterwards ensures that later test files
# can still do `from mozaiksai.core.artifacts import ChangeClassification`.
_orig_artifacts_pkg = sys.modules.get("mozaiksai.core.artifacts")

_artifact_models_mod = import_module_directly("mozaiksai.core.artifacts.models")
_artifact_store_mod = import_module_directly("mozaiksai.core.artifacts.store")

# Restore the real package (or remove the fake stub if none existed before)
if _orig_artifacts_pkg is None:
    sys.modules.pop("mozaiksai.core.artifacts", None)
else:
    sys.modules["mozaiksai.core.artifacts"] = _orig_artifacts_pkg
del _orig_artifacts_pkg

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
async def test_invalidate_artifact_version_refs_marks_only_targeted_known_versions() -> None:
    store = ArtifactStore.__new__(ArtifactStore)
    store.mark_artifact_version_stale = AsyncMock(side_effect=[True, False, True])

    invalidated = await store.invalidate_artifact_version_refs(
        app_id="app-1",
        artifact_version_refs={
            "concept": "av_concept_1",
            "app_bundle": "av_bundle_1",
            "workflow_bundle": "av_workflow_1",
        },
        affected_artifact_kinds=["concept", "missing", "app_bundle", "workflow_bundle"],
        reason="change_request:cr_123",
    )

    assert invalidated == ["av_concept_1", "av_workflow_1"]
    assert store.mark_artifact_version_stale.await_args_list[0].kwargs["artifact_version_id"] == "av_concept_1"
    assert store.mark_artifact_version_stale.await_args_list[1].kwargs["artifact_version_id"] == "av_bundle_1"
    assert store.mark_artifact_version_stale.await_args_list[2].kwargs["artifact_version_id"] == "av_workflow_1"


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
        refinement_request={
            "declared_change_class": "feature",
            "artifact_kind": "app_bundle",
            "artifact_key": "primary",
            "artifact_version_id": "av_123",
            "raw_user_request": "Add export button",
        },
        change_intent={
            "change_class": "feature",
            "rationale": "Feature extension requested for the app bundle; preserve the current concept while widening the owned implementation scope.",
        },
        impact_set={
            "affected_workflows": ["AppGenerator"],
            "affected_bundle_paths": ["src/pages/reports.tsx"],
            "affected_declarative_families": ["app_bundle"],
            "requires_replanning": True,
            "requires_rebuild": True,
            "restart_from": "AppGenerator",
            "scope_summary": "Extend app bundle scope.",
        },
        router_decision={"reentry": "feature_refinement"},
        created_by_user_id="user-1",
    )
    refinement_session = await store.create_refinement_session(
        app_id="app-1",
        artifact_version_id="av_123",
        result_artifact_version_id="av_child_1",
        change_request_id=change_request.id,
        provider="e2b",
        sandbox_id="sbx_123",
        status=RefinementSessionStatus.PROVISIONING,
        preview_url="https://preview.example",
        metadata={"branch": "feature/export"},
    )

    assert change_request.classification == ChangeClassification.FEATURE
    assert change_request.change_intent.change_class == ChangeClassification.FEATURE
    assert change_request.impact_set.requires_replanning is True
    assert refinement_session.provider == "e2b"
    assert refinement_session.sandbox_id == "sbx_123"
    assert refinement_session.preview_url == "https://preview.example"

    inserted_change = change_coll.insert_one.await_args.args[0]
    inserted_session = session_coll.insert_one.await_args.args[0]
    assert inserted_change["router_decision"]["reentry"] == "feature_refinement"
    assert inserted_change["refinement_request"]["declared_change_class"] == "feature"
    assert inserted_change["impact_set"]["restart_from"] == "AppGenerator"
    assert inserted_session["result_artifact_version_id"] == "av_child_1"
    assert inserted_session["status"] == RefinementSessionStatus.PROVISIONING.value


@pytest.mark.asyncio
async def test_update_change_request_router_decision_updates_only_router_metadata() -> None:
    store = ArtifactStore.__new__(ArtifactStore)
    change_coll = MagicMock()
    change_coll.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    store._coll = AsyncMock(return_value=change_coll)

    updated = await store.update_change_request_router_decision(
        app_id="app-1",
        change_request_id="cr_123",
        router_decision={
            "workflow_id": "AppGenerator",
            "requested_workflow_id": None,
            "execution_mode": "workflow",
        },
    )

    assert updated is True
    query, update = change_coll.update_one.await_args.args
    assert query["_id"] == "cr_123"
    assert query["app_id"] == "app-1"
    assert update["$set"]["router_decision"]["workflow_id"] == "AppGenerator"
    assert update["$set"]["router_decision"]["execution_mode"] == "workflow"


@pytest.mark.asyncio
async def test_accept_artifact_version_supersedes_prior_current_version() -> None:
    store = ArtifactStore.__new__(ArtifactStore)
    versions_coll = MagicMock()
    current_raw = {
        "_id": "av_child",
        "app_id": "app-1",
        "artifact_kind": "app_bundle",
        "artifact_key": "primary",
        "version_number": 2,
        "parent_version_id": "av_parent",
        "lineage_root_id": "av_parent",
        "source_workflow": "AppGenerator",
        "source_chat_id": "chat-1",
        "canonical_inputs_version": {},
        "lifecycle_status": ArtifactLifecycleStatus.DRAFT.value,
        "validation_status": ArtifactValidationStatus.PASSED.value,
        "files_manifest": [],
        "commit_metadata": {"metadata": {}},
    }
    refreshed_raw = dict(current_raw)
    refreshed_raw["lifecycle_status"] = ArtifactLifecycleStatus.CURRENT.value
    versions_coll.find_one = AsyncMock(side_effect=[current_raw, refreshed_raw])
    versions_coll.update_many = AsyncMock()
    versions_coll.update_one = AsyncMock()
    store._coll = AsyncMock(return_value=versions_coll)

    accepted = await store.accept_artifact_version(app_id="app-1", artifact_version_id="av_child")

    assert accepted is not None
    assert accepted.lifecycle_status == ArtifactLifecycleStatus.CURRENT
    update_many_query, update_many_doc = versions_coll.update_many.await_args.args
    assert update_many_query["artifact_kind"] == "app_bundle"
    assert update_many_query["artifact_key"] == "primary"
    assert update_many_doc["$set"]["lifecycle_status"] == ArtifactLifecycleStatus.SUPERSEDED.value
    update_one_query, update_one_doc = versions_coll.update_one.await_args.args
    assert update_one_query["_id"] == "av_child"
    assert update_one_doc["$set"]["lifecycle_status"] == ArtifactLifecycleStatus.CURRENT.value


@pytest.mark.asyncio
async def test_accept_artifact_version_can_persist_commit_metadata() -> None:
    store = ArtifactStore.__new__(ArtifactStore)
    versions_coll = MagicMock()
    current_raw = {
        "_id": "av_child",
        "app_id": "app-1",
        "artifact_kind": "app_bundle",
        "artifact_key": "primary",
        "version_number": 2,
        "parent_version_id": "av_parent",
        "lineage_root_id": "av_parent",
        "source_workflow": "AppGenerator",
        "source_chat_id": "chat-1",
        "canonical_inputs_version": {},
        "lifecycle_status": ArtifactLifecycleStatus.DRAFT.value,
        "validation_status": ArtifactValidationStatus.PASSED.value,
        "files_manifest": [],
        "commit_metadata": {"metadata": {"refinement": {"request_id": "req-1"}}},
    }
    refreshed_raw = dict(current_raw)
    refreshed_raw["lifecycle_status"] = ArtifactLifecycleStatus.CURRENT.value
    refreshed_raw["commit_metadata"] = {
        "metadata": {
            "refinement": {"request_id": "req-1"},
            "acceptance": {
                "accepted_by": "operator-1",
                "accepted_at": "2026-05-22T00:00:00Z",
                "refinement_review_status": "promotion_ready",
                "request_id": "req-1",
            },
        }
    }
    versions_coll.find_one = AsyncMock(side_effect=[current_raw, refreshed_raw])
    versions_coll.update_many = AsyncMock()
    versions_coll.update_one = AsyncMock()
    store._coll = AsyncMock(return_value=versions_coll)

    accepted = await store.accept_artifact_version(
        app_id="app-1",
        artifact_version_id="av_child",
        commit_metadata=refreshed_raw["commit_metadata"],
    )

    assert accepted is not None
    update_one_query, update_one_doc = versions_coll.update_one.await_args.args
    assert update_one_query["_id"] == "av_child"
    commit_metadata = update_one_doc["$set"]["commit_metadata"]
    assert commit_metadata["metadata"]["refinement"]["request_id"] == "req-1"
    assert commit_metadata["metadata"]["acceptance"]["accepted_by"] == "operator-1"


@pytest.mark.asyncio
async def test_list_refinement_sessions_filters_by_result_artifact_version_id() -> None:
    store = ArtifactStore.__new__(ArtifactStore)
    coll = MagicMock()
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(
        return_value=[
            {
                "_id": "rs_1",
                "app_id": "app-1",
                "artifact_version_id": "av_parent",
                "result_artifact_version_id": "av_child",
                "change_request_id": "cr_1",
                "provider": "control_plane_coding",
                "status": RefinementSessionStatus.VALIDATED.value,
                "metadata": {},
            }
        ]
    )
    coll.find.return_value = cursor
    store._coll = AsyncMock(return_value=coll)

    rows = await store.list_refinement_sessions(
        app_id="app-1",
        result_artifact_version_id="av_child",
        limit=5,
    )

    assert len(rows) == 1
    assert rows[0].result_artifact_version_id == "av_child"
    coll.find.assert_called_once_with({"app_id": "app-1", "result_artifact_version_id": "av_child"})


@pytest.mark.asyncio
async def test_get_stale_artifact_families_returns_stale_without_current() -> None:
    """Families with a STALE version but no CURRENT version are returned."""
    store = ArtifactStore.__new__(ArtifactStore)
    versions_coll = MagicMock()
    versions_coll.distinct = AsyncMock(
        side_effect=[
            ["design_docs", "workflow_bundle"],  # stale query
            ["workflow_bundle"],                 # current query — workflow_bundle was rebuilt
        ]
    )
    store._coll = AsyncMock(return_value=versions_coll)
    store._ensure_client = AsyncMock()

    result = await store.get_stale_artifact_families(app_id="app-1")

    # workflow_bundle has a CURRENT version, so it is not stale
    # design_docs has no CURRENT version, so it remains stale
    assert result == ["design_docs"]
    assert versions_coll.distinct.await_count == 2


@pytest.mark.asyncio
async def test_get_current_artifact_version_refs_returns_highest_current_per_kind() -> None:
    """Returns the highest-versioned CURRENT artifact_version_id for each kind."""
    store = ArtifactStore.__new__(ArtifactStore)
    versions_coll = MagicMock()
    cursor = MagicMock()
    # sort() returns cursor; to_list() returns rows sorted by version_number desc
    cursor.sort.return_value = cursor
    cursor.to_list = AsyncMock(
        return_value=[
            {"_id": "av_concept_2", "artifact_kind": "concept", "version_number": 2},
            {"_id": "av_concept_1", "artifact_kind": "concept", "version_number": 1},
            {"_id": "av_design_1", "artifact_kind": "design_docs", "version_number": 1},
        ]
    )
    versions_coll.find.return_value = cursor
    store._coll = AsyncMock(return_value=versions_coll)
    store._ensure_client = AsyncMock()

    refs = await store.get_current_artifact_version_refs(app_id="app-1")

    # concept → highest version (av_concept_2); design_docs → only version
    assert refs == {"concept": "av_concept_2", "design_docs": "av_design_1"}
    # confirm only CURRENT versions were queried
    query = versions_coll.find.call_args.args[0]
    assert query["lifecycle_status"] == ArtifactLifecycleStatus.CURRENT.value
    assert query["app_id"] == "app-1"


@pytest.mark.asyncio
async def test_get_current_artifact_version_refs_returns_empty_when_no_current() -> None:
    """Returns empty dict when no CURRENT artifact versions exist."""
    store = ArtifactStore.__new__(ArtifactStore)
    versions_coll = MagicMock()
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.to_list = AsyncMock(return_value=[])
    versions_coll.find.return_value = cursor
    store._coll = AsyncMock(return_value=versions_coll)
    store._ensure_client = AsyncMock()

    refs = await store.get_current_artifact_version_refs(app_id="app-1")

    assert refs == {}


@pytest.mark.asyncio
async def test_get_stale_artifact_families_clears_when_all_rebuilt() -> None:
    """If every stale family has a CURRENT version, the list is empty."""
    store = ArtifactStore.__new__(ArtifactStore)
    versions_coll = MagicMock()
    versions_coll.distinct = AsyncMock(
        side_effect=[
            ["design_docs"],   # stale query
            ["design_docs"],   # current query — rebuilt successfully
        ]
    )
    store._coll = AsyncMock(return_value=versions_coll)
    store._ensure_client = AsyncMock()

    result = await store.get_stale_artifact_families(app_id="app-1")

    assert result == []
