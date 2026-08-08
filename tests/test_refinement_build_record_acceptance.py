from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from mozaiksai.control_plane.artifact_promotion import (
    AcceptedStagedAppBundleBuildRecordError,
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
    approve_refinement_staging,
    create_refinement_review_record,
    load_refinement_review_record,
    mark_refinement_promotion_ready,
)
from mozaiksai.control_plane.scoped_execution import (
    ScopedRefinementChange,
    apply_scoped_refinement_changes,
)
from mozaiksai.control_plane.staging import create_refinement_staging_workspace
from mozaiksai.control_plane.validation_evidence import ValidationEvidence
from mozaiksai.core.artifacts import (
    BuildRecord,
    BuildRecordStatus,
    BuildRecordValidationStatus,
)


def _write_source_bundle(root: Path) -> None:
    (root / "ui" / "pages").mkdir(parents=True, exist_ok=True)
    (root / "ui").mkdir(parents=True, exist_ok=True)
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
        self.created_record: BuildRecord | None = None
        self.create_calls: list[dict] = []
        self.accept_calls: list[dict] = []

    async def get_build_record(self, *, app_id: str, build_record_id: str):
        if self.created_record is not None and app_id == self.created_record.app_id and build_record_id == self.created_record.id:
            return self.created_record
        if app_id == self.source_record.app_id and build_record_id == self.source_record.id:
            return self.source_record
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

    async def accept_build_record(self, *, app_id: str, build_record_id: str, commit_metadata=None):
        self.accept_calls.append(
            {
                "app_id": app_id,
                "build_record_id": build_record_id,
                "commit_metadata": commit_metadata,
            }
        )
        if self.created_record is None or app_id != self.created_record.app_id or build_record_id != self.created_record.id:
            return None
        self.source_record = self.source_record.model_copy(
            update={"lifecycle_status": BuildRecordStatus.SUPERSEDED}
        )
        payload = self.created_record.model_dump(mode="python")
        payload["lifecycle_status"] = BuildRecordStatus.CURRENT.value
        payload["commit_metadata"] = commit_metadata or payload["commit_metadata"]
        self.created_record = BuildRecord.model_validate(payload)
        return self.created_record


async def _build_acceptance_context(tmp_path: Path):
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

    execution_result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[
            ScopedRefinementChange(
                path="ui/pages/dashboard.yaml",
                new_content="title: Updated dashboard\n",
                reason="Acceptance test update.",
            )
        ],
    )
    validation_evidence = ValidationEvidence(
        completed=["route_component_validation", "ui_theme_primitive_validation"],
        warnings=[],
        artifacts=[],
        checked_at="2026-05-22T00:00:00Z",
        source="test_refinement_build_record_acceptance",
    )

    draft_result = await create_draft_app_bundle_from_staged_refinement(
        plan,
        staging_result,
        execution_result,
        review_record,
        validation_evidence,
        record_store=build_record_store,
        source_build_record_id=source_record.id,
        app_id=app_id,
        generated_artifacts_root=tmp_path / "generated",
    )
    return {
        "app_id": app_id,
        "source_bundle": source_bundle,
        "source_record": source_record,
        "build_record_store": build_record_store,
        "plan": plan,
        "staging_result": staging_result,
        "review_record": review_record,
        "execution_result": execution_result,
        "validation_evidence": validation_evidence,
        "draft_result": draft_result,
    }


@pytest.mark.asyncio
async def test_accepts_draft_app_bundle_when_review_is_promotion_ready(tmp_path: Path) -> None:
    context = await _build_acceptance_context(tmp_path)
    result = await accept_staged_refinement_build_record(
        app_id=context["app_id"],
        draft_build_record_id=context["draft_result"].build_record_id,
        review_record=context["review_record"],
        request_id=context["plan"].request_id,
        record_store=context["build_record_store"],
        accepted_by="operator-1",
        notes="review complete",
    )

    assert result.build_family == "app_bundle"
    assert result.lifecycle_status == BuildRecordStatus.CURRENT
    assert result.accepted_by == "operator-1"
    assert result.refinement_review_status == "promotion_ready"
    assert result.metadata["refinement"]["request_id"] == context["plan"].request_id
    assert result.metadata["refinement"]["review"]["status"] == "promotion_ready"
    assert result.metadata["refinement"]["review"]["write_back_mode"] == "generated_artifact"
    assert result.metadata["acceptance"]["accepted_by"] == "operator-1"
    assert result.metadata["acceptance"]["request_id"] == context["plan"].request_id
    assert result.build_record.lifecycle_status == BuildRecordStatus.CURRENT
    assert result.build_record.commit_metadata.metadata["acceptance"]["accepted_by"] == "operator-1"
    assert context["build_record_store"].source_record.lifecycle_status == BuildRecordStatus.SUPERSEDED
    assert load_refinement_review_record(context["staging_result"].staging_area).status == "promotion_ready"


@pytest.mark.asyncio
async def test_acceptance_requires_override_for_skipped_validation(tmp_path: Path) -> None:
    context = await _build_acceptance_context(tmp_path)
    context["build_record_store"].created_record = context["build_record_store"].created_record.model_copy(
        update={"validation_status": BuildRecordValidationStatus.SKIPPED}
    )

    with pytest.raises(AcceptedStagedAppBundleBuildRecordError, match="explicit validation override"):
        await accept_staged_refinement_build_record(
            app_id=context["app_id"],
            draft_build_record_id=context["draft_result"].build_record_id,
            review_record=context["review_record"],
            request_id=context["plan"].request_id,
            record_store=context["build_record_store"],
            accepted_by="operator-1",
        )

    result = await accept_staged_refinement_build_record(
        app_id=context["app_id"],
        draft_build_record_id=context["draft_result"].build_record_id,
        review_record=context["review_record"],
        request_id=context["plan"].request_id,
        record_store=context["build_record_store"],
        accepted_by="operator-1",
        allow_validation_override=True,
    )

    acceptance = result.metadata["acceptance"]
    assert result.lifecycle_status == BuildRecordStatus.CURRENT
    assert acceptance["validation_override"] is True
    assert acceptance["validation_status"] == BuildRecordValidationStatus.SKIPPED.value


@pytest.mark.asyncio
async def test_previous_current_app_bundle_is_superseded(tmp_path: Path) -> None:
    context = await _build_acceptance_context(tmp_path)
    await accept_staged_refinement_build_record(
        app_id=context["app_id"],
        draft_build_record_id=context["draft_result"].build_record_id,
        review_record=context["review_record"],
        request_id=context["plan"].request_id,
        record_store=context["build_record_store"],
        accepted_by="operator-1",
    )

    assert context["build_record_store"].source_record.lifecycle_status == BuildRecordStatus.SUPERSEDED


@pytest.mark.asyncio
async def test_rejects_if_review_status_is_approved_but_not_promotion_ready(tmp_path: Path) -> None:
    context = await _build_acceptance_context(tmp_path)
    create_refinement_review_record(context["staging_result"])
    approved_record = approve_refinement_staging(context["staging_result"].staging_area, reviewer="reviewer_1")

    with pytest.raises(AcceptedStagedAppBundleBuildRecordError, match="promotion_ready"):
        await accept_staged_refinement_build_record(
            app_id=context["app_id"],
            draft_build_record_id=context["draft_result"].build_record_id,
            review_record=approved_record,
            request_id=context["plan"].request_id,
            record_store=context["build_record_store"],
            accepted_by="operator-1",
        )


@pytest.mark.asyncio
async def test_rejects_if_promotion_allowed_is_false(tmp_path: Path) -> None:
    context = await _build_acceptance_context(tmp_path)
    review_path = Path(context["staging_result"].staging_area) / "refinement_review.json"
    review_data = json.loads(review_path.read_text(encoding="utf-8"))
    review_data["promotion_allowed"] = False
    review_path.write_text(json.dumps(review_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    review_record = load_refinement_review_record(context["staging_result"].staging_area)

    with pytest.raises(AcceptedStagedAppBundleBuildRecordError, match="promotion_allowed"):
        await accept_staged_refinement_build_record(
            app_id=context["app_id"],
            draft_build_record_id=context["draft_result"].build_record_id,
            review_record=review_record,
            request_id=context["plan"].request_id,
            record_store=context["build_record_store"],
            accepted_by="operator-1",
        )


@pytest.mark.asyncio
async def test_rejects_request_id_mismatch(tmp_path: Path) -> None:
    context = await _build_acceptance_context(tmp_path)

    with pytest.raises(AcceptedStagedAppBundleBuildRecordError, match="request_id"):
        await accept_staged_refinement_build_record(
            app_id=context["app_id"],
            draft_build_record_id=context["draft_result"].build_record_id,
            review_record=context["review_record"],
            request_id="refine_other",
            record_store=context["build_record_store"],
            accepted_by="operator-1",
        )


@pytest.mark.asyncio
async def test_rejects_write_back_mode_mismatch(tmp_path: Path) -> None:
    context = await _build_acceptance_context(tmp_path)
    record = context["build_record_store"].created_record
    metadata = record.commit_metadata.model_dump(mode="python")
    metadata["metadata"]["refinement"]["review"]["write_back_mode"] = "external_patch"
    context["build_record_store"].created_record = record.model_copy(update={"commit_metadata": metadata})

    with pytest.raises(AcceptedStagedAppBundleBuildRecordError, match="write_back_mode"):
        await accept_staged_refinement_build_record(
            app_id=context["app_id"],
            draft_build_record_id=context["draft_result"].build_record_id,
            review_record=context["review_record"],
            request_id=context["plan"].request_id,
            record_store=context["build_record_store"],
            accepted_by="operator-1",
        )


@pytest.mark.asyncio
async def test_rejects_non_app_bundle_ARTIFACT_KIND(tmp_path: Path) -> None:
    context = await _build_acceptance_context(tmp_path)
    context["build_record_store"].created_record = context["build_record_store"].created_record.model_copy(
        update={"build_family": "workflow_bundle"}
    )

    with pytest.raises(AcceptedStagedAppBundleBuildRecordError, match="build_family='app_bundle'"):
        await accept_staged_refinement_build_record(
            app_id=context["app_id"],
            draft_build_record_id=context["draft_result"].build_record_id,
            review_record=context["review_record"],
            request_id=context["plan"].request_id,
            record_store=context["build_record_store"],
            accepted_by="operator-1",
        )


@pytest.mark.asyncio
async def test_rejects_non_draft_build_record(tmp_path: Path) -> None:
    context = await _build_acceptance_context(tmp_path)
    context["build_record_store"].created_record = context["build_record_store"].created_record.model_copy(
        update={"lifecycle_status": BuildRecordStatus.CURRENT}
    )

    with pytest.raises(AcceptedStagedAppBundleBuildRecordError, match="DRAFT artifact version"):
        await accept_staged_refinement_build_record(
            app_id=context["app_id"],
            draft_build_record_id=context["draft_result"].build_record_id,
            review_record=context["review_record"],
            request_id=context["plan"].request_id,
            record_store=context["build_record_store"],
            accepted_by="operator-1",
        )


@pytest.mark.asyncio
async def test_rejects_missing_refinement_metadata(tmp_path: Path) -> None:
    context = await _build_acceptance_context(tmp_path)
    context["build_record_store"].created_record = context["build_record_store"].created_record.model_copy(
        update={"commit_metadata": {"metadata": {}}}
    )

    with pytest.raises(AcceptedStagedAppBundleBuildRecordError, match="missing staged refinement metadata"):
        await accept_staged_refinement_build_record(
            app_id=context["app_id"],
            draft_build_record_id=context["draft_result"].build_record_id,
            review_record=context["review_record"],
            request_id=context["plan"].request_id,
            record_store=context["build_record_store"],
            accepted_by="operator-1",
        )


@pytest.mark.asyncio
async def test_acceptance_preserves_refinement_metadata_and_records_accepted_by(tmp_path: Path) -> None:
    context = await _build_acceptance_context(tmp_path)
    result = await accept_staged_refinement_build_record(
        app_id=context["app_id"],
        draft_build_record_id=context["draft_result"].build_record_id,
        review_record=context["review_record"],
        request_id=context["plan"].request_id,
        record_store=context["build_record_store"],
        accepted_by="operator-1",
        notes="operator notes",
    )

    refinement_metadata = result.build_record.commit_metadata.metadata["refinement"]
    acceptance_metadata = result.build_record.commit_metadata.metadata["acceptance"]
    assert refinement_metadata["request_id"] == context["plan"].request_id
    assert refinement_metadata["review"]["status"] == "promotion_ready"
    assert refinement_metadata["review"]["write_back_mode"] == "generated_artifact"
    assert acceptance_metadata["accepted_by"] == "operator-1"
    assert acceptance_metadata["refinement_review_status"] == "promotion_ready"
    assert acceptance_metadata["request_id"] == context["plan"].request_id


@pytest.mark.asyncio
async def test_acceptance_does_not_mutate_source_files_or_restore_root(tmp_path: Path) -> None:
    context = await _build_acceptance_context(tmp_path)
    source_file = context["source_bundle"] / "ui/pages/dashboard.yaml"
    before = source_file.read_text(encoding="utf-8")

    await accept_staged_refinement_build_record(
        app_id=context["app_id"],
        draft_build_record_id=context["draft_result"].build_record_id,
        review_record=context["review_record"],
        request_id=context["plan"].request_id,
        record_store=context["build_record_store"],
        accepted_by="operator-1",
    )

    assert source_file.read_text(encoding="utf-8") == before
    assert Path(context["draft_result"].artifact_path).exists()


def test_acceptance_module_stays_out_of_workflow_and_llm_paths() -> None:
    source = Path(__file__).resolve().parents[1] / "mozaiksai" / "control_plane" / "artifact_promotion.py"
    text = source.read_text(encoding="utf-8")

    assert "AppGenerator" not in text
    assert "LLMChangeClassifier" not in text
    assert "execute_workflow" not in text
    assert "conceptual_replan" not in text
    assert "carry_forward" not in text

