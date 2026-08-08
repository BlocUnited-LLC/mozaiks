from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from mozaiksai.control_plane.artifact_promotion import (
    AcceptedStagedAppBundleBuildRecordError,
    DraftAppBundleBuildRecordError,
    accept_staged_refinement_build_record,
    create_draft_app_bundle_from_staged_refinement,
)
from mozaiksai.control_plane.dry_run import (
    RefinementExecutionPlan,
    RefinementExecutionProfiles,
    RefinementValidationItem,
    RefinementValidationPlan,
)
from mozaiksai.control_plane.review import (
    RefinementReviewRecord,
    approve_refinement_staging,
    create_refinement_review_record,
    mark_refinement_promotion_ready,
)
from mozaiksai.control_plane.scoped_execution import (
    ScopedRefinementChange,
    ScopedRefinementResult,
    apply_scoped_refinement_changes,
)
from mozaiksai.control_plane.staging import (
    RefinementStagingResult,
    create_refinement_staging_workspace,
)
from mozaiksai.control_plane.validation_evidence import ValidationEvidence
from mozaiksai.core.artifacts import (
    BuildRecord,
    BuildRecordStatus,
    BuildRecordValidationStatus,
)


def _write_source_bundle(root: Path) -> None:
    (root / "ui" / "pages").mkdir(parents=True, exist_ok=True)
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "ui" / "pages" / "dashboard.yaml").write_text("title: Original dashboard\n", encoding="utf-8")
    (root / "ui" / "index.js").write_text("export default function App() { return null; }\n", encoding="utf-8")
    (root / "config" / "shell.json").write_text(json.dumps({"routes": []}), encoding="utf-8")


def _zip_source_bundle(source_bundle: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(source_bundle.rglob("*")):
            if not file_path.is_file() or file_path.is_symlink():
                continue
            zf.write(file_path, arcname=file_path.relative_to(source_bundle).as_posix())


def _build_plan(*, request_id: str, app_id: str, staging_area: Path) -> RefinementExecutionPlan:
    return RefinementExecutionPlan(
        request_id=request_id,
        app_id=app_id,
        request="Update the dashboard surface.",
        build_family="app_bundle",
        change_class="patch",
        refinement_lane="ui_patch",
        workflow_id="workflow_app",
        workflow_sequence="app_revision",
        target_workflow="AppGenerator",
        affected_workflows=["AppGenerator"],
        affected_declarative_families=["experience_spec"],
        affected_bundle_paths=[
            "ui/pages/dashboard.yaml",
            "ui/index.js",
            "config/shell.json",
        ],
        scope_summary="Narrow UI leaf patch.",
        profiles=RefinementExecutionProfiles(
            classifier="classifier_v1",
            planner_replanner="planner_v1",
            codegen="codegen_v1",
            reviewer_validator="reviewer_v1",
        ),
        validation_plan=RefinementValidationPlan(
            items=[
                RefinementValidationItem(
                    id="route_component_validation",
                    label="Route/component validation",
                    required=True,
                    reason="UI leaf patch validation.",
                ),
                RefinementValidationItem(
                    id="ui_theme_primitive_validation",
                    label="UI theme/primitive validation",
                    required=True,
                    reason="UI leaf patch validation.",
                ),
            ]
        ),
        execution_mode="staged",
        mutation_allowed=False,
        staging_area=str(staging_area),
        generated_files_changed=False,
        human_review_required=True,
        next_step="review",
        warnings=[],
    )


def _build_source_record(*, app_id: str, source_zip: Path) -> BuildRecord:
    return BuildRecord.model_validate(
        {
            "_id": "av_source_1",
            "app_id": app_id,
            "build_family": "app_bundle",
            "build_key": "app_bundle",
            "version_number": 1,
            "lineage_root_id": "av_source_1",
            "canonical_inputs_version": {"concept": "av_concept_1"},
            "lifecycle_status": BuildRecordStatus.CURRENT.value,
            "validation_status": BuildRecordValidationStatus.PASSED.value,
            "files_manifest": [
                {"path": "ui/pages/dashboard.yaml", "sha256": "sha-source", "size_bytes": 27},
            ],
            "commit_metadata": {"metadata": {"artifact_path": str(source_zip.resolve())}},
        }
    )


class _FakeBuildRecordStore:
    def __init__(self, source_record: BuildRecord) -> None:
        self.source_record = source_record
        self.create_calls: list[dict] = []
        self.validation_calls: list[dict] = []
        self.created_record: BuildRecord | None = None

    async def get_build_record(self, *, app_id: str, build_record_id: str):
        if app_id == self.source_record.app_id and build_record_id == self.source_record.id:
            return self.source_record
        if self.created_record is not None and app_id == self.created_record.app_id and build_record_id == self.created_record.id:
            return self.created_record
        return None

    async def list_build_records(self, **kwargs):
        if (
            kwargs.get("app_id") == self.source_record.app_id
            and kwargs.get("build_family") == "app_bundle"
            and kwargs.get("lifecycle_status") == BuildRecordStatus.CURRENT
        ):
            return [self.source_record]
        return []

    async def create_build_record(self, **kwargs):
        self.create_calls.append(dict(kwargs))
        self.created_record = BuildRecord.model_validate(
            {
                "_id": "av_draft_1",
                "app_id": kwargs["app_id"],
                "build_family": kwargs["build_family"],
                "build_key": kwargs["build_key"],
                "version_number": 2,
                "parent_build_record_id": kwargs.get("parent_build_record_id"),
                "lineage_root_id": self.source_record.lineage_root_id,
                "canonical_inputs_version": dict(kwargs.get("canonical_inputs_version") or {}),
                "lifecycle_status": kwargs["lifecycle_status"].value,
                "validation_status": kwargs["validation_status"].value,
                "files_manifest": list(kwargs.get("files_manifest") or []),
                "commit_metadata": kwargs["commit_metadata"],
            }
        )
        return self.created_record

    async def set_validation_status(self, **kwargs):
        self.validation_calls.append(dict(kwargs))
        if self.created_record is not None and kwargs.get("build_record_id") == self.created_record.id:
            commit_metadata = kwargs.get("commit_metadata") or {}
            self.created_record = self.created_record.model_copy(
                update={
                    "validation_status": kwargs["validation_status"],
                    "commit_metadata": commit_metadata,
                }
            )
        return True

    async def accept_build_record(self, *, app_id: str, build_record_id: str, commit_metadata: dict | None = None):
        record = None
        if self.created_record is not None and self.created_record.id == build_record_id and self.created_record.app_id == app_id:
            record = self.created_record
        elif self.source_record.id == build_record_id and self.source_record.app_id == app_id:
            record = self.source_record
        if record is None:
            return None
        accepted = record.model_copy(
            update={
                "lifecycle_status": BuildRecordStatus.CURRENT,
                "commit_metadata": commit_metadata or record.commit_metadata,
            }
        )
        if record is self.created_record:
            self.created_record = accepted
        return accepted


class _FakeGridFSContentStore:
    backend_name = "gridfs"

    def __init__(self) -> None:
        self.put_calls: list[dict] = []

    async def put_bundle(self, data: bytes, *, app_id: str, build_record_id: str) -> str:
        self.put_calls.append(
            {
                "data": data,
                "app_id": app_id,
                "build_record_id": build_record_id,
            }
        )
        return "gridfs_ref_1"


@pytest.mark.asyncio
async def test_creates_draft_build_record_from_staged_workspace(tmp_path: Path) -> None:
    app_id = "app_1"
    source_bundle = tmp_path / "source_bundle"
    _write_source_bundle(source_bundle)
    source_zip = tmp_path / "source_bundle.zip"
    _zip_source_bundle(source_bundle, source_zip)

    staging_root = tmp_path / "staging"
    plan = _build_plan(request_id="refine_123", app_id=app_id, staging_area=staging_root / "refine_123")
    source_record = _build_source_record(app_id=app_id, source_zip=source_zip)
    build_record_store = _FakeBuildRecordStore(source_record)

    staging_result = create_refinement_staging_workspace(
        plan,
        source_bundle_path=source_bundle,
        staging_root=staging_root,
    )
    create_refinement_review_record(staging_result)
    approve_refinement_staging(staging_result.staging_area, reviewer="reviewer_1")
    review_record = mark_refinement_promotion_ready(staging_result.staging_area, reviewer="reviewer_1")
    scoped_result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[
            ScopedRefinementChange(
                path="ui/pages/dashboard.yaml",
                new_content="title: Updated dashboard\n",
                reason="Update copy",
            )
        ],
    )
    source_before = (source_bundle / "ui" / "pages" / "dashboard.yaml").read_text(encoding="utf-8")
    evidence = ValidationEvidence(
        completed=["route_component_validation", "ui_theme_primitive_validation"],
        failed=[],
        warnings=[],
        artifacts=["validation-report.json"],
        checked_at="2026-05-22T00:00:00Z",
        source="refinement_validation_runner",
    )
    policy_decisions = [
        {
            "path": "ui/pages/dashboard.yaml",
            "allowed": True,
            "mode": "direct_leaf_patch",
            "reason": "Allowed for ui_patch.",
            "required_artifacts": [],
            "required_validation": ["route_component_validation", "ui_theme_primitive_validation"],
        }
    ]

    result = await create_draft_app_bundle_from_staged_refinement(
        plan,
        staging_result,
        scoped_result,
        review_record,
        evidence,
        record_store=build_record_store,
        source_build_record_id=source_record.id,
        generated_artifacts_root=tmp_path / "generated",
        policy_decisions=policy_decisions,
    )

    assert result.build_family == "app_bundle"
    assert result.build_record.build_family == "app_bundle"
    assert result.lifecycle_status == BuildRecordStatus.DRAFT
    assert result.parent_build_record_id == source_record.id
    assert result.validation_status == BuildRecordValidationStatus.PASSED
    assert result.content_ref is None
    assert result.build_record.commit_metadata.metadata["refinement"]["request_id"] == plan.request_id
    assert result.build_record.commit_metadata.metadata["refinement"]["change_class"] == "patch"
    assert result.build_record.commit_metadata.metadata["refinement"]["refinement_lane"] == "ui_patch"
    assert result.build_record.commit_metadata.metadata["refinement"]["validation_evidence"]["completed"] == [
        "route_component_validation",
        "ui_theme_primitive_validation",
    ]
    assert result.build_record.commit_metadata.metadata["refinement"]["review"]["status"] == "promotion_ready"
    assert result.build_record.commit_metadata.metadata["refinement"]["review"]["write_back_mode"] == "generated_artifact"
    assert result.build_record.commit_metadata.metadata["refinement"]["review"]["write_back_target"] is None
    assert result.build_record.commit_metadata.metadata["refinement"]["scoped_execution"]["execution_mode"] == "scoped_staging"
    assert result.build_record.commit_metadata.metadata["refinement"]["promotion_policy"]["policy_decisions"] == policy_decisions
    assert result.build_record.commit_metadata.metadata["artifact_path"] == result.artifact_path

    created = build_record_store.create_calls[0]
    assert created["build_family"] == "app_bundle"
    assert created["build_key"] == "app_bundle"
    assert created["parent_build_record_id"] == source_record.id
    assert created["lifecycle_status"] == BuildRecordStatus.DRAFT
    assert created["validation_status"] == BuildRecordValidationStatus.PASSED
    manifest_paths = [entry["path"] for entry in created["files_manifest"]]
    assert manifest_paths == [
        "config/shell.json",
        "ui/index.js",
        "ui/pages/dashboard.yaml",
    ]

    with zipfile.ZipFile(result.artifact_path, "r") as archive:
        zip_names = sorted(name for name in archive.namelist() if not name.endswith("/"))
    assert zip_names == [
        "config/shell.json",
        "ui/index.js",
        "ui/pages/dashboard.yaml",
    ]
    assert "refinement_plan.json" not in zip_names
    assert "affected_paths.json" not in zip_names
    assert "refinement_review.json" not in zip_names
    assert "execution_result.json" not in zip_names
    assert not any(name.startswith("backups/") for name in zip_names)
    assert ".env" not in zip_names
    assert (source_bundle / "ui" / "pages" / "dashboard.yaml").read_text(encoding="utf-8") == source_before


@pytest.mark.asyncio
async def test_non_local_content_store_records_content_ref_and_backend(tmp_path: Path) -> None:
    app_id = "app_1"
    source_bundle = tmp_path / "source_bundle"
    _write_source_bundle(source_bundle)
    source_zip = tmp_path / "source_bundle.zip"
    _zip_source_bundle(source_bundle, source_zip)

    staging_root = tmp_path / "staging"
    plan = _build_plan(request_id="refine_content", app_id=app_id, staging_area=staging_root / "refine_content")
    source_record = _build_source_record(app_id=app_id, source_zip=source_zip)
    build_record_store = _FakeBuildRecordStore(source_record)
    content_store = _FakeGridFSContentStore()

    staging_result = create_refinement_staging_workspace(
        plan,
        source_bundle_path=source_bundle,
        staging_root=staging_root,
    )
    create_refinement_review_record(staging_result)
    approve_refinement_staging(staging_result.staging_area)
    review_record = mark_refinement_promotion_ready(staging_result.staging_area, reviewer="reviewer_1")
    scoped_result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[
            ScopedRefinementChange(
                path="ui/pages/dashboard.yaml",
                new_content="title: Updated dashboard\n",
                reason="Update copy",
            )
        ],
    )
    evidence = ValidationEvidence(
        completed=["route_component_validation"],
        failed=[],
        warnings=[],
        artifacts=[],
        checked_at=None,
        source="refinement_validation_runner",
    )

    result = await create_draft_app_bundle_from_staged_refinement(
        plan,
        staging_result,
        scoped_result,
        review_record,
        evidence,
        record_store=build_record_store,
        content_store=content_store,
        source_build_record_id=source_record.id,
        generated_artifacts_root=tmp_path / "generated",
    )

    assert result.content_ref == "gridfs_ref_1"
    assert result.content_backend == "gridfs"
    assert content_store.put_calls[0]["build_record_id"] == "av_draft_1"
    assert result.metadata["content_ref"] == "gridfs_ref_1"
    assert result.metadata["content_backend"] == "gridfs"


@pytest.mark.asyncio
async def test_bundle_excludes_secret_paths_symlinks_and_staging_metadata(tmp_path: Path) -> None:
    app_id = "app_1"
    source_bundle = tmp_path / "source_bundle"
    _write_source_bundle(source_bundle)
    source_zip = tmp_path / "source_bundle.zip"
    _zip_source_bundle(source_bundle, source_zip)

    staging_root = tmp_path / "staging"
    plan = _build_plan(request_id="refine_456", app_id=app_id, staging_area=staging_root / "refine_456")
    source_record = _build_source_record(app_id=app_id, source_zip=source_zip)
    build_record_store = _FakeBuildRecordStore(source_record)

    staging_result = create_refinement_staging_workspace(
        plan,
        source_bundle_path=source_bundle,
        staging_root=staging_root,
    )
    create_refinement_review_record(staging_result)
    approve_refinement_staging(staging_result.staging_area)
    review_record = mark_refinement_promotion_ready(staging_result.staging_area, reviewer="reviewer_1")
    scoped_result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[
            ScopedRefinementChange(
                path="ui/pages/dashboard.yaml",
                new_content="title: Updated dashboard\n",
                reason="Update copy",
            )
        ],
    )

    # Add staged-only files that must not be bundled.
    (Path(staging_result.staging_area) / "backups").mkdir(parents=True, exist_ok=True)
    (Path(staging_result.staging_area) / "backups" / "old.txt").write_text("backup", encoding="utf-8")
    (Path(staging_result.staging_area) / "workspace" / ".env").write_text("SECRET=1\n", encoding="utf-8")
    symlink_path = Path(staging_result.staging_area) / "workspace" / "ui" / "pages" / "dashboard.link.yaml"
    try:
        symlink_path.symlink_to(Path(staging_result.staging_area) / "workspace" / "ui" / "pages" / "dashboard.yaml")
    except OSError:
        pytest.skip("Symlink creation is not supported in this environment.")

    evidence = ValidationEvidence(
        completed=["route_component_validation", "ui_theme_primitive_validation"],
        failed=[],
        warnings=[],
        artifacts=[],
        checked_at=None,
        source="refinement_validation_runner",
    )

    result = await create_draft_app_bundle_from_staged_refinement(
        plan,
        staging_result,
        scoped_result,
        review_record,
        evidence,
        record_store=build_record_store,
        source_build_record_id=source_record.id,
        generated_artifacts_root=tmp_path / "generated",
    )

    with zipfile.ZipFile(result.artifact_path, "r") as archive:
        zip_names = sorted(name for name in archive.namelist() if not name.endswith("/"))
    assert zip_names == [
        "config/shell.json",
        "ui/index.js",
        "ui/pages/dashboard.yaml",
    ]
    assert "backups/old.txt" not in zip_names
    assert ".env" not in zip_names
    assert "ui/pages/dashboard.link.yaml" not in zip_names


@pytest.mark.asyncio
async def test_draft_build_record_metadata_contains_lineage_validation_and_review(tmp_path: Path) -> None:
    app_id = "app_1"
    source_bundle = tmp_path / "source_bundle"
    _write_source_bundle(source_bundle)
    source_zip = tmp_path / "source_bundle.zip"
    _zip_source_bundle(source_bundle, source_zip)

    staging_root = tmp_path / "staging"
    plan = _build_plan(request_id="refine_789", app_id=app_id, staging_area=staging_root / "refine_789")
    source_record = _build_source_record(app_id=app_id, source_zip=source_zip)
    build_record_store = _FakeBuildRecordStore(source_record)

    staging_result = create_refinement_staging_workspace(
        plan,
        source_bundle_path=source_bundle,
        staging_root=staging_root,
    )
    create_refinement_review_record(staging_result)
    approve_refinement_staging(staging_result.staging_area)
    review_record = mark_refinement_promotion_ready(staging_result.staging_area, reviewer="reviewer_1")
    scoped_result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[
            ScopedRefinementChange(
                path="ui/pages/dashboard.yaml",
                new_content="title: Updated dashboard\n",
                reason="Update copy",
            )
        ],
    )
    evidence = ValidationEvidence(
        completed=["route_component_validation"],
        failed=[],
        warnings=["validator warning"],
        artifacts=["validation-report.json"],
        checked_at="2026-05-22T00:00:00Z",
        source="refinement_validation_runner",
    )

    result = await create_draft_app_bundle_from_staged_refinement(
        plan,
        staging_result,
        scoped_result,
        review_record,
        evidence,
        record_store=build_record_store,
        source_build_record_id=source_record.id,
        generated_artifacts_root=tmp_path / "generated",
        policy_decisions=[
            {
                "path": "ui/pages/dashboard.yaml",
                "allowed": True,
                "mode": "direct_leaf_patch",
                "reason": "Allowed for ui_patch.",
                "required_artifacts": [],
                "required_validation": ["route_component_validation"],
            }
        ],
    )

    metadata = result.metadata["refinement"]
    assert result.build_record.parent_build_record_id == source_record.id
    assert result.build_record.validation_status == BuildRecordValidationStatus.PASSED
    assert metadata["request_id"] == plan.request_id
    assert metadata["change_class"] == "patch"
    assert metadata["refinement_lane"] == "ui_patch"
    assert metadata["workflow_sequence"] == "app_revision"
    assert metadata["affected_bundle_paths"] == plan.affected_bundle_paths
    assert metadata["staging_area"] == staging_result.staging_area
    assert metadata["source_bundle_path"] == staging_result.source_bundle_path
    assert metadata["source_build_record_id"] == source_record.id
    assert metadata["validation_evidence"]["warnings"] == ["validator warning"]
    assert metadata["review"]["status"] == "promotion_ready"
    assert metadata["review"]["reviewer"] == "reviewer_1"
    assert metadata["review"]["write_back_mode"] == "generated_artifact"
    assert metadata["review"]["write_back_target"] is None
    assert metadata["scoped_execution"]["execution_mode"] == "scoped_staging"
    assert metadata["promotion_policy"]["policy_decisions"][0]["path"] == "ui/pages/dashboard.yaml"
    assert result.files_manifest[0].path == "config/shell.json"
    assert result.bundle_sha256
    assert result.bundle_size_bytes > 0
    assert result.artifact_path.endswith("artifact.zip")


@pytest.mark.asyncio
async def test_request_id_mismatch_blocks_creation(tmp_path: Path) -> None:
    app_id = "app_1"
    source_bundle = tmp_path / "source_bundle"
    _write_source_bundle(source_bundle)
    source_zip = tmp_path / "source_bundle.zip"
    _zip_source_bundle(source_bundle, source_zip)

    staging_root = tmp_path / "staging"
    plan = _build_plan(request_id="refine_999", app_id=app_id, staging_area=staging_root / "refine_999")
    source_record = _build_source_record(app_id=app_id, source_zip=source_zip)
    build_record_store = _FakeBuildRecordStore(source_record)

    staging_result = create_refinement_staging_workspace(
        plan,
        source_bundle_path=source_bundle,
        staging_root=staging_root,
    )
    create_refinement_review_record(staging_result)
    approve_refinement_staging(staging_result.staging_area)
    review_record = mark_refinement_promotion_ready(staging_result.staging_area).model_copy(
        update={"request_id": "mismatch"}
    )
    scoped_result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[
            ScopedRefinementChange(
                path="ui/pages/dashboard.yaml",
                new_content="title: Updated dashboard\n",
                reason="Update copy",
            )
        ],
    )

    with pytest.raises(DraftAppBundleBuildRecordError):
        await create_draft_app_bundle_from_staged_refinement(
            plan,
            staging_result,
            scoped_result,
            review_record,
            ValidationEvidence(completed=["route_component_validation"], failed=[], warnings=[], artifacts=[]),
            record_store=build_record_store,
            source_build_record_id=source_record.id,
            generated_artifacts_root=tmp_path / "generated",
        )


@pytest.mark.asyncio
async def test_missing_workspace_blocks_creation(tmp_path: Path) -> None:
    plan = _build_plan(request_id="refine_missing", app_id="app_1", staging_area=tmp_path / "missing_staging")
    staging_result = RefinementStagingResult(
        request_id=plan.request_id,
        staging_area=str(tmp_path / "missing_staging"),
        plan_path=str(tmp_path / "missing_staging" / "refinement_plan.json"),
        affected_paths_path=str(tmp_path / "missing_staging" / "affected_paths.json"),
        manifest_path=str(tmp_path / "missing_staging" / "README.md"),
        files=[],
        source_bundle_path=str(tmp_path / "source_bundle"),
        mutation_allowed=False,
    )
    review_record = RefinementReviewRecord(
        request_id=plan.request_id,
        status="promotion_ready",
        reviewer=None,
        reviewed_at=None,
        decision=None,
        notes=None,
        promotion_allowed=True,
        source_bundle_path=staging_result.source_bundle_path,
        staging_area=staging_result.staging_area,
        affected_bundle_paths=list(plan.affected_bundle_paths),
        mutation_allowed=False,
    )
    scoped_result = ScopedRefinementResult(
        request_id=plan.request_id,
        staging_area=staging_result.staging_area,
        changed_files=[],
        result_path=str(Path(staging_result.staging_area) / "execution_result.json"),
    )

    with pytest.raises(DraftAppBundleBuildRecordError):
        await create_draft_app_bundle_from_staged_refinement(
            plan,
            staging_result,
            scoped_result,
            review_record,
            ValidationEvidence(completed=[], failed=[], warnings=[], artifacts=[]),
            app_id="app_1",
            record_store=_FakeBuildRecordStore(
                BuildRecord.model_validate(
                    {
                        "_id": "av_source_1",
                        "app_id": "app_1",
                        "build_family": "app_bundle",
                        "build_key": "app_bundle",
                        "version_number": 1,
                        "lineage_root_id": "av_source_1",
                        "lifecycle_status": BuildRecordStatus.CURRENT.value,
                        "validation_status": BuildRecordValidationStatus.PASSED.value,
                        "commit_metadata": {"metadata": {}},
                    }
                )
            ),
            source_build_record_id="av_source_1",
            generated_artifacts_root=tmp_path / "generated",
        )


@pytest.mark.asyncio
async def test_failed_validation_maps_to_failed_status(tmp_path: Path) -> None:
    app_id = "app_1"
    source_bundle = tmp_path / "source_bundle"
    _write_source_bundle(source_bundle)
    source_zip = tmp_path / "source_bundle.zip"
    _zip_source_bundle(source_bundle, source_zip)

    staging_root = tmp_path / "staging"
    plan = _build_plan(request_id="refine_failed", app_id=app_id, staging_area=staging_root / "refine_failed")
    source_record = _build_source_record(app_id=app_id, source_zip=source_zip)
    build_record_store = _FakeBuildRecordStore(source_record)

    staging_result = create_refinement_staging_workspace(
        plan,
        source_bundle_path=source_bundle,
        staging_root=staging_root,
    )
    create_refinement_review_record(staging_result)
    approve_refinement_staging(staging_result.staging_area)
    review_record = mark_refinement_promotion_ready(staging_result.staging_area)
    scoped_result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[
            ScopedRefinementChange(
                path="ui/pages/dashboard.yaml",
                new_content="title: Updated dashboard\n",
                reason="Update copy",
            )
        ],
    )

    result = await create_draft_app_bundle_from_staged_refinement(
        plan,
        staging_result,
        scoped_result,
        review_record,
        ValidationEvidence(
            completed=["route_component_validation"],
            failed=["ui_theme_primitive_validation"],
            warnings=[],
            artifacts=[],
        ),
        record_store=build_record_store,
        source_build_record_id=source_record.id,
        generated_artifacts_root=tmp_path / "generated",
    )

    assert result.validation_status == BuildRecordValidationStatus.FAILED
    assert result.build_record.validation_status == BuildRecordValidationStatus.FAILED


@pytest.mark.asyncio
async def test_accept_staged_refinement_promotes_draft_to_current(tmp_path: Path) -> None:
    app_id = "app_accept_1"
    source_bundle = tmp_path / "source_bundle"
    _write_source_bundle(source_bundle)
    source_zip = tmp_path / "source_bundle.zip"
    _zip_source_bundle(source_bundle, source_zip)

    staging_root = tmp_path / "staging"
    plan = _build_plan(request_id="refine_accept_1", app_id=app_id, staging_area=staging_root / "refine_accept_1")
    source_record = _build_source_record(app_id=app_id, source_zip=source_zip)
    build_record_store = _FakeBuildRecordStore(source_record)

    staging_result = create_refinement_staging_workspace(plan, source_bundle_path=source_bundle, staging_root=staging_root)
    create_refinement_review_record(staging_result)
    approve_refinement_staging(staging_result.staging_area, reviewer="reviewer_1")
    review_record = mark_refinement_promotion_ready(staging_result.staging_area, reviewer="reviewer_1")
    scoped_result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[ScopedRefinementChange(path="ui/pages/dashboard.yaml", new_content="title: Accepted\n", reason="Accept test")],
    )

    draft_result = await create_draft_app_bundle_from_staged_refinement(
        plan,
        staging_result,
        scoped_result,
        review_record,
        ValidationEvidence(completed=["route_component_validation", "ui_theme_primitive_validation"], failed=[], warnings=[], artifacts=[]),
        record_store=build_record_store,
        source_build_record_id=source_record.id,
        generated_artifacts_root=tmp_path / "generated",
    )
    assert draft_result.lifecycle_status == BuildRecordStatus.DRAFT

    accepted_result = await accept_staged_refinement_build_record(
        app_id=app_id,
        draft_build_record_id=draft_result.build_record_id,
        review_record=review_record,
        request_id=plan.request_id,
        record_store=build_record_store,
        accepted_by="reviewer_1",
    )

    assert accepted_result.lifecycle_status == BuildRecordStatus.CURRENT
    assert accepted_result.build_family == "app_bundle"
    assert accepted_result.request_id == plan.request_id
    assert accepted_result.accepted_by == "reviewer_1"
    assert accepted_result.refinement_review_status == "promotion_ready"
    acceptance = accepted_result.metadata.get("acceptance")
    assert isinstance(acceptance, dict)
    assert acceptance["accepted_by"] == "reviewer_1"
    assert acceptance["request_id"] == plan.request_id
    assert acceptance["refinement_review_status"] == "promotion_ready"


@pytest.mark.asyncio
async def test_accept_staged_refinement_rejects_non_draft_record(tmp_path: Path) -> None:
    app_id = "app_accept_2"
    source_bundle = tmp_path / "source_bundle"
    _write_source_bundle(source_bundle)
    source_zip = tmp_path / "source_bundle.zip"
    _zip_source_bundle(source_bundle, source_zip)

    staging_root = tmp_path / "staging"
    plan = _build_plan(request_id="refine_accept_2", app_id=app_id, staging_area=staging_root / "refine_accept_2")
    source_record = _build_source_record(app_id=app_id, source_zip=source_zip)
    build_record_store = _FakeBuildRecordStore(source_record)

    staging_result = create_refinement_staging_workspace(plan, source_bundle_path=source_bundle, staging_root=staging_root)
    create_refinement_review_record(staging_result)
    approve_refinement_staging(staging_result.staging_area)
    review_record = mark_refinement_promotion_ready(staging_result.staging_area)
    scoped_result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[ScopedRefinementChange(path="ui/pages/dashboard.yaml", new_content="title: X\n", reason="x")],
    )

    draft_result = await create_draft_app_bundle_from_staged_refinement(
        plan,
        staging_result,
        scoped_result,
        review_record,
        ValidationEvidence(
            completed=["route_component_validation", "ui_theme_primitive_validation"],
            failed=[],
            warnings=[],
            artifacts=[],
        ),
        record_store=build_record_store,
        source_build_record_id=source_record.id,
        generated_artifacts_root=tmp_path / "generated",
    )
    # Accept once to promote to CURRENT
    await accept_staged_refinement_build_record(
        app_id=app_id,
        draft_build_record_id=draft_result.build_record_id,
        review_record=review_record,
        request_id=plan.request_id,
        record_store=build_record_store,
    )
    # Second accept must be rejected — record is now CURRENT, not DRAFT
    with pytest.raises(AcceptedStagedAppBundleBuildRecordError, match="DRAFT"):
        await accept_staged_refinement_build_record(
            app_id=app_id,
            draft_build_record_id=draft_result.build_record_id,
            review_record=review_record,
            request_id=plan.request_id,
            record_store=build_record_store,
        )


@pytest.mark.asyncio
async def test_accept_staged_refinement_rejects_request_id_mismatch(tmp_path: Path) -> None:
    app_id = "app_accept_3"
    source_bundle = tmp_path / "source_bundle"
    _write_source_bundle(source_bundle)
    source_zip = tmp_path / "source_bundle.zip"
    _zip_source_bundle(source_bundle, source_zip)

    staging_root = tmp_path / "staging"
    plan = _build_plan(request_id="refine_accept_3", app_id=app_id, staging_area=staging_root / "refine_accept_3")
    source_record = _build_source_record(app_id=app_id, source_zip=source_zip)
    build_record_store = _FakeBuildRecordStore(source_record)

    staging_result = create_refinement_staging_workspace(plan, source_bundle_path=source_bundle, staging_root=staging_root)
    create_refinement_review_record(staging_result)
    approve_refinement_staging(staging_result.staging_area)
    review_record = mark_refinement_promotion_ready(staging_result.staging_area)
    scoped_result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[ScopedRefinementChange(path="ui/pages/dashboard.yaml", new_content="title: Y\n", reason="y")],
    )

    draft_result = await create_draft_app_bundle_from_staged_refinement(
        plan,
        staging_result,
        scoped_result,
        review_record,
        ValidationEvidence(
            completed=["route_component_validation", "ui_theme_primitive_validation"],
            failed=[],
            warnings=[],
            artifacts=[],
        ),
        record_store=build_record_store,
        source_build_record_id=source_record.id,
        generated_artifacts_root=tmp_path / "generated",
    )

    with pytest.raises(AcceptedStagedAppBundleBuildRecordError, match="request_id mismatch"):
        await accept_staged_refinement_build_record(
            app_id=app_id,
            draft_build_record_id=draft_result.build_record_id,
            review_record=review_record,
            request_id="wrong_request_id",
            record_store=build_record_store,
        )


def test_artifact_promotion_module_stays_out_of_conceptual_replan_and_workflow_or_llm_paths() -> None:
    import mozaiksai.control_plane.artifact_promotion as artifact_promotion_module

    source = Path(artifact_promotion_module.__file__).read_text(encoding="utf-8")
    assert "conceptual_replan" not in source
    assert "carry_forward" not in source
    assert "AppGenerator" not in source
    assert "LLMChangeClassifier" not in source
    assert "execute_workflow" not in source

