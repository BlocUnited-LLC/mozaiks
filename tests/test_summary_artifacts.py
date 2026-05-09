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

    async def list_artifact_versions(self, *, app_id: str, artifact_kind: str, artifact_key: str | None = None, limit: int = 1):
        data = {
            ("design_docs", "design_docs"): [
                ArtifactVersionDoc(
                    _id="av_design_docs_1",
                    app_id=app_id,
                    artifact_kind="design_docs",
                    artifact_key="design_docs",
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
                    artifact_kind="concept",
                    artifact_key="concept",
                    version_number=2,
                    lineage_root_id="av_concept_1",
                    parent_version_id="av_concept_1",
                    lifecycle_status=ArtifactLifecycleStatus.CURRENT,
                    validation_status=ArtifactValidationStatus.SKIPPED,
                    commit_metadata={"metadata": {"summary_payload": {"app_name": "Demo"}}},
                )
            ],
            ("build_plan", "build_plan"): [
                ArtifactVersionDoc(
                    _id="av_build_plan_1",
                    app_id=app_id,
                    artifact_kind="build_plan",
                    artifact_key="build_plan",
                    version_number=1,
                    lineage_root_id="av_build_plan_1",
                    lifecycle_status=ArtifactLifecycleStatus.CURRENT,
                    validation_status=ArtifactValidationStatus.SKIPPED,
                    commit_metadata={"metadata": {}},
                )
            ],
        }
        return data.get((artifact_kind, artifact_key), [])

    async def create_artifact_version(self, **kwargs):
        self.create_calls.append(dict(kwargs))
        return ArtifactVersionDoc(
            _id="av_design_docs_2",
            app_id=kwargs["app_id"],
            artifact_kind=kwargs["artifact_kind"],
            artifact_key=kwargs["artifact_key"],
            version_number=2,
            parent_version_id=kwargs.get("parent_version_id"),
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

    assert store.create_calls[0]["parent_version_id"] == "av_design_docs_1"
    assert store.create_calls[0]["canonical_inputs_version"] == {
        "concept": "av_concept_2",
        "build_plan": "av_build_plan_1",
    }
    assert store.create_calls[0]["lifecycle_status"] == ArtifactLifecycleStatus.DRAFT
    assert store.create_calls[0]["validation_status"] == ArtifactValidationStatus.SKIPPED
    assert extract_summary_payload(artifact) == {"surface_map": {"surfaces": [{"surface_id": "dashboard"}]}}
