from __future__ import annotations

import pytest

from mozaiksai.core.artifacts import (
    BuildRecord,
    BuildRecordStatus,
    BuildRecordValidationStatus,
    extract_summary_payload,
    persist_summary_build_record,
    resolve_latest_build_record_refs,
)


class _FakeBuildRecordStore:
    def __init__(self) -> None:
        self.create_calls = []

    async def list_build_records(
        self,
        *,
        app_id: str,
        build_family: str,
        build_key: str | None = None,
        lifecycle_status: BuildRecordStatus | None = None,
        limit: int = 1,
    ):
        data = {
            ("design_docs", "design_docs"): [
                BuildRecord(
                    _id="av_design_docs_1",
                    app_id=app_id,
                    build_family="design_docs",
                    build_key="design_docs",
                    version_number=1,
                    lineage_root_id="av_design_docs_1",
                    lifecycle_status=BuildRecordStatus.CURRENT,
                    validation_status=BuildRecordValidationStatus.SKIPPED,
                    commit_metadata={"metadata": {}},
                )
            ],
            ("concept", "concept"): [
                BuildRecord(
                    _id="av_concept_2",
                    app_id=app_id,
                    build_family="concept",
                    build_key="concept",
                    version_number=2,
                    lineage_root_id="av_concept_1",
                    parent_build_record_id="av_concept_1",
                    lifecycle_status=BuildRecordStatus.CURRENT,
                    validation_status=BuildRecordValidationStatus.SKIPPED,
                    commit_metadata={"metadata": {"summary_payload": {"app_name": "Demo"}}},
                )
            ],
            ("build_plan", "build_plan"): [
                BuildRecord(
                    _id="av_build_plan_1",
                    app_id=app_id,
                    build_family="build_plan",
                    build_key="build_plan",
                    version_number=1,
                    lineage_root_id="av_build_plan_1",
                    lifecycle_status=BuildRecordStatus.CURRENT,
                    validation_status=BuildRecordValidationStatus.SKIPPED,
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
        return BuildRecord(
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
async def test_resolve_latest_build_record_refs_returns_latest_ids() -> None:
    refs = await resolve_latest_build_record_refs(
        app_id="app_1",
        build_families=("concept", "build_plan", "missing"),
        build_record_store=_FakeBuildRecordStore(),
    )

    assert refs == {
        "concept": "av_concept_2",
        "build_plan": "av_build_plan_1",
    }


@pytest.mark.asyncio
async def test_persist_summary_build_record_registers_parent_inputs_and_summary_payload() -> None:
    store = _FakeBuildRecordStore()

    record = await persist_summary_build_record(
        app_id="app_1",
        build_family="design_docs",
        build_key="design_docs",
        summary_payload={"surface_map": {"surfaces": [{"surface_id": "dashboard"}]}},
        source_workflow="DesignDocs",
        source_chat_id="chat_1",
        author_user_id="user_1",
        revision_mode=True,
        input_ARTIFACT_KINDS=("concept", "build_plan"),
        build_record_store=store,
    )

    assert store.create_calls[0]["parent_build_record_id"] == "av_design_docs_1"
    assert store.create_calls[0]["canonical_inputs_version"] == {
        "concept": "av_concept_2",
        "build_plan": "av_build_plan_1",
    }
    assert store.create_calls[0]["lifecycle_status"] == BuildRecordStatus.DRAFT
    assert store.create_calls[0]["validation_status"] == BuildRecordValidationStatus.SKIPPED
    assert extract_summary_payload(record) == {"surface_map": {"surfaces": [{"surface_id": "dashboard"}]}}


# ── DRAFT suppression and first-run tests ─────────────────────────────────────


class _DraftOnlyBuildRecordStore:
    """Returns only DRAFT records — simulates a mid-revision state."""

    def __init__(self) -> None:
        self.create_calls: list = []

    async def list_build_records(
        self,
        *,
        app_id: str,
        build_family: str,
        build_key: str | None = None,
        lifecycle_status: BuildRecordStatus | None = None,
        limit: int = 1,
    ):
        all_records = [
            BuildRecord(
                _id="av_concept_draft_1",
                app_id=app_id,
                build_family="concept",
                build_key="concept",
                version_number=2,
                lineage_root_id="av_concept_1",
                parent_build_record_id="av_concept_1",
                lifecycle_status=BuildRecordStatus.DRAFT,
                validation_status=BuildRecordValidationStatus.SKIPPED,
                commit_metadata={"metadata": {}},
            )
        ] if build_family == "concept" else []
        if lifecycle_status is not None:
            return [v for v in all_records if v.lifecycle_status == lifecycle_status]
        return all_records

    async def create_build_record(self, **kwargs):
        self.create_calls.append(dict(kwargs))
        return BuildRecord(
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


class _EmptyBuildRecordStore(_DraftOnlyBuildRecordStore):
    """Returns no records — simulates first-run state."""

    async def list_build_records(self, **kwargs):
        return []


@pytest.mark.asyncio
async def test_resolve_latest_build_record_refs_excludes_draft_records() -> None:
    """DRAFT records must not be returned; only CURRENT records are canonical inputs."""
    refs = await resolve_latest_build_record_refs(
        app_id="app_1",
        build_families=("concept",),
        build_record_store=_DraftOnlyBuildRecordStore(),
    )

    # The draft record must not appear in canonical inputs.
    assert "concept" not in refs


@pytest.mark.asyncio
async def test_resolve_latest_build_record_refs_returns_empty_on_first_run() -> None:
    """On first run (no prior records) the result is an empty dict, not an error."""
    refs = await resolve_latest_build_record_refs(
        app_id="app_1",
        build_families=("concept", "design_docs"),
        build_record_store=_EmptyBuildRecordStore(),
    )

    assert refs == {}


@pytest.mark.asyncio
async def test_persist_summary_build_record_first_run_has_no_parent() -> None:
    """On first run the parent lookup returns nothing; parent_build_record_id must be None."""
    store = _EmptyBuildRecordStore()

    await persist_summary_build_record(
        app_id="app_1",
        build_family="concept",
        summary_payload={"name": "My App"},
        build_record_store=store,
    )

    assert store.create_calls[0]["parent_build_record_id"] is None

