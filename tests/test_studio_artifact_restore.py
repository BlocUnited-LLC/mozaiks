from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import stat
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from mozaiksai.core.artifacts import (
    ArtifactCommitMetadata,
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    ArtifactVersionDoc,
    RefinementSessionDoc,
    RefinementSessionStatus,
)
from mozaiksai.core.artifacts.models import canonical_bundle_archive_path
from mozaiksai.core.auth import reset_auth_adapter
from mozaiksai.hosts import shell_config


def _write_bundle_zip(zip_path: Path, entries: dict[str, str], *, symlink_entry: tuple[str, str] | None = None) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative_path, content in entries.items():
            archive.writestr(relative_path, content)
        if symlink_entry is not None:
            symlink_path, symlink_target = symlink_entry
            info = zipfile.ZipInfo(symlink_path)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, symlink_target)


def _version(
    *,
    artifact_version_id: str,
    zip_path: Path,
    build_family: str = "app_bundle",
    lifecycle_status: ArtifactLifecycleStatus = ArtifactLifecycleStatus.CURRENT,
    validation_status: ArtifactValidationStatus = ArtifactValidationStatus.PASSED,
    refinement_request_id: str | None = None,
    files_manifest: list[dict[str, object]] | None = None,
    commit_metadata_extra: dict[str, object] | None = None,
) -> ArtifactVersionDoc:
    metadata: dict[str, object] = {
        "artifact_path": str(zip_path), "build_registry_id": "appreg_1",
        "target_app_id": "app_1", "build_id": "build_1", "phase": "refinement",
        "bundle_name": "GeneratedApp",
    }
    metadata.update(commit_metadata_extra or {})
    if refinement_request_id is not None:
        metadata["refinement"] = {
            "request_id": refinement_request_id,
            "review": {
                "status": "promotion_ready",
                "promotion_allowed": True,
            },
        }
    return ArtifactVersionDoc.model_validate(
        {
            "_id": artifact_version_id,
            "app_id": "app_1",
            "build_family": build_family,
            "build_key": "app_bundle",
            "version_number": 2,
            "parent_version_id": "av_parent_1" if artifact_version_id != "av_parent_1" else None,
            "lineage_root_id": "av_parent_1",
            "source_workflow": "AppGenerator",
            "source_chat_id": "chat_1",
            "canonical_inputs_version": {},
            "lifecycle_status": lifecycle_status.value,
            "validation_status": validation_status.value,
            "app_validation_status": "passed",
            "files_manifest": files_manifest if files_manifest is not None else [{
                "path": canonical_bundle_archive_path("GeneratedApp"),
                "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
                "size_bytes": zip_path.stat().st_size,
                "content_type": "application/zip",
            }],
            "commit_metadata": ArtifactCommitMetadata(
                message="Refinement artifact",
                source_workflow="AppGenerator",
                source_chat_id="chat_1",
                metadata=metadata,
            ).model_dump(mode="python"),
        }
    )


def _session(
    *,
    artifact_version_id: str,
    status: RefinementSessionStatus,
) -> RefinementSessionDoc:
    return RefinementSessionDoc.model_validate(
        {
            "_id": "rs_1",
            "app_id": "app_1",
            "artifact_version_id": artifact_version_id,
            "result_artifact_version_id": artifact_version_id,
            "change_request_id": "cr_1",
            "provider": "control_plane_coding",
            "status": status.value,
            "metadata": {},
        }
    )


class _PromoteStore:
    def __init__(self, version: ArtifactVersionDoc, *, sessions: list[RefinementSessionDoc] | None = None) -> None:
        self.version = version
        self.sessions = list(sessions or [])
        self.updated_sessions: list[dict[str, object]] = []

    async def get_build_record(self, *, app_id: str, build_record_id: str):
        if build_record_id == self.version.id:
            return self.version
        return None

    async def list_refinement_sessions(self, *, app_id: str, result_build_record_id: str, limit: int = 20):
        if result_build_record_id == self.version.id:
            return list(self.sessions[:limit])
        return []

    async def update_refinement_session(self, *, app_id: str, session_id: str, **kwargs):
        self.updated_sessions.append({"app_id": app_id, "session_id": session_id, **kwargs})
        for index, session in enumerate(self.sessions):
            if session.id != session_id:
                continue
            updates = dict(kwargs)
            self.sessions[index] = session.model_copy(update=updates)
            break
        return True

    async def get_change_request(self, **kwargs):
        return None

    async def list_change_requests(self, **kwargs):
        return []


class _AppRegistryServiceDouble:
    def __init__(
        self,
        *,
        app_id: str = "app_1",
        lifecycle_state: str = "review",
        build_registry_id: str = "appreg_1",
        artifact_version_id: str = "av_registry_1",
    ) -> None:
        self.app = {
            "build_registry_id": build_registry_id,
            "app_id": app_id,
            "chat_app_id": "factory",
            "current_build_run": {"build_id": "build_1", "artifact_version_id": artifact_version_id},
            "lifecycle_state": lifecycle_state,
            "bundle_path": "generated/apps/app_1/build_1/app",
        }
        self.promote_calls: list[dict[str, str | None]] = []

    async def get_app_record(self, *, owner_user_id: str, app_id: str | None = None, build_registry_id: str | None = None):
        assert owner_user_id == "demo-user"
        if build_registry_id == self.app["build_registry_id"] or app_id == self.app["app_id"]:
            return {"app": dict(self.app)}
        return {"app": None}

    async def promote_build(
        self, *, build_registry_id: str, promoted_by: str, bundle_path: str,
        expected_build_id: str, expected_artifact_version_id: str,
    ):
        assert expected_build_id == self.app["current_build_run"]["build_id"]
        assert expected_artifact_version_id == self.app["current_build_run"]["artifact_version_id"]
        self.promote_calls.append({"build_registry_id": build_registry_id, "promoted_by": promoted_by})
        self.app = {**self.app, "lifecycle_state": "active", "bundle_path": bundle_path}
        return {"success": True, "app": dict(self.app)}


def _studio_app(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    from mozaiksai.hosts import studio as studio_app

    return studio_app


def _promote_client(monkeypatch, runtime_root: Path, store: _PromoteStore):
    studio_app = _studio_app(monkeypatch)
    monkeypatch.setattr(studio_app, "get_artifact_store", lambda: store)
    monkeypatch.setattr(shell_config, "resolve_app_root", lambda: runtime_root)
    monkeypatch.setattr(
        studio_app, "_start_studio_app_intelligence_index_job",
        AsyncMock(return_value={"status": "queued"}),
    )
    monkeypatch.setenv("MOZAIKS_WORKSPACES_PATH", str(runtime_root.parent / "workspaces"))
    monkeypatch.setattr(studio_app, "_resolve_studio_scope", lambda *args, **kwargs: ("factory", "demo-user"))
    registry = _AppRegistryServiceDouble(artifact_version_id=store.version.id)
    monkeypatch.setattr(studio_app, "_get_app_registry_service", lambda: registry)
    return studio_app, TestClient(studio_app.app)


def test_restore_materializes_accepted_version_without_claiming_rollback(monkeypatch, tmp_path):
    bundle_zip = tmp_path / "accepted.zip"
    _write_bundle_zip(bundle_zip, {"app.json": '{"appId":"app_1"}'})
    version = _version(
        artifact_version_id="av_accepted", zip_path=bundle_zip,
        lifecycle_status=ArtifactLifecycleStatus.SUPERSEDED,
    )
    runtime_root = tmp_path / "factory"
    studio, client = _promote_client(monkeypatch, runtime_root, _PromoteStore(version))
    registry = studio._get_app_registry_service()
    before = dict(registry.app)
    response = client.post(
        "/api/studio/build/restore?build_registry_id=appreg_1",
        json={"artifact_version_id": version.id},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["restored"] is True
    assert body["active_version_changed"] is False
    assert "reverted" not in body
    assert (Path(body["target_path"]) / "app" / "app.json").exists()
    assert registry.app == before
    assert registry.promote_calls == []
    assert not runtime_root.exists()


def test_bundle_advertises_the_registered_review_surface(monkeypatch, tmp_path):
    archive = tmp_path / "bundle.zip"
    _write_bundle_zip(archive, {"app.json": '{"appId":"app_1"}'})
    version = _version(artifact_version_id="av_review", zip_path=archive)
    studio, client = _promote_client(monkeypatch, tmp_path / "factory", _PromoteStore(version))
    monkeypatch.setattr(studio, "_build_artifact_review_payload", AsyncMock(return_value={
        "review": {}, "refinement_session": None, "change_request": None,
    }))

    response = client.get(f"/api/studio/build/artifacts/{version.id}/bundle?build_registry_id=appreg_1")

    assert response.status_code == 200, response.text
    body = response.json()
    surface = body["workbench_ui"]
    index = Path(__file__).resolve().parents[1] / "factory_app/workflows" / surface["workflow_name"] / "ui/index.js"
    assert f"as {surface['component']}" in index.read_text(encoding="utf-8")
    assert body["workbench"]["artifact_version_id"] == version.id
    assert body["workbench"]["generated_files"] == {"app.json": '{"appId":"app_1"}'}


@pytest.mark.parametrize("status", [ArtifactLifecycleStatus.DRAFT, ArtifactLifecycleStatus.DELETED])
def test_restore_rejects_unaccepted_version(monkeypatch, tmp_path, status):
    bundle_zip = tmp_path / "unaccepted.zip"
    _write_bundle_zip(bundle_zip, {"app.json": '{}'})
    version = _version(artifact_version_id="av_draft", zip_path=bundle_zip, lifecycle_status=status)
    _, client = _promote_client(monkeypatch, tmp_path / "factory", _PromoteStore(version))
    response = client.post(
        "/api/studio/build/restore?build_registry_id=appreg_1",
        json={"artifact_version_id": version.id},
    )
    assert response.status_code == 409
    assert not (tmp_path / "workspaces").exists()


def _restore_or_promote(client, version, action):
    if action == "restore":
        return client.post(
            "/api/studio/build/restore?build_registry_id=appreg_1",
            json={"artifact_version_id": version.id},
        )
    return client.post(f"/api/studio/build/artifacts/{version.id}/promote?build_registry_id=appreg_1")


@pytest.mark.parametrize("action", ["restore", "promote"])
@pytest.mark.parametrize("fault", ["changed_archive", "missing_identity", "legacy_manifest", "wrong_archive_path"])
def test_archive_integrity_failure_leaves_workspace_and_registry_unchanged(monkeypatch, tmp_path, action, fault):
    archive = tmp_path / "bundle.zip"
    _write_bundle_zip(archive, {"app.json": '{"appId":"app_1"}', "ui/title.txt": "approved"})
    version = _version(artifact_version_id="av_verified", zip_path=archive)
    if fault == "changed_archive":
        _write_bundle_zip(archive, {"app.json": '{"appId":"app_1"}', "ui/title.txt": "unapproved"})
    elif fault == "missing_identity":
        version.commit_metadata.metadata.pop("bundle_name")
    elif fault == "legacy_manifest":
        version.files_manifest[0].content_type = "text/plain"
    else:
        version.files_manifest[0].path = "another/another.zip"
    store = _PromoteStore(version)
    studio, client = _promote_client(monkeypatch, tmp_path / "factory", store)
    target = tmp_path / "workspaces/app_1/av_verified"
    (target / "app").mkdir(parents=True)
    (target / "app/existing.txt").write_bytes(b"preserve me")
    registry = studio._get_app_registry_service()
    before = dict(registry.app)

    response = _restore_or_promote(client, version, action)

    assert response.status_code == 409
    assert "Revalidate and save a canonical app bundle" in response.json()["detail"]
    assert sorted(path.relative_to(target).as_posix() for path in target.rglob("*")) == ["app", "app/existing.txt"]
    assert (target / "app/existing.txt").read_bytes() == b"preserve me"
    assert registry.app == before
    assert registry.promote_calls == []
    assert store.updated_sessions == []


@pytest.mark.parametrize("action", ["restore", "promote"])
def test_restore_consumes_verified_bytes_even_if_archive_path_changes(monkeypatch, tmp_path, action):
    archive = tmp_path / "bundle.zip"
    _write_bundle_zip(archive, {"app.json": '{"appId":"app_1"}', "ui/title.txt": "approved"})
    version = _version(artifact_version_id="av_once", zip_path=archive)
    studio, client = _promote_client(monkeypatch, tmp_path / "factory", _PromoteStore(version))
    restore = studio._restore_bundle_to_target

    def replace_source_after_verification(**kwargs):
        _write_bundle_zip(archive, {"app.json": '{"appId":"app_1"}', "ui/title.txt": "unapproved"})
        return restore(**kwargs)

    monkeypatch.setattr(studio, "_restore_bundle_to_target", replace_source_after_verification)
    response = _restore_or_promote(client, version, action)
    assert response.status_code == 200, response.text
    assert (Path(response.json()["target_path"]) / "app/ui/title.txt").read_text() == "approved"


@pytest.mark.parametrize("action", ["restore", "promote"])
def test_restore_uses_canonical_content_backend_without_local_path(monkeypatch, tmp_path, action):
    from mozaiksai.core.artifacts import content_store

    archive = tmp_path / "bundle.zip"
    _write_bundle_zip(archive, {"app.json": '{"appId":"app_1"}'})
    version = _version(artifact_version_id="av_remote", zip_path=archive)
    version.commit_metadata.metadata.pop("artifact_path")
    version.commit_metadata.metadata.update(content_ref="owned-archive", content_backend="memory")
    backend = SimpleNamespace(backend_name="memory", get_bundle=AsyncMock(return_value=archive.read_bytes()))
    monkeypatch.setattr(content_store, "get_artifact_content_store", lambda: backend)
    _, client = _promote_client(monkeypatch, tmp_path / "factory", _PromoteStore(version))
    response = _restore_or_promote(client, version, action)
    assert response.status_code == 200, response.text
    assert (Path(response.json()["target_path"]) / "app/app.json").is_file()
    backend.get_bundle.assert_awaited_once_with("owned-archive")


@pytest.mark.parametrize("action", ["restore", "promote"])
@pytest.mark.parametrize("fault", ["foreign_owner", "wrong_registry"])
def test_artifact_scope_is_checked_before_reading_archive(monkeypatch, tmp_path, action, fault):
    archive = tmp_path / "bundle.zip"
    _write_bundle_zip(archive, {"app.json": '{"appId":"app_1"}'})
    version = _version(artifact_version_id="av_scoped", zip_path=archive)
    studio, client = _promote_client(monkeypatch, tmp_path / "factory", _PromoteStore(version))
    registry = studio._get_app_registry_service()
    if fault == "foreign_owner":
        monkeypatch.setattr(registry, "get_app_record", AsyncMock(return_value={"app": None}))
    else:
        version.commit_metadata.metadata["build_registry_id"] = "another_registry"
    reader = AsyncMock(side_effect=AssertionError("Archive must not be read before scope checks"))
    monkeypatch.setattr(studio, "read_verified_artifact_bundle", reader)
    response = _restore_or_promote(client, version, action)
    assert response.status_code == (404 if fault == "foreign_owner" else 409)
    reader.assert_not_awaited()
    assert registry.promote_calls == []
    assert not (tmp_path / "workspaces").exists()


@pytest.mark.asyncio
async def test_verified_archive_reader_enforces_byte_limit(tmp_path):
    from mozaiksai.core.artifacts.content_store import (
        ContentIntegrityError,
        read_verified_artifact_bundle,
    )

    archive = tmp_path / "bundle.zip"
    _write_bundle_zip(archive, {"app.json": '{"appId":"app_1"}'})
    version = _version(artifact_version_id="av_bounded", zip_path=archive)
    raw = archive.read_bytes()
    assert await read_verified_artifact_bundle(version, max_bytes=len(raw)) == raw
    with pytest.raises(ContentIntegrityError, match="archive_too_large"):
        await read_verified_artifact_bundle(version, max_bytes=len(raw) - 1)


def test_promote_restores_current_app_bundle_from_staged_refinement(monkeypatch, tmp_path: Path) -> None:
    bundle_zip = tmp_path / "bundle.zip"
    _write_bundle_zip(
        bundle_zip,
        {
            "GeneratedApp/src/App.jsx": "export default function App() { return <div>Promoted</div>; }\n",
            "GeneratedApp/package.json": "{\"name\":\"demo\"}\n",
        },
    )
    version = _version(
        artifact_version_id="av_current_1",
        zip_path=bundle_zip,
        lifecycle_status=ArtifactLifecycleStatus.CURRENT,
        validation_status=ArtifactValidationStatus.PASSED,
        refinement_request_id="refine_123",
    )
    session = _session(artifact_version_id=version.id, status=RefinementSessionStatus.VALIDATED)
    runtime_root = tmp_path / "runtime_app"
    store = _PromoteStore(version, sessions=[session])
    studio_app, client = _promote_client(monkeypatch, runtime_root, store)
    factory_root = runtime_root
    runtime_root = runtime_root.parent / "workspaces" / "app_1" / version.id / "app"
    assert not factory_root.exists()

    monkeypatch.setattr(studio_app, "prepare_routed_workflow_launch", lambda **kwargs: pytest.fail("workflow launch should not run"))
    monkeypatch.setattr(studio_app, "launch_prepared_workflow", lambda *args, **kwargs: pytest.fail("workflow launch should not run"))

    response = client.post("/api/studio/build/artifacts/av_current_1/promote?build_registry_id=appreg_1")

    assert response.status_code == 200
    body = response.json()
    assert body["promoted"] is True
    assert body["target_path"] == str(runtime_root.parent)
    assert (runtime_root / "GeneratedApp" / "src" / "App.jsx").read_text(encoding="utf-8") == (
        "export default function App() { return <div>Promoted</div>; }\n"
    )
    assert (runtime_root / "GeneratedApp" / "package.json").read_text(encoding="utf-8") == "{\"name\":\"demo\"}\n"
    assert body["restored_files"] == ["app/GeneratedApp/package.json", "app/GeneratedApp/src/App.jsx"]
    assert store.updated_sessions[-1]["status"] == RefinementSessionStatus.PROMOTED
    assert body["app_registry"]["app"]["lifecycle_state"] == "active"
    assert body["review"]["review_status"] == "promoted"
    # promotion installs a separate workspace, so an App Intelligence refresh is
    # always attempted and reported (best-effort: failure never blocks promote)
    assert "app_intelligence_refresh" in body
    assert body["app_intelligence_refresh"] is not None


def test_promote_refuses_override_for_coding_produced_artifacts(monkeypatch, tmp_path: Path) -> None:
    # Artifacts produced by the refinement coding lane must pass real
    # validation; the override escape hatch would let unvalidated model output
    # into the live app root, so the gate refuses it outright.
    bundle_zip = tmp_path / "bundle.zip"
    _write_bundle_zip(
        bundle_zip,
        {
            "GeneratedApp/src/App.jsx": "export default function App() { return <div>Promoted</div>; }\n",
        },
    )
    version = _version(
        artifact_version_id="av_skipped_validation_1",
        zip_path=bundle_zip,
        lifecycle_status=ArtifactLifecycleStatus.CURRENT,
        validation_status=ArtifactValidationStatus.SKIPPED,
        refinement_request_id="refine_skipped_validation",
    )
    runtime_root = tmp_path / "runtime_app"
    store = _PromoteStore(version)
    _, client = _promote_client(monkeypatch, runtime_root, store)
    factory_root = runtime_root
    runtime_root = runtime_root.parent / "workspaces" / "app_1" / version.id / "app"
    assert not factory_root.exists()

    blocked = client.post("/api/studio/build/artifacts/av_skipped_validation_1/promote?build_registry_id=appreg_1")

    assert blocked.status_code == 409
    assert "validation_status='passed' is required" in blocked.json()["detail"]
    assert not (runtime_root / "GeneratedApp" / "src" / "App.jsx").exists()

    overridden = client.post(
        "/api/studio/build/artifacts/av_skipped_validation_1/promote?build_registry_id=appreg_1",
        json={"allow_validation_override": True},
    )

    assert overridden.status_code == 422
    assert not (runtime_root / "GeneratedApp" / "src" / "App.jsx").exists()


def test_promote_rejects_override_for_non_coding_artifacts(monkeypatch, tmp_path: Path) -> None:
    bundle_zip = tmp_path / "bundle.zip"
    _write_bundle_zip(
        bundle_zip,
        {
            "GeneratedApp/src/App.jsx": "export default function App() { return <div>Promoted</div>; }\n",
        },
    )
    version = _version(
        artifact_version_id="av_skipped_plain_1",
        zip_path=bundle_zip,
        lifecycle_status=ArtifactLifecycleStatus.CURRENT,
        validation_status=ArtifactValidationStatus.SKIPPED,
    )
    runtime_root = tmp_path / "runtime_app"
    store = _PromoteStore(version)
    _, client = _promote_client(monkeypatch, runtime_root, store)
    factory_root = runtime_root
    runtime_root = runtime_root.parent / "workspaces" / "app_1" / version.id / "app"
    assert not factory_root.exists()

    allowed = client.post(
        "/api/studio/build/artifacts/av_skipped_plain_1/promote?build_registry_id=appreg_1",
        json={"allow_validation_override": True},
    )

    assert allowed.status_code == 422
    assert not (runtime_root / "GeneratedApp" / "src" / "App.jsx").exists()


@pytest.mark.parametrize("build_status", [None, "pending", "skipped", "failed"])
@pytest.mark.parametrize("operation", ["promote", "restore"])
def test_legacy_contract_pass_cannot_activate_without_passed_build(monkeypatch, tmp_path, build_status, operation):
    archive = tmp_path / "bundle.zip"
    _write_bundle_zip(archive, {"app.json": '{"appId":"app_1"}'})
    version = _version(
        artifact_version_id="av_unverified", zip_path=archive,
    ).model_copy(update={"app_validation_status": build_status})
    studio, client = _promote_client(monkeypatch, tmp_path / "factory", _PromoteStore(version))
    monkeypatch.setattr(studio, "_restore_bundle_to_target", lambda **kwargs: pytest.fail("unverified candidate restored"))
    response = (
        client.post("/api/studio/build/artifacts/av_unverified/promote?build_registry_id=appreg_1")
        if operation == "promote"
        else client.post("/api/studio/build/restore?build_registry_id=appreg_1",
                         json={"artifact_version_id": "av_unverified"})
    )
    assert response.status_code == 409
    assert "whole-app build validation" in response.json()["detail"]
    assert not studio._get_app_registry_service().promote_calls


def test_promote_restores_artifact_and_marks_app_registry_active(monkeypatch, tmp_path: Path) -> None:
    bundle_zip = tmp_path / "bundle.zip"
    _write_bundle_zip(
        bundle_zip,
        {
            "GeneratedApp/src/App.jsx": "export default function App() { return <div>Promoted</div>; }\n",
        },
    )
    version = _version(
        artifact_version_id="av_registry_1",
        zip_path=bundle_zip,
        lifecycle_status=ArtifactLifecycleStatus.CURRENT,
        validation_status=ArtifactValidationStatus.PASSED,
        refinement_request_id="refine_registry",
    )
    runtime_root = tmp_path / "runtime_app"
    store = _PromoteStore(version)
    studio_app, client = _promote_client(monkeypatch, runtime_root, store)
    factory_root = runtime_root
    runtime_root = runtime_root.parent / "workspaces" / "app_1" / version.id / "app"
    assert not factory_root.exists()
    app_registry = _AppRegistryServiceDouble(artifact_version_id=version.id)
    monkeypatch.setattr(studio_app, "_get_app_registry_service", lambda: app_registry)

    response = client.post(
        "/api/studio/build/artifacts/av_registry_1/promote?build_registry_id=appreg_1",
        json={},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["promoted"] is True
    assert body["app_registry"]["success"] is True
    assert body["app_registry"]["app"]["lifecycle_state"] == "active"
    assert app_registry.promote_calls == [{"build_registry_id": "appreg_1", "promoted_by": "demo-user"}]
    assert (runtime_root / "GeneratedApp" / "src" / "App.jsx").exists()


def test_promote_restores_generated_app_bundle_as_loadable_platform_root(monkeypatch, tmp_path: Path) -> None:
    bundle_zip = tmp_path / "GeneratedApp.zip"
    bundle_entries = {
        "GeneratedApp/app.json": json.dumps(
            {
                "appId": "app_1",
                "appName": "Golden Path App",
                "version": "1.0.0",
                "startup": {"landing_spot": "/dashboard"},
            }
        ),
        "GeneratedApp/config/ai.json": json.dumps(
            {
                "chat": {"chat_startup_mode": "ask"},
                "workflows": {"entry_point": "SupportWorkflow"},
            }
        ),
        "GeneratedApp/config/shell.json": json.dumps({"shortcuts": {"header": ["dashboard"]}}),
        "GeneratedApp/ui/route_manifest.json": json.dumps(
            {
                "pages": [
                    {
                        "id": "dashboard",
                        "label": "Dashboard",
                        "path": "/dashboard",
                        "component": "SchemaPage",
                        "schema": "dashboard",
                        "order": 1,
                        "requiresAuth": False,
                        "navigation": {
                            "scope": "app",
                            "group": "main",
                            "icon": "home",
                            "order": 1,
                        },
                    }
                ]
            }
        ),
        "GeneratedApp/ui/pages/dashboard.yaml": "\n".join(
            [
                "schema_version: mozaiks.app_page.v1",
                "name: dashboard",
                "title: Dashboard",
                "route: /dashboard",
                "page_type: record_list",
                "layout: full-width",
                "sections:",
                "  - id: overview",
                "    primitive: PageHeader",
                "    config:",
                "      title: Overview",
            ]
        ),
        "GeneratedApp/modules/tasks/module.yaml": "\n".join(
            [
                "schema_version: mozaiks.module.v1",
                "module:",
                "  id: tasks",
                "  display_name: Tasks",
                "  version: 1.0.0",
                "  handler: backend.handler:TasksHandler",
                "actions:",
                "  - id: list_tasks",
                "    description: List tasks.",
                "    handler_method: list_tasks",
                "    input_schema:",
                "      type: object",
                "      additionalProperties: false",
                "    output_schema:",
                "      type: object",
                "capabilities:",
                "  - capability_id: tasks.list",
                "    kind: action",
                "    target: list_tasks",
                "    title: List tasks",
            ]
        ),
        "GeneratedApp/modules/tasks/backend/handler.py": "\n".join(
            [
                "class TasksHandler:",
                "    async def list_tasks(self, ctx, payload):",
                "        return {'tasks': []}",
                "",
            ]
        ),
        "GeneratedApp/workflows/SupportWorkflow/orchestrator.yaml": "\n".join(
            [
                "schema_version: mozaiks.orchestrator.v1\nworkflow_name: SupportWorkflow",
                "workflow_startup_mode: AgentDriven",
            ]
        ),
    }
    _write_bundle_zip(bundle_zip, bundle_entries)
    version = _version(
        artifact_version_id="av_platform_root_1",
        zip_path=bundle_zip,
        lifecycle_status=ArtifactLifecycleStatus.CURRENT,
        validation_status=ArtifactValidationStatus.PASSED,
        refinement_request_id="refine_platform_root",
    )
    runtime_root = tmp_path / "active_app"
    store = _PromoteStore(version)
    studio_app, client = _promote_client(monkeypatch, runtime_root, store)
    factory_root = runtime_root
    runtime_root = runtime_root.parent / "workspaces" / "app_1" / version.id / "app"
    assert not factory_root.exists()
    app_registry = _AppRegistryServiceDouble(artifact_version_id=version.id)
    monkeypatch.setattr(studio_app, "_get_app_registry_service", lambda: app_registry)

    response = client.post(
        "/api/studio/build/artifacts/av_platform_root_1/promote?build_registry_id=appreg_1",
        json={},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["promoted"] is True
    assert "app/app.json" in body["restored_files"]
    assert "GeneratedApp/app.json" not in body["restored_files"]
    assert (runtime_root / "app.json").exists()
    assert not (runtime_root / "GeneratedApp" / "app.json").exists()

    from mozaiksai.core.runtime.app.loader import AppLoader

    loaded = asyncio.run(AppLoader.load(str(runtime_root)))
    assert loaded.definition.name == "Golden Path App"
    assert [module.name for module in loaded.modules] == ["tasks"]
    assert [page.name for page in loaded.definition.pages] == ["dashboard"]
    assert [workflow.name for workflow in loaded.definition.workflows] == ["SupportWorkflow"]

    monkeypatch.setattr(shell_config, "resolve_app_root", lambda: runtime_root)
    monkeypatch.setattr(shell_config, "resolve_active_app_root", lambda: runtime_root)
    shell = asyncio.run(shell_config.build_shell_config(surface="platform"))
    assert shell["appId"] == "app_1"
    assert shell["appName"] == "Golden Path App"
    assert shell["landing_spot"] == "/dashboard"
    dashboard = next(page for page in shell["pages"] if page["path"] == "/dashboard")
    assert dashboard["component"] == "SchemaPage"
    assert dashboard["schema"] == "dashboard"
    assert dashboard["meta"]["appShell"] is True
    assert body["app_registry"]["app"]["lifecycle_state"] == "active"


def test_promote_rejects_registry_record_not_in_review(monkeypatch, tmp_path: Path) -> None:
    bundle_zip = tmp_path / "bundle.zip"
    _write_bundle_zip(
        bundle_zip,
        {"GeneratedApp/src/App.jsx": "export default function App() { return <div>Active</div>; }\n"},
    )
    version = _version(
        artifact_version_id="av_registry_active_1",
        zip_path=bundle_zip,
        lifecycle_status=ArtifactLifecycleStatus.CURRENT,
        validation_status=ArtifactValidationStatus.PASSED,
    )
    runtime_root = tmp_path / "runtime_app"
    store = _PromoteStore(version)
    studio_app, client = _promote_client(monkeypatch, runtime_root, store)
    factory_root = runtime_root
    runtime_root = runtime_root.parent / "workspaces" / "app_1" / version.id / "app"
    assert not factory_root.exists()
    app_registry = _AppRegistryServiceDouble(lifecycle_state="active")
    monkeypatch.setattr(studio_app, "_get_app_registry_service", lambda: app_registry)

    response = client.post(
        "/api/studio/build/artifacts/av_registry_active_1/promote?build_registry_id=appreg_1",
        json={},
    )

    assert response.status_code == 409
    assert "records in review" in response.json()["detail"]
    assert app_registry.promote_calls == []
    assert not (runtime_root / "GeneratedApp").exists()


def test_promote_rejects_draft_app_bundle(monkeypatch, tmp_path: Path) -> None:
    bundle_zip = tmp_path / "bundle.zip"
    _write_bundle_zip(bundle_zip, {"GeneratedApp/src/App.jsx": "draft\n"})
    version = _version(
        artifact_version_id="av_draft_1",
        zip_path=bundle_zip,
        lifecycle_status=ArtifactLifecycleStatus.DRAFT,
    )
    store = _PromoteStore(version)
    _, client = _promote_client(monkeypatch, tmp_path / "runtime_app", store)

    response = client.post("/api/studio/build/artifacts/av_draft_1/promote?build_registry_id=appreg_1")

    assert response.status_code == 409
    assert "current artifact versions" in response.json()["detail"]


def test_promote_rejects_non_app_bundle_artifact(monkeypatch, tmp_path: Path) -> None:
    bundle_zip = tmp_path / "bundle.zip"
    _write_bundle_zip(bundle_zip, {"workflows/AppGenerator/orchestrator.yaml": "name: workflow\n"})
    version = _version(
        artifact_version_id="av_workflow_1",
        zip_path=bundle_zip,
        build_family="workflow_bundle",
        lifecycle_status=ArtifactLifecycleStatus.CURRENT,
    )
    store = _PromoteStore(version)
    _, client = _promote_client(monkeypatch, tmp_path / "runtime_app", store)

    response = client.post("/api/studio/build/artifacts/av_workflow_1/promote?build_registry_id=appreg_1")

    assert response.status_code == 400
    assert "Unsupported artifact kind" in response.json()["detail"]


def test_promote_rejects_missing_artifact_path(monkeypatch, tmp_path: Path) -> None:
    archive = tmp_path / "bundle.zip"
    _write_bundle_zip(archive, {"app.json": '{"appId":"app_1"}'})
    version = _version(artifact_version_id="av_missing_1", zip_path=archive)
    version.commit_metadata.metadata.pop("artifact_path")
    store = _PromoteStore(version)
    _, client = _promote_client(monkeypatch, tmp_path / "runtime_app", store)

    response = client.post("/api/studio/build/artifacts/av_missing_1/promote?build_registry_id=appreg_1")

    assert response.status_code == 409
    assert "Revalidate and save a canonical app bundle" in response.json()["detail"]
    assert not (tmp_path / "workspaces").exists()


def test_promote_rejects_missing_file_manifest(monkeypatch, tmp_path: Path) -> None:
    bundle_zip = tmp_path / "bundle.zip"
    _write_bundle_zip(bundle_zip, {"GeneratedApp/src/App.jsx": "export default function App() { return <div>Keep</div>; }\n"})
    version = _version(
        artifact_version_id="av_no_manifest_1",
        zip_path=bundle_zip,
        lifecycle_status=ArtifactLifecycleStatus.CURRENT,
        validation_status=ArtifactValidationStatus.PASSED,
        files_manifest=[],
    )
    store = _PromoteStore(version)
    _, client = _promote_client(monkeypatch, tmp_path / "runtime_app", store)

    response = client.post("/api/studio/build/artifacts/av_no_manifest_1/promote?build_registry_id=appreg_1")

    assert response.status_code == 409
    assert "Revalidate and save a canonical app bundle" in response.json()["detail"]
    assert not (tmp_path / "workspaces").exists()


def test_promote_skips_metadata_and_backup_entries(monkeypatch, tmp_path: Path) -> None:
    bundle_zip = tmp_path / "bundle.zip"
    _write_bundle_zip(
        bundle_zip,
        {
            "GeneratedApp/src/App.jsx": "export default function App() { return <div>Keep</div>; }\n",
            "GeneratedApp/refinement_plan.json": "{}\n",
            "GeneratedApp/affected_paths.json": "{\"affected\": true}\n",
            "GeneratedApp/refinement_review.json": "{\"review\": true}\n",
            "GeneratedApp/execution_result.json": "{\"result\": true}\n",
            "GeneratedApp/backups/old.txt": "backup\n",
        },
    )
    version = _version(
        artifact_version_id="av_current_meta_1",
        zip_path=bundle_zip,
        lifecycle_status=ArtifactLifecycleStatus.CURRENT,
        validation_status=ArtifactValidationStatus.PASSED,
        refinement_request_id="refine_789",
    )
    store = _PromoteStore(version)
    runtime_root = tmp_path / "runtime_app"
    _, client = _promote_client(monkeypatch, runtime_root, store)
    factory_root = runtime_root
    runtime_root = runtime_root.parent / "workspaces" / "app_1" / version.id / "app"
    assert not factory_root.exists()

    response = client.post("/api/studio/build/artifacts/av_current_meta_1/promote?build_registry_id=appreg_1")

    assert response.status_code == 200
    assert (runtime_root / "GeneratedApp" / "src" / "App.jsx").exists()
    assert not (runtime_root / "GeneratedApp" / "refinement_plan.json").exists()
    assert not (runtime_root / "GeneratedApp" / "affected_paths.json").exists()
    assert not (runtime_root / "GeneratedApp" / "refinement_review.json").exists()
    assert not (runtime_root / "GeneratedApp" / "execution_result.json").exists()
    assert not (runtime_root / "GeneratedApp" / "backups").exists()
    assert "GeneratedApp/refinement_plan.json" not in response.json()["restored_files"]


def test_promote_blocks_path_traversal_entries(monkeypatch, tmp_path: Path) -> None:
    bundle_zip = tmp_path / "bundle.zip"
    _write_bundle_zip(
        bundle_zip,
        {
            "GeneratedApp/src/App.jsx": "export default function App() { return <div>Unsafe</div>; }\n",
            "../escape.txt": "escape\n",
        },
    )
    version = _version(
        artifact_version_id="av_traversal_1",
        zip_path=bundle_zip,
        lifecycle_status=ArtifactLifecycleStatus.CURRENT,
        validation_status=ArtifactValidationStatus.PASSED,
        refinement_request_id="refine_111",
    )
    store = _PromoteStore(version)
    runtime_root = tmp_path / "runtime_app"
    _, client = _promote_client(monkeypatch, runtime_root, store)
    factory_root = runtime_root
    runtime_root = runtime_root.parent / "workspaces" / "app_1" / version.id / "app"
    assert not factory_root.exists()

    response = client.post("/api/studio/build/artifacts/av_traversal_1/promote?build_registry_id=appreg_1")

    assert response.status_code == 400
    assert "Unsafe artifact archive entry" in response.json()["detail"]
    assert not (tmp_path / "escape.txt").exists()


def test_promote_blocks_absolute_path_entries(monkeypatch, tmp_path: Path) -> None:
    bundle_zip = tmp_path / "bundle.zip"
    _write_bundle_zip(
        bundle_zip,
        {
            "GeneratedApp/src/App.jsx": "export default function App() { return <div>Unsafe</div>; }\n",
            "C:/escape.txt": "escape\n",
        },
    )
    version = _version(
        artifact_version_id="av_absolute_1",
        zip_path=bundle_zip,
        lifecycle_status=ArtifactLifecycleStatus.CURRENT,
        validation_status=ArtifactValidationStatus.PASSED,
        refinement_request_id="refine_222",
    )
    store = _PromoteStore(version)
    runtime_root = tmp_path / "runtime_app"
    _, client = _promote_client(monkeypatch, runtime_root, store)
    factory_root = runtime_root
    runtime_root = runtime_root.parent / "workspaces" / "app_1" / version.id / "app"
    assert not factory_root.exists()

    response = client.post("/api/studio/build/artifacts/av_absolute_1/promote?build_registry_id=appreg_1")

    assert response.status_code == 400
    assert "Unsafe artifact archive entry" in response.json()["detail"]
    assert not (tmp_path / "escape.txt").exists()


def test_promote_skips_symlink_entries(monkeypatch, tmp_path: Path) -> None:
    bundle_zip = tmp_path / "bundle.zip"
    _write_bundle_zip(
        bundle_zip,
        {
            "GeneratedApp/src/App.jsx": "export default function App() { return <div>Safe</div>; }\n",
        },
        symlink_entry=("GeneratedApp/src/App.link.jsx", "src/App.jsx"),
    )
    version = _version(
        artifact_version_id="av_symlink_1",
        zip_path=bundle_zip,
        lifecycle_status=ArtifactLifecycleStatus.CURRENT,
        validation_status=ArtifactValidationStatus.PASSED,
        refinement_request_id="refine_333",
    )
    store = _PromoteStore(version)
    runtime_root = tmp_path / "runtime_app"
    _, client = _promote_client(monkeypatch, runtime_root, store)
    factory_root = runtime_root
    runtime_root = runtime_root.parent / "workspaces" / "app_1" / version.id / "app"
    assert not factory_root.exists()

    response = client.post("/api/studio/build/artifacts/av_symlink_1/promote?build_registry_id=appreg_1")

    assert response.status_code == 200
    assert (runtime_root / "GeneratedApp" / "src" / "App.jsx").exists()
    assert not (runtime_root / "GeneratedApp" / "src" / "App.link.jsx").exists()
    assert "GeneratedApp/src/App.link.jsx" not in response.json()["restored_files"]


def test_promote_endpoint_source_does_not_call_workflow_or_llm_paths() -> None:
    from mozaiksai.hosts import studio as studio_app

    source = inspect.getsource(studio_app.promote_build_artifact_version)
    assert "prepare_routed_workflow_launch" not in source
    assert "launch_prepared_workflow" not in source
    assert "AppGenerator" not in source

