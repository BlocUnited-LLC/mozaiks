from __future__ import annotations

import pytest

from mozaiksai.core.artifacts import (
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    ArtifactVersionDoc,
    extract_summary_payload,
    persist_summary_artifact,
    resolve_latest_artifact_version_refs,
)


class _FakeArtifactStore:
    def __init__(self) -> None:
        self.create_calls = []

    async def list_build_records(
        self,
        *,
        app_id: str,
        build_family: str,
        build_key: str | None = None,
        lifecycle_status: ArtifactLifecycleStatus | None = None,
        limit: int = 1,
    ):
        data = {
            ("design_docs", "design_docs"): [
                ArtifactVersionDoc(
                    _id="av_design_docs_1",
                    app_id=app_id,
                    build_family="design_docs",
                    build_key="design_docs",
                    version_number=1,
                    lineage_root_id="av_design_docs_1",
                    lifecycle_status=ArtifactLifecycleStatus.CURRENT,
                    validation_status=ArtifactValidationStatus.SKIPPED,
                    commit_metadata={"metadata": {}},
                )
            ],
            ("concept", "concept"): [
                ArtifactVersionDoc(
                    _id="av_concept_2",
                    app_id=app_id,
                    build_family="concept",
                    build_key="concept",
                    version_number=2,
                    lineage_root_id="av_concept_1",
                    parent_build_record_id="av_concept_1",
                    lifecycle_status=ArtifactLifecycleStatus.CURRENT,
                    validation_status=ArtifactValidationStatus.SKIPPED,
                    commit_metadata={"metadata": {"summary_payload": {"app_name": "Demo"}}},
                )
            ],
            ("build_plan", "build_plan"): [
                ArtifactVersionDoc(
                    _id="av_build_plan_1",
                    app_id=app_id,
                    build_family="build_plan",
                    build_key="build_plan",
                    version_number=1,
                    lineage_root_id="av_build_plan_1",
                    lifecycle_status=ArtifactLifecycleStatus.CURRENT,
                    validation_status=ArtifactValidationStatus.SKIPPED,
                    commit_metadata={"metadata": {}},
                )
            ],
        }
        rows = data.get((build_family, build_key), [])
        # Honour lifecycle_status filter so tests verify the CURRENT-only contract.
        if lifecycle_status is not None:
            rows = [r for r in rows if r.lifecycle_status == lifecycle_status]
        return rows

    async def create_build_record(self, **kwargs):
        self.create_calls.append(dict(kwargs))
        return ArtifactVersionDoc(
            _id="av_design_docs_2",
            app_id=kwargs["app_id"],
            build_family=kwargs["build_family"],
            build_key=kwargs["build_key"],
            version_number=2,
            parent_build_record_id=kwargs.get("parent_build_record_id"),
            lineage_root_id="av_design_docs_1",
            canonical_inputs_version=dict(kwargs.get("canonical_inputs_version") or {}),
            lifecycle_status=kwargs["lifecycle_status"],
            validation_status=kwargs["validation_status"],
            commit_metadata=kwargs["commit_metadata"],
        )


@pytest.mark.asyncio
async def test_resolve_latest_artifact_version_refs_returns_latest_ids() -> None:
    refs = await resolve_latest_artifact_version_refs(
        app_id="app_1",
        artifact_kinds=("concept", "build_plan", "missing"),
        artifact_store=_FakeArtifactStore(),
    )

    assert refs == {
        "concept": "av_concept_2",
        "build_plan": "av_build_plan_1",
    }


@pytest.mark.asyncio
async def test_persist_summary_artifact_registers_parent_inputs_and_summary_payload() -> None:
    store = _FakeArtifactStore()

    artifact = await persist_summary_artifact(
        app_id="app_1",
        artifact_kind="design_docs",
        artifact_key="design_docs",
        summary_payload={"surface_map": {"surfaces": [{"surface_id": "dashboard"}]}},
        source_workflow="DesignDocs",
        source_chat_id="chat_1",
        author_user_id="user_1",
        revision_mode=True,
        input_artifact_kinds=("concept", "build_plan"),
        artifact_store=store,
    )

    assert store.create_calls[0]["parent_build_record_id"] == "av_design_docs_1"
    assert store.create_calls[0]["canonical_inputs_version"] == {
        "concept": "av_concept_2",
        "build_plan": "av_build_plan_1",
    }
    assert store.create_calls[0]["lifecycle_status"] == ArtifactLifecycleStatus.DRAFT
    assert store.create_calls[0]["validation_status"] == ArtifactValidationStatus.SKIPPED
    assert extract_summary_payload(artifact) == {"surface_map": {"surfaces": [{"surface_id": "dashboard"}]}}


# ── DRAFT suppression and first-run tests ─────────────────────────────────────


class _DraftOnlyArtifactStore:
    """Returns only DRAFT versions — simulates a mid-revision state."""

    def __init__(self) -> None:
        self.create_calls: list = []

    async def list_build_records(
        self,
        *,
        app_id: str,
        build_family: str,
        build_key: str | None = None,
        lifecycle_status: ArtifactLifecycleStatus | None = None,
        limit: int = 1,
    ):
        all_versions = [
            ArtifactVersionDoc(
                _id="av_concept_draft_1",
                app_id=app_id,
                build_family="concept",
                build_key="concept",
                version_number=2,
                lineage_root_id="av_concept_1",
                parent_build_record_id="av_concept_1",
                lifecycle_status=ArtifactLifecycleStatus.DRAFT,
                validation_status=ArtifactValidationStatus.SKIPPED,
                commit_metadata={"metadata": {}},
            )
        ] if build_family == "concept" else []
        if lifecycle_status is not None:
            return [v for v in all_versions if v.lifecycle_status == lifecycle_status]
        return all_versions

    async def create_build_record(self, **kwargs):
        self.create_calls.append(dict(kwargs))
        return ArtifactVersionDoc(
            _id="av_new",
            app_id=kwargs["app_id"],
            build_family=kwargs["build_family"],
            build_key=kwargs["build_key"],
            version_number=1,
            lineage_root_id="av_new",
            lifecycle_status=kwargs["lifecycle_status"],
            validation_status=kwargs["validation_status"],
            commit_metadata=kwargs["commit_metadata"],
        )


class _EmptyArtifactStore(_DraftOnlyArtifactStore):
    """Returns no versions — simulates first-run state."""

    async def list_build_records(self, **kwargs):
        return []


@pytest.mark.asyncio
async def test_resolve_latest_artifact_version_refs_excludes_draft_artifacts() -> None:
    """DRAFT artifacts must not be returned; only CURRENT versions are canonical inputs."""
    refs = await resolve_latest_artifact_version_refs(
        app_id="app_1",
        artifact_kinds=("concept",),
        artifact_store=_DraftOnlyArtifactStore(),
    )

    # The draft version must not appear in canonical inputs.
    assert "concept" not in refs


@pytest.mark.asyncio
async def test_resolve_latest_artifact_version_refs_returns_empty_on_first_run() -> None:
    """On first run (no prior artifacts) the result is an empty dict, not an error."""
    refs = await resolve_latest_artifact_version_refs(
        app_id="app_1",
        artifact_kinds=("concept", "design_docs"),
        artifact_store=_EmptyArtifactStore(),
    )

    assert refs == {}


@pytest.mark.asyncio
async def test_persist_summary_artifact_first_run_has_no_parent() -> None:
    """On first run the parent lookup returns nothing; parent_build_record_id must be None."""
    store = _EmptyArtifactStore()

    await persist_summary_artifact(
        app_id="app_1",
        artifact_kind="concept",
        summary_payload={"name": "My App"},
        artifact_store=store,
    )

    assert store.create_calls[0]["parent_build_record_id"] is None
