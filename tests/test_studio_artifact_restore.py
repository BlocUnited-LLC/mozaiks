from __future__ import annotations

import asyncio
import inspect
import json
import stat
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mozaiksai.core.artifacts import (
    ArtifactCommitMetadata,
    BuildRecordStatus,
    BuildRecordValidationStatus,
    BuildRecord,
    RefinementSessionDoc,
    RefinementSessionStatus,
)
from mozaiksai.core.auth import reset_auth_adapter


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
    build_record_id: str,
    zip_path: Path,
    build_family: str = "app_bundle",
    lifecycle_status: BuildRecordStatus = BuildRecordStatus.CURRENT,
    validation_status: BuildRecordValidationStatus = BuildRecordValidationStatus.PASSED,
    refinement_request_id: str | None = None,
    files_manifest: list[dict[str, object]] | None = None,
    commit_metadata_extra: dict[str, object] | None = None,
) -> BuildRecord:
    metadata: dict[str, object] = {"artifact_path": str(zip_path)}
    metadata.update(commit_metadata_extra or {})
    if refinement_request_id is not None:
        metadata["refinement"] = {
            "request_id": refinement_request_id,
            "review": {
                "status": "promotion_ready",
                "promotion_allowed": True,
            },
        }
    return BuildRecord.model_validate(
        {
            "_id": build_record_id,
            "app_id": "app_1",
            "build_family": build_family,
            "build_key": "app_bundle",
            "version_number": 2,
            "parent_version_id": "av_parent_1" if build_record_id != "av_parent_1" else None,
            "lineage_root_id": "av_parent_1",
            "source_workflow": "AppGenerator",
            "source_chat_id": "chat_1",
            "canonical_inputs_version": {},
            "lifecycle_status": lifecycle_status.value,
            "validation_status": validation_status.value,
            "files_manifest": files_manifest or [],
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
    build_record_id: str,
    status: RefinementSessionStatus,
) -> RefinementSessionDoc:
    return RefinementSessionDoc.model_validate(
        {
            "_id": "rs_1",
            "app_id": "app_1",
            "build_record_id": build_record_id,
            "result_build_record_id": build_record_id,
            "change_request_id": "cr_1",
            "provider": "control_plane_coding",
            "status": status.value,
            "metadata": {},
        }
    )


class _PromoteStore:
    def __init__(self, version: BuildRecord, *, sessions: list[RefinementSessionDoc] | None = None) -> None:
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
    ) -> None:
        self.app = {
            "build_registry_id": build_registry_id,
            "app_id": app_id,
            "lifecycle_state": lifecycle_state,
            "bundle_path": "generated/apps/app_1/build_1/app",
        }
        self.promote_calls: list[dict[str, str | None]] = []

    async def get_app_record(self, *, app_id: str | None = None, build_registry_id: str | None = None):
        if build_registry_id == self.app["build_registry_id"] or app_id == self.app["app_id"]:
            return {"app": dict(self.app)}
        return {"app": None}

    async def promote_build(self, *, build_registry_id: str, promoted_by: str | None):
        self.promote_calls.append({"build_registry_id": build_registry_id, "promoted_by": promoted_by})
        self.app = {**self.app, "lifecycle_state": "active"}
        return {"success": True, "app": dict(self.app)}


def _studio_app(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    sys.modules.pop("factory_app", None)
    from mozaiksai.hosts import studio as studio_app

    return studio_app


def _promote_client(monkeypatch, runtime_root: Path, store: _PromoteStore):
    studio_app = _studio_app(monkeypatch)
    monkeypatch.setattr(studio_app, "get_artifact_store", lambda: store)
    monkeypatch.setattr(studio_app, "resolve_app_root", lambda: runtime_root)
    return studio_app, TestClient(studio_app.app)


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
        build_record_id="av_current_1",
        zip_path=bundle_zip,
        lifecycle_status=BuildRecordStatus.CURRENT,
        validation_status=BuildRecordValidationStatus.PASSED,
        refinement_request_id="refine_123",
        files_manifest=[
            {"path": "GeneratedApp/src/App.jsx", "sha256": "sha-app", "size_bytes": 62},
            {"path": "GeneratedApp/package.json", "sha256": "sha-pkg", "size_bytes": 16},
        ],
    )
    session = _session(build_record_id=version.id, status=RefinementSessionStatus.VALIDATED)
    runtime_root = tmp_path / "runtime_app"
    store = _PromoteStore(version, sessions=[session])
    studio_app, client = _promote_client(monkeypatch, runtime_root, store)

    monkeypatch.setattr(studio_app, "prepare_routed_workflow_launch", lambda **kwargs: pytest.fail("workflow launch should not run"))
    monkeypatch.setattr(studio_app, "launch_prepared_workflow", lambda *args, **kwargs: pytest.fail("workflow launch should not run"))

    response = client.post("/api/studio/build/artifacts/av_current_1/promote")

    assert response.status_code == 200
    body = response.json()
    assert body["promoted"] is True
    assert body["target_path"] == str(runtime_root)
    assert (runtime_root / "GeneratedApp" / "src" / "App.jsx").read_text(encoding="utf-8") == (
        "export default function App() { return <div>Promoted</div>; }\n"
    )
    assert (runtime_root / "GeneratedApp" / "package.json").read_text(encoding="utf-8") == "{\"name\":\"demo\"}\n"
    assert body["restored_files"] == ["GeneratedApp/package.json", "GeneratedApp/src/App.jsx"]
    assert store.updated_sessions[-1]["status"] == RefinementSessionStatus.PROMOTED
    assert body["app_registry"] is None
    assert body["review"]["review_status"] == "promoted"


def test_promote_requires_override_for_skipped_validation(monkeypatch, tmp_path: Path) -> None:
    bundle_zip = tmp_path / "bundle.zip"
    _write_bundle_zip(
        bundle_zip,
        {
            "GeneratedApp/src/App.jsx": "export default function App() { return <div>Promoted</div>; }\n",
        },
    )
    version = _version(
        build_record_id="av_skipped_validation_1",
        zip_path=bundle_zip,
        lifecycle_status=BuildRecordStatus.CURRENT,
        validation_status=BuildRecordValidationStatus.SKIPPED,
        refinement_request_id="refine_skipped_validation",
        files_manifest=[
            {"path": "GeneratedApp/src/App.jsx", "sha256": "sha-app", "size_bytes": 62},
        ],
    )
    runtime_root = tmp_path / "runtime_app"
    store = _PromoteStore(version)
    _, client = _promote_client(monkeypatch, runtime_root, store)

    blocked = client.post("/api/studio/build/artifacts/av_skipped_validation_1/promote")

    assert blocked.status_code == 409
    assert "validation_status='passed' is required" in blocked.json()["detail"]
    assert not (runtime_root / "GeneratedApp" / "src" / "App.jsx").exists()

    allowed = client.post(
        "/api/studio/build/artifacts/av_skipped_validation_1/promote",
        json={"allow_validation_override": True},
    )

    assert allowed.status_code == 200
    assert allowed.json()["promoted"] is True
    assert (runtime_root / "GeneratedApp" / "src" / "App.jsx").exists()


def test_promote_restores_artifact_and_marks_app_registry_active(monkeypatch, tmp_path: Path) -> None:
    bundle_zip = tmp_path / "bundle.zip"
    _write_bundle_zip(
        bundle_zip,
        {
            "GeneratedApp/src/App.jsx": "export default function App() { return <div>Promoted</div>; }\n",
        },
    )
    version = _version(
        build_record_id="av_registry_1",
        zip_path=bundle_zip,
        lifecycle_status=BuildRecordStatus.CURRENT,
        validation_status=BuildRecordValidationStatus.PASSED,
        refinement_request_id="refine_registry",
        files_manifest=[
            {"path": "GeneratedApp/src/App.jsx", "sha256": "sha-app", "size_bytes": 62},
        ],
    )
    runtime_root = tmp_path / "runtime_app"
    store = _PromoteStore(version)
    studio_app, client = _promote_client(monkeypatch, runtime_root, store)
    app_registry = _AppRegistryServiceDouble()
    monkeypatch.setattr(studio_app, "_get_app_registry_service", lambda: app_registry)

    response = client.post(
        "/api/studio/build/artifacts/av_registry_1/promote?app_id=app_1",
        json={"build_registry_id": "appreg_1"},
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
                "name: dashboard",
                "title: Dashboard",
                "route: /dashboard",
                "sections:",
                "  - id: overview",
                "    title: Overview",
                "    layout: stack",
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
                "workflow_name: SupportWorkflow",
                "workflow_startup_mode: AgentDriven",
            ]
        ),
    }
    _write_bundle_zip(bundle_zip, bundle_entries)
    version = _version(
        build_record_id="av_platform_root_1",
        zip_path=bundle_zip,
        lifecycle_status=BuildRecordStatus.CURRENT,
        validation_status=BuildRecordValidationStatus.PASSED,
        refinement_request_id="refine_platform_root",
        files_manifest=[
            {"path": path, "sha256": f"sha-{index}", "size_bytes": len(content)}
            for index, (path, content) in enumerate(bundle_entries.items(), start=1)
        ],
    )
    runtime_root = tmp_path / "active_app"
    store = _PromoteStore(version)
    studio_app, client = _promote_client(monkeypatch, runtime_root, store)
    app_registry = _AppRegistryServiceDouble()
    monkeypatch.setattr(studio_app, "_get_app_registry_service", lambda: app_registry)

    response = client.post(
        "/api/studio/build/artifacts/av_platform_root_1/promote?app_id=app_1",
        json={"build_registry_id": "appreg_1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["promoted"] is True
    assert "app.json" in body["restored_files"]
    assert "GeneratedApp/app.json" not in body["restored_files"]
    assert (runtime_root / "app.json").exists()
    assert not (runtime_root / "GeneratedApp" / "app.json").exists()

    from mozaiksai.core.runtime.app.loader import AppLoader
    from mozaiksai.hosts import platform as platform_app

    loaded = asyncio.run(AppLoader.load(str(runtime_root)))
    assert loaded.definition.name == "Golden Path App"
    assert [module.name for module in loaded.modules] == ["tasks"]
    assert [page.name for page in loaded.definition.pages] == ["dashboard"]
    assert [workflow.name for workflow in loaded.definition.workflows] == ["SupportWorkflow"]

    monkeypatch.setattr(platform_app, "resolve_app_root", lambda: runtime_root)
    monkeypatch.setattr(platform_app, "resolve_active_app_root", lambda: runtime_root)
    shell = asyncio.run(platform_app.build_shell_config(surface="platform"))
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
        build_record_id="av_registry_active_1",
        zip_path=bundle_zip,
        lifecycle_status=BuildRecordStatus.CURRENT,
        validation_status=BuildRecordValidationStatus.PASSED,
        files_manifest=[
            {"path": "GeneratedApp/src/App.jsx", "sha256": "sha-app", "size_bytes": 60},
        ],
    )
    runtime_root = tmp_path / "runtime_app"
    store = _PromoteStore(version)
    studio_app, client = _promote_client(monkeypatch, runtime_root, store)
    app_registry = _AppRegistryServiceDouble(lifecycle_state="active")
    monkeypatch.setattr(studio_app, "_get_app_registry_service", lambda: app_registry)

    response = client.post(
        "/api/studio/build/artifacts/av_registry_active_1/promote?app_id=app_1",
        json={"build_registry_id": "appreg_1"},
    )

    assert response.status_code == 409
    assert "records in review" in response.json()["detail"]
    assert app_registry.promote_calls == []
    assert not (runtime_root / "GeneratedApp").exists()


def test_promote_rejects_draft_app_bundle(monkeypatch, tmp_path: Path) -> None:
    bundle_zip = tmp_path / "bundle.zip"
    _write_bundle_zip(bundle_zip, {"GeneratedApp/src/App.jsx": "draft\n"})
    version = _version(
        build_record_id="av_draft_1",
        zip_path=bundle_zip,
        lifecycle_status=BuildRecordStatus.DRAFT,
    )
    store = _PromoteStore(version)
    _, client = _promote_client(monkeypatch, tmp_path / "runtime_app", store)

    response = client.post("/api/studio/build/artifacts/av_draft_1/promote")

    assert response.status_code == 409
    assert "current artifact versions" in response.json()["detail"]


def test_promote_rejects_non_app_bundle_artifact(monkeypatch, tmp_path: Path) -> None:
    bundle_zip = tmp_path / "bundle.zip"
    _write_bundle_zip(bundle_zip, {"workflows/AppGenerator/orchestrator.yaml": "name: workflow\n"})
    version = _version(
        build_record_id="av_workflow_1",
        zip_path=bundle_zip,
        build_family="workflow_bundle",
        lifecycle_status=BuildRecordStatus.CURRENT,
    )
    store = _PromoteStore(version)
    _, client = _promote_client(monkeypatch, tmp_path / "runtime_app", store)

    response = client.post("/api/studio/build/artifacts/av_workflow_1/promote")

    assert response.status_code == 400
    assert "Unsupported artifact kind" in response.json()["detail"]


def test_promote_rejects_missing_artifact_path(monkeypatch, tmp_path: Path) -> None:
    version = BuildRecord.model_validate(
        {
            "_id": "av_missing_1",
            "app_id": "app_1",
            "build_family": "app_bundle",
            "build_key": "app_bundle",
            "version_number": 2,
            "lineage_root_id": "av_missing_1",
            "canonical_inputs_version": {},
            "lifecycle_status": BuildRecordStatus.CURRENT.value,
            "validation_status": BuildRecordValidationStatus.PASSED.value,
            "files_manifest": [],
            "commit_metadata": {"metadata": {"refinement": {"request_id": "refine_missing"}}},
        }
    )
    store = _PromoteStore(version)
    _, client = _promote_client(monkeypatch, tmp_path / "runtime_app", store)

    response = client.post("/api/studio/build/artifacts/av_missing_1/promote")

    assert response.status_code == 400
    assert "no restorable file path" in response.json()["detail"]


def test_promote_rejects_missing_file_manifest(monkeypatch, tmp_path: Path) -> None:
    bundle_zip = tmp_path / "bundle.zip"
    _write_bundle_zip(bundle_zip, {"GeneratedApp/src/App.jsx": "export default function App() { return <div>Keep</div>; }\n"})
    version = _version(
        build_record_id="av_no_manifest_1",
        zip_path=bundle_zip,
        lifecycle_status=BuildRecordStatus.CURRENT,
        validation_status=BuildRecordValidationStatus.PASSED,
        files_manifest=[],
    )
    store = _PromoteStore(version)
    _, client = _promote_client(monkeypatch, tmp_path / "runtime_app", store)

    response = client.post("/api/studio/build/artifacts/av_no_manifest_1/promote")

    assert response.status_code == 400
    assert "file manifest" in response.json()["detail"]


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
        build_record_id="av_current_meta_1",
        zip_path=bundle_zip,
        lifecycle_status=BuildRecordStatus.CURRENT,
        validation_status=BuildRecordValidationStatus.PASSED,
        refinement_request_id="refine_789",
        files_manifest=[
            {"path": "GeneratedApp/src/App.jsx", "sha256": "sha-app", "size_bytes": 58},
        ],
    )
    store = _PromoteStore(version)
    runtime_root = tmp_path / "runtime_app"
    _, client = _promote_client(monkeypatch, runtime_root, store)

    response = client.post("/api/studio/build/artifacts/av_current_meta_1/promote")

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
        build_record_id="av_traversal_1",
        zip_path=bundle_zip,
        lifecycle_status=BuildRecordStatus.CURRENT,
        validation_status=BuildRecordValidationStatus.PASSED,
        refinement_request_id="refine_111",
        files_manifest=[
            {"path": "GeneratedApp/src/App.jsx", "sha256": "sha-app", "size_bytes": 60},
        ],
    )
    store = _PromoteStore(version)
    runtime_root = tmp_path / "runtime_app"
    _, client = _promote_client(monkeypatch, runtime_root, store)

    response = client.post("/api/studio/build/artifacts/av_traversal_1/promote")

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
        build_record_id="av_absolute_1",
        zip_path=bundle_zip,
        lifecycle_status=BuildRecordStatus.CURRENT,
        validation_status=BuildRecordValidationStatus.PASSED,
        refinement_request_id="refine_222",
        files_manifest=[
            {"path": "GeneratedApp/src/App.jsx", "sha256": "sha-app", "size_bytes": 60},
        ],
    )
    store = _PromoteStore(version)
    runtime_root = tmp_path / "runtime_app"
    _, client = _promote_client(monkeypatch, runtime_root, store)

    response = client.post("/api/studio/build/artifacts/av_absolute_1/promote")

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
        build_record_id="av_symlink_1",
        zip_path=bundle_zip,
        lifecycle_status=BuildRecordStatus.CURRENT,
        validation_status=BuildRecordValidationStatus.PASSED,
        refinement_request_id="refine_333",
        files_manifest=[
            {"path": "GeneratedApp/src/App.jsx", "sha256": "sha-app", "size_bytes": 59},
        ],
    )
    store = _PromoteStore(version)
    runtime_root = tmp_path / "runtime_app"
    _, client = _promote_client(monkeypatch, runtime_root, store)

    response = client.post("/api/studio/build/artifacts/av_symlink_1/promote")

    assert response.status_code == 200
    assert (runtime_root / "GeneratedApp" / "src" / "App.jsx").exists()
    assert not (runtime_root / "GeneratedApp" / "src" / "App.link.jsx").exists()
    assert "GeneratedApp/src/App.link.jsx" not in response.json()["restored_files"]


def test_promote_endpoint_source_does_not_call_workflow_or_llm_paths() -> None:
    from mozaiksai.hosts import studio as studio_app

    source = inspect.getsource(studio_app.promote_build_build_record)
    assert "prepare_routed_workflow_launch" not in source
    assert "launch_prepared_workflow" not in source
    assert "AppGenerator" not in source


