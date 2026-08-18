import json
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mozaiksai.control_plane import dry_run
from mozaiksai.control_plane.artifact_promotion import (
    accept_staged_refinement_artifact_version,
    create_draft_app_bundle_from_staged_refinement,
)
from mozaiksai.control_plane.promotion import promote_refinement_staging
from mozaiksai.control_plane.review import (
    approve_refinement_staging,
    create_refinement_review_record,
    mark_refinement_promotion_ready,
)
from mozaiksai.control_plane.staged_coding_worker import (
    StagedCodingWorkerChange,
    StagedCodingWorkerResult,
    run_deterministic_staged_coding_worker,
)
from mozaiksai.control_plane.staging import create_refinement_staging_workspace
from mozaiksai.control_plane.validation_evidence import ValidationEvidence
from mozaiksai.control_plane.validation_runner import run_refinement_validations
from mozaiksai.core.artifacts import (
    ArtifactCommitMetadata,
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    ArtifactVersionDoc,
    RefinementSessionDoc,
    RefinementSessionStatus,
)
from mozaiksai.core.auth import reset_auth_adapter


def _write_source_bundle(root: Path) -> None:
    (root / "app.json").parent.mkdir(parents=True, exist_ok=True)
    (root / "app.json").write_text(json.dumps({"name": "demo-app", "version": "1.0.0"}, indent=2) + "\n", encoding="utf-8")

    (root / "ui" / "pages" / "custom").mkdir(parents=True, exist_ok=True)
    (root / "modules" / "projects" / "backend").mkdir(parents=True, exist_ok=True)

    (root / "ui" / "pages" / "dashboard.yaml").write_text(
        (
            "schema_version: mozaiks.app_page.v1\n"
            "name: dashboard\n"
            "route: /dashboard\n"
            "title: Dashboard\n"
            "page_type: landing\n"
            "layout: full-width\n"
            "sections:\n"
            "  - id: dashboard-header\n"
            "    primitive: PageHeader\n"
            "    config:\n"
            "      title: Dashboard\n"
        ),
        encoding="utf-8",
    )
    (root / "ui" / "route_manifest.json").write_text(
        json.dumps(
            {
                "pages": [
                    {
                        "id": "reports",
                        "label": "Reports Overview",
                        "path": "/reports",
                        "component": "ReportsOverviewPage",
                        "requiresAuth": True,
                        "purpose": "Reports overview surface.",
                    }
                ]
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "ui" / "index.js").write_text(
        "import ReportsOverviewPage from './pages/custom/ReportsOverviewPage.jsx';\n\n"
        "export function register(registerComponent) {\n"
        "  registerComponent('ReportsOverviewPage', ReportsOverviewPage);\n"
        "}\n",
        encoding="utf-8",
    )
    (root / "ui" / "pages" / "custom" / "ReportsOverviewPage.jsx").write_text(
        "export default function ReportsOverviewPage() {\n"
        "  return <main><h1>Reports Overview</h1></main>;\n"
        "}\n",
        encoding="utf-8",
    )
    (root / "modules" / "projects" / "module.yaml").write_text(
        "schema_version: mozaiks.module.v1\n"
        "module:\n"
        "  id: projects\n"
        "  display_name: Projects\n"
        "  version: 1.0.0\n"
        "  description: Projects module.\n"
        "  handler: backend.handler:ProjectsModule\n"
        "permissions: []\n"
        "actions: []\n"
        "capabilities: []\n",
        encoding="utf-8",
    )
    (root / "modules" / "projects" / "backend" / "service.py").write_text(
        "TITLE = 'Original service'\n",
        encoding="utf-8",
    )


def _zip_source_bundle(source_bundle: Path, zip_path: Path) -> list[dict[str, object]]:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, object]] = []
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_path in sorted(source_bundle.rglob("*")):
            if not file_path.is_file() or file_path.is_symlink():
                continue
            relative_path = file_path.relative_to(source_bundle).as_posix()
            archive.write(file_path, arcname=relative_path)
            manifest.append({"path": relative_path, "size_bytes": file_path.stat().st_size})
    return manifest


def _build_source_version(*, app_id: str, source_zip: Path, files_manifest: list[dict[str, object]]) -> ArtifactVersionDoc:
    return ArtifactVersionDoc.model_validate(
        {
            "_id": "av_source_smoke_1",
            "app_id": app_id,
            "build_family": "app_bundle",
            "build_key": "app_bundle",
            "version_number": 1,
            "lineage_root_id": "av_source_smoke_1",
            "canonical_inputs_version": {"concept": "av_concept_smoke_1"},
            "lifecycle_status": ArtifactLifecycleStatus.CURRENT.value,
            "validation_status": ArtifactValidationStatus.PASSED.value,
            "files_manifest": list(files_manifest),
            "commit_metadata": ArtifactCommitMetadata(
                message="Source app bundle",
                source_workflow="AppGenerator",
                source_chat_id="chat_smoke_1",
                metadata={"artifact_path": str(source_zip.resolve())},
            ).model_dump(mode="python"),
        }
    )


class _E2EArtifactStore:
    def __init__(self, source_version: ArtifactVersionDoc) -> None:
        self.versions: dict[str, ArtifactVersionDoc] = {source_version.id: source_version}
        self.sessions: list[RefinementSessionDoc] = []
        self.create_calls: list[dict[str, object]] = []
        self.validation_calls: list[dict[str, object]] = []
        self.accept_calls: list[dict[str, object]] = []
        self.updated_sessions: list[dict[str, object]] = []

    async def get_build_record(self, *, app_id: str, build_record_id: str):
        version = self.versions.get(build_record_id)
        if version is None:
            return None
        return version

    async def create_build_record(self, **kwargs):  # noqa: ANN003
        self.create_calls.append(dict(kwargs))
        version = ArtifactVersionDoc.model_validate(
            {
                "_id": "av_draft_smoke_1",
                "app_id": kwargs["app_id"],
                "build_family": kwargs["build_family"],
                "build_key": kwargs["build_key"],
                "version_number": 2,
                "parent_build_record_id": kwargs.get("parent_build_record_id"),
                "lineage_root_id": "av_source_smoke_1",
                "source_workflow": kwargs.get("source_workflow"),
                "source_chat_id": kwargs.get("source_chat_id"),
                "canonical_inputs_version": dict(kwargs.get("canonical_inputs_version") or {}),
                "lifecycle_status": kwargs["lifecycle_status"].value,
                "validation_status": kwargs["validation_status"].value,
                "files_manifest": list(kwargs.get("files_manifest") or []),
                "commit_metadata": kwargs["commit_metadata"],
            }
        )
        self.versions[version.id] = version
        return version

    async def set_validation_status(self, **kwargs):  # noqa: ANN003
        self.validation_calls.append(dict(kwargs))
        version = self.versions.get(kwargs["artifact_version_id"])
        if version is None:
            return False
        commit_metadata = kwargs.get("commit_metadata") or {}
        validation_status = kwargs["validation_status"]
        if not isinstance(validation_status, ArtifactValidationStatus):
            validation_status = ArtifactValidationStatus(validation_status)
        updated = version.model_copy(
            update={
                "validation_status": validation_status,
                "commit_metadata": ArtifactCommitMetadata.model_validate(commit_metadata),
            }
        )
        self.versions[version.id] = updated
        return True

    async def accept_build_record(self, **kwargs):  # noqa: ANN003
        self.accept_calls.append(dict(kwargs))
        version = self.versions.get(kwargs["build_record_id"])
        if version is None:
            return None
        for existing_id, existing in list(self.versions.items()):
            if (
                existing_id != version.id
                and existing.app_id == version.app_id
                and existing.build_family == version.build_family
                and existing.build_key == version.build_key
                and existing.lifecycle_status == ArtifactLifecycleStatus.CURRENT
            ):
                self.versions[existing_id] = existing.model_copy(
                    update={"lifecycle_status": ArtifactLifecycleStatus.SUPERSEDED}
                )
        commit_metadata = kwargs.get("commit_metadata") or version.commit_metadata
        if not isinstance(commit_metadata, ArtifactCommitMetadata):
            commit_metadata = ArtifactCommitMetadata.model_validate(commit_metadata)
        updated = version.model_copy(
            update={
                "lifecycle_status": ArtifactLifecycleStatus.CURRENT,
                "commit_metadata": commit_metadata,
            }
        )
        self.versions[updated.id] = updated
        return updated

    async def list_refinement_sessions(self, *, app_id: str, result_build_record_id: str, limit: int = 20):
        matches = [
            session
            for session in self.sessions
            if session.result_build_record_id == result_build_record_id
        ]
        return matches[:limit]

    async def update_refinement_session(self, *, app_id: str, session_id: str, **kwargs):  # noqa: ANN003
        self.updated_sessions.append({"app_id": app_id, "session_id": session_id, **kwargs})
        for index, session in enumerate(self.sessions):
            if session.id != session_id:
                continue
            updates = dict(kwargs)
            self.sessions[index] = session.model_copy(update=updates)
            break
        return True

    async def get_change_request(self, **kwargs):  # noqa: ANN003
        return None

    async def list_change_requests(self, **kwargs):  # noqa: ANN003
        return []


class _FakeContentStore:
    backend_name = "gridfs"

    def __init__(self) -> None:
        self.put_calls: list[dict[str, object]] = []

    async def put_bundle(self, data: bytes, *, app_id: str, artifact_version_id: str) -> str:
        self.put_calls.append(
            {
                "app_id": app_id,
                "artifact_version_id": artifact_version_id,
                "size_bytes": len(data),
            }
        )
        return "gridfs_ref_smoke_1"


def _studio_app(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    sys.modules.pop("factory_app", None)
    from mozaiksai.hosts import studio as studio_app

    return studio_app


def _bundle_entries(zip_path: Path) -> set[str]:
    with zipfile.ZipFile(zip_path, "r") as archive:
        return set(archive.namelist())


@pytest.mark.asyncio
async def test_deterministic_staged_patch_smoke_restores_dashboard_title(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(dry_run, "LLMChangeClassifier", lambda *args, **kwargs: pytest.fail("LLM should not be used"))

    source_bundle = tmp_path / "source_app"
    _write_source_bundle(source_bundle)
    source_zip = tmp_path / "source_app.zip"
    source_manifest = _zip_source_bundle(source_bundle, source_zip)
    source_before = (source_bundle / "ui" / "pages" / "dashboard.yaml").read_text(encoding="utf-8")
    source_service_before = (source_bundle / "modules" / "projects" / "backend" / "service.py").read_text(encoding="utf-8")

    app_id = "sample_app"
    request_id = "refine_smoke_001"
    plan = dry_run.build_refinement_execution_plan_from_route(
        request="Change the dashboard page title to prioritize reports.",
        build_family="app_bundle",
        change_class="patch",
        workflow_id="AppGenerator",
        workflow_sequence="app_revision",
        affected_workflows=["AppGenerator"],
        affected_declarative_families=[],
        affected_bundle_paths=[
            "ui/pages/dashboard.yaml",
            "ui/route_manifest.json",
            "ui/index.js",
            "ui/pages/custom/ReportsOverviewPage.jsx",
        ],
        scope_summary="Narrow UI patch for the reports overview title.",
        app_id=app_id,
        request_id=request_id,
        execution_mode="staged",
        staging_base_path=tmp_path / ".refinement_staging",
    )
    assert plan.change_class == "patch"
    assert plan.refinement_lane == "ui_patch"

    staging_result = create_refinement_staging_workspace(
        plan,
        source_bundle_path=source_bundle,
        staging_root=tmp_path / ".refinement_staging",
    )
    create_refinement_review_record(staging_result)
    approve_refinement_staging(staging_result.staging_area, reviewer="reviewer_1")
    review_record = mark_refinement_promotion_ready(staging_result.staging_area, reviewer="reviewer_1")
    assert review_record.status == "promotion_ready"
    assert review_record.promotion_allowed is True

    worker_result = StagedCodingWorkerResult(
        request_id=request_id,
        source="deterministic",
        changes=[
                StagedCodingWorkerChange(
                    path="ui/pages/dashboard.yaml",
                    new_content=(
                        "schema_version: mozaiks.app_page.v1\n"
                        "name: dashboard\n"
                        "route: /dashboard\n"
                        "title: Reports Overview\n"
                        "page_type: landing\n"
                        "layout: full-width\n"
                        "sections:\n"
                        "  - id: dashboard-header\n"
                        "    primitive: PageHeader\n"
                        "    config:\n"
                        "      title: Reports Overview\n"
                    ),
                    reason="Prioritize reports on the overview surface.",
                )
        ],
    )
    scoped_result = run_deterministic_staged_coding_worker(plan, staging_result, worker_result)
    assert scoped_result.source_mutated is False

    validation_result = run_refinement_validations(plan, staging_result, scoped_result)
    evidence = validation_result.evidence
    assert isinstance(evidence, ValidationEvidence)
    assert evidence.source == "refinement_validation_runner"
    assert "route_component_validation" in evidence.completed
    assert "ui_theme_primitive_validation" in evidence.completed
    assert not evidence.failed

    source_version = _build_source_version(app_id=app_id, source_zip=source_zip, files_manifest=source_manifest)
    artifact_store = _E2EArtifactStore(source_version)
    content_store = _FakeContentStore()
    promotion_result = promote_refinement_staging(
        plan,
        staging_result,
        review_record,
        scoped_result,
        source_bundle_path=source_bundle,
        confirm=True,
        dry_run=True,
        validation_evidence=evidence,
    )
    assert promotion_result.policy_decisions
    assert promotion_result.policy_decisions[0].allowed is True
    assert promotion_result.promoted_files[0].status == "promoted"
    assert set(promotion_result.policy_decisions[0].required_validation) >= {
        "route_component_validation",
        "ui_theme_primitive_validation",
    }

    draft_result = await create_draft_app_bundle_from_staged_refinement(
        plan,
        staging_result,
        scoped_result,
        review_record,
        evidence,
        artifact_store=artifact_store,
        content_store=content_store,
        source_artifact_version_id=source_version.id,
        generated_artifacts_root=tmp_path / "generated",
        promotion_result=promotion_result,
    )
    assert draft_result.artifact_kind == "app_bundle"
    assert draft_result.lifecycle_status == ArtifactLifecycleStatus.DRAFT
    assert draft_result.parent_version_id == source_version.id
    assert draft_result.metadata["refinement"]["request_id"] == request_id
    assert draft_result.metadata["refinement"]["review"]["status"] == "promotion_ready"
    assert draft_result.metadata["refinement"]["validation_evidence"]["source"] == "refinement_validation_runner"

    bundle_entries = _bundle_entries(Path(draft_result.artifact_path))
    assert bundle_entries == {
        "ui/index.js",
        "ui/pages/custom/ReportsOverviewPage.jsx",
        "ui/pages/dashboard.yaml",
        "ui/route_manifest.json",
    }
    assert "refinement_plan.json" not in bundle_entries
    assert "affected_paths.json" not in bundle_entries
    assert "refinement_review.json" not in bundle_entries
    assert "execution_result.json" not in bundle_entries
    assert not any(entry.startswith("backups/") for entry in bundle_entries)

    studio_app = _studio_app(monkeypatch)
    runtime_root = tmp_path / "runtime_app"
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(studio_app, "get_artifact_store", lambda: artifact_store)
    monkeypatch.setattr(studio_app, "resolve_app_root", lambda: runtime_root)
    monkeypatch.setattr(
        studio_app,
        "prepare_routed_workflow_launch",
        lambda **kwargs: pytest.fail("workflow launch should not run"),
    )
    monkeypatch.setattr(
        studio_app,
        "launch_prepared_workflow",
        lambda *args, **kwargs: pytest.fail("workflow launch should not run"),
    )

    artifact_store.sessions.append(
        RefinementSessionDoc.model_validate(
            {
                "_id": "rs_smoke_1",
                "app_id": app_id,
                "artifact_version_id": draft_result.artifact_version_id,
                "result_artifact_version_id": draft_result.artifact_version_id,
                "change_request_id": "cr_smoke_1",
                "provider": "control_plane_coding",
                "status": RefinementSessionStatus.VALIDATED.value,
                "metadata": {},
            }
        )
    )

    draft_review_response = TestClient(studio_app.app).get(
        f"/api/studio/build/artifacts/{draft_result.artifact_version_id}/review"
    )
    assert draft_review_response.status_code == 200
    assert draft_review_response.json()["review"]["write_back_mode"] == "generated_artifact"
    assert draft_review_response.json()["review"]["write_back_target"] is None

    draft_promote_response = TestClient(studio_app.app).post(
        f"/api/studio/build/artifacts/{draft_result.artifact_version_id}/promote"
    )
    assert draft_promote_response.status_code == 409
    assert "current artifact versions" in draft_promote_response.json()["detail"]

    accepted_result = await accept_staged_refinement_artifact_version(
        app_id=app_id,
        draft_artifact_version_id=draft_result.artifact_version_id,
        review_record=review_record,
        request_id=request_id,
        artifact_store=artifact_store,
        accepted_by="reviewer_1",
    )
    assert accepted_result.lifecycle_status == ArtifactLifecycleStatus.CURRENT
    assert accepted_result.refinement_review_status == "promotion_ready"
    assert accepted_result.metadata["refinement"]["review"]["status"] == "promotion_ready"
    assert accepted_result.metadata["acceptance"]["accepted_by"] == "reviewer_1"

    client = TestClient(studio_app.app)
    promote_response = client.post(f"/api/studio/build/artifacts/{accepted_result.artifact_version_id}/promote")
    assert promote_response.status_code == 200
    payload = promote_response.json()
    assert payload["promoted"] is True
    assert payload["build_family"] == "app_bundle"
    assert payload["restored_files"] == [
        "ui/index.js",
        "ui/pages/custom/ReportsOverviewPage.jsx",
        "ui/pages/dashboard.yaml",
        "ui/route_manifest.json",
    ]
    assert "refinement_plan.json" not in payload["restored_files"]
    assert "affected_paths.json" not in payload["restored_files"]
    assert "refinement_review.json" not in payload["restored_files"]
    assert "execution_result.json" not in payload["restored_files"]
    assert not (runtime_root / "refinement_plan.json").exists()
    assert not (runtime_root / "affected_paths.json").exists()
    assert not (runtime_root / "refinement_review.json").exists()
    assert not (runtime_root / "execution_result.json").exists()
    assert not (runtime_root / "backups").exists()
    assert "title: Reports Overview\n" in (
        runtime_root / "ui" / "pages" / "dashboard.yaml"
    ).read_text(encoding="utf-8")
    assert (runtime_root / "ui" / "index.js").read_text(encoding="utf-8") == (source_bundle / "ui" / "index.js").read_text(encoding="utf-8")
    assert (runtime_root / "ui" / "route_manifest.json").read_text(encoding="utf-8") == (
        source_bundle / "ui" / "route_manifest.json"
    ).read_text(encoding="utf-8")
    assert (runtime_root / "ui" / "pages" / "custom" / "ReportsOverviewPage.jsx").read_text(encoding="utf-8") == (
        source_bundle / "ui" / "pages" / "custom" / "ReportsOverviewPage.jsx"
    ).read_text(encoding="utf-8")
    assert (source_bundle / "ui" / "pages" / "dashboard.yaml").read_text(encoding="utf-8") == source_before
    assert (source_bundle / "modules" / "projects" / "backend" / "service.py").read_text(encoding="utf-8") == source_service_before
    assert artifact_store.updated_sessions[-1]["status"] == RefinementSessionStatus.PROMOTED
    assert artifact_store.versions[accepted_result.artifact_version_id].lifecycle_status == ArtifactLifecycleStatus.CURRENT
    assert artifact_store.versions[source_version.id].lifecycle_status == ArtifactLifecycleStatus.SUPERSEDED
    assert content_store.put_calls

