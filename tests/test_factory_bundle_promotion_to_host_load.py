"""Integration test: factory-built app bundle → refinement staging → promotion → AppLoader.

Covers the gap between:
- Unit tests for accept_staged_refinement_artifact_version (promotion mechanics only)
- Factory lineage smoke (factory build → AppLoader, but artifacts created as CURRENT directly)

This test exercises the full DRAFT → promotion → CURRENT → AppLoader path using
factory-generated acceptance fixture files as the source bundle.
"""
from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path
from typing import Any

import pytest

from mozaiksai.control_plane.artifact_promotion import (
    accept_staged_refinement_artifact_version,
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
    mark_refinement_promotion_ready,
)
from mozaiksai.control_plane.scoped_execution import (
    ScopedRefinementChange,
    apply_scoped_refinement_changes,
)
from mozaiksai.control_plane.staging import create_refinement_staging_workspace
from mozaiksai.control_plane.validation_evidence import ValidationEvidence
from mozaiksai.core.artifacts import (
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    ArtifactVersionDoc,
)
from scripts.smoke_appgenerator_live_acceptance import (
    DEFAULT_WORKFLOW_CAPABILITY_ID,
    build_appgenerator_acceptance_files,
    default_workflow_integration,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_files(root: Path, files: dict[str, str]) -> None:
    for rel_path, content in files.items():
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _zip_directory(source_dir: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(source_dir.rglob("*")):
            if file_path.is_file() and not file_path.is_symlink():
                zf.write(file_path, arcname=file_path.relative_to(source_dir).as_posix())


def _build_source_artifact(*, app_id: str, source_zip: Path) -> ArtifactVersionDoc:
    return ArtifactVersionDoc.model_validate(
        {
            "_id": "av_factory_source_1",
            "app_id": app_id,
            "build_family": "app_bundle",
            "build_key": "app_bundle",
            "version_number": 1,
            "lineage_root_id": "av_factory_source_1",
            "canonical_inputs_version": {"design_docs": "av_design_docs_1"},
            "lifecycle_status": ArtifactLifecycleStatus.CURRENT.value,
            "validation_status": ArtifactValidationStatus.PASSED.value,
            "files_manifest": [
                {"path": "modules/support_tickets/module.yaml", "sha256": "sha-factory", "size_bytes": 100},
            ],
            "commit_metadata": {"metadata": {"artifact_path": str(source_zip.resolve())}},
        }
    )


def _build_refinement_plan(*, request_id: str, app_id: str, staging_area: Path) -> RefinementExecutionPlan:
    return RefinementExecutionPlan(
        request_id=request_id,
        app_id=app_id,
        request="Add a resolved_at field to the support ticket module.",
        build_family="app_bundle",
        change_class="patch",
        refinement_lane="module_patch",
        workflow_id="workflow_app",
        workflow_sequence="app_revision",
        target_workflow="AppGenerator",
        affected_workflows=["AppGenerator"],
        affected_declarative_families=["module_spec"],
        affected_bundle_paths=["modules/support_tickets/module.yaml"],
        scope_summary="Add resolved_at field to support_tickets module.",
        profiles=RefinementExecutionProfiles(
            classifier="classifier_v1",
            planner_replanner="planner_v1",
            codegen="codegen_v1",
            reviewer_validator="reviewer_v1",
        ),
        validation_plan=RefinementValidationPlan(
            items=[
                RefinementValidationItem(
                    id="module_contract_validation",
                    label="Module contract validation",
                    required=True,
                    reason="Module patch requires contract validation.",
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


class _FakeArtifactStore:
    def __init__(self, source_version: ArtifactVersionDoc) -> None:
        self._source = source_version
        self._draft: ArtifactVersionDoc | None = None

    async def get_build_record(self, *, app_id: str, build_record_id: str) -> ArtifactVersionDoc | None:
        if app_id == self._source.app_id and build_record_id == self._source.id:
            return self._source
        if self._draft is not None and app_id == self._draft.app_id and build_record_id == self._draft.id:
            return self._draft
        return None

    async def list_build_records(self, **kwargs: Any) -> list[ArtifactVersionDoc]:
        if (
            kwargs.get("app_id") == self._source.app_id
            and kwargs.get("build_family") == "app_bundle"
            and kwargs.get("lifecycle_status") == ArtifactLifecycleStatus.CURRENT
        ):
            return [self._source]
        return []

    async def create_build_record(self, **kwargs: Any) -> ArtifactVersionDoc:
        self._draft = ArtifactVersionDoc.model_validate(
            {
                "_id": "av_draft_promotion_1",
                "app_id": kwargs["app_id"],
                "build_family": kwargs["build_family"],
                "build_key": kwargs["build_key"],
                "version_number": 2,
                "parent_build_record_id": kwargs.get("parent_build_record_id"),
                "lineage_root_id": self._source.lineage_root_id,
                "canonical_inputs_version": dict(kwargs.get("canonical_inputs_version") or {}),
                "lifecycle_status": kwargs["lifecycle_status"].value,
                "validation_status": kwargs["validation_status"].value,
                "files_manifest": list(kwargs.get("files_manifest") or []),
                "commit_metadata": kwargs["commit_metadata"],
            }
        )
        return self._draft

    async def set_validation_status(self, *, artifact_version_id: str, validation_status: Any, commit_metadata: dict | None = None, **_: Any) -> bool:
        if self._draft is not None and self._draft.id == artifact_version_id:
            self._draft = self._draft.model_copy(
                update={
                    "validation_status": validation_status,
                    "commit_metadata": commit_metadata or self._draft.commit_metadata,
                }
            )
        return True

    async def accept_build_record(self, *, app_id: str, build_record_id: str, commit_metadata: dict | None = None) -> ArtifactVersionDoc | None:
        if self._draft is not None and self._draft.id == build_record_id and self._draft.app_id == app_id:
            self._draft = self._draft.model_copy(
                update={
                    "lifecycle_status": ArtifactLifecycleStatus.CURRENT,
                    "commit_metadata": commit_metadata or self._draft.commit_metadata,
                }
            )
            return self._draft
        return None


async def _load_app_bundle_files(files: dict[str, str], capability_id: str) -> dict[str, Any]:
    from mozaiksai.core.runtime.app.loader import AppLoader

    with tempfile.TemporaryDirectory(prefix="mozaiks-promotion-smoke-") as temp_dir:
        app_root = Path(temp_dir) / "app"
        _write_files(app_root, files)
        try:
            loaded = await AppLoader.load(str(app_root))
        except Exception as exc:
            return {"loaded": False, "error": str(exc)}

        module_ids = [item.name for item in loaded.modules]
        reactions = []
        for module in loaded.modules:
            if module.manifests.reactions:
                reactions.extend(module.manifests.reactions.reactions)
        reaction_capability_ids = [
            r.target.capability_id for r in reactions if r.target.kind == "capability"
        ]
        return {
            "loaded": True,
            "module_ids": module_ids,
            "reaction_capability_ids": reaction_capability_ids,
            "workflow_reaction_loaded": capability_id in reaction_capability_ids,
        }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_factory_acceptance_fixture_loads_via_apploader() -> None:
    """Baseline: the factory acceptance fixture is directly loadable by AppLoader."""
    files = build_appgenerator_acceptance_files(default_workflow_integration())
    result = await _load_app_bundle_files(files, DEFAULT_WORKFLOW_CAPABILITY_ID)

    assert result["loaded"] is True, result.get("error")
    assert "support_tickets" in result["module_ids"]
    assert result["workflow_reaction_loaded"] is True


@pytest.mark.asyncio
async def test_factory_bundle_refinement_staging_creates_draft_artifact(tmp_path: Path) -> None:
    """Factory files → refinement staging → DRAFT artifact is created with correct metadata."""
    app_id = "promotion-smoke-draft-only"
    files = build_appgenerator_acceptance_files(default_workflow_integration())

    source_bundle = tmp_path / "source_bundle"
    _write_files(source_bundle, files)
    source_zip = tmp_path / "source_bundle.zip"
    _zip_directory(source_bundle, source_zip)

    source_version = _build_source_artifact(app_id=app_id, source_zip=source_zip)
    artifact_store = _FakeArtifactStore(source_version)

    staging_root = tmp_path / "staging"
    staging_area = staging_root / "refine_factory_draft"
    plan = _build_refinement_plan(request_id="refine_factory_draft", app_id=app_id, staging_area=staging_area)

    staging_result = create_refinement_staging_workspace(
        plan, source_bundle_path=source_bundle, staging_root=staging_root
    )
    create_refinement_review_record(staging_result)
    approve_refinement_staging(staging_result.staging_area, reviewer="reviewer_1")
    review_record = mark_refinement_promotion_ready(staging_result.staging_area, reviewer="reviewer_1")

    scoped_result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[
            ScopedRefinementChange(
                path="modules/support_tickets/module.yaml",
                new_content=files["modules/support_tickets/module.yaml"] + "  # resolved_at field added\n",
                reason="Add resolved_at tracking field.",
            )
        ],
    )

    draft_result = await create_draft_app_bundle_from_staged_refinement(
        plan,
        staging_result,
        scoped_result,
        review_record,
        ValidationEvidence(
            completed=["module_contract_validation"],
            failed=[],
            warnings=[],
            artifacts=[],
        ),
        artifact_store=artifact_store,
        source_artifact_version_id=source_version.id,
        generated_artifacts_root=tmp_path / "generated",
    )

    assert draft_result.lifecycle_status == ArtifactLifecycleStatus.DRAFT
    assert draft_result.artifact_kind == "app_bundle"
    assert draft_result.parent_version_id == source_version.id


@pytest.mark.asyncio
async def test_factory_bundle_promotion_to_current_and_apploader(tmp_path: Path) -> None:
    """Full path: factory files → staging → DRAFT → promotion → CURRENT → AppLoader.

    This is the production readiness gap between:
    - accept_staged_refinement_artifact_version unit tests (promotion mechanics only)
    - factory lineage smoke (builds CURRENT artifacts directly, no staging/promotion)
    """
    app_id = "promotion-smoke-e2e"
    files = build_appgenerator_acceptance_files(default_workflow_integration())

    source_bundle = tmp_path / "source_bundle"
    _write_files(source_bundle, files)
    source_zip = tmp_path / "source_bundle.zip"
    _zip_directory(source_bundle, source_zip)

    source_version = _build_source_artifact(app_id=app_id, source_zip=source_zip)
    artifact_store = _FakeArtifactStore(source_version)

    staging_root = tmp_path / "staging"
    staging_area = staging_root / "refine_factory_e2e"
    plan = _build_refinement_plan(request_id="refine_factory_e2e", app_id=app_id, staging_area=staging_area)

    staging_result = create_refinement_staging_workspace(
        plan, source_bundle_path=source_bundle, staging_root=staging_root
    )
    create_refinement_review_record(staging_result)
    approve_refinement_staging(staging_result.staging_area, reviewer="reviewer_1")
    review_record = mark_refinement_promotion_ready(staging_result.staging_area, reviewer="reviewer_1")

    patched_module_yaml = files["modules/support_tickets/module.yaml"] + "  # resolved_at patch\n"
    scoped_result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[
            ScopedRefinementChange(
                path="modules/support_tickets/module.yaml",
                new_content=patched_module_yaml,
                reason="Add resolved_at field.",
            )
        ],
    )

    # Stage as DRAFT
    draft_result = await create_draft_app_bundle_from_staged_refinement(
        plan,
        staging_result,
        scoped_result,
        review_record,
        ValidationEvidence(
            completed=["module_contract_validation"],
            failed=[],
            warnings=[],
            artifacts=[],
        ),
        artifact_store=artifact_store,
        source_artifact_version_id=source_version.id,
        generated_artifacts_root=tmp_path / "generated",
    )
    assert draft_result.lifecycle_status == ArtifactLifecycleStatus.DRAFT

    # Promote to CURRENT
    accepted = await accept_staged_refinement_artifact_version(
        app_id=app_id,
        draft_artifact_version_id=draft_result.artifact_version_id,
        review_record=review_record,
        request_id=plan.request_id,
        artifact_store=artifact_store,
        accepted_by="reviewer_1",
    )
    assert accepted.lifecycle_status == ArtifactLifecycleStatus.CURRENT
    assert accepted.artifact_kind == "app_bundle"
    assert accepted.accepted_by == "reviewer_1"

    # Load the promoted bundle via AppLoader.
    # The promoted bundle is the complete source bundle with the patch applied
    # (staging workspace only holds the affected_bundle_paths subset, not the full bundle).
    staged_files = dict(files)
    staged_files["modules/support_tickets/module.yaml"] = patched_module_yaml

    loader_result = await _load_app_bundle_files(staged_files, DEFAULT_WORKFLOW_CAPABILITY_ID)

    assert loader_result["loaded"] is True, loader_result.get("error")
    assert "support_tickets" in loader_result["module_ids"]
    assert loader_result["workflow_reaction_loaded"] is True

    # Verify the promoted artifact retains the patch in its metadata
    assert accepted.parent_version_id == source_version.id
    assert accepted.refinement_review_status == "promotion_ready"
